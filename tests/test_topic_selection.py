import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.engine import Engine
from research_swarm.topic_selection import normalize_intent, pending, validate_topics, message_action
from research_swarm.research_cycle_prompts import schema_for
from research_swarm.workspace import WorkspaceApplication
from test_research_cycle import CycleRunner, cycle_library, topic_candidates
from test_workspace import wait_until


class TopicContractTests(unittest.TestCase):
    def test_candidates_require_distinct_questions_real_evidence_and_complete_fields(self):
        good = {'topicCandidates': topic_candidates()}
        validate_topics(good, {'101'})
        bad = []
        for field, value in [('evidenceIds', []), ('evidenceIds', ['invented']), ('question', ''),
                             ('id', 'T2'), ('title', good['topicCandidates'][1]['title']),
                             ('question', good['topicCandidates'][1]['question'] + '！')]:
            changed = copy.deepcopy(good)
            changed['topicCandidates'][0][field] = value
            bad.append(changed)
        bad.extend([{'topicCandidates': good['topicCandidates'][:2]},
                    {'topicCandidates': good['topicCandidates'] * 2},
                    {**good, 'selectedTopic': good['topicCandidates'][0]},
                    {**good, 'researchDecision': {'question': 'strategy'}},
                    {**good, 'hypotheses': [{'id': 'H1'}]}])
        for changed in bad:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_topics(changed, {'101'})

    def test_mode_specific_schemas_are_not_conflicting(self):
        self.assertEqual(set(json.loads(schema_for('topic'))), {'topicCandidates'})
        self.assertEqual(set(json.loads(schema_for('topic', 'direct'))), {'selectedTopic'})
        self.assertEqual(set(json.loads(schema_for('topic', 'delegate'))), {'topicCandidates', 'topicRecommendation'})
        self.assertEqual(set(json.loads(schema_for('topic', 'reproduction'))), {'researchTopic'})

    def test_intent_requires_actual_user_quote_and_broad_rag_question_stays_explore(self):
        broad = 'RAG 是否真的能够显著降低大语言模型幻觉？为什么不同论文的结论不一致？'
        self.assertEqual(normalize_intent('direct', broad, broad)[0], 'explore')
        self.assertEqual(normalize_intent('direct', '指定课题：索引收益', broad)[0], 'explore')
        direct = '指定课题：比较固定数据集上不同检索器的幻觉率'
        self.assertEqual(normalize_intent('direct', direct, direct), ('direct', direct))
        delegated = '请你帮我选一个最适合的课题'
        self.assertEqual(normalize_intent('delegate', delegated, delegated), ('delegate', delegated))
        denied = '不要替我选择课题'
        self.assertEqual(normalize_intent('delegate', denied, denied)[0], 'explore')
        self.assertEqual(normalize_intent(None, None, broad)[0], 'explore')

    def test_questions_do_not_commit_and_custom_text_is_preserved(self):
        candidates = topic_candidates()
        for question in ('有其他课题吗', '第一个和第二个有什么区别？', '选第二个是否合适？', '研究范围是什么？'):
            self.assertIsNone(message_action(question, candidates))
        self.assertEqual(message_action('我选第二个', candidates), {'mode': 'candidate', 'candidateId': 'T2'})
        self.assertEqual(message_action('2', candidates), {'mode': 'candidate', 'candidateId': 'T2'})
        self.assertEqual(message_action('自定义课题：只比较缓存边界', candidates), {'mode': 'custom', 'customText': '只比较缓存边界'})


class TopicEngineTests(unittest.TestCase):
    def make_engine(self, mode=None, **options):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        runner = CycleRunner()
        engine = Engine(cycle_library(), Path(directory.name) / 'state.sqlite', runner=runner, workflow='autonomous')
        self.addCleanup(engine.close)
        values = {'researchCycle': True, 'mode': 'llm', **options}
        if mode is not None: values['topicMode'] = mode
        engine.command('start-autonomous', values)
        return engine, runner

    def wait_pending(self, engine):
        wait_until(lambda: pending(engine.snapshot()['project']))
        return engine.snapshot()

    def test_default_exploration_pauses_before_any_hypothesis_and_cannot_resume(self):
        engine, runner = self.make_engine()
        state = self.wait_pending(engine)
        self.assertEqual(state['status'], 'waiting_user')
        self.assertEqual(state['project']['researchCycle']['stage'], 'topic_selection')
        self.assertIsNone(state['project']['researchCycle']['topic'])
        self.assertFalse(state['project']['researchCycle']['hypotheses'])
        self.assertFalse(state['project'].get('researchDecision'))
        self.assertEqual([step for step, _, _ in runner.calls], ['background', 'literature', 'topic'])
        with self.assertRaisesRegex(ValueError, '课题'):
            engine.command('resume', {})
        selected = engine.command('topic-selection', {'expectedRevision': state['revision'], 'mode': 'candidate', 'candidateId': 'T2'})
        self.assertEqual(selected['project']['researchCycle']['topicSelection']['sourceCandidateIds'], ['T2'])
        wait_until(lambda: engine.snapshot()['report']['ready'])
        self.assertEqual(sum(step == 'topic' for step, _, _ in runner.calls), 1)

    def test_custom_and_edited_choices_do_not_inherit_unverified_source_claims(self):
        for mode in ('custom', 'edited'):
            with self.subTest(mode=mode):
                engine, _ = self.make_engine('explore'); state = self.wait_pending(engine)
                value = '研究实体归因\n只使用公开数据，保留反例'
                request = {'expectedRevision': state['revision'], 'mode': mode, 'customText': value}
                if mode == 'edited': request['baseCandidateId'] = 'T1'
                accepted = engine.command('topic-selection', request)
                cycle = accepted['project']['researchCycle']; selection = cycle['topicSelection']
                self.assertEqual(selection['customText'], value)
                self.assertEqual(selection['selectedTopic']['question'], value)
                self.assertEqual(selection['selectedTopic']['evidenceIds'], [])
                self.assertEqual(len(cycle['topicCandidates']), 3)
                self.assertEqual(selection['sourceCandidateIds'], ['T1'] if mode == 'edited' else [])
                wait_until(lambda: engine.snapshot()['report']['ready'])

    def test_stale_unknown_and_duplicate_selection_cannot_resume_again(self):
        engine, _ = self.make_engine('explore'); state = self.wait_pending(engine)
        for request in ({'candidateId': 'missing'}, {'candidateId': []}, {'expectedRevision': state['revision'] - 1},
                        {'expectedRevision': True}):
            with self.assertRaises(ValueError):
                engine.command('topic-selection', {'mode': 'candidate', 'candidateId': 'T1', 'expectedRevision': state['revision'], **request})
            self.assertEqual(engine.snapshot()['revision'], state['revision'])
        request = {'mode': 'candidate', 'candidateId': 'T1', 'expectedRevision': state['revision']}
        engine.command('topic-selection', request)
        with self.assertRaises(ValueError): engine.command('topic-selection', request)
        self.assertEqual(sum(h['type'] == 'topic-selected' for h in engine.snapshot()['history']), 1)

    def test_direct_and_delegate_continue_with_recorded_reason(self):
        for mode in ('direct', 'delegate'):
            with self.subTest(mode=mode):
                engine, _ = self.make_engine(mode)
                wait_until(lambda: engine.snapshot()['report']['ready'])
                cycle = engine.snapshot()['project']['researchCycle']
                selection = cycle['topicSelection']
                self.assertEqual(selection['status'], 'selected'); self.assertTrue(selection['reason'])
                self.assertEqual(selection['mode'], 'direct' if mode == 'direct' else 'delegated')
                self.assertEqual(len(cycle['topicCandidates']), 0 if mode == 'direct' else 3)
                if mode == 'delegate': self.assertEqual(selection['candidateId'], 'T2')

    def test_reproduction_keeps_its_own_decision(self):
        engine, _ = self.make_engine(taskMode='reproduction', paperResearch=True)
        wait_until(lambda: bool(engine.snapshot()['project'].get('researchDecision')))
        project = engine.snapshot()['project']
        self.assertIsNone(project['researchCycle']['topicSelection'])
        self.assertEqual(project['researchDecision']['options'][0]['label'], '优先严格复现原设置')

    def test_restart_preserves_pending_candidates_and_selected_topic(self):
        engine, _ = self.make_engine('explore'); state = self.wait_pending(engine)
        path = engine._artifact_root / 'state.sqlite'; engine.close()
        restored = Engine(cycle_library(), path, runner=CycleRunner(), workflow='autonomous'); self.addCleanup(restored.close)
        self.assertEqual(restored.snapshot()['project']['researchCycle']['topicCandidates'], state['project']['researchCycle']['topicCandidates'])
        state = restored.snapshot()
        restored.command('topic-selection', {'expectedRevision': state['revision'], 'mode': 'candidate', 'candidateId': 'T1'})
        wait_until(lambda: restored.snapshot()['report']['ready'])
        selected = restored.snapshot()['project']['researchCycle']['topicSelection']; restored.close()
        again = Engine(cycle_library(), path, runner=None, workflow='autonomous'); self.addCleanup(again.close)
        self.assertEqual(again.snapshot()['project']['researchCycle']['topicSelection'], selected)

    def test_refresh_reuses_literature_and_invalidates_old_selection_revision(self):
        engine, runner = self.make_engine('explore'); state = self.wait_pending(engine)
        previous_id = state['project']['researchCycle']['topicSelection']['id']
        engine.command('topic-refresh', {'expectedRevision': state['revision'], 'text': '换一批，更侧重写入成本'})
        wait_until(lambda: pending(engine.snapshot()['project']))
        cycle = engine.snapshot()['project']['researchCycle']
        self.assertNotEqual(cycle['topicSelection']['id'], previous_id)
        self.assertEqual(sum(step == 'background' for step, _, _ in runner.calls), 1)
        self.assertEqual(sum(step == 'literature' for step, _, _ in runner.calls), 1)
        self.assertEqual(sum(step == 'topic' for step, _, _ in runner.calls), 2)
        with self.assertRaises(ValueError):
            engine.command('topic-selection', {'expectedRevision': state['revision'], 'mode': 'candidate', 'candidateId': 'T1'})


class TopicWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.workspace = WorkspaceApplication(source, Path(self.directory.name) / 'state', import_existing=False)
        self.addCleanup(self.workspace.close)
        self.task_id = self.workspace.post('/api/tasks', {})['task']['id']
        app = self.workspace._ensure_app(self.task_id); app.engine.close()
        app.engine = Engine(cycle_library(), self.workspace._data_root / self.task_id / 'runtime' / 'swarm.sqlite', runner=CycleRunner(), workflow='autonomous')
        self.engine = app.engine
        self.engine.command('refresh-library', {'library': cycle_library()})
        record = self.workspace._records[self.task_id]; record['phase'] = 'researching'
        record['document']['markdown'] = '# 用户原始研究主题'
        self.engine.command('start-autonomous', {'researchCycle': True, 'mode': 'llm', 'topicMode': 'explore'})
        wait_until(lambda: pending(self.engine.snapshot()['project']))
        self.workspace.detail(self.task_id)

    def test_question_then_explicit_selection_uses_topic_routes_and_does_not_rewrite_requirements(self):
        path = '/api/tasks/' + self.task_id
        with patch.object(self.workspace.backend, 'ask_overview', side_effect=AssertionError('must stay in topic flow')):
            detail = self.workspace.post(path + '/topic-discussion', {'text': '有其他课题吗', 'expectedRevision': self.engine.snapshot()['revision']})
            self.assertIn('索引维护代价', detail['messages'][-1]['content'])
            self.assertTrue(pending(detail['state']['project']))
            detail = self.workspace.post(path + '/messages', {'text': '第一个和第二个有什么区别？'})
            self.assertTrue(pending(detail['state']['project']))
            detail = self.workspace.post(path + '/topic-discussion', {'text': '我选第二个', 'expectedRevision': self.engine.snapshot()['revision']})
        self.assertEqual(detail['state']['project']['researchCycle']['topicSelection']['candidateId'], 'T2')
        self.assertEqual(detail['document']['markdown'], '# 用户原始研究主题')

    def test_api_rejects_stale_or_duplicate_without_adding_messages(self):
        path = '/api/tasks/' + self.task_id + '/topic-selection'
        revision = self.engine.snapshot()['revision']
        before = len(self.workspace.detail(self.task_id)['messages'])
        request = {'expectedRevision': revision, 'mode': 'custom', 'customText': '用户自定义课题完整文本'}
        with self.assertRaises(ValueError): self.workspace.post(path, {**request, 'expectedRevision': revision-1})
        self.assertEqual(len(self.workspace.detail(self.task_id)['messages']), before)
        detail = self.workspace.post(path, request)
        self.assertIn('用户自定义课题完整文本', detail['messages'][-2]['content'])
        with self.assertRaises(ValueError): self.workspace.post(path, request)

    def test_model_discussion_receives_candidates_without_selecting_or_executing(self):
        calls = []
        def reply(messages, **kwargs):
            calls.append(messages)
            return '这两个课题的区别是读取延迟与维护成本。'
        with patch.object(self.workspace.settings, 'public', return_value={'mode': 'llm', 'capabilities': {'modelReady': True}}), \
                patch.object(self.workspace.settings, 'chat', side_effect=reply):
            self.workspace.post('/api/tasks/' + self.task_id + '/topic-discussion',
                {'text': '第一个和第二个有什么区别？', 'expectedRevision': self.engine.snapshot()['revision']})
            wait_until(lambda: any(i['status'] == 'completed' for i in self.workspace.detail(self.task_id)['interactions']))
        self.assertTrue(pending(self.engine.snapshot()['project']))
        self.assertIn('选题讨论助手', calls[0][0]['content'])
        context = json.loads(calls[0][-1]['content'])['topicContext']
        self.assertEqual(len(context['topicCandidates']), 3)
        self.assertFalse(self.engine.snapshot()['project']['researchCycle']['hypotheses'])

    def test_requirement_draft_accepts_only_verified_user_topic_intent(self):
        draft = {'markdown': '# 候选研究', 'summary': '已整理', 'requirements': [{'description': '研究', 'acceptance': '证据'}], 'queries': ['retrieval hallucination']}
        user = '请你帮我选择一个有研究价值的课题'
        for quote, expected in ((user, 'delegate'), ('模型自己提出的委托', 'explore')):
            with patch.object(self.workspace.settings, 'public', return_value={'mode': 'llm', 'capabilities': {'modelReady': True}}), \
                    patch('research_swarm.workspace.draft_with_concepts', return_value=({**draft, 'topicMode': 'delegate', 'topicIntent': quote}, {})):
                compiled = self.workspace._draft(user, '')
            self.assertEqual(compiled['topicMode'], expected)
