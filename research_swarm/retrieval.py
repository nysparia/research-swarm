"""Bounded multi-query retrieval and one-hop citation discovery (stdlib only).

No model is needed to generate the conservative query variants. API metadata,
rankings and citation edges never imply scientific support or full-text reading.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote, quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .search_settings import normalize_search


ENDPOINTS = {'openalex': 'https://api.openalex.org/works',
             'arxiv': 'https://export.arxiv.org/api/query',
             'semantic_scholar': 'https://api.semanticscholar.org/graph/v1',
             'crossref': 'https://api.crossref.org/works'}
FIELDS = 'paperId,title,abstract,year,authors,venue,externalIds,citationCount,url,openAccessPdf'
SYNONYMS = (('large language model', 'llm'), ('graph neural network', 'gnn'),
            ('retrieval augmented generation', 'rag'), ('reinforcement learning', 'rl'),
            ('out of distribution', 'ood'), ('parameter efficient fine tuning', 'peft'))
_RATE_LOCK = threading.Lock()
_NEXT_REQUEST = {}


def doi(value):
    value = unquote(str(value or '')).strip().lower()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value)
    return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else None


def arxiv_id(value):
    value = unquote(str(value or '')).strip()
    value = re.sub(r'^(?:https?://(?:export\.|www\.)?arxiv\.org/(?:abs|pdf)/|10\.48550/arxiv\.|arxiv:)', '', value, flags=re.I)
    value = re.sub(r'(?:v\d+)?(?:\.pdf)?$', '', value, flags=re.I)
    return value.lower() if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]*/\d{7})', value, re.I) else None


def normalized_title(value):
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold()))


def identifiers(paper):
    result = []
    for key, normalizer in (('DOI', doi), ('ArxivId', arxiv_id)):
        value = normalizer(paper.get(key))
        if value:
            result.append(key + ':' + value)
    derived = arxiv_id(paper.get('DOI'))
    if derived:
        result.append('ArxivId:' + derived)
    for key in ('OpenAlexId', 'S2PaperId'):
        if paper.get(key):
            result.append(key + ':' + str(paper[key]).rstrip('/').rsplit('/', 1)[-1].lower())
    for record in paper.get('records', []):
        metadata = record.get('metadata')
        if isinstance(metadata, dict):
            result.extend(identifiers({k: v for k, v in metadata.items() if k != 'records'}))
    return set(result)


def same_paper(a, b):
    if identifiers(a) & identifiers(b):
        return True
    # Different persistent identifiers are not overwritten by a fuzzy title hit.
    if doi(a.get('DOI')) and doi(b.get('DOI')) and doi(a['DOI']) != doi(b['DOI']):
        return False
    title = normalized_title(a.get('Title'))
    if len(title) < 25 or title != normalized_title(b.get('Title')):
        return False
    if not a.get('Year') or not b.get('Year') or abs(a['Year'] - b['Year']) > 1:
        return False
    authors = lambda p: {normalized_title(s) for s in (p.get('Authors') or '').split(';') if s.strip()}
    return bool(authors(a) & authors(b))


def merge_paper(target, incoming):
    for key in ('Title', 'Abstract', 'Year', 'Venue', 'DOI', 'ArxivId', 'S2PaperId', 'OpenAlexId', 'Authors', 'Publisher', 'URL'):
        if incoming.get(key) and (not target.get(key) or (key == 'Abstract' and len(incoming[key]) > len(target[key]))):
            target[key] = incoming[key]
    target['CitationCount'] = max(target.get('CitationCount') or 0, incoming.get('CitationCount') or 0)
    for key in ('records', 'discoveries', 'OAUrls', 'references'):
        current = target.setdefault(key, [])
        for item in incoming.get(key, []):
            if item not in current:
                current.append(copy.deepcopy(item))
    return target


def deduplicate(papers):
    result = []
    for paper in papers:
        matches = [p for p in result if same_paper(p, paper)]
        if not matches:
            result.append(copy.deepcopy(paper))
        else:
            primary = matches[0]
            merge_paper(primary, paper)
            for other in matches[1:]:
                merge_paper(primary, other)
                result.remove(other)
    return result


def query_plan(query, settings):
    base = ' '.join(query.split())[:1000]
    # Conservative acronym expansion; never invent a translation or a method.
    alternative = base
    for long, short in SYNONYMS:
        if re.search(r'\b' + re.escape(short) + r'\b', base, re.I):
            alternative = re.sub(r'\b' + re.escape(short) + r'\b', long, alternative, flags=re.I)
        elif long in base.lower():
            alternative = re.sub(re.escape(long), short, alternative, flags=re.I)
    variants = [(base, 'core', None), (base, 'recent', settings['yearFrom'])]
    if alternative != base:
        variants.append((alternative, 'synonym', None))
    for suffix, intent in [('(survey OR review)', 'review'), ('(benchmark OR evaluation)', 'benchmark'),
                           ('(limitations OR failure)', 'counterevidence'), ('(method OR comparison)', 'methods'),
                           ('(replication OR reproducibility)', 'replication'), ('dataset', 'data'),
                           ('ablation', 'ablation'), ('(theory OR convergence)', 'theory')]:
        variants.append((base + ' ' + suffix, intent, None))
    # Deep mode explores the same lenses with the alternate terminology and
    # recent windows, rather than silently repeating identical requests.
    for text, intent, year in list(variants[2:]):
        variants.append((text, intent + '_recent', settings['yearFrom']))
        if alternative != base:
            variants.append((text.replace(base, alternative, 1), intent + '_synonym', year))
    seen, result = set(), []
    for text, intent, year in variants:
        key = (text.lower(), year)
        if key not in seen:
            result.append({'query': text, 'intent': intent, 'yearFrom': year})
            seen.add(key)
    return result[:settings['queryCount']]


def arxiv_query(text, year=None):
    # Parse grouping and AND precedence rather than expanding "A (B OR C)"
    # into "A AND B OR C", which would retrieve unrelated C-only papers.
    tokens = re.findall(r'"[^"]+"|[()]|[\w-]+', text, re.UNICODE)
    position = 0

    def atom(depth):
        nonlocal position
        if depth > 12 or position >= len(tokens):
            raise ValueError('arxiv_query_error')
        token = tokens[position]
        position += 1
        if token == '(':
            expression = disjunction(depth + 1)
            if position >= len(tokens) or tokens[position] != ')':
                raise ValueError('arxiv_query_error')
            position += 1
            return '(' + expression + ')'
        if token in (')', 'OR', 'AND', 'ANDNOT'):
            raise ValueError('arxiv_query_error')
        clean = token.strip('"')
        return f'(ti:"{clean}" OR abs:"{clean}")'

    def conjunction(depth):
        nonlocal position
        parts = [atom(depth)]
        while position < len(tokens) and tokens[position] not in ('OR', ')'):
            operator = 'AND'
            if tokens[position] in ('AND', 'ANDNOT'):
                operator = tokens[position]
                position += 1
            parts.extend([operator, atom(depth)])
        return ' '.join(parts)

    def disjunction(depth):
        nonlocal position
        parts = [conjunction(depth)]
        while position < len(tokens) and tokens[position] == 'OR':
            position += 1
            parts.append(conjunction(depth))
        return ' OR '.join(parts)

    expression = disjunction(0)
    if position != len(tokens):
        raise ValueError('arxiv_query_error')
    if year:
        expression = f'({expression}) AND submittedDate:[{year}01010000 TO 299912312359]'
    return expression


def failure_reason(exc):
    message = str(exc)
    return message if re.fullmatch(r'http_\d{3}|rate_limited|network_unavailable|response_too_large|arxiv_query_error', message) else 'invalid_response'


class SearchStopped(Exception):
    pass


class PublicClient:
    """Global per-service pacing, bounded retries, per-task 24h metadata cache."""
    def __init__(self, settings, keys=None, cache_dir=None, endpoints=None):
        self.settings, self.keys = settings, keys or {}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.endpoints = dict(ENDPOINTS, **(endpoints or {}))
        self.deadline = time.monotonic() + settings['maxSeconds']
        self.requests, self.cache_hits = 0, 0

    def request(self, source, path='', params=None, xml=False):
        if time.monotonic() >= self.deadline:
            raise SearchStopped('time_budget')
        params = dict(params or {})
        base = self.endpoints[source].rstrip('/') + path
        key = hashlib.sha256(json.dumps([base, params, xml], sort_keys=True).encode()).hexdigest()
        cache = self.cache_dir / (key + '.json') if self.cache_dir else None
        if cache and cache.is_file() and not cache.is_symlink() and time.time() - cache.stat().st_mtime < 86400:
            try:
                data = json.loads(cache.read_text(encoding='utf-8'))
                self.cache_hits += 1
                return data
            except (ValueError, OSError):
                pass
        headers = {'User-Agent': 'research-swarm/2.0 (literature research)', 'Accept': 'application/atom+xml' if xml else 'application/json'}
        if self.keys.get(source):
            if source == 'openalex':
                params['api_key'] = self.keys[source]
            elif source == 'semantic_scholar':
                headers['x-api-key'] = self.keys[source]
        url = base + ('?' + urlencode(params) if params else '')
        for attempt in range(2):
            if self.requests >= self.settings['maxRequests'] or time.monotonic() >= self.deadline:
                raise SearchStopped('request_or_time_budget')
            interval = {'arxiv': 3.1, 'semantic_scholar': 1.1}.get(source, .2)
            with _RATE_LOCK:
                now = time.monotonic()
                wait = max(0, _NEXT_REQUEST.get(source, 0) - now)
                _NEXT_REQUEST[source] = now + wait + interval
            if wait >= self.deadline - time.monotonic():
                raise SearchStopped('time_budget')
            if wait:
                time.sleep(wait)
            self.requests += 1
            try:
                with urlopen(Request(url, headers=headers), timeout=max(.1, min(15, self.deadline - time.monotonic()))) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('response_too_large')
                data = raw.decode('utf-8') if xml else json.loads(raw)
                # Validate XML before caching error pages.
                if xml:
                    root = ET.fromstring(data)
                    if any((e.findtext('{http://www.w3.org/2005/Atom}id') or '').endswith('/errors') for e in root.findall('{http://www.w3.org/2005/Atom}entry')):
                        raise ValueError('arxiv_query_error')
                if cache:
                    try:
                        cache.parent.mkdir(parents=True, exist_ok=True)
                        temp = cache.with_suffix('.tmp')
                        if cache.is_symlink() or temp.is_symlink():
                            raise OSError('unsafe_cache_path')
                        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                        temp.replace(cache)
                    except OSError:
                        pass  # A cache failure must not discard fetched evidence.
                return data
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt:
                    raise ValueError('http_' + str(exc.code)) from None
                retry = exc.headers.get('Retry-After', '2')
                try:
                    seconds = float(retry)
                except ValueError:
                    try:
                        seconds = parsedate_to_datetime(retry).timestamp() - time.time()
                    except (TypeError, ValueError, OverflowError):
                        seconds = 2
                seconds = max(1, seconds) if math.isfinite(seconds) else 30
                if seconds > 30 or seconds >= self.deadline - time.monotonic():
                    raise ValueError('rate_limited') from None
                time.sleep(seconds)
            except (URLError, TimeoutError, OSError) as exc:
                if attempt:
                    raise ValueError('network_unavailable') from None
        raise ValueError('network_unavailable')


def _paper(source, external_id, **fields):
    fields['DOI'] = doi(fields.get('DOI'))
    fields['ArxivId'] = arxiv_id(fields.get('ArxivId')) or arxiv_id(fields.get('DOI'))
    fields['Title'] = ' '.join((fields.get('Title') or '').split())
    if not isinstance(fields.get('Year'), int):
        fields['Year'] = None
    if not isinstance(fields.get('CitationCount'), int) or fields['CitationCount'] < 0:
        fields['CitationCount'] = 0
    fields['records'] = [{'source': source, 'externalId': str(external_id or ''),
                          'url': fields.get('URL') or '', 'metadata': copy.deepcopy(fields)}]
    fields['discoveries'] = []
    return fields


def from_openalex(w):
    abstract = w.get('abstract_inverted_index') or {}
    words = sorted((i, word) for word, positions in abstract.items() for i in positions if isinstance(i, int) and 0 <= i < 30000)
    location = w.get('primary_location') or {}
    return _paper('openalex', w.get('id'), Title=w.get('title') or w.get('display_name'),
        Abstract=' '.join(word for _, word in words), Year=w.get('publication_year'), DOI=w.get('doi'),
        OpenAlexId=w.get('id'), Authors='; '.join((a.get('author') or {}).get('display_name') or '' for a in w.get('authorships', [])),
        CitationCount=w.get('cited_by_count') or 0, Venue=(location.get('source') or {}).get('display_name') or '',
        URL=location.get('landing_page_url') or w.get('doi') or w.get('id'),
        OAUrls=[l['pdf_url'] for l in w.get('locations', []) if l.get('pdf_url') and l.get('is_oa')],
        references=w.get('referenced_works') or [])


def from_s2(w):
    ids = w.get('externalIds') or {}
    pdf = (w.get('openAccessPdf') or {}).get('url')
    return _paper('semantic_scholar', w.get('paperId'), Title=w.get('title'), Abstract=w.get('abstract') or '',
        Year=w.get('year'), DOI=ids.get('DOI'), ArxivId=ids.get('ArXiv'), S2PaperId=w.get('paperId'),
        Authors='; '.join(a.get('name') or '' for a in w.get('authors', [])), CitationCount=w.get('citationCount') or 0,
        Venue=w.get('venue') or '', URL=w.get('url') or '', OAUrls=[pdf] if pdf else [])


def from_arxiv(data):
    ns = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
    result = []
    for e in ET.fromstring(data).findall('a:entry', ns):
        url = e.findtext('a:id', '', ns)
        date = e.findtext('a:published', '', ns)
        result.append(_paper('arxiv', url, Title=e.findtext('a:title', '', ns),
            Abstract=' '.join(e.findtext('a:summary', '', ns).split()), Year=int(date[:4]) if date[:4].isdigit() else None,
            DOI=e.findtext('x:doi', '', ns), ArxivId=arxiv_id(url), Venue='arXiv', Publisher='arXiv',
            Authors='; '.join(a.findtext('a:name', '', ns) for a in e.findall('a:author', ns)),
            CitationCount=0, URL=url, OAUrls=[l.get('href') for l in e.findall('a:link', ns) if l.get('title') == 'pdf']))
    return result


def _items(data, key):
    if not isinstance(data, dict) or not isinstance(data.get(key), list) or any(not isinstance(w, dict) for w in data[key]):
        raise ValueError('invalid_response')
    return data[key]


def search_source(client, source, plan, limit):
    query, year = plan['query'], plan['yearFrom']
    if source == 'openalex':
        params = {'search': query, 'per-page': min(limit, 100), 'cursor': '*', 'sort': 'relevance_score:desc'}
        if year:
            params['filter'] = f'from_publication_date:{year}-01-01'
        return [from_openalex(w) for w in _items(client.request(source, params=params), 'results')]
    if source == 'arxiv':
        return from_arxiv(client.request(source, params={'search_query': arxiv_query(query, year),
            'start': 0, 'max_results': limit, 'sortBy': 'submittedDate' if plan['intent'] == 'recent' else 'relevance',
            'sortOrder': 'descending'}, xml=True))
    if source == 'semantic_scholar':
        # Relevance search accepts ordinary text, not OpenAlex/arXiv's Boolean
        # language. Keep the words and omit our grouping/operator syntax.
        query = ' '.join(re.sub(r'\b(?:AND|OR|ANDNOT)\b|[()]', ' ', query).split())
        params = {'query': query, 'limit': min(limit, 100), 'fields': FIELDS, 'offset': 0}
        if year:
            params['year'] = f'{year}-'
        return [from_s2(w) for w in _items(client.request(source, '/paper/search', params), 'data')]
    params = {'query.bibliographic': query, 'rows': min(limit, 100)}
    if year:
        params['filter'] = f'from-pub-date:{year}-01-01'
    data = client.request('crossref', params=params)
    papers = []
    for w in _items(data.get('message') if isinstance(data, dict) else None, 'items'):
        dates = ((w.get('issued') or {}).get('date-parts') or [[None]])[0]
        papers.append(_paper('crossref', w.get('DOI'), Title=' '.join(w.get('title') or []),
            Abstract=re.sub('<[^>]+>', ' ', w.get('abstract') or '').strip(), Year=dates[0] if dates else None,
            DOI=w.get('DOI'), Venue=' '.join(w.get('container-title') or []), Publisher=w.get('publisher') or '',
            Authors='; '.join((a.get('given', '') + ' ' + a.get('family', '')).strip() for a in w.get('author', [])),
            CitationCount=w.get('is-referenced-by-count') or 0, URL=w.get('URL') or '', OAUrls=[]))
    return papers


def relevance(paper, query):
    terms = set(re.findall(r'\w+', normalized_title(query))) - {'and', 'or', 'the', 'of', 'for', 'in'}
    text = normalized_title((paper.get('Title') or '') + ' ' + (paper.get('Abstract') or ''))
    coverage = sum(t in text for t in terms) / max(1, len(terms))
    # Query-hit rank preserves API semantic matches; citation count is not a
    # proxy for relevance and never dominates new work.
    rank = min((d.get('rank', 100) for d in paper.get('discoveries', []) if d.get('kind') == 'search'), default=100)
    return round(.75 * coverage + .20 / math.sqrt(rank) + (.05 if paper.get('Abstract') else 0), 6)


def select_diverse(papers, query, limit):
    ranked = sorted(papers, key=lambda p: relevance(p, query), reverse=True)
    sources = sorted({r['source'] for p in ranked for r in p.get('records', [])})
    selected = []
    # Reserve at most one third for source diversity; fill by relevance.
    quota = max(1, limit // max(1, len(sources) * 3))
    for source in sources:
        hits = [p for p in ranked if any(r['source'] == source for r in p.get('records', []))][:quota]
        for paper in hits:
            if paper not in selected:
                selected.append(paper)
    for paper in ranked:
        if paper not in selected:
            selected.append(paper)
        if len(selected) >= limit:
            break
    return sorted(selected[:limit], key=lambda p: relevance(p, query), reverse=True)


def citation_neighbors(client, seed, direction, limit, enabled):
    s2_id = seed.get('S2PaperId') or ('DOI:' + seed['DOI'] if seed.get('DOI') else
                                    'ARXIV:' + seed['ArxivId'] if seed.get('ArxivId') else None)
    if s2_id and 'semantic_scholar' in enabled:
        data = client.request('semantic_scholar', '/paper/' + quote(s2_id, safe=':') + '/' + direction,
                              {'fields': FIELDS, 'limit': limit, 'offset': 0})
        field = 'citedPaper' if direction == 'references' else 'citingPaper'
        return [from_s2(item[field]) for item in _items(data, 'data') if item.get(field)], 'semantic_scholar'
    if seed.get('OpenAlexId') and 'openalex' in enabled:
        if direction == 'references':
            ids = [i.rsplit('/', 1)[-1] for i in seed.get('references', [])[:limit]]
            if not ids:
                return [], 'openalex'
            filter_value = 'openalex_id:' + '|'.join(ids)
        else:
            filter_value = 'cites:' + seed['OpenAlexId'].rsplit('/', 1)[-1]
        data = client.request('openalex', params={'filter': filter_value, 'per-page': limit})
        return [from_openalex(w) for w in _items(data, 'results')], 'openalex'
    return [], None


def retrieve_candidates(query, settings=None, *, keys=None, cache_dir=None, client=None):
    settings = normalize_search(settings)
    client = client or PublicClient(settings, keys, cache_dir)
    plan = query_plan(query, settings)
    raw, edges, errors, searches, citation_searches = [], [], [], [], []
    stopped = None
    for item in plan:
        for source in settings['sources']:
            try:
                papers = search_source(client, source, item, settings['perQuery'])
                for rank, paper in enumerate(papers, 1):
                    paper['discoveries'].append(dict(item, kind='search', source=source, rank=rank))
                raw.extend(p for p in papers if p.get('Title'))
                searches.append({'source': source, **item, 'count': len(papers), 'status': 'ok'})
            except SearchStopped as exc:
                stopped = str(exc)
                break
            except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError) as exc:
                # No exception string, URL or key is allowed into exported logs.
                errors.append({'source': source, 'query': item['query'], 'stage': 'search', 'reason': failure_reason(exc)})
        if stopped:
            break
    if not stopped and len(deduplicate(raw)) < min(10, settings['candidateLimit']) and settings['crossrefFallback']:
        try:
            papers = search_source(client, 'crossref', plan[0], settings['perQuery'])
            for rank, paper in enumerate(papers, 1):
                paper['discoveries'].append(dict(plan[0], kind='search', source='crossref', rank=rank))
            raw.extend(p for p in papers if p.get('Title'))
            searches.append({'source': 'crossref', **plan[0], 'count': len(papers), 'status': 'ok'})
        except SearchStopped as exc:
            stopped = str(exc)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append({'source': 'crossref', 'stage': 'search', 'reason': failure_reason(exc)})
    initial = deduplicate(raw)
    # Freeze seeds before expansion: neighbors are never recursively expanded.
    seeds = select_diverse(initial, query, settings['seedCount']) if settings['seedCount'] else []
    if settings['citationDepth'] and not stopped:
        for seed in seeds:
            for direction in ('references', 'citations'):
                try:
                    try:
                        neighbors, provider = citation_neighbors(client, seed, direction, settings['neighborsPerSeed'], settings['sources'])
                    except (ValueError, KeyError, TypeError, AttributeError) as exc:
                        errors.append({'source': 'semantic_scholar', 'stage': direction, 'reason': failure_reason(exc)})
                        neighbors, provider = citation_neighbors(client, seed, direction, settings['neighborsPerSeed'], [s for s in settings['sources'] if s != 'semantic_scholar'])
                    citation_searches.append({'seed': sorted(identifiers(seed)), 'direction': direction,
                                              'source': provider, 'count': len(neighbors),
                                              'status': 'ok' if provider else 'no_supported_identifier_or_source'})
                    for neighbor in neighbors:
                        if not neighbor.get('Title') or same_paper(seed, neighbor):
                            continue
                        neighbor['discoveries'].append({'kind': direction, 'source': provider, 'seed': sorted(identifiers(seed))})
                        raw.append(neighbor)
                        citing, cited = (seed, neighbor) if direction == 'references' else (neighbor, seed)
                        edges.append({'from': sorted(identifiers(citing)), 'to': sorted(identifiers(cited)), 'source': provider, 'kind': 'cites'})
                except SearchStopped as exc:
                    stopped = str(exc)
                    break
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    errors.append({'source': 'openalex', 'stage': direction, 'reason': failure_reason(exc)})
            if stopped:
                break
    unique = deduplicate(raw)
    candidates = select_diverse(unique, query, settings['candidateLimit'])
    for p in candidates:
        p['retrievalScore'] = relevance(p, query)
    source_counts = {s: sum(any(r['source'] == s for r in p['records']) for p in candidates)
                     for s in (*settings['sources'], 'crossref')}
    status = 'partial' if errors or stopped else 'complete'
    if not candidates and not searches:
        status = 'failed'
    return {'papers': candidates, 'edges': edges, 'summary': {
        'version': 'multi-source/v1', 'status': status, 'query': query, 'settings': settings,
        'queries': plan, 'searches': searches, 'citationSearches': citation_searches, 'errors': errors, 'stopReason': stopped,
        'requestCount': client.requests, 'cacheHits': client.cache_hits,
        'rawCount': len(raw), 'uniqueCount': len(unique), 'candidateCount': len(candidates),
        'duplicateCount': len(raw) - len(unique), 'sourceCounts': source_counts,
        'seedCount': len(seeds) if settings['citationDepth'] else 0,
        'citationEdgeCount': len(edges)}}
