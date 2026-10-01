import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import io
import urllib.error
import os

from research_swarm.providers import Settings, parse_json_object


class ProviderTests(unittest.TestCase):
    def test_legacy_configuration_migrates_only_to_main_and_survives_role_update(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'config.local.json'
            path.write_text(json.dumps({'mode': 'llm', 'provider': {'type': 'openai', 'baseUrl': 'https://legacy.example/v1',
                                                                    'model': 'original', 'apiKey': 'legacy-secret'}}))
            settings = Settings(Path(temp), None)
            self.assertIs(settings.data['provider'], settings.data['providers']['main'])
            self.assertEqual(settings.public()['provider']['model'], 'original')
            self.assertFalse(settings.role_status('judge')['configured'])
            self.assertEqual(settings.data['providers']['judge']['apiKey'], '')
            settings.update({'providers': {'judge': {'baseUrl': 'http://localhost:11434/v1', 'model': 'judge-model'}}})
            restarted = Settings(Path(temp), None)
            self.assertEqual(restarted.data['provider']['apiKey'], 'legacy-secret')
            self.assertEqual(restarted.role_status('judge')['model'], 'judge-model')
            self.assertFalse(restarted.role_status('redteam')['configured'])

    def test_each_role_uses_its_own_endpoint_model_and_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'providers': {role: {'baseUrl': f'https://{role}.example/v1', 'model': role + '-model', 'apiKey': role + '-secret'}
                                          for role in Settings.ROLES}})
            requests = []
            def respond(request, **kwargs):
                requests.append(request)
                return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
            with patch('urllib.request.urlopen', side_effect=respond):
                for role in Settings.ROLES:
                    self.assertEqual(settings.chat([{'role': 'user', 'content': 'OK'}], role=role), 'OK')
            for role, request in zip(Settings.ROLES, requests):
                self.assertEqual(request.full_url, f'https://{role}.example/v1/chat/completions')
                self.assertEqual(json.loads(request.data)['model'], role + '-model')
                self.assertEqual(request.get_header('Authorization'), 'Bearer ' + role + '-secret')

    def test_unconfigured_secondary_and_unknown_roles_never_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'main-secret'
            with patch('urllib.request.urlopen') as request:
                for role in ('judge', 'redteam', 'typo'):
                    with self.subTest(role=role), self.assertRaises(ValueError):
                        settings.chat([{'role': 'user', 'content': 'test'}], role=role)
            request.assert_not_called()

    def test_role_secrets_and_environment_values_are_redacted_together(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'TEST_JUDGE_KEY': 'judge-env-secret', 'TEST_REDTEAM_KEY': 'redteam-env-secret'}):
            settings = Settings(Path(temp), None)
            public = settings.update({'providers': {
                'main': {'apiKey': 'main-secret'},
                'judge': {'baseUrl': 'https://judge.example/v1', 'model': 'judge', 'apiKeyEnv': 'TEST_JUDGE_KEY'},
                'redteam': {'baseUrl': 'https://redteam.example/v1', 'model': 'redteam', 'apiKeyEnv': 'TEST_REDTEAM_KEY', 'apiKey': 'redteam-direct-secret'},
            }})
            secrets = ('main-secret', 'judge-env-secret', 'redteam-env-secret', 'redteam-direct-secret')
            redacted = settings.safe_error(Exception(' '.join(secrets)))
            for secret in secrets:
                self.assertNotIn(secret, json.dumps(public))
                self.assertNotIn(secret, redacted)
            self.assertTrue(all(public['providers'][role]['hasKey'] for role in Settings.ROLES))

    def test_independence_compares_identity_without_claiming_different_vendors(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            public = settings.update({'providers': {
                'main': {'baseUrl': 'http://localhost:11434/v1', 'model': 'same'},
                'judge': {'baseUrl': 'http://LOCALHOST:11434/v1/', 'model': 'same'},
                'redteam': {'baseUrl': 'http://localhost:11434/v1', 'model': 'different'},
            }})
            self.assertFalse(public['providers']['judge']['independentFromMain'])
            self.assertTrue(public['providers']['redteam']['independentFromMain'])
            self.assertFalse(public['capabilities']['independentReviewReady'])
            public = settings.update({'providers': {'judge': {'model': 'third'}}})
            self.assertTrue(public['capabilities']['independentReviewReady'])
            self.assertEqual(public['capabilities']['independenceBasis'], 'configured_endpoint_and_model')
            self.assertIn('不保证', public['capabilities']['independenceNotice'])
            public = settings.update({'providers': {'redteam': {'model': 'third'}}})
            self.assertFalse(public['capabilities']['independentReviewReady'])

    def test_default_port_and_trailing_slash_do_not_create_independence(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            public = settings.update({'providers': {
                'main': {'baseUrl': 'https://same.example/v1', 'model': 'same', 'apiKey': 'a'},
                'judge': {'baseUrl': 'https://SAME.example:443/v1/', 'model': 'same', 'apiKey': 'b'},
            }})
            self.assertFalse(public['providers']['judge']['independentFromMain'])
            for endpoint in ('http://localhost:11434/v1', 'http://127.0.0.1:11434/v1', 'http://[::1]:11434/v1'):
                public = settings.update({'providers': {
                    'main': {'baseUrl': 'http://localhost:11434/v1', 'model': 'same'},
                    'judge': {'baseUrl': endpoint, 'model': 'same'},
                }})
                self.assertFalse(public['providers']['judge']['independentFromMain'])

    def test_public_capabilities_state_actual_execution_scope_without_cost_or_gpu_guarantee(self):
        with tempfile.TemporaryDirectory() as temp:
            capability = Settings(Path(temp), None).public()['capabilities']
            self.assertEqual(capability['reproductionScope'], 'preflight_and_small_experiments')
            self.assertEqual(capability['executionLimits']['maxTimeoutSeconds'], 180)
            self.assertEqual(capability['executionLimits']['resume'], 'restart_node')
            self.assertFalse(capability['executionLimits']['costEstimateAvailable'])
            self.assertFalse(capability['executionLimits']['gpuConfigured'])

    def test_role_update_preserves_other_secrets_and_explicit_reset_removes_them(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'providers': {
                'main': {'apiKey': 'main-secret'},
                'judge': {'baseUrl': 'https://judge.example/v1', 'model': 'judge', 'apiKey': 'judge-secret'},
            }})
            settings.update({'providers': {'judge': {'apiKey': '', 'model': 'updated'}}})
            self.assertEqual(settings.data['providers']['judge']['apiKey'], 'judge-secret')
            self.assertEqual(settings.data['provider']['apiKey'], 'main-secret')
            settings.update({'providers': {'judge': None}})
            self.assertEqual(settings.data['providers']['judge'], Settings.EMPTY_PROVIDER)
            self.assertFalse(Settings(Path(temp), None).role_status('judge')['configured'])

    def test_invalid_secondary_update_is_atomic_and_legacy_main_update_is_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'provider': {'model': 'legacy-update'}})
            before = (Path(temp) / 'config.local.json').read_text()
            with self.assertRaises(ValueError):
                settings.update({'providers': {'main': {'model': 'uncommitted'},
                                               'judge': {'baseUrl': 'http://remote.example/v1', 'model': 'judge'}}})
            self.assertEqual(settings.role_status('main')['model'], 'legacy-update')
            self.assertEqual((Path(temp) / 'config.local.json').read_text(), before)
            for invalid in ({'providers': []}, {'providers': {'typo': {}}}, {'providers': {'judge': 'invalid'}}, {'provider': None}):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    settings.update(invalid)

    def test_secondary_json_retry_keeps_role_endpoint_and_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'providers': {'judge': {'baseUrl': 'http://localhost:11434/v1', 'model': 'judge-model'}}})
            responses = iter(['invalid', '{"result":"ok"}'])
            requests = []
            def respond(request, **kwargs):
                requests.append(request)
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': next(responses)}}]}).encode())
            with patch('urllib.request.urlopen', side_effect=respond):
                self.assertEqual(parse_json_object(settings.chat([{'role': 'user', 'content': 'judge'}], role='judge', json_mode=True)), {'result': 'ok'})
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r.full_url == 'http://localhost:11434/v1/chat/completions' for r in requests))
            self.assertTrue(all(json.loads(r.data)['model'] == 'judge-model' for r in requests))

    def test_secret_not_returned_and_configuration_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.update({'provider': {'type': 'openai', 'baseUrl': 'https://example.com/v1', 'model': 'research-model', 'apiKey': 'secret-for-test'}})
            public = settings.public()
            self.assertNotIn('secret-for-test', json.dumps(public))
            self.assertTrue(public['provider']['hasKey'])
            restarted = Settings(Path(temp), None)
            self.assertEqual(restarted.public()['provider']['model'], 'research-model')
            self.assertNotIn('secret-for-test', restarted.safe_error(Exception('key secret-for-test failed')))

    def test_remote_plain_http_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            with self.assertRaises(ValueError):
                settings.update({'provider': {'baseUrl': 'http://remote.example/v1'}})

    def test_json_response_has_no_silent_partial_parse(self):
        self.assertEqual(parse_json_object('```json\n{"summary":"ok"}\n```')['summary'], 'ok')
        with self.assertRaises(ValueError):
            parse_json_object('preface {"summary":"ok"} trailing')

    def test_deepseek_structured_calls_request_json_and_retry_invalid_content_once(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            requests, logs = [], []
            contents = iter(['preface {"summary":"bad format"}', '{"summary":"valid"}'])
            def respond(request, **kwargs):
                requests.append(json.loads(request.data))
                return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': next(contents)}}]}).encode())
            with patch('urllib.request.urlopen', side_effect=respond):
                text = settings.chat([{'role': 'user', 'content': 'Return JSON {"summary":"text"}'}], json_mode=True, on_retry=logs.append)
            self.assertEqual(parse_json_object(text)['summary'], 'valid')
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r['response_format'] == {'type': 'json_object'} for r in requests))
            self.assertEqual(len(logs), 1)
            self.assertNotIn('test-key', ''.join(logs))

    def test_invalid_structured_output_stops_after_one_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            def respond(*args, **kwargs):
                return io.BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"invalid"}}]}')
            with patch('urllib.request.urlopen', side_effect=respond) as request:
                with self.assertRaisesRegex(ValueError, 'JSON'):
                    settings.chat([{'role': 'user', 'content': 'Return JSON'}], json_mode=True)
            self.assertEqual(request.call_count, 2)

    def test_connection_test_remains_plain_text(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')) as request:
                self.assertEqual(settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32), 'OK')
            self.assertNotIn('response_format', json.loads(request.call_args.args[0].data))

    def test_transient_connection_failure_has_one_bounded_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            with patch('urllib.request.urlopen', side_effect=[urllib.error.URLError('temporary connection failure'), io.BytesIO(b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\"}"}}]}')]) as request:
                result = settings.chat([{'role':'user','content':'Return JSON'}], json_mode=True)
            self.assertEqual(parse_json_object(result)['summary'], 'ok')
            self.assertEqual(request.call_count, 2)

    def test_connection_retry_does_not_consume_json_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            requests, logs = [], []
            responses = iter([urllib.error.URLError('temporary connection failure'),
                              '{"summary":"topic"} trailing', '{"summary":"valid topic"}'])
            def respond(request, **kwargs):
                requests.append(json.loads(request.data))
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': response}}]}).encode())
            with patch('urllib.request.urlopen', side_effect=respond):
                text = settings.chat([{'role': 'user', 'content': 'Return JSON'}], json_mode=True, on_retry=logs.append)
            self.assertEqual(parse_json_object(text)['summary'], 'valid topic')
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(logs), 2)
            self.assertIn('完整 JSON', requests[-1]['messages'][-1]['content'])

    def test_json_repair_does_not_consume_connection_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            malformed = io.BytesIO(b'{"choices":[{"message":{"content":"invalid"}}]}')
            valid = io.BytesIO(b'{"choices":[{"message":{"content":"{\\"summary\\":\\"ok\\"}"}}]}')
            with patch('urllib.request.urlopen', side_effect=[malformed, urllib.error.URLError('temporary'), valid]) as request:
                text = settings.chat([{'role': 'user', 'content': 'Return JSON'}], json_mode=True)
            self.assertEqual(parse_json_object(text)['summary'], 'ok')
            self.assertEqual(request.call_count, 3)

    def test_mixed_failures_still_stop_after_one_json_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            invalid = lambda: io.BytesIO(b'{"choices":[{"message":{"content":"invalid"}}]}')
            with patch('urllib.request.urlopen', side_effect=[urllib.error.URLError('temporary'), invalid(), invalid()]) as request:
                with self.assertRaisesRegex(ValueError, 'JSON'):
                    settings.chat([{'role': 'user', 'content': 'Return JSON'}], json_mode=True)
            self.assertEqual(request.call_count, 3)

    def test_authentication_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            failure = urllib.error.HTTPError('https://api.deepseek.com/chat/completions', 401, 'Unauthorized', {}, None)
            with patch('urllib.request.urlopen', side_effect=failure) as request:
                with self.assertRaisesRegex(RuntimeError, '401'):
                    settings.chat([{'role':'user','content':'Return JSON'}], json_mode=True)
            self.assertEqual(request.call_count, 1)

    def test_truncation_retry_expands_budget_but_remains_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp), None)
            settings.data['provider']['apiKey'] = 'test-key'
            budgets = []
            def respond(request, **kwargs):
                budgets.append(json.loads(request.data)['max_tokens'])
                return io.BytesIO(b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}')
            with patch('urllib.request.urlopen', side_effect=respond):
                with self.assertRaisesRegex(ValueError, '截断'):
                    settings.chat([{'role':'user','content':'Return JSON'}], max_tokens=12000, json_mode=True)
            self.assertEqual(budgets, [12000,16000])


if __name__ == '__main__':
    unittest.main()
