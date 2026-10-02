"""Bounded terminology lookup for requirement writing, never scientific evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = 'https://api.tavily.com/search'
POLICY_VERSION = 'concept-definition-v1'
MAX_CONCEPTS = 3
MAX_REQUESTS = 3
MAX_SECONDS = 30
CACHE_SECONDS = 24 * 60 * 60
UNAVAILABLE_MESSAGE = '概念搜索服务暂不可用'


class ObsoleteDraft(Exception):
    """The user has replaced this requirement operation."""


def normalize_settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'enabled', 'apiKey'}:
        raise ValueError('概念搜索配置无效')
    # The former user switch is ignored when loading existing configurations.
    # Only deployment-owned credentials survive normalization.
    result = {}
    if 'apiKey' in value:
        key = value['apiKey']
        if key is not None and (not isinstance(key, str) or len(key) > 4096 or re.search(r'[\r\n\x00]', key)):
            raise ValueError('Tavily 密钥格式无效')
        if isinstance(key, str) and key.strip():
            result['apiKey'] = key.strip()
    return result


def credential(config):
    return (config.get('apiKey') or '').strip() or os.getenv('TAVILY_API_KEY', '').strip()


def public_settings(config):
    return {'provider': 'tavily', 'ready': bool(credential(config))}


def _phrase(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('概念名称与领域必须为简短文本')
    if re.search(r'[?？!！;；\r\n<>:/=\[\]{}]', value) or re.search(
            r'(?i)\b(vs|versus|better|worse|compar(?:e|ed|ing|ison)|differences?|benefits?|outperforms?|advantages?|disadvantages?|why|how|whether)\b|相比|比起|优于|优势|劣势|为什么|如何|是否|更好|一定|有什么|比较|对比|区别|优缺点|哪个好|能否', value):
        raise ValueError('只能查询概念名称与领域，不能查询完整问题或优劣比较')
    return ' '.join(value.split())


def term_in_context(term, context):
    # Latin acronyms must be whole terms: EV inside JEV is not user input.
    left = r'(?<![A-Za-z0-9_])' if re.match(r'[A-Za-z0-9_]', term[0]) else ''
    right = r'(?![A-Za-z0-9_])' if re.match(r'[A-Za-z0-9_]', term[-1]) else ''
    return re.search(left + re.escape(term) + right, context, re.IGNORECASE) is not None


def normalize_concepts(items, user_text, previous):
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_CONCEPTS:
        raise ValueError('一次最多理解 3 个概念；其余未知项须向用户澄清')
    context = (user_text + '\n' + previous).casefold()
    normalized, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {'term', 'domain', 'reason', 'core'}:
            raise ValueError('概念请求格式无效；不得提交任意 query')
        term = _phrase(item.get('term'), 64)
        domain_value = item.get('domain') or 'computer science'
        # Models often join domain names with slashes (AI/ML). Treat these
        # as separators, while still rejecting URLs, operators and questions.
        if isinstance(domain_value, str):
            domain_value = re.sub(r'[/／、，,]+', ' ', domain_value)
        domain = _phrase(domain_value, 64)
        if not term_in_context(term, context) or len(term.split()) > 8 or len(domain.split()) > 6:
            raise ValueError('概念须来自用户输入或当前需求，且不能是整句问题')
        if type(item.get('core')) is not bool or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            raise ValueError('概念请求须说明未知原因及是否影响核心研究对象')
        if term.casefold() not in seen:
            normalized.append({'term': term, 'domain': domain, 'reason': item['reason'][:500], 'core': item['core']})
            seen.add(term.casefold())
    return normalized


def query_for(concept):
    term = concept['term'].replace('"', '').replace('“', '').replace('”', '')
    return f'"{term}" {concept["domain"]} definition full name official documentation'


def cache_key(concept):
    value = json.dumps([POLICY_VERSION, concept['term'].casefold(), concept['domain'].casefold()], ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()


def cached_results(runs, concept, now=None):
    now = time.time() if now is None else now
    key = cache_key(concept)
    for run in reversed(runs):
        if run.get('stale'):
            continue
        for entry in run.get('lookups', []):
            age = now - entry.get('fetchedAt', 0)
            if entry.get('cacheKey') == key and entry.get('status') == 'complete' and entry.get('sources') and 0 <= age < CACHE_SECONDS:
                return copy.deepcopy(entry)
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TavilyConceptClient:
    def __init__(self, api_key, *, opener=None, clock=None, sleep=None):
        self._key = api_key
        self._open = opener or urllib.request.build_opener(_NoRedirect()).open
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self.deadline = self._clock() + MAX_SECONDS
        self.request_count = 0

    def _clean(self, value, limit):
        text = str(value or '').replace(self._key, '[已隐藏]') if self._key else str(value or '')
        return text[:limit]

    def search(self, concept, current=lambda: True):
        entry = {**concept, 'query': query_for(concept), 'cacheKey': cache_key(concept),
                 'sources': [], 'status': 'failed', 'requests': 0, 'attempts': [], 'cacheHit': False}
        self.last_entry = entry
        body = {'query': entry['query'], 'topic': 'general', 'search_depth': 'basic',
                'auto_parameters': False, 'include_answer': False, 'include_raw_content': False,
                'include_images': False, 'max_results': 4, 'include_usage': True}
        for attempt in range(2):
            if not current():
                raise ObsoleteDraft()
            remaining = self.deadline - self._clock()
            if self.request_count >= MAX_REQUESTS or remaining <= 0:
                entry.update(status='budget_exhausted', error='概念搜索请求或时间预算已用完')
                break
            self.request_count += 1
            entry['requests'] += 1
            request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self._key}, method='POST')
            retry = False
            delay = 0
            try:
                with self._open(request, timeout=min(10, remaining)) as response:
                    chunks, size = [], 0
                    read = getattr(response, 'read1', response.read)
                    while True:
                        if not current():
                            raise ObsoleteDraft()
                        if self._clock() >= self.deadline:
                            raise TimeoutError()
                        chunk = read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 512 * 1024:
                            raise ValueError('response too large')
                        chunks.append(chunk)
                result = json.loads(b''.join(chunks).decode('utf-8'))
                if not isinstance(result, dict) or not isinstance(result.get('results'), list):
                    raise ValueError('invalid result')
                for raw in result['results'][:4]:
                    if not isinstance(raw, dict):
                        continue
                    url = raw.get('url')
                    if not isinstance(url, str) or len(url) > 2000 or (self._key and self._key in url):
                        continue
                    try:
                        parsed = urllib.parse.urlsplit(url)
                    except ValueError:
                        continue
                    content = self._clean(raw.get('content'), 1500)
                    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or not content.strip():
                        continue
                    sid = 'concept-ref-' + hashlib.sha256((url + content).encode()).hexdigest()[:16]
                    entry['sources'].append({'id': sid, 'title': self._clean(raw.get('title'), 240), 'url': url, 'content': content})
                credits = (result.get('usage') or {}).get('credits') if isinstance(result.get('usage'), dict) else None
                usage = {'credits': credits} if type(credits) in (int, float) and 0 <= credits < 100000 else None
                entry['attempts'].append({'status': 'complete', 'usage': usage, 'requestId': self._clean(result.get('request_id'), 120)})
                entry.update(status='complete' if entry['sources'] else 'empty', fetchedAt=time.time())
                break
            except urllib.error.HTTPError as error:
                entry['error'] = f'{UNAVAILABLE_MESSAGE}（HTTP {error.code}）'
                retry = error.code in (408, 429, 500, 502, 503, 504)
                try:
                    delay = max(0, float(error.headers.get('Retry-After', '0')))
                except (ValueError, AttributeError):
                    delay = 0
                error.close()
            except (urllib.error.URLError, TimeoutError, OSError):
                entry['error'] = UNAVAILABLE_MESSAGE + '（网络连接失败或超时）'
                retry = True
            except (ValueError, TypeError, UnicodeError):
                entry['error'] = UNAVAILABLE_MESSAGE + '（响应格式无效或超出大小限制）'
            entry['attempts'].append({'status': 'failed', 'error': entry['error'], 'usage': None})
            if not retry or attempt or self.request_count >= MAX_REQUESTS or delay >= self.deadline - self._clock():
                break
            if delay:
                self._sleep(delay)
        if not current():
            raise ObsoleteDraft()
        return entry
