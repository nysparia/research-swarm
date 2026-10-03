import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.engine import Engine
from research_swarm.providers import Settings, ModelConnectionError, ModelRequestCancelled
from research_swarm.runner import ResearchRunner
from research_swarm.local_tools import LocalResearchTools
from research_swarm.workspace import WorkspaceApplication
from test_engine import sample_library
from test_research_cycle import CycleRunner, cycle_library
from test_runner import LIBRARY
from test_workspace import wait_until
from test_provider_recovery import response, overload
from recovery_fixtures import ImmediateRecovery


EMPTY = {'summary': 'done', 'structured': {}, 'evidenceIds': [], 'claims': [], 'unresolved': []}


class RecoveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)

    def engine(self, runner):
        engine = Engine(sample_library(), self.root / 'state.sqlite', runner=runner, workflow='autonomous')
        self.addCleanup(engine.close)
        return engine

    def test_partial_failure_progresses_sibling_then_blocks_and_versioned_retry_recovers(self):
        entered, release = threading.Event(), threading.Event()
        failed = [False]
        def runner(node, context, log):
            if node['phase'] == 'plan':
                return {**EMPTY, 'children': [{'title': title, 'description': title, 'kind': 'evidence', 'acceptance': 'evidence'} for title in ('failure', 'independent')]}
            if node['title'] == 'failure' and not failed[0]:
                failed[0] = True
                raise ModelConnectionError('busy', 429, exhausted=True)
            if node['title'] == 'independent':
                entered.set(); release.wait(3)
            return copy.deepcopy(EMPTY)
        engine = self.engine(runner)
        self.addCleanup(release.set)
        engine.command('start-autonomous', {})
        self.assertTrue(entered.wait(2))
        wait_until(lambda: engine.snapshot()['executionSummary']['failed'] == 1)
        state = engine.snapshot()
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['executionSummary']['status'], 'partially_blocked')
        self.assertEqual(state['executionSummary']['running'], 1)
        release.set(); wait_until(lambda: engine.snapshot()['status'] == 'failed')
        state = engine.snapshot(); self.assertEqual(state['executionSummary']['status'], 'blocked')
        sibling = next(n for n in state['nodes'] if n['title'] == 'independent')
        failed_node = next(n for n in state['nodes'] if n['status'] == 'failed')
        engine.command('resume', {})
        self.assertEqual(engine.snapshot()['status'], 'failed')
        with self.assertRaisesRegex(ValueError, '版本'):
            engine.command('retry', {'nodeId': failed_node['id'], 'expectedNodeVersion': failed_node['version'] - 1})
        request = {'nodeId': failed_node['id'], 'expectedNodeVersion': failed_node['version']}
        engine.command('retry', request)
        with self.assertRaises(ValueError): engine.command('retry', request)
        wait_until(lambda: engine.snapshot()['report']['ready'])
        final_sibling = next(n for n in engine.snapshot()['nodes'] if n['id'] == sibling['id'])
        self.assertEqual(final_sibling['version'], sibling['version'])
        self.assertEqual(final_sibling['output'], sibling['output'])

    def test_wait_state_clears_on_pause_and_restart_and_does_not_self_resume(self):
        started, stopped = threading.Event(), threading.Event()
        def runner(node, context, log):
            context['model_wait']({'reason': 'busy', 'retryNumber': 1, 'maxRetries': 3, 'nextRetryAt': '2099-01-01T00:00:00Z', 'kind': 'cooldown'})
            started.set()
            while not context['cancelled'](): stopped.wait(.01)
            stopped.set()
            raise ModelRequestCancelled('cancelled')
        engine = self.engine(runner); engine.command('start-autonomous', {})
        self.assertTrue(started.wait(2))
        state = engine.snapshot()
        self.assertEqual(state['executionSummary']['waitingProvider'], 1)
        self.assertEqual(state['executionSummary']['running'], 0)
        revision = state['revision']
        self.assertEqual(engine.snapshot()['revision'], revision)
        engine.command('pause', {}); self.assertTrue(stopped.wait(2))
        self.assertFalse(any(n.get('modelWait') for n in engine.snapshot()['nodes']))
        engine.close()
        resumed = Engine(sample_library(), self.root / 'state.sqlite', runner=lambda *_: self.fail('read restarted work'), workflow='autonomous')
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.snapshot()['paused'])
        self.assertEqual(resumed.snapshot()['executionSummary']['waitingProvider'], 0)

    def test_transport_retries_preserve_prior_tool_result_without_reexecuting_tool(self):
        settings = Settings(self.root, None)
        settings.update({'provider': {'baseUrl': 'https://example.test/v1', 'apiKey': 'key', 'model': 'm'}})
        settings.recovery = ImmediateRecovery()
        calls = response(json.dumps({'toolCalls': [{'name': 'paper_search', 'arguments': {'query': 'sparse attention'}}]}))
        with patch('urllib.request.urlopen', side_effect=[calls, overload(), response(json.dumps(EMPTY))]) as http, \
                patch('research_swarm.tools.ResearchTools.call', return_value={'papers': []}) as tool:
            result = ResearchRunner(settings, self.root)({'id': 'n', 'kind': 'research', 'phase': 'execute', 'input': {}},
                {'library': LIBRARY, 'mode': 'llm', 'children': [], 'requirements': []}, lambda _: None)
        self.assertEqual(result['summary'], 'done')
        self.assertEqual(tool.call_count, 1); self.assertEqual(http.call_count, 3)
        second = json.loads(http.call_args_list[1].args[0].data)
        third = json.loads(http.call_args_list[2].args[0].data)
        self.assertEqual(second, third)

    def test_retry_redteam_aggregation_does_not_rerun_completed_experiment(self):
        class UnavailableReview(CycleRunner):
            outage = False
            def __call__(self, node, context, log):
                if node['input'].get('researchStep') == 'experiment_design' and node['phase'] == 'aggregate':
                    latest = context['children'][-1]['output']['structured']['experimentRun']
                    if latest.get('verified') and not self.outage:
                        self.outage = True
                        raise ModelConnectionError('redteam busy', 429, exhausted=True)
                return super().__call__(node, context, log)
        runner = UnavailableReview(experiment=True); runner.tools = LocalResearchTools(self.root)
        engine = Engine(cycle_library(), self.root / 'state.sqlite', runner=runner, workflow='autonomous'); self.addCleanup(engine.close)
        engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct', 'mode': 'llm'})
        wait_until(lambda: engine.snapshot()['status'] == 'failed', timeout=8)
        state = engine.snapshot(); node = next(n for n in state['nodes'] if n['status'] == 'failed')
        self.assertEqual(node['phase'], 'aggregate')
        runs = sum(step == 'experiment_execution' for step, _, _ in runner.calls)
        engine.command('retry', {'nodeId': node['id'], 'expectedNodeVersion': node['version']})
        wait_until(lambda: engine.snapshot()['report']['ready'], timeout=8)
        self.assertEqual(sum(step == 'experiment_execution' for step, _, _ in runner.calls), runs)
        self.assertEqual(engine.snapshot()['project']['researchCycle']['status'], 'converged')

    def test_workspace_sync_tracks_new_failures_and_removes_recovered_error_without_duplicate_messages(self):
        ws = WorkspaceApplication(Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access', self.root / 'workspace', import_existing=False)
        self.addCleanup(ws.close)
        tid = ws.post('/api/tasks', {})['task']['id']; runtime = ws._ensure_app(tid)
        engine = runtime.engine
        with engine._condition:
            record = ws._records[tid]; record['phase'] = 'researching'
            root = engine._get_node('central'); root.update(active=True, status='failed', error={'message': 'temporary overload', 'retryable': True})
            engine._state['project']['researchStarted'] = True
            engine._commit()
            first = ws.detail(tid)
            self.assertEqual(first['phase'], 'failed')
            before = len(first['messages'])
            self.assertEqual(len(ws.detail(tid)['messages']), before)
            root['version'] += 1; root['error']['message'] = 'another failure'; engine._commit()
            self.assertEqual(len(ws.detail(tid)['messages']), before + 1)
            root.update(status='pending', error=None); engine._commit()
            recovered = ws.detail(tid)
            self.assertEqual(recovered['phase'], 'researching'); self.assertIsNone(recovered['error'])
