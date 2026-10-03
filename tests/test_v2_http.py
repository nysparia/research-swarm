"""Loopback HTTP acceptance for the new workflow and retained public routes."""
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from research_swarm.server import make_handler
from research_swarm.v2_tools import RunTools
from research_swarm.workspace import WorkspaceApplication
import test_v2_behavior as fixtures


class V2HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = WorkspaceApplication(Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access',
                                       Path(self.temp.name), import_existing=False)
        self.addCleanup(self.ws.close)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.ws.settings, 'public', return_value=fixtures.PUBLIC))
        self.stack.enter_context(patch.object(self.ws.settings, 'role_status', return_value=fixtures.REVIEW))
        self.stack.enter_context(patch.object(self.ws.settings, 'chat', side_effect=lambda messages, **kwargs:
                                            fixtures.V2BehaviorTests.model(self, messages, **kwargs)))
        self.papers = self.stack.enter_context(patch.object(RunTools, '_paper', return_value=fixtures.SOURCE))
        self.stack.enter_context(patch('research_swarm.engine.Engine', side_effect=AssertionError('legacy Engine in v2')))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.ws))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, payload=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if payload is not None:
            headers['Origin'] = origin or self.url
        request = urllib.request.Request(self.url + path,
            data=json.dumps(payload).encode('utf-8') if payload is not None else None, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if 'application/json' in response.headers.get('Content-Type', ''):
                body = json.loads(body)
            return response.status, body

    def wait_detail(self, base, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, detail = self.request(base)
            self.assertEqual(status, 200)
            if predicate(detail):
                return detail
            time.sleep(.01)
        self.fail('HTTP workflow did not settle')

    def test_public_confirmation_start_report_events_and_export(self):
        status, created = self.request('/api/tasks', {'workflowVersion': 'conversation_only_v2'})
        self.assertEqual(status, 200)
        base = '/api/tasks/' + created['task']['id']
        status, _ = self.request(base + '/messages', {'text': '研究 Jev 架构，不做成本分析。'})
        self.assertEqual(status, 200)
        self.wait_detail(base, lambda detail: not detail['document']['polishing'])
        self.papers.assert_not_called()
        status, error = self.request(base + '/start', {'expectedRevision': 1, 'requestId': 'http-start'})
        self.assertEqual(status, 409)
        self.assertEqual(error['code'], 'brief_not_confirmed')
        status, _ = self.request(base + '/brief/confirm', {'expectedBriefVersion': 99})
        self.assertEqual(status, 409)
        status, confirmed = self.request(base + '/brief/confirm', {'expectedBriefVersion': 1})
        self.assertEqual(status, 200)
        self.assertIsNone(confirmed['research']['run'])
        self.papers.assert_not_called()
        self.assertEqual(self.request(base + '/start', {'expectedRevision': 1, 'requestId': 'http-start'})[0], 200)
        detail = self.wait_detail(base, lambda detail: detail['research']['run']['status'] == 'completed')
        status, duplicate = self.request(base + '/research/start', {'expectedBriefVersion': 1, 'requestId': 'http-start'})
        self.assertEqual(status, 200)
        self.assertEqual(duplicate['research']['run']['runId'], detail['research']['run']['runId'])
        self.papers.assert_called_once()
        self.assertIsInstance(detail['state']['nodes'], list)
        for field in ('plan', 'draft', 'report', 'artifacts'):
            self.assertIn(field, detail['workbench'])
        self.assertTrue(detail['capabilities']['conversationOnly'])
        self.assertTrue(detail['document']['readOnly'])
        self.assertEqual(self.request(base + '/document', {'markdown': 'bypass', 'expectedRevision': 1})[0], 403)
        self.assertEqual(self.request(base + '/jobs', {'code': 'print("bypass")'})[0], 403)
        status, page = self.request(base + '/events?after=0&limit=2')
        self.assertEqual(status, 200)
        self.assertTrue(page['more'])
        self.assertEqual(page['nextCursor'], page['events'][-1]['id'])
        request = urllib.request.Request(self.url + base + '/events/stream',
                                         headers={'Last-Event-ID': str(page['nextCursor'])})
        with urllib.request.urlopen(request, timeout=10) as stream:
            first = stream.readline().decode()
            self.assertTrue(first.startswith('id: '))
            self.assertGreater(int(first.split(':')[1]), page['nextCursor'])
            self.assertEqual(stream.readline().decode().strip(), 'event: research')
        status, data = self.request(base + '/export')
        self.assertEqual(status, 200)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            report = archive.read('report.md').decode('utf-8')
            self.assertIn('Jev 将准备与执行分离', report)
            research = json.loads(archive.read('research-data.json'))
            self.assertEqual(research['run']['runId'], detail['research']['run']['runId'])
            self.assertNotIn('settings', research['run'])

    def test_legacy_default_and_http_origin_protection_remain(self):
        status, legacy = self.request('/api/tasks', {})
        self.assertEqual(status, 200)
        self.assertNotEqual(legacy.get('workflowVersion'), 'conversation_only_v2')
        self.assertIn('document', legacy)
        before = self.request('/api/tasks')[1]['tasks']
        self.assertEqual(self.request('/api/tasks', {}, origin='https://untrusted.invalid')[0], 403)
        status, error = self.request('/api/tasks', {'workflowVersion': 'future-unknown'})
        self.assertEqual(status, 409)
        self.assertEqual(error['code'], 'unsupported_workflow')
        self.assertEqual(self.request('/api/tasks')[1]['tasks'], before)


if __name__ == '__main__':
    unittest.main()
