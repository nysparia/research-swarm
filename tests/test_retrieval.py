from contextlib import closing
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from research_swarm.library import Library
from research_swarm.providers import Settings
from research_swarm.retrieval import (PublicClient, SearchStopped, arxiv_id, arxiv_query, deduplicate,
    doi, from_s2, identifiers, query_plan, retrieve_candidates, same_paper)
from research_swarm.search_settings import normalize_search
from research_swarm.sources import prepare_source
from research_swarm.server import ResearchApplication


def s2(identifier='seed', title='Graph neural network stability mechanisms', paper_doi='10.1234/seed', arxiv=None):
    return {'paperId': identifier, 'title': title, 'abstract': title + ' measured on several graphs.',
            'year': 2025, 'externalIds': {'DOI': paper_doi, 'ArXiv': arxiv},
            'authors': [{'name': 'Alice Smith'}], 'citationCount': 10, 'venue': 'Test Journal',
            'url': 'https://www.semanticscholar.org/paper/' + identifier, 'openAccessPdf': None}


ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2501.00001v2</id><title>Graph neural network stability mechanisms</title>
<summary>Graph neural network stability measurements.</summary><published>2025-01-01T00:00:00Z</published>
<author><name>Alice Smith</name></author><link title="pdf" href="https://arxiv.org/pdf/2501.00001v2"/></entry>
<entry><id>http://arxiv.org/abs/2501.00002</id><title>Graph neural network independent evaluation</title>
<summary>Independent graph benchmarks.</summary><published>2025-01-02T00:00:00Z</published>
<author><name>Bob Jones</name></author></entry></feed>'''


class FixtureClient:
    def __init__(self, settings, fail=None):
        self.settings, self.fail = settings, fail or {}
        self.requests, self.cache_hits, self.calls = 0, 0, []

    def request(self, source, path='', params=None, xml=False):
        if self.requests >= self.settings['maxRequests']:
            raise SearchStopped('request_or_time_budget')
        self.requests += 1
        self.calls.append((source, path, params or {}))
        if source in self.fail:
            raise ValueError(self.fail[source])
        if path.endswith('/references'):
            return {'data': [{'citedPaper': s2('ref', 'Graph neural network foundation', '10.1234/ref')}]}
        if path.endswith('/citations'):
            return {'data': [{'citingPaper': s2('citing', 'Graph neural network replication results', '10.1234/citing')}]}
        if source == 'semantic_scholar':
            return {'data': [s2(arxiv='2501.00001')]}
        if source == 'arxiv':
            return ATOM
        if source == 'openalex':
            is_neighbor = 'filter' in (params or {}) and not (params or {})['filter'].startswith('from_publication')
            return {'results': [{'id': 'https://openalex.org/W2' if is_neighbor else 'https://openalex.org/W1',
                'title': 'Graph neural network foundation' if is_neighbor else 'Graph neural network stability mechanisms',
                'doi': 'https://doi.org/10.1234/ref' if is_neighbor else 'https://doi.org/10.1234/SEED',
                'abstract_inverted_index': {'Graph': [0], 'neural': [1], 'network': [2], 'stability': [3]},
                'publication_year': 2025, 'authorships': [{'author': {'display_name': 'Alice Smith'}}],
                'cited_by_count': 10, 'referenced_works': ['https://openalex.org/W2'], 'locations': [],
                'primary_location': {'source': {'display_name': 'Test Journal'}}}]}
        return {'message': {'items': []}}


class RetrievalTests(unittest.TestCase):
    def test_identifiers_normalize_versions_urls_and_case(self):
        self.assertEqual(doi(' HTTPS://DOI.ORG/10.1234/ABC '), '10.1234/abc')
        self.assertEqual(arxiv_id('https://arxiv.org/pdf/2501.12345v3.pdf'), '2501.12345')
        self.assertEqual(arxiv_id('10.48550/arXiv.2501.12345'), '2501.12345')
        self.assertEqual(arxiv_id('hep-th/9901001v2'), 'hep-th/9901001')
        self.assertIsNone(doi('not-a-doi'))

    def test_no_doi_is_not_a_shared_identity(self):
        a = from_s2(s2('a', 'Short title', None))
        b = from_s2(s2('b', 'Another title', None))
        self.assertEqual(len(deduplicate([a, b])), 2)

    def test_title_matching_needs_author_and_year_and_preserves_conflicting_dois(self):
        a, b = from_s2(s2('a')), from_s2(s2('b', paper_doi=None))
        self.assertTrue(same_paper(a, b))
        b['Authors'] = 'Someone Else'
        self.assertFalse(same_paper(a, b))
        b['Authors'], b['Year'] = a['Authors'], 2020
        self.assertFalse(same_paper(a, b))
        b['Year'], b['DOI'] = a['Year'], '10.1234/different'
        self.assertFalse(same_paper(a, b))

    def test_identifier_bridge_merges_groups_and_keeps_every_source_record(self):
        a = from_s2(s2('a', 'First metadata title', '10.1234/bridge'))
        b = from_s2(s2('b', 'Second metadata title', None, '2501.12345'))
        bridge = from_s2(s2('bridge', 'Canonical metadata title', '10.1234/bridge', '2501.12345v2'))
        merged = deduplicate([a, b, bridge])
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]['records']), 3)
        self.assertTrue(identifiers(a) <= identifiers(merged[0]))

    def test_query_plan_has_recent_classic_and_evidence_gap_lenses(self):
        plan = query_plan('GNN oversmoothing', normalize_search())
        self.assertEqual(len(plan), 6)
        self.assertIsNone(plan[0]['yearFrom'])
        self.assertIsNotNone(plan[1]['yearFrom'])
        self.assertIn('graph neural network', plan[2]['query'])
        self.assertIn('counterevidence', [p['intent'] for p in plan])
        self.assertEqual(len({(p['query'], p['yearFrom']) for p in plan}), len(plan))
        encoded = arxiv_query('"graph neural" OR GNN', 2022)
        self.assertIn('ti:"graph neural"', encoded)
        self.assertIn('OR', encoded)
        self.assertNotIn('ti:"OR"', encoded)
        self.assertIn('submittedDate:[20220101', encoded)
        grouped = arxiv_query('GNN (survey OR review)')
        self.assertEqual(grouped, '(ti:"GNN" OR abs:"GNN") AND ((ti:"survey" OR abs:"survey") OR (ti:"review" OR abs:"review"))')
        self.assertEqual(len(query_plan('graph learning', normalize_search({'profile': 'deep'}))), 18)
        for malformed in ('GNN (survey', 'GNN OR', 'AND GNN', 'GNN )'):
            with self.assertRaises(ValueError):
                arxiv_query(malformed)

    def test_three_sources_search_even_when_first_has_results_and_expand_exactly_one_hop(self):
        settings = normalize_search({'queryCount': 2, 'seedCount': 1, 'crossrefFallback': False})
        client = FixtureClient(settings)
        result = retrieve_candidates('graph neural network', settings, client=client)
        self.assertEqual(len(result['summary']['searches']), 6)
        self.assertEqual(set(result['summary']['sourceCounts']) - {'crossref'}, set(settings['sources']))
        self.assertEqual(len(result['papers']), 4)
        self.assertEqual(result['summary']['duplicateCount'], 6)
        seed = next(p for p in result['papers'] if p.get('DOI') == '10.1234/seed')
        self.assertEqual({r['source'] for r in seed['records']}, set(settings['sources']))
        expansion = [c for c in client.calls if c[1].endswith(('/references', '/citations'))]
        self.assertEqual(len(expansion), 2)
        self.assertTrue(all('/seed/' in c[1] for c in expansion))
        self.assertIn('DOI:10.1234/seed', result['edges'][0]['from'])
        self.assertIn('DOI:10.1234/ref', result['edges'][0]['to'])
        self.assertIn('DOI:10.1234/citing', result['edges'][1]['from'])
        self.assertIn('DOI:10.1234/seed', result['edges'][1]['to'])

    def test_partial_failure_preserves_other_sources_and_safe_reasons(self):
        settings = normalize_search({'queryCount': 1, 'citationDepth': 0, 'crossrefFallback': False})
        client = FixtureClient(settings, {'semantic_scholar': 'http_429'})
        result = retrieve_candidates('graph neural network', settings, client=client)
        self.assertTrue(result['papers'])
        self.assertEqual(result['summary']['status'], 'partial')
        self.assertEqual(result['summary']['errors'][0]['reason'], 'http_429')
        all_failed = FixtureClient(settings, {s: 'secret-in-error' for s in settings['sources']})
        result = retrieve_candidates('query', settings, client=all_failed)
        self.assertEqual(result['summary']['status'], 'failed')
        self.assertNotIn('secret-in-error', json.dumps(result))

    def test_empty_success_is_distinguished_from_failed_sources(self):
        settings = normalize_search({'sources': ['semantic_scholar'], 'queryCount': 1, 'crossrefFallback': False})
        client = FixtureClient(settings)
        client.request = lambda *args, **kwargs: {'data': []}
        result = retrieve_candidates('no matches', settings, client=client)
        self.assertEqual(result['summary']['status'], 'complete')
        self.assertEqual(result['papers'], [])

    def test_budget_and_depth_zero_are_enforced(self):
        settings = normalize_search({'maxRequests': 3, 'queryCount': 6, 'citationDepth': 0})
        client = FixtureClient(settings)
        result = retrieve_candidates('graph neural network', settings, client=client)
        self.assertEqual(client.requests, 3)
        self.assertIsNotNone(result['summary']['stopReason'])
        self.assertEqual(result['edges'], [])
        self.assertLessEqual(result['summary']['candidateCount'], settings['candidateLimit'])

    def test_s2_citation_failure_uses_openalex_and_keeps_direction(self):
        settings = normalize_search({'queryCount': 1, 'seedCount': 1, 'crossrefFallback': False})
        client = FixtureClient(settings, {'semantic_scholar': 'http_429'})
        result = retrieve_candidates('graph neural network', settings, client=client)
        self.assertTrue(result['edges'])
        self.assertTrue(all(e['source'] == 'openalex' for e in result['edges']))
        self.assertTrue(any('openalex_id:' in c[2].get('filter', '') for c in client.calls))
        self.assertTrue(any('cites:' in c[2].get('filter', '') for c in client.calls))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.clock = 0.0
        self.addCleanup(patch.stopall)
        patch('research_swarm.retrieval.time.monotonic', side_effect=lambda: self.clock).start()
        patch('research_swarm.retrieval.time.sleep', side_effect=self.advance).start()
        patch('research_swarm.retrieval._NEXT_REQUEST', {}).start()
        self.settings = normalize_search({'maxRequests': 3, 'maxSeconds': 10})

    def advance(self, seconds):
        self.clock += seconds

    def test_credentials_go_to_right_source_and_never_cache_or_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            client = PublicClient(self.settings, {'openalex': 'oa-secret', 'semantic_scholar': 's2-secret'}, temp)
            with patch('research_swarm.retrieval.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'{"data":[]}')) as network:
                client.request('openalex', params={'search': 'test'})
                client.request('semantic_scholar', '/paper/search', {'query': 'test'})
                self.assertIn('api_key=oa-secret', network.call_args_list[0].args[0].full_url)
                self.assertEqual(network.call_args_list[1].args[0].get_header('X-api-key'), 's2-secret')
                self.assertNotIn('oa-secret', network.call_args_list[1].args[0].full_url)
                client.request('openalex', params={'search': 'test'})
                self.assertEqual(network.call_count, 2)
                self.assertEqual(client.cache_hits, 1)
            files = ''.join(p.read_text() for p in Path(temp).glob('*'))
            self.assertNotIn('secret', files)

    def test_retry_counts_against_budget_and_preserves_retry_after(self):
        client = PublicClient(self.settings)
        error = HTTPError('https://api.semanticscholar.org', 429, 'limited', {'Retry-After': '3'}, None)
        with patch('research_swarm.retrieval.urlopen', side_effect=[error, io.BytesIO(b'{"data":[]}')]) as network:
            self.assertEqual(client.request('semantic_scholar'), {'data': []})
            self.assertEqual(client.requests, 2)
            self.assertGreaterEqual(self.clock, 3)
            self.assertEqual(network.call_count, 2)
        client.requests = 3
        with self.assertRaises(SearchStopped):
            client.request('arxiv')

    def test_long_retry_after_and_deadline_stop_without_extra_requests(self):
        client = PublicClient(self.settings)
        error = HTTPError('https://example.test', 429, 'limited', {'Retry-After': '100'}, None)
        with patch('research_swarm.retrieval.urlopen', side_effect=error) as network:
            with self.assertRaisesRegex(ValueError, 'rate_limited'):
                client.request('semantic_scholar')
            self.assertEqual(network.call_count, 1)
        self.clock = 11
        with self.assertRaises(SearchStopped):
            client.request('openalex')

    def test_malformed_provider_payload_is_a_reported_partial_failure(self):
        settings = normalize_search({'queryCount': 1, 'sources': ['semantic_scholar'], 'crossrefFallback': False})
        client = PublicClient(settings)
        with patch('research_swarm.retrieval.urlopen', return_value=io.BytesIO(b'{"error":"broken"}')):
            result = retrieve_candidates('test', settings, client=client)
        self.assertEqual(result['summary']['status'], 'failed')
        self.assertEqual(result['summary']['errors'][0]['reason'], 'invalid_response')


class ManagedIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        template = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.source = prepare_source(template, root / 'source', 'search-test', 'Graph neural networks', 'Graph neural network stability')
        self.library = Library(self.source)
        self.settings = normalize_search({'queryCount': 2, 'seedCount': 1, 'crossrefFallback': False})

    def retrieve(self, limit=1):
        result = retrieve_candidates('graph neural network', self.settings, client=FixtureClient(self.settings))
        with patch('research_swarm.retrieval.retrieve_candidates', return_value=result):
            return self.library.retrieve('graph neural network', limit, search_settings=self.settings)

    def test_candidates_survive_top_k_and_existing_tasks_use_new_retrieval_without_cli(self):
        with patch('research_swarm.library.subprocess.run', side_effect=AssertionError('legacy CLI must not run')):
            result = self.retrieve()
        self.assertEqual(len(result['library']['papers']), 4)
        self.assertEqual(len(result['retrieval']['resultPaperIds']), 1)
        self.assertEqual(len(result['retrieval']['candidatePaperIds']), 4)
        self.assertEqual(result['retrieval']['retainedCitationEdges'], 2)
        self.assertTrue(result['retrieval']['resultLookupComplete'])
        evidence = result['library']['evidence']
        self.assertTrue(all(e['type'] == 'abstract' for e in evidence))
        self.assertTrue(all(e['locator'] == 'Abstract' for e in evidence))
        self.assertTrue(all(e['extractor'].startswith('metadata:') for e in evidence))

    def test_repeat_retrieval_keeps_ids_evidence_and_all_source_provenance(self):
        first, second = self.retrieve(3), self.retrieve(3)
        self.assertEqual(second['retrieval']['newPaperIds'], [])
        self.assertEqual(first['library']['evidence'], second['library']['evidence'])
        self.assertEqual(first['retrieval']['resultPaperIds'], second['retrieval']['resultPaperIds'])
        self.assertEqual(len(second['library']['relations']), 2)
        with closing(sqlite3.connect(second['library']['sourceDb'])) as conn:
            seed = conn.execute("SELECT PaperID FROM Paper WHERE DOI='10.1234/seed'").fetchone()[0]
            self.assertEqual({r[0] for r in conn.execute('SELECT SourceType FROM SrcRecord WHERE PaperID=?', (seed,))},
                             {'openalex', 'arxiv', 'semantic_scholar'})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM LiteratureSearchRun').fetchone()[0], 2)
            refs = conn.execute('SELECT a.DOI,b.DOI FROM LiteratureCitation c JOIN Paper a ON a.PaperID=c.FromPaperID JOIN Paper b ON b.PaperID=c.ToPaperID').fetchall()
            self.assertIn(('10.1234/seed', '10.1234/ref'), refs)
            self.assertIn(('10.1234/citing', '10.1234/seed'), refs)

    def test_failed_search_does_not_import_or_claim_complete(self):
        failed = retrieve_candidates('graph neural network', self.settings,
                                     client=FixtureClient(self.settings, {s: 'http_503' for s in self.settings['sources']}))
        with patch('research_swarm.retrieval.retrieve_candidates', return_value=failed):
            result = self.library.retrieve('query')
        self.assertFalse(result['ok'])
        self.assertEqual(result['library']['papers'], [])
        self.assertEqual(result['retrieval']['status'], 'failed')


class SearchSettingsTests(unittest.TestCase):
    def test_application_enforces_configured_shared_round_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            app = ResearchApplication.__new__(ResearchApplication)
            app.settings = Settings(Path(temp), None)
            app.settings.update({'search': {'maxCalls': 1}})
            app.operation_lock, app.retrieval_lock = threading.RLock(), threading.Lock()
            app.search_budgets, app.budget_path = {}, Path(temp) / 'budgets.json'
            node = {'id': 'node', 'version': 1, 'status': 'running', 'input': {'allowNewSearch': True, 'searchBudgetId': 'round'}}
            from unittest.mock import Mock
            app.engine = Mock()
            app.engine.snapshot.return_value = {'paused': False, 'nodes': [node]}
            app._retrieve = Mock(return_value={'ok': True})
            app._node_retrieve(node, {'workflow': 'autonomous'}, 'query', 3)
            with self.assertRaisesRegex(ValueError, '1 次'):
                app._node_retrieve(node, {'workflow': 'autonomous'}, 'another query', 3)
            app._retrieve.assert_called_once()
            self.assertEqual(json.loads(app.budget_path.read_text()), {'round': 1})

    def test_validation_rejects_invalid_budgets_and_unknown_sources(self):
        for settings in ({'queryCount': True}, {'maxRequests': -1}, {'candidateLimit': 50000},
                         {'citationDepth': 2}, {'sources': []}, {'sources': ['google']},
                         {'sources': ['arxiv', 'arxiv']}, {'profile': []}, {'unexpected': 1}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                normalize_search(settings)

    def test_keys_are_optional_private_and_clear_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'SEMANTIC_SCHOLAR_API_KEY': 'env-secret'}):
            settings = Settings(Path(temp), None)
            public = settings.update({'search': {'profile': 'deep', 'maxCalls': 12},
                                      'searchKeys': {'openalex': 'oa-secret'}})
            self.assertEqual(public['search']['candidateLimit'], 500)
            self.assertEqual(public['search']['maxCalls'], 12)
            self.assertTrue(public['searchKeys']['semantic_scholar']['hasKey'])
            self.assertNotIn('oa-secret', json.dumps(public))
            self.assertNotIn('env-secret', settings.safe_error(Exception('env-secret oa-secret')))
            settings.update({'searchKeys': {'semantic_scholar': None, 'openalex': ''}})
            restarted = Settings(Path(temp), None)
            self.assertFalse(restarted.public()['searchKeys']['semantic_scholar']['hasKey'])
            self.assertEqual(restarted.search_credentials()['openalex'], 'oa-secret')


if __name__ == '__main__':
    unittest.main()
