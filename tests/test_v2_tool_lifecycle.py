"""Run-local tool lifecycle tests; real scripts use only Python's standard library."""
import copy
import hashlib
import json
import shutil
import sqlite3
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from research_swarm.experiment_jobs import ExperimentJobReader, ExperimentJobs
from research_swarm.local_tools import LocalResearchTools
from research_swarm.v2_contracts import DomainError
from research_swarm.v2_store import ResearchStore
from research_swarm.v2_tools import RunTools
from test_v2_experiments import CODE, PROTOCOL
from test_v2_store import brief


TARGET = {
    'paperId': 'paper:counter', 'evidenceIds': ['evidence:counter-table'],
    'metric': 'mean', 'expected': '2', 'tolerance': '0.01',
    'conditions': 'three fixed observations, measured with standard-library arithmetic',
}
SOURCE_EVIDENCE = {
    'id': 'evidence:counter-table', 'paperId': 'paper:counter',
    'type': 'full_text', 'locator': 'page 3, table 1', 'quote': 'The mean is 2.',
}


class ToolLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / 'research.sqlite', 'd' * 32)
        self.tools = RunTools(self.store, self.root / 'source', self.root / 'artifacts', None)
        self.addCleanup(self.tools.close)
        self.gpu_probe = patch.object(ExperimentJobs, '_detect_gpus', return_value=0)
        self.gpu_probe.start()
        self.addCleanup(self.gpu_probe.stop)
        self.store.owner('lifecycle-test')
        self.heartbeat_stop = threading.Event()
        self.heartbeat_errors = []

        def heartbeat():
            while not self.heartbeat_stop.wait(1):
                try:
                    if not self.store.heartbeat('lifecycle-test'):
                        self.heartbeat_errors.append('owner lease expired')
                        return
                except Exception as exc:
                    self.heartbeat_errors.append(exc)
                    return

        self.heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        self.heartbeat_thread.start()
        self.addCleanup(self.stop_heartbeat)

    def stop_heartbeat(self):
        self.heartbeat_stop.set()
        self.heartbeat_thread.join(timeout=5)
        self.assertFalse(self.heartbeat_thread.is_alive())
        self.assertEqual(self.heartbeat_errors, [])

    def start(self, stage='Experiment', install=False, plan=None):
        mid, generation, version = self.store.message('Explicit standard-library experiment', 'draft', True)
        value = brief()
        value['executionPolicy'] = {
            'allowLocalExperiment': True, 'allowReplication': True, 'allowInstall': install,
        }
        value['sourceMessageIds'] = [mid]
        self.store.draft(value, generation, version)
        self.store.confirm(version + 1)
        nodes = plan or [{'id': 'execute', 'stage': stage, 'objectiveId': 'objective:1', 'dependencies': []}]
        self.run = self.store.start(
            {'expectedBriefVersion': version + 1, 'requestId': 'run-' + str(version + 1)}, {}, lambda _: nodes,
        )[0]
        self.node, self.run = self.store.claim(self.run['runId'], 'lifecycle-test')
        return self.run, self.node

    def arguments(self, code=CODE, protocol=None, **extra):
        return {'code': code, 'timeoutSeconds': 10, 'protocol': copy.deepcopy(protocol or PROTOCOL), **extra}

    def execute(self, code=CODE, protocol=None, **extra):
        return self.tools.call(self.run, self.node, 'python_run', self.arguments(code, protocol, **extra))

    def action(self, action, **extra):
        run = next(item for item in self.store.snapshot()['runs'] if item['runId'] == self.run['runId'])
        self.run = self.store.action({'action': action, 'runId': run['runId'], 'revision': run['revision'], **extra})

    def retry(self, acknowledge=False):
        self.action('pause')
        self.action('retry', acknowledgeUnknownSideEffects=acknowledge)
        self.node, self.run = self.store.claim(self.run['runId'], 'lifecycle-test')

    def assert_domain(self, code, operation):
        with self.assertRaises(DomainError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code, str(raised.exception))

    def register_claim(self, target=TARGET, stage='Replication', version=3):
        nodes = [
            {'id': 'claim', 'stage': 'Claim Extraction', 'objectiveId': 'objective:1', 'dependencies': []},
            {'id': 'execute', 'stage': stage, 'objectiveId': 'objective:1', 'dependencies': ['claim']},
        ]
        self.start(plan=nodes)
        claim = {'id': 'claim:counter', 'version': version, 'statement': 'mean equals two'}
        if target is not None:
            claim['reproductionTarget'] = copy.deepcopy(target)
        claim['versions'] = [copy.deepcopy(claim)]
        self.store.finish(self.node, {'state': {
            'claimGraph': {'claims': [claim]}, 'evidence': [copy.deepcopy(SOURCE_EVIDENCE)],
        }})
        self.claim_node = self.node
        self.node, self.run = self.store.claim(self.run['runId'], 'lifecycle-test')
        return claim

    def test_construction_and_denied_calls_never_open_queue(self):
        self.start()
        with patch('research_swarm.v2_tools._RunExperimentJobs', side_effect=AssertionError('queue created')):
            other = RunTools(self.store, self.root / 'source', self.root / 'lazy', None)
            other.close()
            other.close()
            self.assertFalse(other.root.exists())
            self.assert_domain('policy_denied', lambda: self.tools.call(
                self.run, self.node, 'python_install', {'packages': ['numpy']},
            ))
            self.assert_domain('policy_denied', lambda: self.tools.call(self.run, self.node, None, {}))
            self.assert_domain('policy_denied', lambda: self.tools.call(
                self.run, self.node, 'artifact_read', {'path': '../outside'},
            ))
            self.assert_domain('stale_result', lambda: self.tools.call(
                self.run, dict(self.node, objectiveId='objective:foreign'), 'python_run', self.arguments(),
            ))
            self.assert_domain('invalid_tool', lambda: self.execute(protocol={'id': 'incomplete'}))
            self.assert_domain('invalid_tool', lambda: self.execute(hostPath='outside'))
        self.assertEqual(self.store.tool_history(), [])
        self.assertFalse(self.tools.root.exists())

    def test_two_real_calls_share_environment_and_files_with_independent_receipts(self):
        self.start()
        first_code = 'from pathlib import Path\nPath("shared.txt").write_text("retained")\n' + CODE
        second_protocol = copy.deepcopy(PROTOCOL)
        second_protocol['id'] = 'second-protocol'
        second_code = (
            'from pathlib import Path\nassert Path("shared.txt").read_text() == "retained"\n'
            'print("read earlier file")\n' + CODE.replace('tiny-protocol', 'second-protocol')
        )
        original_process = LocalResearchTools._process
        setups = []

        def observe_process(tools, argv, *args, **kwargs):
            if '-m' in argv and 'venv' in argv:
                setups.append(argv)
            return original_process(tools, argv, *args, **kwargs)

        with patch.object(LocalResearchTools, '_process', autospec=True, side_effect=observe_process):
            first = self.execute(first_code)
            second = self.execute(second_code, second_protocol)
        self.assertEqual(len(setups), 1)
        self.assertEqual(first['status'], 'completed', first)
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(first['measurements']['mean'], 2)
        self.assertEqual(second['measurements']['mean'], 2)
        self.assertEqual(first['artifactRoot'], second['artifactRoot'])
        self.assertEqual(first['workingDirectory'], second['workingDirectory'])
        self.assertEqual(first['command'][0], second['command'][0])
        self.assertEqual(first['executionToken'], second['executionToken'])
        self.assertNotEqual(first['receipt'], second['receipt'])
        self.assertNotEqual(first['script'], second['script'])
        self.assertIn('read earlier file', second['stdout'])
        root = self.tools.root / first['artifactRoot']
        for result in (first, second):
            self.assertTrue((root / result['receipt']).is_file())
            self.assertTrue((root / result['script']).is_file())
        # Later work never changes an earlier call's copied artifacts.
        raw = next(item for item in first['artifacts'] if item['name'] == 'raw.csv')
        self.assertEqual(hashlib.sha256((root / raw['path']).read_bytes()).hexdigest(), raw['sha256'])
        self.assertEqual(len(self.tools._jobs), 1)
        self.assertEqual(len(self.store.tool_history()), 2)

    def test_mock_install_and_real_execution_use_the_same_run_venv(self):
        self.start(install=True)
        original_process = LocalResearchTools._process
        installed = []

        def fake_install(tools, argv, cwd, run, timeout, log, cancelled):
            if argv[0] != 'mock-uv':
                return original_process(tools, argv, cwd, run, timeout, log, cancelled)
            executable = Path(argv[argv.index('--python') + 1])
            installed.append(executable)
            (executable.parent.parent / 'mock-installed.txt').write_text('mock package')
            run.mkdir(parents=True, exist_ok=True)
            (run / 'stdout.txt').write_text('mock install')
            (run / 'stderr.txt').write_text('')
            return {'status': 'completed', 'returnCode': 0, 'elapsedMs': 1, 'stdout': 'mock install', 'stderr': ''}

        with patch('research_swarm.local_tools.shutil.which', return_value='mock-uv'), patch.object(
            LocalResearchTools, '_process', autospec=True, side_effect=fake_install,
        ):
            receipt = self.tools.call(self.run, self.node, 'python_install', {'packages': ['numpy']})
        result = self.execute(
            'import sys\nfrom pathlib import Path\n'
            'assert (Path(sys.prefix) / "mock-installed.txt").read_text() == "mock package"\n' + CODE,
        )
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(receipt['artifactRoot'], result['artifactRoot'])
        self.assertEqual(installed, [Path(result['command'][0])])
        self.assertNotEqual(receipt['receipt'], result['receipt'])

    def test_run_and_node_boundaries_isolate_files_but_not_same_run_venv(self):
        nodes = [
            {'id': 'one', 'stage': 'Experiment', 'objectiveId': 'objective:1', 'dependencies': []},
            {'id': 'two', 'stage': 'Experiment', 'objectiveId': 'objective:1', 'dependencies': ['one']},
        ]
        self.start(plan=nodes)
        first = self.execute('from pathlib import Path\nPath("private.txt").write_text("first")\n' + CODE)
        self.store.finish(self.node, {'summary': 'done'})
        self.node, self.run = self.store.claim(self.run['runId'], 'lifecycle-test')
        second = self.execute('from pathlib import Path\nassert not Path("private.txt").exists()\n' + CODE)
        self.assertEqual(first['artifactRoot'], second['artifactRoot'])
        self.assertEqual(first['command'][0], second['command'][0])
        self.assertNotEqual(first['workingDirectory'], second['workingDirectory'])
        self.action('cancel')
        self.start()
        third = self.execute('from pathlib import Path\nassert not Path("private.txt").exists()\n' + CODE)
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(third['status'], 'completed', third)
        self.assertNotEqual(first['artifactRoot'], third['artifactRoot'])
        self.assertNotEqual(first['command'][0], third['command'][0])
        self.assertEqual(len(self.tools._jobs), 2)
        queues = list(self.tools._jobs.values())
        self.tools.close()
        self.assertTrue(all(not jobs._worker.is_alive() and jobs._ownership.closed for jobs in queues))

    def test_same_attempt_cache_verifies_all_hashed_files_and_rejects_tampering(self):
        self.start()
        result = self.execute()
        root = self.tools.root / result['artifactRoot']
        with patch.object(LocalResearchTools, 'call', side_effect=AssertionError('unexpected execution')):
            reused = self.execute()
            self.assertEqual(result, reused)
            reused['measurements']['mean'] = 999
            self.assertEqual(self.execute()['measurements']['mean'], 2)
            files = [result['script'], result['environmentPath'], result['receipt']]
            files.extend(item['path'] for item in result['artifacts'])
            for relative in files:
                with self.subTest(file=relative):
                    path = root / relative
                    original = path.read_bytes()
                    try:
                        path.write_bytes(b'tampered')
                        self.assert_domain('invalid_receipt', self.execute)
                    finally:
                        path.write_bytes(original)
        self.assertEqual(len(self.store.tool_history()), 1)

    def test_explicit_retry_runs_again_in_new_attempt_directory(self):
        self.start()
        code = (
            'from pathlib import Path\nassert not Path("leftover.txt").exists()\n'
            'Path("leftover.txt").write_text("attempt-specific")\n' + CODE
        )
        first = self.execute(code)
        stale_node = self.node
        self.retry()
        self.assertEqual(self.node['attempt'], 2)
        self.assert_domain('stale_result', lambda: self.tools.call(
            self.run, stale_node, 'python_run', self.arguments(code),
        ))
        second = self.execute(code)
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(first['artifactRoot'], second['artifactRoot'])
        self.assertEqual(first['command'][0], second['command'][0])
        self.assertNotEqual(first['workingDirectory'], second['workingDirectory'])
        self.assertNotEqual(first['receipt'], second['receipt'])
        self.assertNotEqual(first['executionToken'], second['executionToken'])
        self.assertEqual([call['attempt'] for call in self.store.tool_history()], [1, 2])

    def test_restart_never_executes_old_queue_and_unknown_call_needs_explicit_retry(self):
        self.start()
        arguments = self.arguments()
        digest = hashlib.sha256(json.dumps({'tool': 'python_run', 'arguments': arguments}, sort_keys=True).encode()).hexdigest()
        self.store.charge(self.node, 'toolCalls', 'unknown-call', {
            'tool': 'python_run', 'arguments': arguments, 'digest': digest,
            'attempt': self.node['attempt'], 'sideEffect': True,
        })
        run_root = self.tools.root / self.run['runId']
        with patch.object(ExperimentJobs, '_loop', return_value=None):
            dormant = ExperimentJobs(run_root)
            try:
                pending = dormant.submit({'code': 'raise AssertionError("must never run")'})
            finally:
                dormant.close()
        self.tools.close()
        self.tools = RunTools(self.store, self.root / 'source', self.root / 'artifacts', None)
        self.addCleanup(self.tools.close)
        self.assertEqual(self.tools._jobs, {})
        self.assertEqual(ExperimentJobReader(run_root).get(pending['id'])['status'], 'queued')
        self.assert_domain('unknown_side_effect', self.execute)
        self.assertEqual(self.tools._jobs, {})
        self.action('pause')
        self.assert_domain('unknown_side_effect', lambda: self.action('resume'))
        self.assert_domain('unknown_side_effect', lambda: self.action('retry'))
        self.action('retry', acknowledgeUnknownSideEffects=True)
        self.node, self.run = self.store.claim(self.run['runId'], 'lifecycle-test')
        result = self.execute()
        self.assertEqual(result['status'], 'completed', result)
        old = ExperimentJobReader(run_root).get(pending['id'])
        self.assertEqual(old['status'], 'interrupted')
        self.assertEqual(old['attempts'], [])
        self.assertEqual(self.store.tool_history()[0]['status'], 'started')

    def test_cached_receipt_can_be_read_after_adapter_restart_without_queue(self):
        self.start()
        first = self.execute()
        self.tools.close()
        self.tools = RunTools(self.store, self.root / 'source', self.root / 'artifacts', None)
        self.addCleanup(self.tools.close)
        with patch('research_swarm.v2_tools._RunExperimentJobs', side_effect=AssertionError('unexpected queue')):
            self.assertEqual(self.execute(), first)
        self.assertEqual(self.tools._jobs, {})

    def test_queue_environment_failure_is_resource_unavailable_with_durable_failure(self):
        self.start()
        with patch.object(LocalResearchTools, '_ensure_python', side_effect=OSError('interpreter unavailable')):
            self.assert_domain('resource_unavailable', self.execute)
        history = self.store.tool_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['status'], 'completed')
        receipt = history[0]['result']
        self.assertEqual(receipt['status'], 'failed')
        self.assertIsNone(receipt['returnCode'])
        self.assertTrue((self.tools.root / receipt['artifactRoot'] / receipt['receipt']).is_file())
        self.assert_domain('resource_unavailable', self.execute)
        self.assertEqual(len(self.store.tool_history()), 1)

    def test_queue_creation_failure_keeps_started_ledger_and_maps_resource_error(self):
        self.start()
        with patch('research_swarm.v2_tools._RunExperimentJobs', side_effect=OSError('cannot open queue')):
            self.assert_domain('resource_unavailable', self.execute)
        self.assertEqual(self.store.tool_history()[0]['status'], 'started')
        self.assert_domain('unknown_side_effect', self.execute)

    def test_queue_constructor_failure_releases_opened_database_and_lock(self):
        self.start()
        with patch.object(ExperimentJobs, '_detect_gpus', side_effect=OSError('probe unavailable')):
            self.assert_domain('resource_unavailable', self.execute)
        self.assertEqual(self.tools._jobs, {})
        self.assertEqual(self.store.tool_history()[0]['status'], 'started')
        shutil.rmtree(self.tools.root)
        self.assertFalse(self.tools.root.exists())

    def test_normal_nonzero_script_returns_failure_without_user_blocker(self):
        self.start()
        result = self.execute('raise RuntimeError("ordinary script failure")')
        self.assertEqual(result['status'], 'failed')
        self.assertNotEqual(result['returnCode'], 0)
        self.assertNotIn('errorCode', result)
        self.assertIn('ordinary script failure', result['stderr'])
        self.assertEqual(result['scientificStatus'], 'invalid')
        self.assertEqual(self.store.tool_history()[0]['status'], 'completed')

    def test_close_cancels_active_call_and_releases_windows_handles(self):
        self.start()
        outcomes = []

        def execute():
            try:
                outcomes.append(self.execute(
                    'from pathlib import Path\nimport time\nPath("active.txt").write_text("ready")\ntime.sleep(60)',
                ))
            except DomainError as exc:
                outcomes.append(exc)

        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 10)
        deadline = time.monotonic() + 30
        while not list(self.tools.root.rglob('active.txt')) and time.monotonic() < deadline and worker.is_alive():
            time.sleep(0.05)
        self.assertTrue(list(self.tools.root.rglob('active.txt')), outcomes)
        jobs = self.tools._jobs[self.run['runId']]
        self.tools.close()
        self.tools.close()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], DomainError)
        self.assertEqual(outcomes[0].code, 'stale_result')
        self.assertFalse(jobs._worker.is_alive())
        self.assertTrue(jobs._ownership.closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            jobs._db.execute('SELECT 1')
        self.assertEqual(self.tools._jobs, {})
        self.assert_domain('resource_unavailable', self.execute)
        # On Windows this fails if jobs.sqlite3 or the ownership file is still open.
        shutil.rmtree(self.tools.root)
        self.assertFalse(self.tools.root.exists())

    def test_replication_binds_completed_claim_version_target_and_conditions(self):
        claim = self.register_claim()
        protocol = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'])
        result = self.execute(protocol=protocol, claimId=claim['id'])
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['claimId'], claim['id'])
        self.assertEqual(result['claimVersion'], claim['version'])
        self.assertEqual(result['reproductionTarget'], TARGET)
        self.assertEqual(result['conditions'], TARGET['conditions'])
        self.assertEqual(result['measurements']['mean'], 2)
        self.assertEqual(self.store.tool_history()[0]['result']['reproductionTarget'], TARGET)

    def test_replication_missing_inputs_and_mismatched_protocols_fail_before_queue(self):
        self.register_claim()
        correct = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'])
        invalid = []
        for field, value in (
            ('metrics', ['unrelated']), ('outputSchema', {'other': 'number'}),
            ('conditions', ''), ('conditions', 'different conditions'), ('metric', 'other'),
        ):
            protocol = copy.deepcopy(correct)
            protocol[field] = value
            invalid.append(protocol)
        invalid.append(copy.deepcopy(PROTOCOL))
        wrong_schema = copy.deepcopy(correct)
        wrong_schema['outputSchema'] = {'other': 'number'}
        wrong_schema['rawData']['metrics'] = {'other': {'column': 'value', 'statistic': 'mean'}}
        invalid.append(wrong_schema)
        with patch('research_swarm.v2_tools._RunExperimentJobs', side_effect=AssertionError('unexpected queue')):
            self.assert_domain('invalid_tool', lambda: self.execute(protocol=correct))
            self.assert_domain('invalid_tool', lambda: self.execute(protocol=correct, claimId='claim:unknown'))
            self.assert_domain('invalid_tool', lambda: self.execute(
                protocol=correct, claimId='claim:counter', reproductionTarget=TARGET,
            ))
            for protocol in invalid:
                with self.subTest(protocol=protocol):
                    self.assert_domain('invalid_tool', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        self.assertEqual(self.store.tool_history(), [])
        self.assertFalse(self.tools.root.exists())

    def test_replication_requires_registered_target_not_protocol_self_report(self):
        self.register_claim(target=None)
        protocol = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'], reproductionTarget=TARGET)
        self.assert_domain('invalid_tool', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        self.assertEqual(self.store.tool_history(), [])

    def test_replication_ignores_foreign_run_objective_and_unfinished_claims(self):
        self.start(stage='Replication')
        protocol = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'])
        real_snapshot = self.store.snapshot
        state = {'claimGraph': {'claims': [{'id': 'claim:counter', 'version': 1, 'reproductionTarget': TARGET}]}}
        for foreign in (
            {'runId': 'another-run', 'objectiveId': 'objective:1', 'status': 'completed'},
            {'runId': self.run['runId'], 'objectiveId': 'objective:foreign', 'status': 'completed'},
            {'runId': self.run['runId'], 'objectiveId': 'objective:1', 'status': 'running'},
        ):
            snapshot = real_snapshot()
            snapshot['nodes'].append(dict(foreign, result={'state': copy.deepcopy(state)}))
            with self.subTest(foreign=foreign), patch.object(self.store, 'snapshot', return_value=snapshot):
                self.assert_domain('invalid_tool', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        self.assertEqual(self.store.tool_history(), [])

    def test_replication_rejects_incomplete_targets_and_non_fulltext_sources(self):
        self.register_claim()
        protocol = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'])
        original = self.store.snapshot()
        invalid_states = []
        state = next(item['result']['state'] for item in original['nodes'] if item['id'] == self.claim_node['id'])
        for field, value in (
            ('paperId', 'paper:foreign'), ('evidenceIds', []), ('evidenceIds', ['missing']),
            ('expected', 'NaN'), ('expected', '2 ms'), ('expected', 'Infinity'),
            ('tolerance', '-1'), ('tolerance', '5%'), ('conditions', ''), ('unit', 1),
        ):
            invalid = copy.deepcopy(state)
            invalid['claimGraph']['claims'][0]['reproductionTarget'][field] = value
            invalid_states.append(invalid)
        for field, value in (('type', 'abstract'), ('locator', ''), ('paperId', 'paper:foreign')):
            invalid = copy.deepcopy(state)
            invalid['evidence'][0][field] = value
            invalid_states.append(invalid)
        wrong_version = copy.deepcopy(state)
        wrong_version['claimGraph']['claims'][0]['versions'][0]['reproductionTarget']['expected'] = '999'
        invalid_states.append(wrong_version)
        for invalid in invalid_states:
            snapshot = copy.deepcopy(original)
            prior = next(item for item in snapshot['nodes'] if item['id'] == self.claim_node['id'])
            prior['result']['state'] = invalid
            with self.subTest(state=invalid), patch.object(self.store, 'snapshot', return_value=snapshot):
                self.assert_domain('invalid_tool', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        self.assertEqual(self.store.tool_history(), [])

    def test_latest_state_cannot_revive_removed_claim_or_relabel_cached_version(self):
        self.register_claim()
        protocol = dict(copy.deepcopy(PROTOCOL), conditions=TARGET['conditions'])
        result = self.execute(protocol=protocol, claimId='claim:counter')
        original = self.store.snapshot()
        prior = next(item for item in original['nodes'] if item['id'] == self.claim_node['id'])
        later = copy.deepcopy(prior)
        later['id'] = self.run['runId'] + ':later-review'
        later['result']['state']['claimGraph']['claims'] = []
        snapshot = copy.deepcopy(original)
        snapshot['nodes'].append(later)
        with patch.object(self.store, 'snapshot', return_value=snapshot):
            self.assert_domain('invalid_tool', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        later = copy.deepcopy(prior)
        later['id'] = self.run['runId'] + ':later-review'
        claim = later['result']['state']['claimGraph']['claims'][0]
        claim['version'] += 1
        claim['reproductionTarget']['expected'] = '3'
        claim['versions'].append({
            'version': claim['version'], 'reproductionTarget': copy.deepcopy(claim['reproductionTarget']),
        })
        snapshot = copy.deepcopy(original)
        snapshot['nodes'].append(later)
        with patch.object(self.store, 'snapshot', return_value=snapshot):
            self.assert_domain('invalid_receipt', lambda: self.execute(protocol=protocol, claimId='claim:counter'))
        self.assertEqual(self.store.tool_history()[0]['result']['claimVersion'], result['claimVersion'])
        self.assertEqual(len(self.store.tool_history()), 1)

    def test_experiment_claim_binding_is_optional_and_preserves_registered_version(self):
        self.register_claim(target=None, stage='Experiment')
        result = self.execute(claimId='claim:counter')
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['claimVersion'], 3)
        self.assertIsNone(result['reproductionTarget'])


if __name__ == '__main__':
    unittest.main()
