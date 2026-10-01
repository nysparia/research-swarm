import copy
import json
import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

from research_swarm.workspace import WorkspaceApplication
from research_swarm.server import make_handler
from research_swarm.claims import create_claim
from test_engine import sample_library
from test_workspace import wait_until


class WorkspaceBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        self.app = WorkspaceApplication(self.source, self.root / 'state', import_existing=False)
        self.task = self.app.post('/api/tasks', {})['task']['id']
        self.base = '/api/tasks/' + self.task

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def read(self, action):
        result = self.app.read_api(self.base + '/' + action)
        self.assertIsNotNone(result, 'backend route must exist: ' + action)
        return result[1]

    def seed_research(self):
        runtime = self.app._ensure_app(self.task)
        engine = runtime.engine
        with engine._lock:
            engine._runner = None
            state = engine._state
            state['project'].update(researchStarted=True, description='Compare two methods')
            state['paused'] = True
            engine._manual_paused = True
            root = next(n for n in state['nodes'] if n['id'] == 'central')
            root.update(active=True, status='completed', phase='aggregate')
            for nid, statement in [('test-a', 'Method A reduces latency'), ('test-b', 'Method B saves memory')]:
                node = engine._node(nid, 'central', statement, 'research', None,
                                    {'description': statement}, True)
                node.update(status='completed', phase='execute', output={'summary': statement,
                            'claims': [], 'evidenceIds': [], 'structured': {}, 'unresolved': []})
                claim = create_claim(state, statement, owner_node_id=nid)
                node['input'].update(claimId=claim['id'], claimVersion=1)
                state['nodes'].append(node)
                state['edges'].append({'source': 'central', 'target': nid, 'type': 'decompose'})
            engine._commit()
        record = self.app._records[self.task]
        record['phase'] = 'researching'
        record['document'].update(markdown='# Research\n\nUse CPU only.', revision=2)
        self.app._save(record)
        return engine

    def target(self, node_id='test-a'):
        items = self.read('workbench')['artifacts']
        item = next(a for a in items if node_id in a.get('nodeIds', []) and a['kind'] == 'claim')
        return {'artifactId': item['id'], 'revision': item['revision']}

    def test_empty_task_has_notebook_plan_and_replayable_workbench(self):
        detail = self.app.detail(self.task)
        self.assertIn('workbench', detail)
        first = self.read('workbench')
        self.assertIn('draft', first)
        self.assertEqual(first, self.read('workbench'))
        events = self.read('events')
        self.assertIn('nextCursor', events)
        self.assertTrue(all('apiKey' not in json.dumps(e) for e in events['events']))

    def test_shutdown_cannot_create_late_background_job_manager(self):
        self.app.close()
        with self.assertRaisesRegex(ValueError, '停止|closed'):
            self.app.backend.jobs(self.task)

    def test_get_existing_jobs_artifacts_logs_and_downloads_never_activates_queue(self):
        runtime = self.app._data_root / self.task / 'runtime'
        runtime.mkdir(parents=True)
        queued_id, finished_id = 'job-' + 'a' * 32, 'job-' + 'b' * 32
        attempt = runtime / 'runs' / finished_id / 'attempt-1'
        attempt.mkdir(parents=True)
        (attempt / 'stdout.txt').write_text('An already recorded measurement\n', encoding='utf-8')
        (attempt / 'stderr.txt').write_text('', encoding='utf-8')
        receipt = attempt / 'receipt.json'
        receipt.write_text('{"status":"completed","artifacts":[]}', encoding='utf-8')
        queued = {'id': queued_id, 'revision': 1, 'status': 'queued', 'createdAt': '2026-01-01',
                  'request': {'code': 'raise AssertionError("Reading must never execute this")', 'materialIds': []},
                  'attempts': []}
        finished = {'id': finished_id, 'revision': 2, 'status': 'completed', 'createdAt': '2026-01-02',
                    'request': {'materialIds': []}, 'attempts': [{
                        'stdoutPath': (attempt / 'stdout.txt').relative_to(runtime).as_posix(),
                        'stderrPath': (attempt / 'stderr.txt').relative_to(runtime).as_posix(),
                        'receipt': {'path': receipt.relative_to(runtime).as_posix(),
                                    'sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}}]}
        path = runtime / 'jobs.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            connection.executemany('INSERT INTO jobs VALUES (?,?)', [(j['id'], json.dumps(j)) for j in (queued, finished)])
        connection.close()
        with patch('research_swarm.experiment_jobs.ExperimentJobs.__init__',
                   side_effect=AssertionError('GET must not claim or start the queue')):
            detail = self.app.read_api(self.base)[1]
            self.assertEqual(next(a['content'] for a in detail['workbench']['artifacts']
                                  if a['id'] == 'experiment_job:' + queued_id), queued)
            self.assertEqual(self.read('jobs'), [queued, finished])
            self.assertEqual(self.read('jobs/' + queued_id), queued)
            self.assertEqual(self.read('jobs/' + queued_id + '/logs')['text'], '')
            self.assertIn('recorded measurement', self.read('jobs/' + finished_id + '/logs')['text'])
            download = self.app.read_api(self.base + '/jobs/' + finished_id + '/files',
                                       'path=' + receipt.relative_to(runtime).as_posix())
            self.assertEqual(download[0], 200)
            self.assertEqual(download[1], receipt.read_bytes())
        self.assertEqual(self.app.backend._jobs, {})
        self.assertEqual(self.app._apps, {})
        with sqlite3.connect(path) as connection:
            saved = [json.loads(row[0]) for row in connection.execute('SELECT data FROM jobs ORDER BY id')]
        connection.close()
        self.assertEqual(saved, [queued, finished])

    def test_supervisor_preserves_current_download_artifact(self):
        engine = self.seed_research()
        root = self.app._data_root / self.task / 'runtime'
        output = root / 'runs' / 'local-test' / 'metrics.json'
        output.parent.mkdir(parents=True)
        output.write_text('{"measurement": 1}', encoding='utf-8')
        with engine._lock:
            engine._state['history'].append({'type': 'tool-executed', 'execution': {
                'artifacts': [{'path': output.relative_to(root).as_posix()}]}})
            engine._commit()
        first = self.read('workbench')
        files = [a for a in first['artifacts'] if a['kind'] == 'file']
        self.assertTrue(files)
        time.sleep(.7)
        second = self.app.backend.store(self.task).snapshot()
        self.assertEqual(files, [a for a in second['artifacts'] if a['kind'] == 'file'])

    def test_anchored_question_preserves_active_research_and_requirements(self):
        engine = self.seed_research()
        before = engine.snapshot()
        document = copy.deepcopy(self.app.detail(self.task)['document'])
        result = self.app.post(self.base + '/interactions', {
            'kind': 'ask', 'target': self.target(), 'text': '这个结论的依据是什么？'})
        self.assertEqual(result['kind'], 'ask')
        wait_until(lambda: self.read('interactions/' + result['id'])['status'] != 'running')
        self.assertEqual(engine.snapshot()['revision'], before['revision'])
        self.assertEqual(self.app.detail(self.task)['document'], document)
        self.assertEqual(self.app.detail(self.task)['phase'], 'researching')

    def test_ordinary_chat_during_research_is_a_question_not_scope_reset(self):
        engine = self.seed_research()
        before = engine.snapshot()['revision']
        self.app.post(self.base + '/messages', {'text': '目前取得了哪些结果？'})
        wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        self.assertEqual(self.app.detail(self.task)['document']['revision'], 2)
        self.assertEqual(engine.snapshot()['revision'], before)

    def node_target(self, node_id='test-a'):
        item = next(a for a in self.read('workbench')['artifacts'] if a['id'] == 'node_state:' + node_id)
        return {'artifactId': item['id'], 'revision': item['revision']}

    def conversation(self, question, **overrides):
        payload = {'kind': 'ask', 'target': self.node_target(), 'nodeId': 'test-a', 'scope': 'node',
                   'showInConversation': True, 'text': question}
        payload.update(overrides)
        return self.app.post(self.base + '/interactions', payload)

    def test_fast_overview_answer_is_atomically_visible_without_duplicate_messages(self):
        self.seed_research()
        with patch.object(self.app, '_spawn', side_effect=lambda fn, *args: fn(*args)):
            detail = self.app.backend.ask_overview(self.task, '现在哪个假设还缺证据？')
        interaction = detail['interactions'][-1]
        self.assertEqual(interaction['status'], 'completed')
        messages = [m for m in detail['messages'] if m.get('interactionId') == interaction['id']]
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant'])
        self.assertTrue(messages[1]['content'])
        self.assertEqual(messages[1]['context']['scope'], 'overview')
        self.assertEqual(messages[0]['context'], messages[1]['context'])
        saved = json.loads((self.app._data_root / self.task / 'conversation.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['messages'], detail['messages'])

    def test_conversation_answers_are_fifo_and_include_completed_prior_turns_only(self):
        self.seed_research()
        entered, release = threading.Event(), threading.Event()
        prompts = []
        def chat(messages, **kwargs):
            prompts.append(copy.deepcopy(messages))
            if len(prompts) == 1:
                entered.set()
                release.wait(5)
                return '第一轮：这里没有实验结果，只能先设计比较。'
            return '第二轮：沿用前面的比较，控制相同硬件。'
        public = {'mode': 'llm', 'capabilities': {'modelReady': True}}
        with patch.object(self.app.settings, 'public', return_value=public), patch.object(self.app.settings, 'chat', side_effect=chat):
            first = self.conversation('有什么实验可以验证？')
            self.assertTrue(entered.wait(3))
            try:
                second = self.conversation('那硬件怎么控制？')
                self.assertEqual(second['status'], 'queued')
                pending = [m for m in self.app.detail(self.task)['messages'] if m.get('interactionId')]
                self.assertEqual([m['interactionId'] for m in pending], [first['id'], first['id'], second['id'], second['id']])
            finally:
                release.set()
            wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        self.assertEqual(len(prompts), 2)
        self.assertNotIn('那硬件怎么控制', json.dumps(prompts[0], ensure_ascii=False))
        self.assertIn('第一轮：这里没有实验结果', json.dumps(prompts[1], ensure_ascii=False))
        self.assertIn('有什么实验可以验证', json.dumps(prompts[1], ensure_ascii=False))
        messages = [m for m in self.app.detail(self.task)['messages'] if m.get('interactionId')]
        self.assertEqual(len(messages), 4)
        self.assertTrue(all(m['status'] == 'completed' for m in messages))

    def test_failed_explanation_is_visible_in_conversation_and_preserves_research(self):
        engine = self.seed_research()
        revision = engine.snapshot()['revision']
        public = {'mode': 'llm', 'capabilities': {'modelReady': True}}
        with patch.object(self.app.settings, 'public', return_value=public), patch.object(self.app.settings, 'chat', side_effect=RuntimeError('请求失败 api_key=secret-test')):
            result = self.conversation('为什么这个节点停了？')
            wait_until(lambda: self.read('interactions/' + result['id'])['status'] == 'failed')
        detail = self.app.detail(self.task)
        reply = next(m for m in detail['messages'] if m.get('interactionId') == result['id'] and m['role'] == 'assistant')
        self.assertEqual(reply['status'], 'failed')
        self.assertIn('请求失败', reply['content'])
        self.assertNotIn('secret-test', json.dumps(detail))
        self.assertEqual(engine.snapshot()['revision'], revision)
        self.assertEqual(detail['document']['revision'], 2)

    def test_restart_recovers_queued_conversation_messages_durably(self):
        self.seed_research()
        with patch.object(self.app, '_spawn'):
            first = self.conversation('先解释数据')
            second = self.conversation('再解释测量范围')
        self.assertEqual(first['status'], 'running')
        self.assertEqual(second['status'], 'queued')
        self.app.close()
        self.app = WorkspaceApplication(self.source, self.root / 'state', import_existing=False)
        detail = self.app.detail(self.task)
        for interaction_id in (first['id'], second['id']):
            interaction = next(i for i in detail['interactions'] if i['id'] == interaction_id)
            self.assertEqual(interaction['status'], 'failed')
            messages = [m for m in detail['messages'] if m.get('interactionId') == interaction_id]
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[1]['status'], 'failed')
            self.assertIn('重启中断', messages[1]['content'])
        saved = json.loads((self.app._data_root / self.task / 'conversation.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['interactions'], detail['interactions'])

    def test_restart_reads_current_graph_without_starting_workers_or_invalidating_outputs(self):
        from research_swarm.store import Store
        engine = self.seed_research()
        original = self.app.detail(self.task)
        record = self.app._record(self.task)
        # A prior completed report must not replace current unfinished research.
        record['runs'] = [{'round': 1}]
        old = engine.snapshot()
        old['nodes'] = [n for n in old['nodes'] if n['id'] != 'test-b']
        reports = self.app._data_root / self.task / 'reports'
        reports.mkdir()
        (reports / 'round-1.json').write_text(json.dumps(old), encoding='utf-8')
        self.app._save(record)
        engine_path = self.app._data_root / self.task / 'runtime' / 'swarm.sqlite'
        self.app.close()
        persisted = Store.read_snapshot(engine_path)
        self.app = WorkspaceApplication(self.source, self.root / 'state', import_existing=False)
        with patch.object(self.app, '_ensure_app', side_effect=AssertionError('Read must not start a runtime')):
            detail = self.app.read_api(self.base)[1]
            self.assertEqual({n['id'] for n in detail['state']['nodes']}, {n['id'] for n in persisted['nodes']})
            self.assertIn('test-b', [n['id'] for n in detail['state']['nodes']])
            self.assertEqual(detail['state']['snapshotOrigin'], 'stored')
            self.assertTrue(detail['state']['paused'])
            before = {a['id']: a for a in original['workbench']['artifacts'] if a['kind'] in ('claim', 'node_output')}
            after = {a['id']: a for a in detail['workbench']['artifacts'] if a['kind'] in ('claim', 'node_output')}
            self.assertEqual(before, after)
            self.assertEqual(detail['workbench'], self.app.detail(self.task)['workbench'])
            self.assertEqual(detail['workbench'], self.read('workbench'))
        self.assertEqual(self.app._apps, {})
        self.assertEqual(self.app._threads, [])
        self.assertEqual(Store.read_snapshot(engine_path), persisted)

    def test_interrupted_execution_is_shown_as_paused_without_rewriting_engine_state(self):
        from research_swarm.store import Store
        self.seed_research()
        engine_path = self.app._data_root / self.task / 'runtime' / 'swarm.sqlite'
        self.app.close()
        store = Store(engine_path)
        envelope = store.load()
        persisted = envelope['state']
        persisted.update(paused=False, status='running')
        node = next(n for n in persisted['nodes'] if n['id'] == 'test-a')
        node.update(status='running', output=None)
        store.save(persisted, envelope['epoch'], library=envelope['library'])
        store.close()
        self.app = WorkspaceApplication(self.source, self.root / 'state', import_existing=False)
        detail = self.app.detail(self.task)
        displayed = next(n for n in detail['state']['nodes'] if n['id'] == 'test-a')
        self.assertEqual(displayed['status'], 'pending')
        self.assertTrue(displayed['interrupted'])
        self.assertEqual(detail['state']['status'], 'idle')
        self.assertTrue(detail['state']['paused'])
        self.assertEqual(self.app._apps, {})
        self.assertEqual(Store.read_snapshot(engine_path), persisted)

    def test_completed_task_discussion_does_not_start_a_new_round(self):
        engine = self.seed_research()
        record = self.app._record(self.task)
        record['phase'] = 'completed'
        self.app._save(record)
        document = copy.deepcopy(record['document'])
        revision = engine.snapshot()['revision']
        result = self.conversation('这个结果有哪些局限？')
        wait_until(lambda: self.read('interactions/' + result['id'])['status'] == 'completed')
        detail = self.app.detail(self.task)
        self.assertEqual(detail['phase'], 'completed')
        self.assertEqual(detail['document'], document)
        self.assertEqual(engine.snapshot()['revision'], revision)

    def test_pending_node_deepen_requires_confirmation_and_refuses_changed_context(self):
        engine = self.seed_research()
        with engine._lock:
            node = next(n for n in engine._state['nodes'] if n['id'] == 'test-a')
            node.update(status='pending', output=None)
            engine._commit()
        revision = engine.snapshot()['revision']
        result = self.conversation('增加针对内存带宽的验证', kind='deepen')
        proposal = result['proposal']
        self.assertEqual(engine.snapshot()['revision'], revision)
        self.assertEqual(result['status'], 'proposed')
        self.assertIn('test-a', proposal['affectedNodeIds'])
        self.assertNotIn('test-b', proposal['affectedNodeIds'])
        path = self.base + '/proposals/' + proposal['id'] + '/apply'
        with self.assertRaisesRegex(ValueError, '确认'):
            self.app.post(path, {'expectedRevision': proposal['revision']})
        with engine._lock:
            node['input']['description'] = 'A changed request while the preview was open'
            engine._commit()
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.app.post(path, {'expectedRevision': proposal['revision'], 'confirmed': True})
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.conversation('旧版本不能继续修改', target=result['target'], kind='deepen')

    def test_reply_remains_pinned_to_selected_node_version_when_live_context_changes(self):
        engine = self.seed_research()
        entered, release = threading.Event(), threading.Event()
        captured = []
        def chat(messages, **kwargs):
            captured.extend(messages)
            entered.set()
            release.wait(5)
            return '这是所选版本的说明。'
        public = {'mode': 'llm', 'capabilities': {'modelReady': True}}
        with patch.object(self.app.settings, 'public', return_value=public), patch.object(self.app.settings, 'chat', side_effect=chat):
            result = self.conversation('说明现在的输入')
            self.assertTrue(entered.wait(3))
            try:
                with engine._lock:
                    next(n for n in engine._state['nodes'] if n['id'] == 'test-a')['input']['description'] = 'A newer request'
                    engine._commit()
            finally:
                release.set()
            wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        interaction = self.read('interactions/' + result['id'])
        self.assertTrue(interaction['stale'])
        self.assertEqual(interaction['basedOnRevision'], result['target']['revision'])
        self.assertNotIn('A newer request', json.dumps(captured))
        message = next(m for m in self.app.detail(self.task)['messages'] if m.get('interactionId') == result['id'] and m['role'] == 'assistant')
        self.assertTrue(message['stale'])

    def test_historical_node_can_be_asked_without_allowing_a_stale_intervention(self):
        engine = self.seed_research()
        target = self.node_target()
        target['selection'] = {'quote': 'Method A reduces latency'}
        with engine._lock:
            node = next(n for n in engine._state['nodes'] if n['id'] == 'test-a')
            node['input']['description'] = 'A changed current requirement'
            node['output']['summary'] = 'A changed current result'
            node['title'] = 'A changed current title'
            engine._commit()
        result = self.conversation('解释我选择的旧记录', target=target)
        wait_until(lambda: self.read('interactions/' + result['id'])['status'] == 'completed')
        reply = self.read('interactions/' + result['id'])
        self.assertTrue(reply['stale'])
        self.assertIn('Method A reduces latency', reply['reply'])
        self.assertNotIn('A changed current requirement', reply['reply'])
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.conversation('修改这个旧节点', target=target, kind='deepen')

    def test_confirmed_revision_only_invalidates_affected_branch_and_is_idempotent(self):
        engine = self.seed_research()
        sibling = next(n for n in engine.snapshot()['nodes'] if n['id'] == 'test-b')
        result = self.app.post(self.base + '/interactions', {'kind': 'revise', 'target': self.target(),
            'text': '把结论限制在 CPU 环境', 'replacement': 'Method A reduces latency on CPU'})
        proposal = result['proposal']
        self.assertIn('test-a', proposal['affectedNodeIds'])
        self.assertNotIn('test-b', proposal['affectedNodeIds'])
        path = self.base + '/proposals/' + proposal['id'] + '/apply'
        with self.assertRaises(ValueError):
            self.app.post(path, {'expectedRevision': proposal['revision']})
        applied = self.app.post(path, {'expectedRevision': proposal['revision'], 'confirmed': True})
        self.assertEqual(applied['status'], 'applied')
        state = engine.snapshot()
        self.assertEqual(next(n for n in state['nodes'] if n['id'] == 'test-b'), sibling)
        self.assertEqual(next(c for c in state['claimGraph']['claims'] if c['ownerNodeId'] == 'test-a')['statement'],
                         'Method A reduces latency on CPU')
        self.app.post(path, {'expectedRevision': proposal['revision'], 'confirmed': True})
        self.assertEqual(engine.snapshot()['revision'], state['revision'])

    def test_changed_target_rejects_stale_proposal(self):
        engine = self.seed_research()
        result = self.app.post(self.base + '/interactions', {'kind': 'challenge', 'target': self.target(),
                                                           'text': '请检查测量是否公平'})
        proposal = result['proposal']
        engine.command('pause', {})
        with self.assertRaisesRegex(ValueError, '版本|变化'):
            self.app.post(self.base + '/proposals/' + proposal['id'] + '/apply',
                          {'expectedRevision': proposal['revision'], 'confirmed': True})

    def test_guarded_draft_edits_preserve_identity_and_persist(self):
        self.app.post(self.base + '/messages', {'text': '比较两种数据库索引'})
        wait_until(lambda: not self.app.detail(self.task)['document']['polishing'])
        draft = self.read('draft')
        block = draft['blocks'][0]
        result = self.app.post(self.base + '/draft', {'expectedRevision': draft['revision'],
                    'operations': [{'op': 'update', 'id': block['id'], 'content': '只允许 CPU，保持预算固定。'}]})
        saved = next(b for b in result['blocks'] if b['id'] == block['id'])
        self.assertIn('只允许 CPU', saved['content'])
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(self.base + '/draft', {'expectedRevision': draft['revision'], 'operations': []})
        self.app.close()
        self.app = WorkspaceApplication(self.source, self.root / 'state', import_existing=False)
        self.assertTrue(any(b['id'] == block['id'] and '只允许 CPU' in b['content'] for b in self.read('draft')['blocks']))

    def test_draft_edit_during_start_setup_returns_to_editable_requirements(self):
        self.app.post(self.base + '/messages', {'text': '比较数据库索引'})
        wait_until(lambda: not self.app.detail(self.task)['document']['polishing'])
        draft = self.read('draft')
        entered, release = threading.Event(), threading.Event()
        original = self.app._ensure_app
        def wait_for_start(task_id):
            entered.set()
            release.wait(5)
            return original(task_id)
        with patch.object(self.app, '_ensure_app', side_effect=wait_for_start):
            self.app.post(self.base + '/start', {'expectedRevision': draft['revision']})
            self.assertTrue(entered.wait(3))
            try:
                self.app.post(self.base + '/draft', {'expectedRevision': draft['revision'], 'operations': [
                    {'op': 'update', 'id': draft['blocks'][0]['id'], 'content': '只比较 CPU 索引延迟'}]})
            finally:
                release.set()
            wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        detail = self.app.detail(self.task)
        self.assertEqual(detail['phase'], 'requirements')
        self.assertIn('只比较 CPU', detail['document']['markdown'])

    def test_events_replay_and_artifacts_are_task_isolated(self):
        self.seed_research()
        target = self.target()
        other = self.app.post('/api/tasks', {})['task']['id']
        with self.assertRaises(ValueError):
            self.app.post('/api/tasks/' + other + '/interactions',
                          {'kind': 'ask', 'target': target, 'text': '读取别的任务'})
        first = self.read('events')
        cursor = first['nextCursor']
        self.app.post(self.base + '/interactions', {'kind': 'ask', 'target': target, 'text': '解释'})
        wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        later = self.app.read_api(self.base + '/events', 'after=' + str(cursor))[1]
        self.assertTrue(later['events'])
        self.assertTrue(all(e['id'] > cursor for e in later['events']))

    def test_requirement_patch_preserves_unrelated_execution(self):
        engine = self.seed_research()
        requirements = [{'id': 'ra', 'description': 'Measure latency', 'acceptance': 'raw measurements', 'constraints': '', 'version': 1},
                        {'id': 'rb', 'description': 'Measure memory', 'acceptance': 'raw measurements', 'constraints': '', 'version': 1}]
        with engine._lock:
            engine._state['requirements'] = requirements
            for node in engine._state['nodes']:
                if node['id'] == 'test-a': node['requirementIds'] = ['ra']
                if node['id'] == 'test-b': node['requirementIds'] = ['rb']
            engine._commit()
        replacement = copy.deepcopy(requirements)
        replacement[0]['description'] = 'Measure p95 latency on CPU'
        before = engine.snapshot()
        impact = engine.command('draft-impact', {'requirements': replacement})
        self.assertIn('test-a', impact['affectedIds'])
        self.assertNotIn('test-b', impact['affectedIds'])
        result = engine.command('revise-requirements', {'requirements': replacement,
                                'expectedRevision': impact['revision'], 'operationId': 'draft-test'})
        self.assertEqual(next(n for n in result['nodes'] if n['id'] == 'test-b'),
                         next(n for n in before['nodes'] if n['id'] == 'test-b'))
        again = engine.command('revise-requirements', {'requirements': replacement,
                               'expectedRevision': impact['revision'], 'operationId': 'draft-test'})
        self.assertEqual(result['revision'], again['revision'])
        # A finished autonomous round is paused automatically, not by the user.
        # Confirming the next scoped revision must make affected work schedulable.
        replacement[0]['description'] = 'Measure p99 latency on CPU'
        with engine._condition:
            engine._state.update(paused=True, stage=8)
            engine._manual_paused = False
            revision = engine._state['revision']
            resumed = engine.command('revise-requirements', {'requirements': replacement, 'expectedRevision': revision})
            self.assertFalse(resumed['paused'])
            self.assertEqual(resumed['stage'], 6)
            engine.command('pause', {})

    def test_http_event_stream_replays_cursor(self):
        self.seed_research()
        self.read('workbench')
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}' + self.base + '/events/stream'
            with urllib.request.urlopen(urllib.request.Request(url, headers={'Last-Event-ID': '0'}), timeout=4) as response:
                self.assertEqual(response.headers.get_content_type(), 'text/event-stream')
                lines = [response.readline().decode() for _ in range(3)]
                self.assertTrue(any(line.startswith('id: ') for line in lines))
                self.assertTrue(any(line.startswith('data: ') for line in lines))
        finally:
            server.shutdown()
            server.server_close()

    def test_material_to_real_job_is_visible_and_does_not_become_claim_evidence(self):
        material = self.app.post(self.base + '/materials', {'name': 'values.csv', 'text': 'value\n2\n5\n'})
        job = self.app.post(self.base + '/jobs', {'materialIds': [material['id']],
            'code': "from pathlib import Path\nimport json\np=next(Path('inputs').rglob('*.csv'))\nvalues=[int(x) for x in p.read_text().splitlines()[1:]]\nPath('metrics.json').write_text(json.dumps({'sum':sum(values)}))\nprint(sum(values))"})
        wait_until(lambda: self.read('jobs/' + job['id'])['status'] in ('completed', 'failed'), timeout=20)
        finished = self.read('jobs/' + job['id'])
        self.assertEqual(finished['status'], 'completed', finished)
        self.assertIn('7', self.read('jobs/' + job['id'] + '/logs')['text'])
        board = self.read('workbench')
        self.assertTrue(any(a['kind'] == 'experiment_job' and a['status'] == 'completed' for a in board['artifacts']))
        self.assertFalse(any(a['kind'] == 'claim' for a in board['artifacts']))

    def test_long_execution_settings_are_guarded_and_persisted(self):
        before = self.read('execution-settings')
        saved = self.app.post(self.base + '/execution-settings', {'expectedRevision': before['revision'],
                              'maxTimeoutSeconds': 7200, 'materialIds': []})
        self.assertEqual(saved['maxTimeoutSeconds'], 7200)
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(self.base + '/execution-settings', {'expectedRevision': before['revision'],
                          'maxTimeoutSeconds': 8000})
        with self.assertRaises(ValueError):
            self.app.post(self.base + '/execution-settings', {'expectedRevision': saved['revision'],
                          'maxTimeoutSeconds': 99999999})

    def test_node_execution_uses_managed_task_inputs_and_receipts(self):
        material = self.app.post(self.base + '/materials', {'name': 'sample.csv', 'text': 'value\n8\n'})
        self.app.post(self.base + '/execution-settings', {'expectedRevision': 0,
                      'materialIds': [material['id']], 'maxTimeoutSeconds': 600})
        runtime = self.app._ensure_app(self.task)
        original = runtime.runner
        captured = {}
        def capture(node, context, log):
            captured.update(context)
            return original.local_tools.call('python_run', {'code':
                "from pathlib import Path\nprint(Path('inputs/sample.csv').read_text())", 'timeout': 5}, node, context, log)
        runtime.runner = capture
        result = runtime.engine._runner({'id': 'test-node', 'version': 1}, {}, lambda _: None)
        self.assertIn('8', result['stdout'])
        self.assertEqual(captured['experimentJobs'].root, original.local_tools.root)
        receipt = result['receipt']
        self.assertTrue((original.local_tools.root / receipt).is_file())
        self.assertEqual(len(self.read('jobs')), 1)
        card = next(a for a in self.read('workbench')['artifacts'] if a['kind'] == 'experiment_job')
        self.assertEqual(card['nodeIds'], ['test-node'])
        self.assertIn('material:' + material['id'], card['dependencies'])
        self.assertTrue(any(ref.get('path') == receipt for ref in card['sourceRefs']))

    def test_engine_restart_recovers_managed_receipts_as_historical_only(self):
        from research_swarm.engine import Engine
        runtime = self.app._ensure_app(self.task)
        root = runtime.runner.local_tools.root
        receipt = root / 'runs' / 'job-test' / 'attempt-1' / 'receipt.json'
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({'nodeId': 'central', 'nodeVersion': 1, 'executionToken': 'old-token',
            'round': 1, 'tool': 'python_run', 'status': 'completed', 'returnCode': 0, 'artifacts': []}), encoding='utf-8')
        runtime.engine.close()
        restored = Engine(sample_library(), root / 'swarm.sqlite')
        try:
            entries = [e for e in restored.snapshot()['history'] if e.get('recovered')]
            self.assertTrue(any(e.get('execution', {}).get('receipt') == receipt.relative_to(root).as_posix() for e in entries))
            self.assertTrue(all(e['valid'] is False for e in entries))
        finally:
            restored.close()

    def test_draft_retains_valid_model_capabilities(self):
        result = self.app._local_draft('研究缓存', '', False)
        result.update(source='model', plan={'capabilities': ['investigation', 'experimentation'],
                                          'rationale': '需要测量与机制分析'})
        record = self.app._record(self.task)
        with patch.object(self.app, '_draft', return_value=result):
            self.app._polish(self.task, record['token'], record['document']['revision'], '研究缓存', '', False)
        self.assertEqual(self.read('plan')['capabilities'], ['investigation', 'experimentation'])
        self.assertEqual(self.read('plan')['source'], 'model')

    def test_pause_cancels_jobs_and_preserves_downloadable_attempt(self):
        job = self.app.post(self.base + '/jobs', {'code': 'import time\nprint("started", flush=True)\ntime.sleep(30)'})
        wait_until(lambda: self.read('jobs/' + job['id'])['status'] == 'running')
        self.app.post(self.base + '/actions/pause', {})
        wait_until(lambda: self.read('jobs/' + job['id'])['status'] in ('cancelled', 'interrupted'), timeout=15)
        finished = self.read('jobs/' + job['id'])
        receipt = finished['attempts'][-1]['receipt']['path']
        result = self.app.read_api(self.base + '/jobs/' + job['id'] + '/files',
                                   'path=' + urllib.parse.quote(receipt))
        self.assertEqual(result[0], 200)
        self.assertIn('cancelled', result[1].decode())
        with self.assertRaises(ValueError):
            self.app.read_api(self.base + '/jobs/' + job['id'] + '/files', 'path=../config.local.json')

    def test_draft_proposal_resumes_after_engine_commit_without_duplicate_mutation(self):
        engine = self.seed_research()
        result = self.app.post(self.base + '/interactions', {'kind': 'revise', 'target': self.target(),
                'text': '限制硬件', 'replacement': 'Method A reduces latency on CPU'})
        proposal = result['proposal']
        command = dict(proposal['command'], expectedRevision=proposal['revision'], operationId=proposal['id'])
        engine.command('intervene', command)
        revision = engine.snapshot()['revision']
        applied = self.app.post(self.base + '/proposals/' + proposal['id'] + '/apply',
                               {'expectedRevision': proposal['revision'], 'confirmed': True})
        self.assertEqual(applied['status'], 'applied')
        self.assertEqual(engine.snapshot()['revision'], revision)


if __name__ == '__main__':
    unittest.main()
