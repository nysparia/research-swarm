import copy
import json
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
