"""Public workspace compatibility, independent of v2 implementation details."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.workspace import WorkspaceApplication


class V2InterfaceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.workspace = WorkspaceApplication(source, Path(self.temp.name), import_existing=False)
        self.addCleanup(self.workspace.close)

    def create_v2(self):
        detail = self.workspace.post('/api/tasks', {'workflowVersion': 'conversation_only_v2'})
        return detail, '/api/tasks/' + detail['task']['id']

    def read(self, path):
        response = self.workspace.read_api(path)
        self.assertIsNotNone(response)
        self.assertEqual(response[0], 200)
        return response[1]

    def test_legacy_remains_the_default_and_keeps_existing_detail_shape(self):
        detail = self.workspace.post('/api/tasks', {})
        for key in ('task', 'messages', 'document', 'state', 'workbench'):
            self.assertIn(key, detail)
        task_id = detail['task']['id']
        listed = self.read('/api/tasks')['tasks']
        self.assertIn(task_id, [task['id'] for task in listed])
        self.assertEqual(self.read('/api/tasks/' + task_id)['task']['id'], task_id)

    def test_v2_uses_existing_task_and_read_routes_without_creating_legacy_engine(self):
        with patch('research_swarm.engine.Engine', side_effect=AssertionError('v2 constructed legacy Engine')):
            detail, base = self.create_v2()
            for key in ('task', 'messages', 'document', 'state', 'workbench'):
                self.assertIn(key, detail)
            self.assertEqual(self.read(base)['task']['id'], detail['task']['id'])
            self.assertIsInstance(self.read(base + '/workbench'), dict)
            self.assertIsInstance(self.read(base + '/report'), dict)
            self.assertIsInstance(self.read(base + '/brief'), dict)
            events = self.read(base + '/events')
            for key in ('events', 'nextCursor', 'more'):
                self.assertIn(key, events)
            self.assertIsInstance(events['events'], list)
            listed = self.read('/api/tasks')['tasks']
            self.assertIn(detail['task']['id'], [task['id'] for task in listed])

    def test_compatibility_start_cannot_implicitly_confirm_a_brief(self):
        _, base = self.create_v2()
        before = self.read(base + '/brief')
        with patch('research_swarm.engine.Engine', side_effect=AssertionError('legacy fallback')):
            for suffix, payload in (
                ('/start', {'expectedRevision': 0}),
                ('/research/start', {'expectedBriefVersion': 0, 'requestId': 'without-confirmation'}),
            ):
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    self.workspace.post(base + suffix, payload)
        self.assertEqual(self.read(base + '/brief'), before)

    def test_legacy_write_routes_cannot_modify_v2_contract(self):
        _, base = self.create_v2()
        before = self.read(base + '/brief')
        requests = (
            ('/document', {'markdown': '# bypass', 'expectedRevision': 0}),
            ('/draft', {'expectedRevision': 0, 'operations': []}),
            ('/research-choice', {'decisionId': 'fake', 'expectedRevision': 0, 'optionIndex': 0}),
            ('/actions/intervene', {'nodeId': 'central', 'kind': 'modify', 'text': 'bypass'}),
            ('/deepen', {'nodeId': 'central', 'text': 'bypass', 'allowNewSearch': True}),
            ('/jobs', {'code': 'print("unauthorized")'}),
        )
        with patch('research_swarm.engine.Engine', side_effect=AssertionError('legacy fallback')):
            for suffix, payload in requests:
                with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                    self.workspace.post(base + suffix, payload)
        self.assertEqual(self.read(base + '/brief'), before)

    def test_unknown_workflow_is_not_silently_created_as_legacy(self):
        before = self.read('/api/tasks')['tasks']
        with self.assertRaises(ValueError):
            self.workspace.post('/api/tasks', {'workflowVersion': 'unknown-future-workflow'})
        self.assertEqual(self.read('/api/tasks')['tasks'], before)


if __name__ == '__main__':
    unittest.main()
