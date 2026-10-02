import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.workspace import WorkspaceApplication


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('background operation did not finish')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = WorkspaceApplication(self.root / 'missing-source', self.root / 'state', import_existing=False)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def new(self):
        return self.app.post('/api/tasks', {})['task']['id']

    def detail(self, task_id):
        return self.app.detail(task_id)

    def test_save_retries_temporary_windows_locks_and_preserves_atomicity(self):
        replace = Path.replace
        for winerror in (5, 32, 33):
            with self.subTest(winerror=winerror):
                task_id = self.new()
                record = self.app._record(task_id)
                path = self.app._data_root / task_id / 'conversation.json'
                before = path.read_bytes()
                record['document']['markdown'] = 'New document'
                error = PermissionError('temporarily locked')
                error.winerror = winerror
                sources = []

                def locked(source, destination):
                    sources.append(source)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(json.loads(source.read_text(encoding='utf-8'))['document']['markdown'], 'New document')
                    if len(sources) < 3:
                        raise error
                    return replace(source, destination)

                with patch.object(Path, 'replace', locked), patch('research_swarm.workspace.time.sleep') as sleep:
                    self.app._save(record)
                self.assertEqual(len(sources), 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), record)
                self.assertEqual(list(path.parent.glob('*.tmp')), [])
                with patch.object(Path, 'replace', autospec=True, side_effect=replace) as next_replace:
                    self.app._save(record)
                self.assertNotEqual(sources[0], next_replace.call_args.args[0])

    @unittest.skipUnless(os.name == 'nt', 'Windows file sharing semantics')
    def test_save_recovers_when_a_real_windows_reader_releases_the_file(self):
        task_id = self.new()
        record = self.app._record(task_id)
        path = self.app._data_root / task_id / 'conversation.json'
        record['document']['markdown'] = 'Update after reader releases file'
        reader = path.open('rb')
        denied = threading.Event()
        errors = []
        replace = Path.replace

        def release_reader():
            denied.wait(2)
            reader.close()

        def observed_replace(source, destination):
            try:
                return replace(source, destination)
            except OSError as error:
                errors.append(error)
                denied.set()
                raise

        release = threading.Thread(target=release_reader)
        release.start()
        try:
            with patch.object(Path, 'replace', observed_replace):
                self.app._save(record)
        finally:
            denied.set()
            release.join(timeout=3)
            reader.close()
        self.assertTrue(errors)
        self.assertTrue(all(error.winerror in (5, 32, 33) for error in errors))
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), record)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_save_stops_retrying_persistent_lock_without_damaging_existing_record(self):
        task_id = self.new()
        record = self.app._record(task_id)
        path = self.app._data_root / task_id / 'conversation.json'
        before = path.read_bytes()
        record['document']['markdown'] = 'Unsaved update'
        error = PermissionError('persistently locked')
        error.winerror = 5
        with patch.object(Path, 'replace', side_effect=error) as replace, patch('research_swarm.workspace.time.sleep') as sleep:
            with self.assertRaises(PermissionError) as raised:
                self.app._save(record)
        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(sleep.call_count, 5)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_save_does_not_retry_unrelated_io_errors(self):
        task_id = self.new()
        record = self.app._record(task_id)
        path = self.app._data_root / task_id / 'conversation.json'
        before = path.read_bytes()
        for error in (PermissionError('permission denied'), OSError(28, 'disk full')):
            with self.subTest(error=error), patch.object(Path, 'replace', side_effect=error) as replace, patch('research_swarm.workspace.time.sleep') as sleep:
                with self.assertRaises(OSError) as raised:
                    self.app._save(record)
                self.assertIs(raised.exception, error)
                replace.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_new_tasks_are_empty_and_independent_and_restart_persists(self):
        first, second = self.new(), self.new()
        self.assertNotEqual(first, second)
        self.app.post(f'/api/tasks/{first}/messages', {'text': '比较图神经网络过平滑的缓解方法'})
        wait_until(lambda: not self.detail(first)['document']['polishing'])
        self.assertIn('过平滑', self.detail(first)['document']['markdown'])
        self.assertEqual(self.detail(second)['phase'], 'empty')
        self.assertEqual(self.detail(second)['messages'], [])
        self.app.close()
        self.app = WorkspaceApplication(self.root / 'missing-source', self.root / 'state', import_existing=False)
        self.assertEqual(len(self.app.tasks()), 2)
        self.assertIn('过平滑', self.detail(first)['document']['markdown'])

    def test_late_polish_cannot_replace_newer_user_document(self):
        task_id = self.new()
        arrived, release = threading.Event(), threading.Event()
        original = self.app._draft
        def draft(text, previous, editing=False, research_context=None):
            if text == '较旧的方向':
                arrived.set()
                release.wait(2)
            return original(text, previous, editing, research_context)
        self.app._draft = draft
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '较旧的方向'})
        self.assertTrue(arrived.wait(1))
        current = self.detail(task_id)['document']['revision']
        self.app.post(f'/api/tasks/{task_id}/document', {'markdown': '# 更新后的用户研究需求\n新方向必须保留', 'expectedRevision': current})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        release.set()
        wait_until(lambda: not any(t.is_alive() for t in self.app._threads))
        self.assertIn('新方向必须保留', self.detail(task_id)['document']['markdown'])
        self.assertNotIn('较旧的方向', self.detail(task_id)['document']['markdown'])
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(f'/api/tasks/{task_id}/document', {'markdown': 'stale', 'expectedRevision': current})

    def test_local_draft_is_not_reported_as_model_polish(self):
        task_id = self.new()
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '研究编译优化'})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        detail = self.detail(task_id)
        self.assertEqual(detail['document']['source'], 'local')
        self.assertTrue(detail['document']['questions'])
        self.assertTrue(any('模型' in m['content'] for m in detail['messages'] if m['role'] == 'assistant'))

    def test_explicit_new_round_requires_completed_phase_and_current_document(self):
        task_id = self.new()
        record = self.app._record(task_id)
        record['document'].update(markdown='# Earlier research', revision=3)
        for phase in ('empty', 'requirements', 'retrieving', 'researching', 'failed'):
            record['phase'] = phase
            with self.subTest(phase=phase), patch.object(self.app, '_ensure_app', side_effect=AssertionError('Must reject before restoration')):
                with self.assertRaisesRegex(ValueError, '阶段|完成'):
                    self.app.post(f'/api/tasks/{task_id}/messages', {
                        'text': 'Investigate a new mechanism', 'startNewRound': True, 'expectedRevision': 3})
            self.assertEqual(record['document']['revision'], 3)
            self.assertEqual(record['messages'], [])
        record['phase'] = 'completed'
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(f'/api/tasks/{task_id}/messages', {
                'text': 'Investigate a new mechanism', 'startNewRound': True, 'expectedRevision': 2})
        with patch.object(self.app, '_spawn') as spawn:
            detail = self.app.post(f'/api/tasks/{task_id}/messages', {
                'text': 'Investigate a new mechanism', 'startNewRound': True, 'expectedRevision': 3})
        self.assertEqual(detail['phase'], 'requirements')
        self.assertEqual(detail['document']['revision'], 4)
        self.assertTrue(detail['document']['polishing'])
        self.assertIn('Earlier research', detail['document']['markdown'])
        self.assertIn('Investigate a new mechanism', detail['document']['markdown'])
        self.assertEqual(spawn.call_count, 1)

    def test_start_requires_latest_finished_document_and_failure_visible(self):
        task_id = self.new()
        with self.assertRaises(ValueError):
            self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': 0})
        self.app.post(f'/api/tasks/{task_id}/messages', {'text': '研究数据库查询优化'})
        wait_until(lambda: not self.detail(task_id)['document']['polishing'])
        revision = self.detail(task_id)['document']['revision']
        with self.assertRaisesRegex(ValueError, '版本'):
            self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': revision - 1})
        self.app.post(f'/api/tasks/{task_id}/start', {'expectedRevision': revision})
        wait_until(lambda: self.detail(task_id)['phase'] == 'failed')
        self.assertTrue(self.detail(task_id)['error'])
        self.assertFalse(self.detail(task_id)['artifacts'])

    def test_task_path_cannot_escape_workspace(self):
        for task_id in ('../config.local.json', '..', 'foo/bar', 'unknown'):
            with self.assertRaises(ValueError):
                self.app.detail(task_id)

    def test_recorded_artifacts_remain_downloadable_without_model_output(self):
        task_id = self.new()
        record = self.app._records[task_id]
        record['phase'] = 'completed'
        record['runs'] = [{'round':1}]
        root = self.app._data_root / task_id
        artifact = root/'runtime/runs/local-test/artifacts/metrics.json'
        artifact.parent.mkdir(parents=True)
        artifact.write_text('{"score":1}')
        state = {'nodes':[], 'history':[{'type':'tool-executed','valid':False,
            'execution':{'artifacts':[{'name':'metrics.json','path':'runs/local-test/artifacts/metrics.json'}]}}]}
        (root/'reports').mkdir()
        (root/'reports/round-1.json').write_text(json.dumps(state))
        artifacts = self.detail(task_id)['artifacts']
        self.assertTrue(any(a['name']=='metrics.json' for a in artifacts))
        download = self.app.read_api(f'/api/tasks/{task_id}/artifacts/runs/local-test/artifacts/metrics.json')
        self.assertEqual(download[1], b'{"score":1}')

    def test_single_deepseek_key_sets_model_and_shared_connection_without_exposing_secret(self):
        self.new()
        observed = []
        self.app.settings.chat = lambda messages, max_tokens: observed.append(self.app.settings.public()) or 'OK'
        with patch.object(self.app.settings, '_deepseek_models', return_value={'models': [{'id': 'deepseek-flash'}]}):
            result = self.app.post('/api/setup', {'apiKey': 'unit-test-key-not-real', 'model': 'deepseek-flash'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'llm')
        self.assertEqual(result['provider']['baseUrl'], 'https://api.deepseek.com')
        self.assertEqual(result['provider']['model'], 'deepseek-flash')
        self.assertTrue(result['capabilities']['modelReady'])
        self.assertEqual(len(observed), 1)
        self.assertNotIn('unit-test-key-not-real', json.dumps(result))

    def test_failed_key_setup_restores_previous_configuration(self):
        before = self.app.settings.public()
        def reject(*args, **kwargs):
            raise ValueError('rejected unit-test-key-not-real')
        self.app.settings.chat = reject
        with patch.object(self.app.settings, '_deepseek_models', return_value={'models': [{'id': 'deepseek-flash'}]}), self.assertRaisesRegex(ValueError, '已隐藏'):
            self.app.post('/api/setup', {'apiKey': 'unit-test-key-not-real', 'model': 'deepseek-flash'})
        self.assertEqual(self.app.settings.public(), before)

    def test_role_settings_and_connection_test_select_requested_role(self):
        public = self.app.post('/api/settings', {'providers': {'judge': {'baseUrl': 'http://localhost:11434/v1', 'model': 'local-judge'}}})
        self.assertTrue(public['providers']['judge']['ready'])
        with patch.object(self.app.settings, 'chat', return_value='OK') as chat:
            result = self.app.post('/api/provider/test', {'role': 'judge'})
        self.assertEqual(result['role'], 'judge')
        self.assertEqual(chat.call_args.kwargs['role'], 'judge')
        self.assertTrue(public['capabilities']['evidenceReady'])

    def test_evidence_mode_never_polishes_with_configured_cloud_model(self):
        self.app.settings.update({'mode': 'evidence', 'provider': {'apiKey': 'configured-secret'}})
        with patch.object(self.app.settings, 'chat', side_effect=AssertionError('must stay offline')):
            result = self.app._draft('核验已有资料', '', False)
        self.assertEqual(result['source'], 'local')
        self.assertEqual(self.app.settings.public()['mode'], 'evidence')

    def test_checkpoint_acknowledgement_payload_reaches_task_engine(self):
        task_id = self.new()
        self.app.source = Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access'
        app = self.app._ensure_app(task_id)
        payload = {'id': 'checkpoint-1', 'decision': 'confirm', 'expectedRevision': 1,
                   'responsibilityAcknowledged': True, 'responsibilityName': 'Reviewer'}
        with patch.object(app.engine, 'command', return_value={'ok': True}) as command:
            self.app.post(f'/api/tasks/{task_id}/actions/checkpoint', payload)
        command.assert_called_once_with('checkpoint', payload)


if __name__ == '__main__':
    unittest.main()
