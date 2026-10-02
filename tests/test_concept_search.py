import copy
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from research_swarm.concept_search import (ENDPOINT, ObsoleteDraft, TavilyConceptClient,
    cached_results, credential, normalize_concepts, normalize_settings, query_for, source_kind)
from research_swarm.providers import Settings
from research_swarm.local_tools import LocalResearchTools
from research_swarm.server import ResearchApplication


CONCEPT = {'term': 'JEV', 'domain': 'language models', 'reason': '无法确定缩写所指', 'core': True}


def response(value):
    return io.BytesIO(json.dumps(value).encode())


class ConceptClientTests(unittest.TestCase):
    def test_only_bounded_definition_queries_and_snippets_are_sent(self):
        seen = []
        def opener(request, timeout):
            seen.append((request, timeout))
            return response({'results': [{'title': 'JEV definition', 'url': 'https://example.test/jev', 'content': 'JEV means a test model.'}],
                             'answer': 'not used', 'usage': {'credits': 1}, 'request_id': 'request-1'})
        result = TavilyConceptClient('secret', opener=opener).search(CONCEPT)
        request, timeout = seen[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        body = json.loads(request.data)
        self.assertEqual(body['query'], query_for(CONCEPT))
        self.assertNotIn('优势', body['query'])
        self.assertEqual(body['search_depth'], 'basic')
        self.assertEqual(body['max_results'], 4)
        for key in ('include_answer', 'include_raw_content', 'include_images', 'auto_parameters'):
            self.assertIs(body[key], False)
        self.assertTrue(body['include_usage'])
        self.assertLessEqual(timeout, 10)
        self.assertEqual(result['attempts'][0]['usage'], {'credits': 1})
        self.assertNotIn('answer', result)
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('secret', json.dumps(result))

    def test_publisher_context_is_preserved_and_official_domains_are_prioritized(self):
        seen = []
        def opener(request, timeout):
            seen.append(json.loads(request.data))
            if len(seen) == 1:
                return response({'results': []})
            return response({'results': [{'title': 'Dots', 'url': 'https://help.openai.com/en/articles/dots', 'content': 'Official dots description.'},
                                         {'title': 'Guide', 'url': 'https://example.test/dots', 'content': 'Third party guide.'}]})
        concept = normalize_concepts([{'term': 'dots', 'domain': 'AI agents', 'qualifier': 'OpenAI',
                                      'identityStatus': 'confirmed', 'identityQuote': 'OpenAI 的 dots',
                                      'reason': '产品定义需联网核实', 'core': False}], 'OpenAI 的 dots 是什么？', '')[0]
        result = TavilyConceptClient('secret', opener=opener).search(concept)
        self.assertIn('"OpenAI" "dots"', seen[0]['query'])
        self.assertEqual(seen[0]['include_domains'], ['openai.com', 'help.openai.com', 'chatgpt.com', 'developers.openai.com'])
        self.assertNotIn('include_domains', seen[1])
        self.assertEqual({source['sourceKind'] for source in result['sources']}, {'official', 'third_party'})

    def test_identity_requires_explicit_publisher_quote_but_not_definition(self):
        normalized = normalize_concepts([{'term': 'dots', 'domain': 'AI agents', 'qualifier': 'OpenAI',
                                          'identityStatus': 'confirmed', 'identityQuote': 'OpenAI 新推出的 dots',
                                          'reason': '事实尚待核实', 'core': False}], 'OpenAI 新推出的 dots 是什么？', '')
        self.assertEqual(normalized[0]['identityStatus'], 'confirmed')
        with self.assertRaises(ValueError):
            normalize_concepts([{'term': 'dots', 'domain': 'AI agents', 'qualifier': 'OpenAI',
                                  'identityStatus': 'confirmed', 'identityQuote': 'dots 是什么',
                                  'reason': '事实尚待核实', 'core': False}], 'OpenAI 新推出的 dots 是什么？', '')

    def test_official_success_avoids_general_search_and_lookalike_hosts_are_not_official(self):
        concept = {**CONCEPT, 'term': 'dots', 'qualifier': 'OpenAI'}
        opener = Mock(return_value=response({'results': [
            {'title': 'Dots', 'url': 'https://openai.com/index/dots/', 'content': 'Dots are agents.'},
            {'title': 'Fake official Dots', 'url': 'https://openai.com.example.test/dots', 'content': 'Dots are agents.'}]}))
        result = TavilyConceptClient('secret', opener=opener).search(concept)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['sources'][0]['sourceKind'], 'official')
        for url in ('https://openai.com.example.test', 'https://notopenai.com', 'https://example.test/openai.com'):
            self.assertEqual(source_kind(url, concept), 'third_party')

    def test_official_off_topic_results_trigger_general_fallback_with_shared_budget(self):
        concept = {**CONCEPT, 'term': 'dots', 'qualifier': 'OpenAI'}
        calls = []
        def opener(request, timeout):
            calls.append(json.loads(request.data))
            if 'include_domains' in calls[-1]:
                return response({'results': [{'title': 'OpenAI home', 'url': 'https://openai.com/', 'content': 'Welcome.'}]})
            raise TimeoutError()
        client = TavilyConceptClient('secret', opener=opener)
        result = client.search(concept)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['requests'], 3)
        exhausted = client.search(concept)
        self.assertEqual(exhausted['status'], 'budget_exhausted')
        self.assertEqual(exhausted['requests'], 0)

    def test_context_inference_does_not_treat_comparison_cooccurrence_as_ownership(self):
        for text in ('OpenAI新推出的dots是什么？', '就是OpenAI的dots，你自己去查吧'):
            value = normalize_concepts([{**CONCEPT, 'term': 'dots'}], text, '')[0]
            self.assertEqual(value['identityStatus'], 'confirmed')
            self.assertFalse(value['core'])
            self.assertIn('"openai" "dots"', query_for(value))
        value = normalize_concepts([{**CONCEPT, 'term': 'dots'}], 'OpenAI 的 Codex 与 dots 有什么区别？', '')[0]
        self.assertNotIn('identityStatus', value)
        self.assertTrue(value['core'])

    def test_cache_separates_publishers_and_old_protocol_versions(self):
        concept = {**CONCEPT, 'qualifier': 'OpenAI'}
        entry = TavilyConceptClient('key', opener=lambda *a, **k: response({'results': [
            {'title': 'JEV', 'url': 'https://openai.com/jev', 'content': 'JEV definition'}]})).search(concept)
        history = [{'lookups': [entry]}]
        self.assertIsNotNone(cached_results(history, concept, entry['fetchedAt']))
        self.assertIsNone(cached_results(history, {**concept, 'qualifier': 'Anthropic'}, entry['fetchedAt']))
        with patch('research_swarm.concept_search.POLICY_VERSION', 'concept-definition-v1'):
            self.assertIsNone(cached_results(history, concept, entry['fetchedAt']))

    def test_host_rejects_full_questions_unrelated_terms_and_arbitrary_query(self):
        for changes in ({'term': 'JEV 相比 LLM 有什么优势'}, {'term': 'made-up-unmentioned'},
                        {'domain': 'compare JEV versus LLM'}, {'query': 'JEV vs LLM'}, {'term': 'why JEV'},
                        {'term': 'JEV\nLLM'}, {'core': 'yes'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_concepts([{**CONCEPT, **changes}], 'JEV 相比 LLM 有什么优势？', '')
        value = normalize_concepts([CONCEPT, CONCEPT], '研究 JEV', '')
        self.assertEqual(len(value), 1)
        self.assertEqual(value[0]['term'], 'JEV')

    def test_compared_phrase_and_acronym_suffix_are_not_a_concept(self):
        for term, question in (('JEV compared to LLM', 'JEV compared to LLM'), ('EV', '研究 JEV')):
            with self.subTest(term=term), self.assertRaises(ValueError):
                normalize_concepts([{**CONCEPT, 'term': term}], question, '')

    def test_domain_separators_are_normalized_without_allowing_urls_or_questions(self):
        for domain, expected in (('人工智能/语言模型', '人工智能 语言模型'), ('AI／language models', 'AI language models')):
            with self.subTest(domain=domain):
                result = normalize_concepts([{**CONCEPT, 'domain': domain}], 'JEV 相比 LLM 有什么优势？', '')
                self.assertEqual(result[0]['domain'], expected)
                self.assertIn(expected, query_for(result[0]))
        for domain in ('https://example.test/search', 'AI/compare JEV versus LLM'):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                normalize_concepts([{**CONCEPT, 'domain': domain}], '研究 JEV', '')

    def test_three_request_budget_includes_retries_across_concepts(self):
        calls = []
        def unavailable(request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(ENDPOINT, 503, 'unavailable', {}, None)
        client = TavilyConceptClient('secret', opener=unavailable)
        results = [client.search(CONCEPT) for _ in range(3)]
        self.assertEqual(len(calls), 3)
        self.assertEqual([r['requests'] for r in results], [2, 1, 0])
        self.assertEqual(results[-1]['status'], 'budget_exhausted')

    def test_authentication_and_redirect_errors_never_retry(self):
        for code in (400, 401, 403, 302):
            with self.subTest(code=code):
                def fail(request, timeout):
                    raise urllib.error.HTTPError(ENDPOINT, code, 'secret echo', {}, None)
                result = TavilyConceptClient('secret', opener=fail).search(CONCEPT)
                self.assertEqual(result['requests'], 1)
                self.assertNotIn('secret', json.dumps(result))

    def test_redirect_handler_does_not_forward_authorization(self):
        from research_swarm.concept_search import _NoRedirect
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    def test_retry_after_outside_deadline_stops_without_sleep(self):
        def fail(request, timeout):
            raise urllib.error.HTTPError(ENDPOINT, 429, 'rate limit', {'Retry-After': '60'}, None)
        with patch('time.sleep') as sleep:
            result = TavilyConceptClient('secret', opener=fail).search(CONCEPT)
        self.assertEqual(result['requests'], 1)
        sleep.assert_not_called()

    def test_timeout_and_bad_json_are_not_confused_with_empty_results(self):
        for opener, status, requests in (
            (lambda request, timeout: response({'results': []}), 'empty', 1),
            (lambda request, timeout: io.BytesIO(b'not JSON secret'), 'failed', 1),
        ):
            result = TavilyConceptClient('secret', opener=opener).search(CONCEPT)
            self.assertEqual((result['status'], result['requests']), (status, requests))
        def timeout(request, timeout):
            raise TimeoutError()
        self.assertEqual(TavilyConceptClient('secret', opener=timeout).search(CONCEPT)['requests'], 2)

    def test_deadline_and_cancellation_prevent_next_request(self):
        times = iter([0, 31])
        client = TavilyConceptClient('secret', opener=lambda *a, **k: self.fail('network'), clock=lambda: next(times))
        self.assertEqual(client.search(CONCEPT)['status'], 'budget_exhausted')
        with self.assertRaises(ObsoleteDraft):
            TavilyConceptClient('secret').search(CONCEPT, lambda: False)

    def test_results_are_bounded_and_unsafe_urls_and_secret_echoes_are_removed(self):
        raw = [{'title': 'secret', 'url': url, 'content': 'secret ' + 'x' * 2000}
               for url in ('javascript:alert(1)', 'https://user:secret@example.test', 'https://example.test/definition')]
        result = TavilyConceptClient('secret', opener=lambda *a, **k: response({'results': raw})).search(CONCEPT)
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(len(result['sources'][0]['content']), 1500)
        self.assertNotIn('secret', json.dumps(result))
        self.assertIsNone(result['attempts'][0]['usage'])

    def test_cache_requires_same_task_history_domain_policy_and_fresh_nonempty_success(self):
        entry = TavilyConceptClient('key', opener=lambda *a, **k: response({'results': [
            {'title': 'Definition', 'url': 'https://example.test', 'content': 'JEV definition'}]})).search(CONCEPT)
        history = [{'lookups': [entry]}]
        when = entry['fetchedAt']
        self.assertIsNotNone(cached_results(history, CONCEPT, when + 10))
        for runs, concept, at in (([], CONCEPT, when), (history, {**CONCEPT, 'domain': 'medicine'}, when),
                                  (history, CONCEPT, when + 86401), ([{'stale': True, 'lookups': [entry]}], CONCEPT, when),
                                  ([{'lookups': [{**entry, 'sources': []}]}], CONCEPT, when)):
            self.assertIsNone(cached_results(runs, concept, at))
        cached = cached_results(history, CONCEPT, when)
        cached['sources'].clear()
        self.assertTrue(entry['sources'])


class ConceptSettingsTests(unittest.TestCase):
    def test_default_capability_uses_environment_without_user_switch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'env-tavily-secret'}):
            settings = Settings(Path(directory), None)
            self.assertEqual(settings.public()['conceptSearch'], {'provider': 'tavily', 'ready': True})
            self.assertEqual(settings.concept_search_credential(), 'env-tavily-secret')
            self.assertNotIn('env-tavily-secret', json.dumps(settings.public()))
            self.assertNotIn('env-tavily-secret', settings.safe_error(Exception('env-tavily-secret')))

    def test_private_key_overrides_environment_and_survives_llm_changes_and_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'env-tavily-secret'}):
            path = Path(directory)/'config.local.json'
            path.write_text(json.dumps({'conceptSearch': {'apiKey': 'saved-tavily-secret'},
                'provider': {'type': 'openai', 'baseUrl': 'https://example.test/v1', 'model': 'test', 'apiKey': 'llm-secret'}}))
            settings = Settings(Path(directory), None)
            self.assertEqual(settings.concept_search_credential(), 'saved-tavily-secret')
            settings.update({'provider': {'model': 'updated'}})
            saved = json.loads(path.read_text())
            self.assertEqual(saved['conceptSearch'], {'apiKey': 'saved-tavily-secret'})
            self.assertEqual(saved['providers']['main']['apiKey'], 'llm-secret')
            restored = Settings(Path(directory), None)
            self.assertEqual(restored.concept_search_credential(), 'saved-tavily-secret')
            self.assertNotIn('saved-tavily-secret', json.dumps(restored.public()))
            self.assertNotIn('saved-tavily-secret', restored.safe_error(Exception('saved-tavily-secret')))

    def test_legacy_switch_is_removed_and_empty_keys_fall_back_to_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'env-secret'}):
            path = Path(directory)/'config.local.json'
            for key in (None, '', '   ', 'saved-secret'):
                for enabled in (False, True):
                    with self.subTest(key=key, enabled=enabled):
                        path.write_text(json.dumps({'conceptSearch': {'enabled': enabled, 'apiKey': key}}))
                        settings = Settings(Path(directory), None)
                        self.assertNotIn('enabled', settings.data['conceptSearch'])
                        self.assertEqual(settings.concept_search_credential(), key if key and key.strip() else 'env-secret')
                        self.assertTrue(settings.public()['conceptSearch']['ready'])
                        settings.update({'mode': 'evidence'})
                        self.assertNotIn('enabled', json.loads(path.read_text())['conceptSearch'])

    def test_no_deployment_key_reports_only_unavailable_metadata(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': ''}):
            settings = Settings(Path(directory), None)
            self.assertEqual(settings.public()['conceptSearch'], {'provider': 'tavily', 'ready': False})
            self.assertEqual(settings.data['conceptSearch'], {})

    def test_user_update_rejects_all_concept_changes_atomically(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'server-key'}):
            settings = Settings(Path(directory), None)
            before = copy.deepcopy(settings.data)
            for changes in ({'enabled': False}, {'apiKey': 'replace-server-key'}, {'clearKey': True}, {}, None):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '服务端预配置.*刷新'):
                    settings.update({'mode': 'llm', 'conceptSearch': changes})
                self.assertEqual(settings.data, before)
                self.assertFalse(settings.path.exists())

    def test_legacy_application_route_cannot_modify_deployment_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'server-key'}):
            app = ResearchApplication.__new__(ResearchApplication)
            app.settings = Settings(Path(directory), None)
            app.mutation_lock = threading.RLock()
            app.engine = Mock()
            app.engine.snapshot.return_value = {'paused': True, 'nodes': [], 'project': {'mode': 'evidence'}}
            before = copy.deepcopy(app.settings.data)
            for changes in ({'enabled': False}, {'apiKey': 'replace-server-key'}, {'clearKey': True}):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '服务端预配置.*刷新'):
                    app.post('/api/settings', {'conceptSearch': changes})
                self.assertEqual(app.settings.data, before)
                self.assertEqual(app.settings.concept_search_credential(), 'server-key')
            app.engine.command.assert_not_called()

    def test_private_configuration_still_validates_keys_and_fixed_endpoint(self):
        for value in ({'apiKey': 'bad\nkey'}, {'apiKey': 123}, {'baseUrl': 'https://other.test'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_settings(value)

    def test_experiment_environment_does_not_inherit_tavily_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TAVILY_API_KEY': 'not-for-experiments'}):
            env = LocalResearchTools(Path(directory))._env(Path(directory))
            self.assertNotIn('TAVILY_API_KEY', env)


if __name__ == '__main__':
    unittest.main()
