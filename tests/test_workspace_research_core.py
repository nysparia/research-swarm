import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.engine import Engine
from research_swarm.workspace import WorkspaceApplication
from test_engine import sample_library
from test_workspace import wait_until


class WorkspaceResearchCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.workspace = WorkspaceApplication(source, self.root / 'state', import_existing=False)
        self.task_id = self.workspace.post('/api/tasks', {})['task']['id']
        record = self.workspace._records[self.task_id]
        record['document'].update(markdown='# 我的研究需求', revision=3, polishing=False)
        record['compiled'] = self.workspace._local_draft('我的研究需求', '', False)
        self.app = self.workspace._ensure_app(self.task_id)
        self.app.engine.close()
        store = self.workspace._data_root / self.task_id / 'runtime' / 'swarm.sqlite'
        self.app.engine = Engine(sample_library(), store, runner=lambda *_: {}, workflow='autonomous')
        self.app.engine.command('refresh-library', {'library': sample_library()})

    def tearDown(self):
        self.workspace.close()
        self.temp.cleanup()

    def test_model_start_delegates_literature_after_background_instead_of_preretrieving(self):
        self.app._retrieve = lambda *_: self.fail('Model cycle must first expand background')
        with patch.object(self.workspace.settings, 'public', return_value={'mode': 'llm', 'capabilities': {'modelReady': True}}):
            self.workspace._run(self.task_id, self.workspace._records[self.task_id]['token'])
        state = self.app.engine.snapshot()
        self.assertTrue(state['project'].get('researchCycle'))
        self.assertTrue(state['project'].get('paperResearch'))

    def test_evidence_mode_runs_existing_library_without_any_network_or_model_call(self):
        self.workspace.settings.update({'mode': 'evidence', 'provider': {'apiKey': 'configured-cloud-key'}})
        with patch.object(self.app, '_retrieve', side_effect=AssertionError('must not retrieve')) as retrieve, \
             patch.object(self.workspace.settings, 'chat', side_effect=AssertionError('must not call model')) as chat:
            self.workspace._run(self.task_id, self.workspace._records[self.task_id]['token'])
        state = self.app.engine.snapshot()
        self.assertEqual(state['project']['mode'], 'evidence')
        self.assertFalse(state['project'].get('paperResearch'))
        self.assertFalse(state['project'].get('allowNewSearch'))
        self.assertNotEqual(self.workspace._records[self.task_id]['phase'], 'failed')
        retrieve.assert_not_called()
        chat.assert_not_called()

    def seed_decision(self):
        with self.app.engine._lock:
            state = self.app.engine._state
            root = next(n for n in state['nodes'] if n['id'] == 'central')
            root['input']['researchStep'] = 'topic'
            state['project'].update(researchStarted=True, paperResearch=True,
                researchCycle={'topic': {}}, researchDecision={'id': 'choice-1', 'nodeId': 'central', 'version': 1,
                    'question': '先比较基线，还是探索新机制？', 'options': [
                        {'label': '比较基线', 'effect': '先取得可比数据'},
                        {'label': '探索机制', 'effect': '设计机制差异实验'}]})
            state['paused'] = True
            self.app.engine._manual_paused = True
            self.app.engine._commit()
        record = self.workspace._records[self.task_id]
        record['phase'] = 'researching'
        self.workspace._save(record)
        self.workspace._synchronize(record)
        return record

    def test_chat_answers_a_pending_research_choice_without_rewriting_requirements(self):
        record = self.seed_decision()
        self.assertTrue(any('先比较基线' in m['content'] for m in record['messages']))
        self.app.engine._runner = None
        detail = self.workspace.post(f'/api/tasks/{self.task_id}/messages', {'text': '2'})
        self.assertEqual(detail['document']['markdown'], '# 我的研究需求')
        self.assertEqual(detail['document']['revision'], 3)
        self.assertFalse(detail['document']['polishing'])
        self.assertFalse(self.app.engine.snapshot()['project'].get('researchDecision'))
        self.assertIn('探索机制', self.app.engine.snapshot()['project']['researchChoices'][-1]['answer'])

    def test_explicit_research_choice_keeps_the_selected_decision_and_revision(self):
        record = self.seed_decision()
        self.app.engine._runner = None
        revision = self.app.engine.snapshot()['revision']
        before = len(record['messages'])
        # Inspect the accepted decision before a worker can execute the next
        # research step; this test does not supply an experimental runner.
        with self.app.engine._condition:
            detail = self.workspace.post(f'/api/tasks/{self.task_id}/research-choice', {
                'decisionId': 'choice-1', 'expectedRevision': revision, 'optionIndex': 1, 'note': '保持 CPU 条件一致'})
        self.assertEqual(detail['document']['revision'], 3)
        self.assertEqual(detail['phase'], 'researching')
        choice = self.app.engine.snapshot()['project']['researchChoices'][-1]
        self.assertIn('探索机制', choice['answer'])
        self.assertIn('保持 CPU 条件一致', choice['answer'])
        self.assertEqual([m['role'] for m in detail['messages'][before:]], ['user', 'assistant'])
        self.assertTrue(all(m['decisionId'] == 'choice-1' and m['stateRevision'] == revision
                            for m in detail['messages'][before:]))

    def test_explicit_decision_refuses_replaced_question_or_state_and_leaves_no_answer(self):
        record = self.seed_decision()
        self.app.engine._runner = None
        revision = self.app.engine.snapshot()['revision']
        path = f'/api/tasks/{self.task_id}/research-choice'
        before = len(record['messages'])
        with self.assertRaisesRegex(ValueError, '变化'):
            self.workspace.post(path, {'decisionId': 'an-old-question', 'expectedRevision': revision, 'optionIndex': 1})
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.workspace.post(path, {'decisionId': 'choice-1', 'expectedRevision': revision - 1, 'optionIndex': 1})
        self.assertEqual(len(record['messages']), before)
        self.assertEqual(self.app.engine.snapshot()['revision'], revision)
        self.assertEqual(self.app.engine.snapshot()['project']['researchDecision']['id'], 'choice-1')

    def test_new_round_request_cannot_be_interpreted_as_an_active_research_choice(self):
        record = self.seed_decision()
        self.app.engine._runner = None
        revision = self.app.engine.snapshot()['revision']
        before = len(record['messages'])
        with self.assertRaisesRegex(ValueError, '阶段|完成'):
            self.workspace.post(f'/api/tasks/{self.task_id}/messages', {
                'text': '2', 'startNewRound': True, 'expectedRevision': 3})
        self.assertEqual(len(record['messages']), before)
        self.assertEqual(self.app.engine.snapshot()['revision'], revision)
        self.assertEqual(self.app.engine.snapshot()['project']['researchDecision']['id'], 'choice-1')

    def test_decision_reply_after_restart_restores_runtime_before_routing(self):
        self.seed_decision()
        self.workspace.close()
        source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.workspace = WorkspaceApplication(source, self.root / 'state', import_existing=False)
        self.assertNotIn(self.task_id, self.workspace._apps)
        detail = self.workspace.post(f'/api/tasks/{self.task_id}/messages', {'text': '2'})
        self.assertEqual(detail['document']['markdown'], '# 我的研究需求')
        self.assertEqual(detail['document']['revision'], 3)
        self.assertFalse(detail['document']['polishing'])
        engine = self.workspace._apps[self.task_id].engine
        self.assertFalse(engine.snapshot()['project'].get('researchDecision'))
        self.assertIn('探索机制', engine.snapshot()['project']['researchChoices'][-1]['answer'])

    def test_rewriting_requirements_supersedes_decision_and_keeps_followup_in_document(self):
        self.seed_decision()
        self.workspace.post(f'/api/tasks/{self.task_id}/messages', {'text': '修改需求：换一个研究问题'})
        wait_until(lambda: not self.workspace.detail(self.task_id)['document']['polishing'])
        self.assertFalse(self.app.engine.snapshot()['project'].get('researchDecision'))
        self.workspace.post(f'/api/tasks/{self.task_id}/messages', {'text': '只允许 CPU 实验'})
        wait_until(lambda: not self.workspace.detail(self.task_id)['document']['polishing'])
        detail = self.workspace.detail(self.task_id)
        self.assertEqual(detail['phase'], 'requirements')
        self.assertIn('只允许 CPU 实验', detail['document']['markdown'])
        self.assertFalse(self.app.engine.snapshot()['project'].get('researchChoices'))
