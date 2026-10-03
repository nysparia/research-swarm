import io
import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from research_swarm.providers import Settings, ModelOutputError, ModelConnectionError, ModelRequestCancelled
from research_swarm.provider_recovery import ProviderRecovery, WaitBudget, retry_after, timestamp
from recovery_fixtures import ImmediateRecovery


def response(text='{"summary":"ok"}'):
    return io.BytesIO(json.dumps({'choices': [{'message': {'content': text}}], 'usage': {'total_tokens': 3}}).encode())


def overload(code=429, header=None):
    return urllib.error.HTTPError('https://example.test/v1/chat/completions', code, 'Busy',
        {'Retry-After': header} if header else {}, io.BytesIO(b'{"error":{"message":"upstream busy"}}'))


class ProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.settings = Settings(Path(temp.name), None)
        self.settings.update({'provider': {'baseUrl': 'https://example.test/v1', 'apiKey': 'private-key', 'model': 'm'}})
        self.clock = self.settings.recovery = ImmediateRecovery()

    def test_backoff_three_retries_plain_text_and_usage_counts_actual_requests(self):
        waits, logs = [], []
        with self.settings.request_context(on_wait=waits.append), patch('urllib.request.urlopen', side_effect=[overload(), overload(), overload(), response('OK')]) as send:
            self.assertEqual(self.settings.chat([], on_retry=logs.append), 'OK')
        self.assertEqual(send.call_count, 4)
        self.assertAlmostEqual(self.clock.elapsed, 65)
        self.assertEqual([w['retryNumber'] for w in waits if w], [1, 2, 3])
        self.assertIsNone(waits[-1])
        self.assertEqual(len(logs), 3)
        self.assertEqual(self.settings.usage()['calls'], 4)
        self.assertEqual(self.settings.usage()['errors'], 3)
        self.assertNotIn('private-key', json.dumps(waits))

    def test_exhaustion_preserves_structured_reason_and_shared_cooldown(self):
        with patch('urllib.request.urlopen', side_effect=[overload() for _ in range(4)]) as send:
            with self.assertRaises(ModelConnectionError) as caught:
                self.settings.chat([], json_mode=True)
        error = caught.exception
        self.assertTrue(error.exhausted); self.assertEqual(error.status_code, 429)
        self.assertTrue(error.next_retry_at); self.assertEqual(send.call_count, 4)
        previous = self.clock.elapsed
        with patch('urllib.request.urlopen', return_value=response()):
            self.settings.chat([], json_mode=True)
        self.assertEqual(self.clock.elapsed - previous, 45)

    def test_retry_after_header_and_long_wait_budget(self):
        with patch('urllib.request.urlopen', side_effect=[overload(header='22'), response()]) as send:
            self.settings.chat([], json_mode=True)
        self.assertEqual(self.clock.elapsed, 22); self.assertEqual(send.call_count, 2)
        with patch('urllib.request.urlopen', side_effect=overload(header='900')) as send:
            with self.assertRaises(ModelConnectionError) as caught:
                self.settings.chat([], json_mode=True)
        self.assertTrue(caught.exception.exhausted)
        self.assertIsNotNone(caught.exception.next_retry_at)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(self.clock.elapsed, 22)

    def test_interrupted_response_body_retries_current_request(self):
        broken = response()
        with patch.object(broken, 'read', side_effect=http.client.IncompleteRead(b'partial', 12)), \
                patch('urllib.request.urlopen', side_effect=[broken, response()]) as send:
            self.settings.chat([], json_mode=True)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(self.clock.elapsed, 5)

    def test_retry_after_dates_invalid_values_and_jitter(self):
        self.assertEqual(retry_after('12', now=1000), 12)
        self.assertEqual(retry_after(format_datetime(datetime.fromtimestamp(1025, timezone.utc)), now=1000), 25)
        self.assertEqual(retry_after(format_datetime(datetime.fromtimestamp(900, timezone.utc)), now=1000), 0)
        for value in (None, 'invalid', 'nan', 'inf'):
            self.assertIsNone(retry_after(value))
        recovery = ProviderRecovery(jitter=lambda: 1.)
        self.assertEqual([recovery.delay(i) for i in range(3)], [6, 18, 54])

    def test_format_repair_and_transport_retry_have_separate_budgets(self):
        sent = []
        replies = iter([overload(), response('invalid'), overload(), overload(), response()])
        def send(request, **kwargs):
            sent.append(json.loads(request.data))
            next_reply = next(replies)
            if isinstance(next_reply, Exception): raise next_reply
            return next_reply
        with patch('urllib.request.urlopen', side_effect=send):
            self.settings.chat([{'role': 'user', 'content': 'question'}], json_mode=True)
        self.assertEqual(len(sent), 5)
        self.assertEqual(sent[0], sent[1])
        self.assertEqual(sent[2:], [sent[2]] * 3)
        self.assertEqual(len(sent[-1]['messages']), 2)
        with patch('urllib.request.urlopen', side_effect=[response('invalid'), response('invalid')]) as request:
            with self.assertRaises(ModelOutputError): self.settings.chat([], json_mode=True)
        self.assertEqual(request.call_count, 2)

    def test_non_transient_http_errors_are_not_retried(self):
        for code in (400, 401, 403):
            with self.subTest(code=code), patch('urllib.request.urlopen', side_effect=overload(code)) as send:
                with self.assertRaises(RuntimeError): self.settings.chat([], json_mode=True)
                self.assertEqual(send.call_count, 1)
        self.assertEqual(self.clock.elapsed, 0)

    def test_cancellation_during_wait_stops_before_next_http_call(self):
        waits = []
        with self.settings.request_context(cancelled=lambda: self.clock.elapsed >= 1, on_wait=waits.append), patch('urllib.request.urlopen', side_effect=overload()) as send:
            with self.assertRaises(ModelRequestCancelled): self.settings.chat([], json_mode=True)
        self.assertEqual(send.call_count, 1); self.assertIsNone(waits[-1])
        self.assertEqual(self.clock.elapsed, 1)

    def test_roles_share_cooldown_but_different_accounts_do_not(self):
        self.settings.update({'reviewPolicy': 'shared'})
        with patch('urllib.request.urlopen', side_effect=overload(header='400')):
            with self.assertRaises(ModelConnectionError): self.settings.chat([], role='main')
        with patch('urllib.request.urlopen') as send:
            with self.assertRaises(ModelConnectionError): self.settings.chat([], role='judge')
            self.assertEqual(send.call_count, 0)
        self.settings.update({'providers': {'redteam': {'baseUrl': 'https://other.test/v1', 'apiKey': 'other-key', 'model': 'm'}}})
        with patch('urllib.request.urlopen', return_value=response('OK')) as send:
            self.assertEqual(self.settings.chat([], role='redteam'), 'OK')
            self.assertEqual(send.call_count, 1)

    def test_retry_budget_callback_runs_only_after_admission(self):
        charged = []
        with self.settings.request_context(on_request=lambda: charged.append(1)), patch('urllib.request.urlopen', side_effect=overload(header='600')):
            with self.assertRaises(ModelConnectionError): self.settings.chat([])
        self.assertEqual(len(charged), 1)

    def test_shutdown_cancels_pending_transport_before_another_request(self):
        def waiting(value):
            if value: self.settings.close()
        with self.settings.request_context(on_wait=waiting), patch('urllib.request.urlopen', side_effect=overload()) as send:
            with self.assertRaises(ModelRequestCancelled): self.settings.chat([])
        self.assertEqual(send.call_count, 1)

    def test_restored_retry_not_before_is_respected_without_automatic_request(self):
        due = timestamp(self.clock.wall_time() + 32)
        with self.settings.request_context(not_before=due), patch('urllib.request.urlopen', return_value=response()) as send:
            self.settings.chat([], json_mode=True)
        self.assertEqual(self.clock.elapsed, 32)
        self.assertEqual(send.call_count, 1)

    def test_v2_meter_charges_each_http_attempt_once_and_checks_cancellation(self):
        from research_swarm.v2_store import ResearchStore
        from research_swarm.v2_pipeline import MeteredSettings
        from test_v2_store import brief, plan
        store = ResearchStore(self.settings.path.parent / 'v2.sqlite', 'a' * 32)
        mid, generation, version = store.message('scope', 'draft', True)
        value = brief(); value['sourceMessageIds'] = [mid]
        store.draft(value, generation, version); store.confirm(1)
        run = store.start({'expectedBriefVersion': 1, 'requestId': 'x'}, {}, plan)[0]
        store.owner('test'); node, _ = store.claim(run['runId'], 'test')
        metered = MeteredSettings(self.settings, store, node)
        with patch('urllib.request.urlopen', side_effect=[overload(), response()]) as send:
            metered.chat([], json_mode=True)
        self.assertEqual(send.call_count, 2)
        current = store.snapshot()['runs'][0]
        self.assertEqual(current['usage']['modelCalls'], 2)
        store.action({'runId': current['runId'], 'revision': current['revision'], 'action': 'pause'})
        with patch('urllib.request.urlopen') as send:
            with self.assertRaises(ModelRequestCancelled): metered.chat([])
        self.assertEqual(send.call_count, 0)


class ProbeConcurrencyTests(unittest.TestCase):
    def test_only_one_probe_and_two_successes_restore_normal_admission(self):
        manager = ProviderRecovery()
        key = ('endpoint', b'account')
        manager.defer(key, 0, ModelConnectionError('busy', 429))
        first = manager.acquire(key, 0, WaitBudget(), lambda: False, lambda _: None, 1)
        waiting, granted = threading.Event(), threading.Event()
        second = []
        def acquire():
            second.append(manager.acquire(key, 0, WaitBudget(), lambda: False, lambda _: waiting.set(), 1))
            granted.set()
        worker = threading.Thread(target=acquire); worker.start()
        self.assertTrue(waiting.wait(1)); self.assertFalse(granted.is_set())
        manager.release(key, first, True)
        self.assertTrue(granted.wait(1)); worker.join(1)
        self.assertTrue(manager.states[key]['recovering'])
        manager.release(key, second[0], True)
        self.assertFalse(manager.states[key]['recovering'])

    def test_late_success_cannot_clear_new_cooldown(self):
        manager = ProviderRecovery(); key = ('a', b'k')
        old = manager.acquire(key, 0, WaitBudget(), lambda: False, lambda _: None, 0)
        manager.defer(key, 60, ModelConnectionError('new busy', 429))
        manager.release(key, old, True)
        self.assertTrue(manager.states[key]['recovering'])
        self.assertEqual(manager.states[key]['successes'], 0)
