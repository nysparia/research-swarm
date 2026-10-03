import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from research_swarm.providers import Settings
from research_swarm.runner import ResearchRunner
from research_swarm.semantic_review import relation_gaps
from test_review_routing import ScriptedRoleSettings, judge_fixture, redteam_fixture, RecordedArtifacts


class SharedProviderTests(unittest.TestCase):
    def setUp(self):
        from recovery_fixtures import ImmediateRecovery
        recovery = patch('research_swarm.providers.ProviderRecovery', ImmediateRecovery)
        recovery.start(); self.addCleanup(recovery.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        models = patch.object(Settings, '_deepseek_models', return_value={'models': [{'id': 'deepseek-flash'}, {'id': 'deepseek-chat'}]})
        models.start()
        self.addCleanup(models.stop)

    def test_one_key_configure_preserves_secret_and_uses_official_endpoint(self):
        settings = Settings(self.root, None)
        settings.update({'provider': {'apiKey': 'existing-secret'}})
        public = settings.configure_deepseek({'model': 'deepseek-chat'})
        self.assertEqual(public['mode'], 'llm')
        self.assertEqual(public['reviewPolicy'], 'shared')
        self.assertNotIn('existing-secret', json.dumps(public))
        requests = []
        def respond(request, **kwargs):
            requests.append(request)
            return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        with patch('urllib.request.urlopen', side_effect=respond):
            for role in Settings.ROLES:
                settings.chat([{'role': 'user', 'content': 'OK'}], role=role)
        self.assertTrue(all(r.full_url == 'https://api.deepseek.com/chat/completions' for r in requests))
        self.assertTrue(all(r.get_header('Authorization') == 'Bearer existing-secret' for r in requests))
        self.assertFalse(settings.public()['capabilities']['independentReviewReady'])
        self.assertEqual(Settings(self.root, None).public()['reviewPolicy'], 'shared')
        with self.assertRaises(ValueError):
            settings.configure_deepseek({'baseUrl': 'https://other.example'})

    def test_shared_reviews_complete_blinded_without_independence(self):
        for role in ('judge', 'redteam'):
            if role == 'judge':
                node, context, response = judge_fixture()
            else:
                node, context, response, _ = redteam_fixture()
            settings = ScriptedRoleSettings(self.root, [response])
            settings.update({'reviewPolicy': 'shared', 'providers': {role: None}})
            result = ResearchRunner(settings, self.root, local_tools=RecordedArtifacts())(node, context, lambda _: None)
            review = result['structured']['review']
            self.assertEqual(review['status'], 'completed')
            self.assertEqual(review['reviewLevel'], 'same_model')
            self.assertFalse(review['independent'])
            self.assertTrue(review['blinded'])
            self.assertNotIn('PRODUCER_', json.dumps(settings.calls))

    def test_shared_measurement_requires_explicit_review_lineage_and_actual_execution(self):
        evidence = {'id': 'e1', 'type': 'experiment', 'tool': 'python_run', 'executionStatus': 'completed',
                    'quote': 'actual measurement', 'locator': 'runs/a/receipt.json'}
        relation = {'type': 'support', 'polarity': 'for', 'quote': evidence['quote'], 'locator': evidence['locator'],
                    'rule': 'verified_measurement', 'confidence': .9}
        approval = {'artifactHashes': {'runs/a/raw.csv': 'abc'}, 'review': {'role': 'redteam', 'status': 'completed',
                    'independent': False, 'reviewLevel': 'same_model', 'policy': 'shared', 'blinded': True}}
        self.assertEqual(relation_gaps(relation, evidence, {}, {'e1': approval}), [])
        for invalid in ({**evidence, 'executionStatus': 'failed'}, {**evidence, 'tool': 'paper_read'}):
            self.assertTrue(relation_gaps(relation, invalid, {}, {'e1': approval}))
        forged = {**approval, 'review': {**approval['review'], 'policy': 'independent'}}
        self.assertTrue(relation_gaps(relation, evidence, {}, {'e1': forged}))

    def test_real_usage_persists_per_task_including_retries_and_transport_errors(self):
        settings = Settings(self.root, None)
        settings.configure_deepseek({'apiKey': 'secret', 'model': 'deepseek-flash'})
        response = {'choices': [{'message': {'content': 'OK'}}],
                    'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}}
        with settings.usage_context('task-a', 'node-a'), patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            settings.chat([{'role': 'user', 'content': 'OK'}])
        with settings.usage_context('task-b'), patch('urllib.request.urlopen', side_effect=urllib.error.URLError('outage')):
            with self.assertRaises(Exception):
                settings.chat([{'role': 'user', 'content': 'OK'}])
        restarted = Settings(self.root, None)
        self.assertEqual(restarted.usage('task-a')['totalTokens'], 18)
        self.assertEqual(restarted.usage('task-a')['calls'], 1)
        self.assertEqual(restarted.usage('task-a')['errors'], 0)
        self.assertEqual(restarted.usage('task-b')['errors'], 4)
        self.assertEqual(restarted.usage()['calls'], 5)
        self.assertIsNone(restarted.usage()['billedCurrency'])
        self.assertNotIn('secret', json.dumps(restarted.usage()))

    def test_format_retry_counts_each_actual_response_and_its_tokens(self):
        settings = Settings(self.root, None)
        settings.configure_deepseek({'apiKey': 'secret', 'model': 'deepseek-flash'})
        def response(content):
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': content}}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())
        with settings.usage_context('task-a'), patch('urllib.request.urlopen', side_effect=[response('invalid'), response('{}')]):
            settings.chat([{'role': 'user', 'content': 'JSON'}], json_mode=True)
        self.assertEqual(settings.usage('task-a')['calls'], 2)
        self.assertEqual(settings.usage('task-a')['errors'], 1)
        self.assertEqual(settings.usage('task-a')['totalTokens'], 30)

    def test_context_is_thread_local_and_restores_nested_task(self):
        from concurrent.futures import ThreadPoolExecutor
        settings = Settings(self.root, None)
        settings.configure_deepseek({'apiKey': 'secret', 'model': 'deepseek-flash'})
        def call(task):
            with settings.usage_context(task):
                settings.chat([{'role': 'user', 'content': task}])
        def respond(*args, **kwargs):
            return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}],"usage":{"total_tokens":9}}')
        with patch('urllib.request.urlopen', side_effect=respond), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(call, ['task-a', 'task-b']))
            with settings.usage_context('outer'):
                call('inner')
                settings.chat([{'role': 'user', 'content': 'outer'}])
        for task in ('task-a', 'task-b', 'outer', 'inner'):
            self.assertEqual(settings.usage(task)['calls'], 1)
            self.assertEqual(settings.usage(task)['totalTokens'], 9)

    def test_shared_lineage_survives_real_engine_and_actual_experiment(self):
        from research_swarm.engine import Engine
        from research_swarm.local_tools import LocalResearchTools
        from test_research_cycle import CycleRunner, cycle_library
        from test_workspace import wait_until
        class SharedCycle(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                review = value['structured'].get('review')
                if review:
                    review.update(independent=False, reviewLevel='same_model', policy='shared', blinded=True)
                return value
        runner = SharedCycle(experiment=True)
        engine = Engine(cycle_library(), self.root / 'state.sqlite', runner=runner, workflow='autonomous', max_workers=2)
        self.addCleanup(engine.close)
        runner.tools = LocalResearchTools(engine._artifact_root)
        engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct', 'mode': 'llm'})
        wait_until(lambda: engine.snapshot()['report'].get('ready') or engine.snapshot()['status'] == 'failed')
        state = engine.snapshot()
        self.assertNotEqual(state['status'], 'failed', [n.get('error') for n in state['nodes']])
        current = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        self.assertEqual(current['assessment']['status'], 'supported')
        self.assertFalse(current['assessment']['review']['independent'])
        self.assertEqual(current['assessment']['review']['reviewLevel'], 'same_model')
        self.assertTrue(state['project']['researchEvidenceApprovals'])
        for approval in state['project']['researchEvidenceApprovals'].values():
            self.assertFalse(approval['review']['independent'])
            self.assertEqual(approval['review']['reviewLevel'], 'same_model')
        row = next(c for c in state['report']['claims'] if c['claimId'] == current['id'])
        self.assertEqual(row['status'], 'candidate')
        self.assertFalse(row['review']['independent'])

    def test_main_model_receives_user_plan_and_experiment_scope(self):
        from test_review_routing import base_context
        context = base_context()
        context['researchPlan'] = {'intent': 'review', 'capabilities': ['literature', 'report'], 'modules': ['review']}
        settings = ScriptedRoleSettings(self.root, [{'summary': 'Read-only review', 'claims': [], 'structured': {}, 'unresolved': []}])
        ResearchRunner(settings, self.root)({'id': 'main', 'phase': 'execute', 'input': {}}, context, lambda _: None)
        messages = settings.calls[0]['messages']
        self.assertEqual(json.loads(messages[1]['content'])['researchPlan'], context['researchPlan'])
        self.assertIn('未经用户指定实验能力', messages[0]['content'])

    def test_main_model_receives_safe_input_names_and_declared_execution_limits(self):
        from test_review_routing import base_context
        context = base_context()
        context['inputMaterials'] = [{'id': 'material-a', 'name': 'data.csv', 'sha256': 'a' * 64,
            'path': 'C:/private/managed/data.csv', 'sourcePath': 'C:/private/original.csv',
            'source': {'sourcePath': 'C:/private/original.csv'}, 'localName': 'C:/private/bad.csv'}]
        context['executionSettings'] = {'maxTimeoutSeconds': 3600, 'resources': {'cpuCores': 2, 'gpuCount': 0,
            'privatePath': 'C:/private/resource'}, 'revision': 4, 'internalPath': 'C:/private/settings'}
        settings = ScriptedRoleSettings(self.root, [{'summary': 'Ready', 'claims': [], 'structured': {}, 'unresolved': []}])
        ResearchRunner(settings, self.root)({'id': 'main', 'phase': 'execute', 'input': {}}, context, lambda _: None)
        messages = settings.calls[0]['messages']
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['inputMaterials'], [{'id': 'material-a', 'name': 'data.csv',
            'sha256': 'a' * 64, 'localName': 'inputs/data.csv'}])
        self.assertEqual(payload['executionSettings'], {'maxTimeoutSeconds': 3600, 'resources': {'cpuCores': 2, 'gpuCount': 0}})
        self.assertNotIn('C:/private', json.dumps(messages))
        self.assertNotIn('sourcePath', json.dumps(messages))
        self.assertIn('localName', messages[0]['content'])

    def test_reproduction_prompt_uses_configured_long_budget_consistently(self):
        from test_review_routing import base_context
        context = base_context()
        context.update(taskMode='reproduction', executionSettings={'maxTimeoutSeconds': 7200})
        settings = ScriptedRoleSettings(self.root, [{'summary': 'Await actual reproduction evidence',
            'claims': [], 'structured': {}, 'unresolved': []}])
        ResearchRunner(settings, self.root, local_tools=RecordedArtifacts())(
            {'id': 'main', 'phase': 'execute', 'input': {}}, context, lambda _: None)
        system = settings.calls[0]['messages'][0]['content']
        self.assertIn('7200秒', system)
        self.assertNotIn('最多180秒', system)
        self.assertNotIn('180秒内无法完成', system)
        self.assertNotIn('仅支持复现预检与小实验', system)
        self.assertIn('reproductionTarget.expected/tolerance', system)
        self.assertIn('不承诺', system)
