"""Compatibility and dependency assembly at the task-runtime boundary."""
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from research_swarm import exports, runtime, server
from research_swarm.providers import Settings
from research_swarm.workspace import WorkspaceApplication
from test_engine import sample_library


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        library = patch('research_swarm.library.Library')
        self.library = library.start()
        self.addCleanup(library.stop)
        self.library.return_value.load.return_value = sample_library()

    def application(self, **kwargs):
        app = runtime.ResearchApplication(self.source, self.root / 'runtime', **kwargs)
        self.addCleanup(app.engine.close)
        return app

    def test_old_imports_keep_the_same_objects(self):
        self.assertIs(server.ResearchApplication, runtime.ResearchApplication)
        self.assertIs(server.export_bundle, exports.export_bundle)
        self.assertIs(server.utc_now, runtime.utc_now)
        self.assertEqual(server.APP_ROOT, runtime.APP_ROOT)

    def test_standalone_defaults_preserve_configuration_and_paused_state(self):
        app = self.application()
        self.assertIs(app.runner.settings, app.settings)
        self.assertIs(app.engine._runner, app.runner)
        self.assertEqual(app.settings.path, self.root / 'runtime' / 'config.local.json')
        self.assertEqual(app.static_dir, runtime.APP_ROOT / 'dist')
        state = app.engine.snapshot()
        self.assertEqual(state['project']['workflow'], 'gated')
        self.assertTrue(state['paused'])

    def test_shared_settings_and_lock_preserve_object_identity(self):
        shared = Settings(self.root, None)
        lock = threading.RLock()
        app = self.application(settings=shared, mutation_lock=lock)
        self.assertIs(app.settings, shared)
        self.assertIs(app.runner.settings, shared)
        self.assertIs(app.mutation_lock, lock)

    def test_falsey_injected_objects_are_not_replaced(self):
        class FalseySettings(Settings):
            def __bool__(self):
                return False
        class FalseyLock:
            def __bool__(self):
                return False
        shared, lock = FalseySettings(self.root, None), FalseyLock()
        app = self.application(settings=shared, mutation_lock=lock)
        self.assertIs(app.settings, shared)
        self.assertIs(app.mutation_lock, lock)

    def test_engine_receives_final_runner_at_construction(self):
        shared = Settings(self.root, None)
        wrapped = Mock()
        observed = []
        def wrapper(app):
            self.assertIs(app.settings, shared)
            self.assertIs(app.runner.settings, shared)
            observed.append(app)
            return wrapped
        with patch('research_swarm.engine.Engine') as engine:
            app = runtime.ResearchApplication(self.source, self.root / 'runtime',
                                              settings=shared, runner_wrapper=wrapper)
        self.assertEqual(observed, [app])
        self.assertIs(engine.call_args.kwargs['runner'], wrapped)

    def test_shared_settings_do_not_bypass_invalid_local_configuration(self):
        directory = self.root / 'runtime'
        directory.mkdir()
        (directory / 'config.local.json').write_text('{invalid', encoding='utf-8')
        with patch('research_swarm.engine.Engine') as engine:
            with self.assertRaises(ValueError):
                self.application(settings=Settings(self.root, None))
            engine.assert_not_called()

    def test_retrieval_budget_and_interrupted_journal_survive_construction(self):
        directory = self.root / 'runtime'
        directory.mkdir()
        (directory / 'search-budgets.json').write_text('{"budget-1": 2}', encoding='utf-8')
        (directory / 'operations.jsonl').write_text(json.dumps({
            'id': 'retrieval-1', 'status': 'running', 'type': 'retrieval'}) + '\n', encoding='utf-8')
        app = self.application()
        self.assertEqual(app.search_budgets, {'budget-1': 2})
        self.assertEqual(app.operations[0]['status'], 'failed')
        self.assertEqual(app.operations[0]['id'], 'retrieval-1')

    def test_export_compatibility_compares_members_not_zip_timestamps(self):
        state = {'project': {'title': '研究', 'round': 1, 'mode': 'evidence'},
                 'report': {'ready': True, 'summary': '有限材料', 'claims': [], 'unresolved': ['待核实']},
                 'evidence': [], 'activities': [], 'nodes': [], 'requirements': []}
        def members(data):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return {name: archive.read(name) for name in archive.namelist()}
        old = members(server.export_bundle(state, self.root))
        new = members(exports.export_bundle(state, self.root))
        self.assertEqual(old, new)
        self.assertIn('claim-graph.json', new)
        self.assertNotIn('config.local.json', new)
        self.assertNotIn('claimGraph', state)

    def test_workspace_wrapper_reads_current_runner_and_task_context(self):
        workspace = WorkspaceApplication(self.source, self.root / 'workspace', import_existing=False)
        self.addCleanup(workspace.close)
        record = workspace._create()
        task_id = record['id']
        record['executionSettings'] = {'maxTimeoutSeconds': 30, 'materialIds': []}
        record['plan'] = {'capabilities': ['review']}
        with patch('research_swarm.sources.prepare_source', return_value=self.source), \
             patch.object(workspace.backend, 'jobs') as jobs:
            app = workspace._ensure_app(task_id)
            jobs.assert_not_called()
            self.assertIs(app.settings, workspace.settings)
            self.assertIs(app.runner.settings, workspace.settings)
            self.assertIs(app.mutation_lock, workspace._mutation_lock)
            received = []
            def replacement(node, context, log):
                received.append((context, workspace.settings._usage_context.get()))
                return {'summary': 'replacement'}
            app.runner = replacement
            result = app.engine._runner({'id': 'node-1'}, {}, lambda _: None)
            self.assertEqual(result, {'summary': 'replacement'})
            context, meter = received[0]
            self.assertEqual(meter, (task_id, 'node-1'))
            self.assertEqual(context['executionSettings'], record['executionSettings'])
            self.assertIsNot(context['executionSettings'], record['executionSettings'])
            self.assertEqual(context['researchPlan'], record['plan'])
            self.assertIsNot(context['researchPlan'], record['plan'])
            self.assertEqual(context['inputMaterials'], [])
            jobs.assert_called_once_with(task_id)
            self.assertEqual(workspace.settings._usage_context.get(), (None, None))


if __name__ == '__main__':
    unittest.main()
