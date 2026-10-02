import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from research_swarm.providers import Settings
from research_swarm.semantic_review import review_metadata
from research_swarm.workspace import WorkspaceApplication


def model_response(ids=('deepseek-flash', 'deepseek-v4-pro')):
    return io.BytesIO(json.dumps({'data': [{'id': model} for model in ids]}).encode())


class DeepSeekSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(self.root, None)

    def test_discovery_uses_fixed_official_address_and_never_saves_or_counts_usage(self):
        before = copy.deepcopy(self.settings.data)
        with patch('urllib.request.urlopen', return_value=model_response(('deepseek-flash', 'deepseek-flash', ' deepseek-v4-pro '))) as request:
            result = self.settings.deepseek_models({'apiKey': 'new-unsaved-secret'})
        self.assertEqual(result, {'models': [{'id': 'deepseek-flash'}, {'id': 'deepseek-v4-pro'}]})
        sent = request.call_args.args[0]
        self.assertEqual(sent.full_url, 'https://api.deepseek.com/models')
        self.assertEqual(sent.get_method(), 'GET')
        self.assertEqual(sent.get_header('Authorization'), 'Bearer new-unsaved-secret')
        self.assertEqual(request.call_args.kwargs['timeout'], 15)
        self.assertEqual(self.settings.data, before)
        self.assertFalse(self.settings.path.exists())
        self.assertFalse(self.settings.usage_path.exists())

    def test_saved_credentials_are_reused_only_for_official_endpoints(self):
        endpoints = ('https://api.deepseek.com', 'https://api.deepseek.com/v1/', 'https://api.deepseek.com:443/')
        for endpoint in endpoints:
            self.settings.update({'provider': {'baseUrl': endpoint, 'apiKey': 'official-secret'}})
            with patch('urllib.request.urlopen', return_value=model_response()) as request:
                self.settings.deepseek_models({})
            self.assertEqual(request.call_args.args[0].get_header('Authorization'), 'Bearer official-secret')
        for endpoint in ('https://api.example.com/v1', 'https://api.deepseek.com:8443', 'https://api.deepseek.com/proxy'):
            self.settings.update({'provider': {'baseUrl': endpoint, 'apiKey': 'custom-secret'}})
            with patch('urllib.request.urlopen') as request, self.assertRaisesRegex(ValueError, '官方 API Key'):
                self.settings.deepseek_models({})
            request.assert_not_called()
        self.settings.update({'provider': {'baseUrl': 'https://api.deepseek.com', 'type': 'anthropic'}})
        with patch('urllib.request.urlopen') as request, self.assertRaises(ValueError):
            self.settings.deepseek_models({})
        request.assert_not_called()

    def test_discovery_rejects_address_and_bad_key_fields_before_requesting(self):
        for payload in ({'baseUrl': 'https://custom.example'}, {'apiKey': 123}, [], {'apiKey': ''}):
            with patch('urllib.request.urlopen') as request, self.assertRaises(ValueError):
                self.settings.deepseek_models(payload)
            request.assert_not_called()

    def test_discovery_errors_do_not_expose_unsaved_key_or_upstream_body(self):
        key = 'secret-not-yet-saved'
        for failure in (urllib.error.HTTPError('https://api.deepseek.com/models', 401, key, {}, io.BytesIO(key.encode())),
                        urllib.error.HTTPError('https://api.deepseek.com/models', 503, key, {}, None),
                        urllib.error.URLError(key), TimeoutError(key)):
            with patch('urllib.request.urlopen', side_effect=failure), self.assertRaises(ValueError) as caught:
                self.settings.deepseek_models({'apiKey': key})
            self.assertNotIn(key, str(caught.exception))
            self.assertFalse(self.settings.path.exists())

    def test_empty_and_malformed_model_lists(self):
        with patch('urllib.request.urlopen', return_value=model_response(())):
            self.assertEqual(self.settings.deepseek_models({'apiKey': 'secret'}), {'models': []})
        for raw in (b'invalid', b'[]', b'{"data":{}}', b'{"choices":[]}', b'x' * (1024 * 1024 + 1)):
            with patch('urllib.request.urlopen', return_value=io.BytesIO(raw)), self.assertRaises(ValueError):
                self.settings.deepseek_models({'apiKey': 'secret'})

    def test_official_save_requires_explicit_available_model(self):
        before = copy.deepcopy(self.settings.data)
        for payload in ({'apiKey': 'secret'}, {'apiKey': 'secret', 'model': ''}, {'apiKey': 'secret', 'model': 1}):
            with patch('urllib.request.urlopen') as request, self.assertRaises(ValueError):
                self.settings.configure_deepseek(payload)
            request.assert_not_called()
        for ids in ((), ('deepseek-flash',)):
            with patch('urllib.request.urlopen', return_value=model_response(ids)), self.assertRaises(ValueError):
                self.settings.configure_deepseek({'apiKey': 'secret', 'model': 'old-custom-model'})
        self.assertEqual(self.settings.data, before)
        self.assertFalse(self.settings.path.exists())

    def test_official_save_replaces_custom_main_and_preserves_independent_roles(self):
        self.settings.update({'providerRouting': 'shared_main', 'providers': {
            role: {'baseUrl': f'https://{role}.example/v1', 'model': f'{role}-model', 'apiKey': f'{role}-secret', 'apiKeyEnv': 'OLD_KEY_ENV'}
            for role in Settings.ROLES}})
        previous = copy.deepcopy(self.settings.data['providers'])
        with patch('urllib.request.urlopen', return_value=model_response()):
            public = self.settings.configure_deepseek({'apiKey': 'official-secret', 'model': 'deepseek-v4-pro'})
        self.assertEqual(public['providerRouting'], 'per_role')
        self.assertEqual(public['provider']['baseUrl'], 'https://api.deepseek.com')
        self.assertEqual(public['provider']['model'], 'deepseek-v4-pro')
        self.assertEqual(self.settings.data['providers']['main']['apiKeyEnv'], '')
        for role in ('judge', 'redteam'):
            self.assertEqual(self.settings.data['providers'][role], previous[role])
            self.assertTrue(public['providers'][role]['independentFromMain'])
        self.assertNotIn('secret', json.dumps(public))

    def test_shared_routing_follows_main_and_restores_saved_connections_and_policy(self):
        self.settings.update({'providers': {role: {'baseUrl': f'https://{role}.example/v1', 'model': f'{role}-model', 'apiKey': f'{role}-secret'} for role in Settings.ROLES}})
        previous = copy.deepcopy(self.settings.data['providers'])
        public = self.settings.update({'providerRouting': 'shared_main'})
        self.assertEqual(public['reviewPolicy'], 'shared')
        self.assertEqual(self.settings.data['reviewPolicy'], 'independent')
        self.assertFalse(public['capabilities']['independentReviewReady'])
        self.assertTrue(public['capabilities']['reviewReady'])
        for role in ('judge', 'redteam'):
            self.assertEqual(public['providerConfigurations'][role]['model'], f'{role}-model')
            self.assertEqual(public['providers'][role]['model'], 'main-model')
            self.assertTrue(public['providers'][role]['sharedWithMain'])
            self.assertEqual(review_metadata(self.settings.role_status(role), role)['reviewLevel'], 'same_model')
        self.settings.update({'provider': {'type': 'anthropic', 'baseUrl': 'https://new-main.example/v1', 'model': 'new-model', 'apiKey': 'new-secret'}})
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"content":[{"type":"text","text":"OK"}]}')) as request:
            self.settings.chat([{'role': 'user', 'content': 'OK'}], role='judge')
        self.assertEqual(request.call_args.args[0].full_url, 'https://new-main.example/v1/messages')
        self.assertEqual(request.call_args.args[0].get_header('X-api-key'), 'new-secret')
        self.settings = Settings(self.root, None)
        self.assertEqual(self.settings.role_status('redteam')['model'], 'new-model')
        restored = self.settings.update({'providerRouting': 'per_role'})
        self.assertEqual(restored['reviewPolicy'], 'independent')
        for role in ('judge', 'redteam'):
            self.assertEqual(self.settings.data['providers'][role], previous[role])
            self.assertFalse(restored['providers'][role]['sharedWithMain'])
        self.assertNotIn('secret', json.dumps(restored))

    def test_old_routing_is_preserved_and_invalid_routing_update_is_atomic(self):
        self.assertEqual(self.settings.public()['providerRouting'], 'per_role')
        self.settings.update({'reviewPolicy': 'shared', 'provider': {'apiKey': 'main-secret'}})
        self.assertTrue(self.settings.role_status('judge')['sharedWithMain'])
        self.assertEqual(self.settings.public()['providerConfigurations']['judge']['baseUrl'], '')
        before = copy.deepcopy(self.settings.data)
        with self.assertRaises(ValueError):
            self.settings.update({'providerRouting': 'unknown', 'provider': {'model': 'changed'}})
        self.assertEqual(self.settings.data, before)


class DeepSeekWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = WorkspaceApplication(self.root / 'missing', self.root / 'state', import_existing=False)
        self.addCleanup(self.app.close)

    def test_discovery_is_read_only_and_available_while_research_is_busy(self):
        self.app._configuring = True
        before = copy.deepcopy(self.app.settings.data)
        with patch('urllib.request.urlopen', return_value=model_response()):
            result = self.app.post('/api/provider/models/deepseek', {'apiKey': 'unsaved-secret'})
        self.assertEqual(len(result['models']), 2)
        self.assertEqual(self.app.settings.data, before)
        self.assertFalse(self.app.settings.path.exists())

    def test_saved_key_setup_test_failure_rolls_back_entire_config(self):
        self.app.settings.update({'providerRouting': 'shared_main', 'provider': {'apiKey': 'saved-secret', 'model': 'old-model'}})
        before = copy.deepcopy(self.app.settings.data)
        before_file = self.app.settings.path.read_bytes()
        with patch('urllib.request.urlopen', return_value=model_response()), patch.object(self.app.settings, 'chat', side_effect=RuntimeError('test failed saved-secret')), self.assertRaisesRegex(ValueError, '已隐藏'):
            self.app.post('/api/setup', {'model': 'deepseek-flash'})
        self.assertEqual(self.app.settings.data, before)
        self.assertEqual(self.app.settings.path.read_bytes(), before_file)

    def test_failed_model_validation_does_not_create_configuration(self):
        for path in ('/api/setup', '/api/settings/deepseek'):
            with self.assertRaises(ValueError):
                self.app.post(path, {'apiKey': 'unsaved-secret'})
            self.assertFalse(self.app.settings.path.exists())

    def test_setup_uses_selected_model_for_test_and_shares_unconfigured_roles(self):
        requests = []
        def respond(request, **kwargs):
            requests.append(request)
            return model_response() if request.get_method() == 'GET' else io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        with patch('urllib.request.urlopen', side_effect=respond):
            result = self.app.post('/api/setup', {'apiKey': 'official-secret', 'model': 'deepseek-v4-pro'})
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(requests[-1].data)['model'], 'deepseek-v4-pro')
        for role in ('judge', 'redteam'):
            self.assertTrue(result['providers'][role]['sharedWithMain'])
            self.assertEqual(result['providerConfigurations'][role]['model'], '')
