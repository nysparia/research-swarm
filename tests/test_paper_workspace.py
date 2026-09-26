import io
import json
import zipfile
import tempfile
import time
import unittest
from pathlib import Path

from research_swarm.engine import Engine
from research_swarm.workspace import WorkspaceApplication
from test_engine import sample_library
from test_workspace import wait_until


class PaperWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = WorkspaceApplication(self.root / 'source', self.root / 'state', import_existing=False)
        self.id = self.app.post('/api/tasks', {})['task']['id']
        self.prefix = '/api/tasks/' + self.id

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_section_edits_persist_and_export_before_research_completes(self):
        self.app.post(self.prefix + '/paper/sections/method', {'markdown': '用户的方法', 'expectedRevision': 0})
        detail = self.app.detail(self.id)
        self.assertEqual(detail['paper']['sections'][3]['author'], 'user')
        self.assertIn('用户的方法', self.app.read_api(self.prefix + '/paper/manuscript')[1].decode('utf-8'))
        with zipfile.ZipFile(io.BytesIO(self.app.read_api(self.prefix + '/paper/export')[1])) as archive:
            self.assertIn('用户的方法', archive.read('paper/manuscript.md').decode('utf-8'))
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(self.prefix + '/paper/sections/method', {'markdown': 'old', 'expectedRevision': 0})
        self.app.close()
        self.app = WorkspaceApplication(self.root / 'source', self.root / 'state', import_existing=False)
        self.assertEqual(self.app.detail(self.id)['paper']['sections'][3]['markdown'], '用户的方法')

    def test_topic_choice_becomes_a_real_requirement_and_is_preserved(self):
        record = self.app._records[self.id]
        record['paper']['topics'] = [{'id': 'topic-1', 'title': '降低查询延迟', 'question': '索引是否有效',
            'hypothesis': '选择性影响索引收益', 'contribution': '系统测量', 'feasibility': '本机可执行', 'firstExperiment': 'SQLite 对照'}]
        self.app.post(self.prefix + '/paper/topic', {'topicId': 'topic-1', 'expectedRevision': 0, 'note': '优先小数据'})
        wait_until(lambda: not self.app.detail(self.id)['document']['polishing'])
        detail = self.app.detail(self.id)
        self.assertIn('优先小数据', detail['document']['markdown'])
        self.assertIn('索引是否有效', detail['document']['markdown'])
        self.assertEqual(detail['paper']['selectedTopicId'], 'topic-1')
        self.assertTrue(detail['paper']['decisions'])

    def test_user_can_approve_and_then_edit_a_draft_before_any_research_state(self):
        detail = self.app.post(self.prefix + '/paper/sections/method', {'markdown': '候选方法，尚未验证', 'expectedRevision': 0})
        detail = self.app.post(self.prefix + '/paper/approve', {'expectedRevision': detail['paper']['revision'], 'acknowledgeIssues': True})
        self.assertTrue(detail['paper']['approved'])
        self.app.post(self.prefix + '/paper/sections/method', {'markdown': '修改后的方法', 'expectedRevision': 1})
        self.assertFalse(self.app.detail(self.id)['paper']['approved'])

    def test_active_research_can_export_an_honest_draft_snapshot(self):
        from research_swarm.server import export_bundle
        state = {'project': {}, 'report': {'summary': '尚在进行', 'ready': False}, 'nodes': [], 'history': []}
        with self.assertRaises(ValueError):
            export_bundle(state, self.root)
        with zipfile.ZipFile(io.BytesIO(export_bundle(state, self.root, allow_draft=True))) as archive:
            text = archive.read('report.md').decode('utf-8')
            self.assertIn('进行中的草稿', text)
            self.assertNotIn('本轮执行已结束', text)

    def test_terminal_reads_only_registered_process_logs_and_caps_size(self):
        record = self.app._records[self.id]
        record['phase'] = 'completed'; record['runs'] = [{'round': 1}]
        directory = self.app._data_root / self.id
        (directory / 'reports').mkdir()
        (directory / 'runtime/runs/run1').mkdir(parents=True)
        (directory / 'runtime/runs/run1/stdout.txt').write_text('x' * 130000)
        (directory / 'runtime/private.txt').write_text('must not read')
        state = {'nodes': [], 'history': [{'id': 'exec1', 'type': 'tool-executed', 'execution': {
            'stdoutPath': 'runs/run1/stdout.txt', 'stderrPath': 'private.txt'}}]}
        (directory / 'reports/round-1.json').write_text(json.dumps(state))
        result = self.app.read_api(self.prefix + '/paper/terminal', 'execution=exec1')[1]
        self.assertLess(len(result['stdout']), 121000)
        self.assertIn('截断', result['stdout'])
        self.assertEqual(result['stderr'], '')
        with self.assertRaises(ValueError):
            self.app.read_api(self.prefix + '/paper/terminal', 'execution=unknown')


class DecisionGateTests(unittest.TestCase):
    def test_budget_limits_real_dispatch_and_pending_decision_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.sqlite'
            engine = Engine(sample_library(), path, max_workers=8, workflow='autonomous')
            try:
                with engine._lock:
                    engine._start_autonomous({'paperResearch': True, 'budgetTier': 'swarm'})
                    self.assertEqual(engine._limit('maxTasks'), 240)
                    self.assertEqual(engine._limit('maxParallel'), 8)
                    central = engine._get_node('central')
                    engine._add_children(central, [{'title': f'child-{i}', 'description': 'D', 'acceptance': 'A', 'kind': 'evidence'} for i in range(12)])
                    central.update(phase='aggregate')
                    jobs = [engine._next_job() for _ in range(8)]
                    self.assertTrue(all(jobs))
                    self.assertIsNone(engine._next_job())
                    engine._state['project']['researchDecision'] = {'id': 'persisted-decision', 'question': 'Choose'}
                    engine._state['paused'] = True
                    engine._commit()
            finally:
                engine.close()
            engine = Engine(sample_library(), path, workflow='autonomous')
            try:
                self.assertEqual(engine.snapshot()['project']['researchDecision']['id'], 'persisted-decision')
                with self.assertRaisesRegex(ValueError, '决策'):
                    engine.command('resume', {})
            finally:
                engine.close()

    def test_retry_cannot_bypass_an_unanswered_research_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(sample_library(), Path(temp) / 'test.sqlite', max_workers=1, workflow='autonomous')
            try:
                with engine._lock:
                    node = engine._state['nodes'][0]
                    node.update(active=True, status='failed')
                    engine._state['project'].update(researchStarted=True, researchDecision={'id': 'decision-1'})
                    engine._state['paused'] = True
                    engine._manual_paused = True
                    engine._retry({'nodeId': node['id']})
                    self.assertTrue(engine._state['paused'])
                    engine._state['paused'] = False  # The scheduler must independently enforce the gate.
                    self.assertIsNone(engine._next_job())
            finally:
                engine.close()

    def test_decision_pauses_scheduler_and_choice_is_validated_and_given_to_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            observed = []
            def runner(node, context, log):
                observed.append(context)
                if not context.get('researchChoices'):
                    return {'summary': '需要选择实验侧重点', 'claims': [], 'evidenceIds': [], 'structured': {
                        'researchDecision': {'question': '优先延迟还是精度？', 'rationale': '预算只够先验证一项',
                            'options': [{'label': '延迟', 'effect': '按延迟预算比较'}, {'label': '精度', 'effect': '按精度比较'}]}}}
                return {'summary': '已按用户的选择工作', 'claims': [], 'evidenceIds': [], 'structured': {}}
            engine = Engine(sample_library(), Path(temp) / 'test.sqlite', runner=runner, workflow='autonomous', max_workers=1)
            try:
                engine.command('start-autonomous', {'paperResearch': True, 'paperContext': {'sections': [{'id': 'method', 'markdown': '用户的原始方法'}]}})
                wait_until(lambda: bool(engine.snapshot()['project'].get('researchDecision')))
                state = engine.snapshot()
                self.assertTrue(state['paused'])
                with self.assertRaisesRegex(ValueError, '决策'):
                    engine.command('resume', {})
                question = state['project']['researchDecision']
                with self.assertRaises(ValueError):
                    engine.command('research-choice', {'decisionId': question['id'], 'expectedRevision': state['revision'] - 1, 'optionIndex': 0})
                self.assertTrue(engine.snapshot()['project']['researchDecision'])
                engine.command('research-choice', {'decisionId': question['id'], 'expectedRevision': state['revision'], 'optionIndex': 0, 'note': 'CPU 延迟'})
                wait_until(lambda: engine.snapshot()['report']['ready'])
                self.assertTrue(observed[-1]['researchChoices'])
                self.assertIn('CPU 延迟', observed[-1]['researchChoices'][0]['answer'])
                audit = engine.node_history('central')
                contexts = [entry.get('context', {}) for entry in audit]
                self.assertTrue(any(c.get('paperContext', {}).get('sections') for c in contexts))
                self.assertTrue(any(c.get('researchChoices') for c in contexts))
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()
