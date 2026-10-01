import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import sys
from types import SimpleNamespace

from research_swarm.process_scope import ProcessScope

from research_swarm.experiment_jobs import ExperimentJobs
from research_swarm.research_materials import ResearchMaterials
from research_swarm.local_tools import LocalResearchTools


def wait_job(jobs, job_id, statuses=('completed', 'failed', 'cancelled', 'timed_out', 'interrupted'), timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get(job_id)
        if job['status'] in statuses:
            return job
        time.sleep(.03)
    raise AssertionError(jobs.get(job_id))


class ExperimentJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = ExperimentJobs(self.root)

    def tearDown(self):
        self.jobs.close()
        self.temp.cleanup()

    def test_real_process_receipt_artifacts_and_secret_free_environment(self):
        os.environ['TEST_JOB_API_KEY'] = 'do-not-inherit'
        try:
            job = self.jobs.submit({'code': 'import os,pathlib,json\nassert "TEST_JOB_API_KEY" not in os.environ\npathlib.Path("metrics.json").write_text(json.dumps({"score":3}))\nprint("真实实验")', 'timeoutSeconds': 86400, 'protocolId': 'protocol:1'})
            done = wait_job(self.jobs, job['id'])
        finally:
            os.environ.pop('TEST_JOB_API_KEY', None)
        self.assertEqual(done['status'], 'completed')
        self.assertIn('真实实验', self.jobs.logs(job['id'])['text'])
        receipt = done['attempts'][-1]['receipt']
        data = json.loads((self.root / receipt['path']).read_text('utf-8'))
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['protocolId'], 'protocol:1')
        self.assertEqual(data['metrics'], {'score': 3})
        self.assertTrue(data['environment']['python'])
        self.assertEqual(data['environmentSha256'], hashlib.sha256((self.root / data['environmentPath']).read_bytes()).hexdigest())
        self.assertIn('packages', data['environment'])
        self.assertEqual(receipt['sha256'], hashlib.sha256((self.root / receipt['path']).read_bytes()).hexdigest())
        self.assertTrue(data['artifacts'])
        self.jobs.close()
        self.jobs = ExperimentJobs(self.root)
        self.assertEqual(self.jobs.get(job['id'])['status'], 'completed')

    def test_queue_cancellation_and_revision_guard(self):
        first = self.jobs.submit({'code': 'import time\ntime.sleep(60)'})
        wait_job(self.jobs, first['id'], ('running',))
        queued = self.jobs.submit({'code': 'print("queued")'})
        self.assertEqual(queued['status'], 'queued')
        with self.assertRaises(ValueError):
            self.jobs.action(queued['id'], 'cancel', queued['revision'] + 1)
        cancelled = self.jobs.action(queued['id'], 'cancel', queued['revision'])
        self.assertEqual(cancelled['status'], 'cancelled')
        first = self.jobs.get(first['id'])
        self.jobs.action(first['id'], 'cancel', first['revision'])
        self.assertEqual(wait_job(self.jobs, first['id'])['status'], 'cancelled')
        self.assertFalse(self.jobs.get(queued['id'])['attempts'])

    def test_failed_retry_retains_prior_receipt(self):
        job = self.jobs.submit({'code': 'raise RuntimeError("real failure")'})
        failed = wait_job(self.jobs, job['id'])
        self.assertEqual(failed['status'], 'failed')
        retried = self.jobs.action(job['id'], 'retry', failed['revision'])
        done = wait_job(self.jobs, retried['id'])
        self.assertEqual(len(done['attempts']), 2)
        self.assertEqual(done['attempts'][0]['receipt']['sha256'], failed['attempts'][0]['receipt']['sha256'])

    def test_actual_checkpoint_resume_and_tamper_rejection(self):
        job = self.jobs.submit({'code': 'from pathlib import Path\nPath("state.json").write_text("41")\nraise RuntimeError("stop")', 'checkpoint': {'path': 'state.json', 'resumeCode': 'from pathlib import Path\nprint(int(Path("state.json").read_text())+1)'}})
        failed = wait_job(self.jobs, job['id'])
        self.assertTrue(failed['checkpoint']['sha256'])
        resumed = self.jobs.action(job['id'], 'resume', failed['revision'])
        done = wait_job(self.jobs, resumed['id'])
        self.assertEqual(done['status'], 'completed')
        self.assertIn('42', self.jobs.logs(job['id'])['text'])
        self.assertEqual(done['attempts'][-1]['mode'], 'resume')
        (self.root / done['checkpoint']['path']).write_text('tampered')
        with self.assertRaises(ValueError):
            self.jobs.action(job['id'], 'resume', done['revision'])

    def test_shutdown_recovers_as_interrupted_without_claiming_resume(self):
        job = self.jobs.submit({'code': 'import time\ntime.sleep(60)'})
        wait_job(self.jobs, job['id'], ('running',))
        self.jobs.close()
        self.jobs = ExperimentJobs(self.root)
        interrupted = self.jobs.get(job['id'])
        self.assertEqual(interrupted['status'], 'interrupted')
        with self.assertRaises(ValueError):
            self.jobs.action(job['id'], 'resume', interrupted['revision'])
        retried = self.jobs.action(job['id'], 'retry', interrupted['revision'])
        self.assertEqual(retried['resumeMode'], 'restart')

    def test_material_is_copied_to_attempt_and_missing_material_is_rejected(self):
        material = ResearchMaterials(self.root).add({'name': 'input.txt', 'text': 'science'})
        job = self.jobs.submit({'code': 'from pathlib import Path\nprint(Path("inputs/input.txt").read_text())', 'materialIds': [material['id']]})
        done = wait_job(self.jobs, job['id'])
        self.assertEqual(done['status'], 'completed')
        self.assertIn('science', self.jobs.logs(job['id'])['text'])
        self.assertEqual(done['inputs'][0]['sha256'], material['sha256'])
        self.assertTrue(done['result']['receipt'])
        second = ResearchMaterials(self.root).add({'name': 'INPUT.TXT', 'text': 'other'})
        with self.assertRaises(ValueError):
            self.jobs.submit({'code': 'print(1)', 'materialIds': [material['id'], second['id']]})
        with self.assertRaises(ValueError):
            self.jobs.submit({'code': 'print(1)', 'materialIds': ['unknown']})

    def test_resource_and_timeout_validation(self):
        for payload in ({'timeoutSeconds': 86401}, {'resources': {'cpuCores': 100000}}, {'resources': {'gpuCount': 1000}}, {'checkpoint': {'path': '../escape', 'resumeCode': 'print(1)'}}, {'env': {'API_KEY': 'secret'}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.jobs.submit({'code': 'print(1)', **payload})

    def test_actual_timeout_and_descendant_cancellation(self):
        job = self.jobs.submit({'code': 'import time\ntime.sleep(60)', 'timeoutSeconds': 1})
        self.assertEqual(wait_job(self.jobs, job['id'])['status'], 'timed_out')
        job = self.jobs.submit({'code': 'import subprocess,sys,time,pathlib\nsubprocess.Popen([sys.executable,"-c",\'import time,pathlib;time.sleep(2);pathlib.Path("escaped.txt").write_text("bad")\'])\npathlib.Path("ready.txt").write_text("ready")\ntime.sleep(60)'})
        started = wait_job(self.jobs, job['id'], ('running',))
        work = self.root / 'runs' / job['id'] / 'attempt-1' / 'work'
        deadline = time.monotonic() + 10
        while not (work / 'ready.txt').exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue((work / 'ready.txt').exists())
        current = self.jobs.get(job['id'])
        self.jobs.action(job['id'], 'cancel', current['revision'])
        cancelled = wait_job(self.jobs, job['id'])
        self.assertEqual(cancelled['status'], 'cancelled')
        time.sleep(2.2)
        self.assertFalse((work / 'escaped.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'Abrupt owner-death cleanup requires Windows kill-on-close Job Objects')
    def test_crashed_executor_recovers_checkpoint_and_interrupted_receipt(self):
        other_root = self.root / 'crash-task'
        code = 'from pathlib import Path\nimport time\nPath("saved.txt").write_text("actual")\nprint("checkpoint-ready",flush=True)\ntime.sleep(60)'
        manager_code = 'import sys,time,json\nfrom research_swarm.experiment_jobs import ExperimentJobs\njobs=ExperimentJobs(sys.argv[1])\njob=jobs.submit(json.loads(sys.argv[2]))\nwhile not jobs.get(job["id"])["attempts"]:time.sleep(.02)\nprint(json.dumps({"id":job["id"],"cwd":jobs.get(job["id"])["attempts"][-1]["workingDirectory"]}),flush=True)\ntime.sleep(60)'
        payload = {'code': code, 'nodeId': 'managed-node', 'nodeVersion': 2, 'round': 3, 'executionToken': 'crashed-host-token',
                   'checkpoint': {'path': 'saved.txt', 'resumeCode': 'from pathlib import Path\nprint(Path("saved.txt").read_text())'}}
        tools = LocalResearchTools(self.root)
        with (self.root / 'manager.out').open('wb') as stdout, (self.root / 'manager.err').open('wb') as stderr:
            scope = ProcessScope([sys.executable, '-u', '-c', manager_code, str(other_root), json.dumps(payload)], cwd=Path.cwd(), env=tools._env(self.root), stdout=stdout, stderr=stderr)
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    output = (self.root / 'manager.out').read_text('utf-8').strip()
                    if output and (other_root / json.loads(output)['cwd'] / 'saved.txt').exists():
                        break
                    time.sleep(.05)
                self.assertTrue(output, (self.root / 'manager.err').read_text('utf-8'))
                declaration = json.loads(output)
                self.assertTrue((other_root / declaration['cwd'] / 'saved.txt').exists())
                output = declaration['id']
            finally:
                scope.close()
        recovered = ExperimentJobs(other_root)
        try:
            job = recovered.get(output)
            self.assertEqual(job['status'], 'interrupted')
            self.assertEqual(job['attempts'][-1]['status'], 'interrupted')
            self.assertEqual(json.loads((other_root / job['attempts'][-1]['receipt']['path']).read_text('utf-8'))['status'], 'interrupted')
            self.assertTrue(job['checkpoint']['sha256'])
            recovered.action(output, 'resume', job['revision'])
            self.assertEqual(wait_job(recovered, output)['status'], 'completed')
            self.assertIn('actual', recovered.logs(output)['text'])
        finally:
            recovered.close()


class MaterialTests(unittest.TestCase):
    def test_reserved_manifest_filename_cannot_overwrite_uploaded_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            materials = ResearchMaterials(Path(temp))
            for name in ('metadata.json', 'METADATA.JSON', 'Metadata.Json'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    materials.add({'name': name, 'text': '{"original":true}'})
            item = materials.add({'name': 'manifest.json', 'text': '{"original":true}'})
            self.assertEqual(materials.path(item['id']).read_text('utf-8'), '{"original":true}')

    def test_intake_hash_is_immutable_and_paths_are_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = ResearchMaterials(root)
            source = root / 'explicit.txt'
            source.write_text('original')
            item = materials.add({'name': 'explicit.txt', 'sourcePath': str(source)})
            source.write_text('changed')
            self.assertEqual(materials.path(item['id']).read_text(), 'original')
            self.assertEqual(item['sha256'], hashlib.sha256(b'original').hexdigest())
            self.assertEqual(ResearchMaterials(root).get(item['id']), item)
            for payload in ({'name': '../escape', 'text': 'x'}, {'name': 'x', 'base64': '!'}, {'name': 'x', 'sourcePath': '../explicit.txt'}, {'name': 'x', 'text': 'x', 'apiKey': 'secret'}, {'name': 'repo', 'repository': {'url': 'https://user:secret@example.com/repo.git', 'commit': 'a'*40}}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    materials.add(payload)

    def test_original_source_path_is_durable_provenance_but_not_in_model_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'original.txt'
            source.write_text('input')
            materials = ResearchMaterials(root)
            material = materials.add({'name': 'input.txt', 'sourcePath': str(source)})
            tools = LocalResearchTools(root)
            result = tools.call('python_run', {'code': 'print("done")'}, {'id': 'execution'}, {'inputMaterials': [{**material, 'path': str(materials.path(material['id']))}]}, lambda _: None)
            self.assertNotIn('sourcePath', result['inputs'][0]['source'])
            receipt = json.loads((root / result['evidence'][0]['locator']).read_text('utf-8'))
            self.assertEqual(receipt['inputs'][0]['source']['sourcePath'], str(source))

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'real').write_text('x')
            try:
                (root / 'link').symlink_to(root / 'real')
            except OSError:
                self.skipTest('OS does not permit test symlinks')
            with self.assertRaises(ValueError):
                ResearchMaterials(root).add({'name': 'x', 'sourcePath': str(root / 'link')})


class LongEngineExecutionTests(unittest.TestCase):
    def test_managed_retry_and_resume_isolate_leftovers_from_previous_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = ExperimentJobs(root)
            try:
                code = 'from pathlib import Path\nassert not Path("undeclared.txt").exists()\nprint("fresh-start")\nPath("undeclared.txt").write_text("leftover")\nPath("state.json").write_text("actual-checkpoint")\nraise RuntimeError("intentional failure")'
                job = jobs.submit({'code': code, 'nodeId': 'execution', 'nodeVersion': 1, 'round': 1,
                                   'executionToken': 'managed-retry-token', 'checkpoint': {'path': 'state.json',
                                   'resumeCode': 'from pathlib import Path\nassert not Path("undeclared.txt").exists()\nassert Path("state.json").read_text()=="actual-checkpoint"\nprint("checkpoint-only")'}})
                first = wait_job(jobs, job['id'])
                self.assertEqual(first['status'], 'failed')
                jobs.action(job['id'], 'retry', first['revision'])
                retried = wait_job(jobs, job['id'])
                self.assertIn('fresh-start', jobs.logs(job['id'])['text'])
                self.assertNotEqual(first['result']['workingDirectory'], retried['result']['workingDirectory'])
                jobs.action(job['id'], 'resume', retried['revision'])
                resumed = wait_job(jobs, job['id'])
                self.assertEqual(resumed['status'], 'completed')
                self.assertIn('checkpoint-only', jobs.logs(job['id'])['text'])
                self.assertNotEqual(retried['result']['workingDirectory'], resumed['result']['workingDirectory'])
            finally:
                jobs.close()

    def test_managed_node_calls_keep_files_and_receipts_but_new_execution_is_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = ExperimentJobs(root)
            try:
                material = jobs.materials.add({'name': 'input.txt', 'text': 'attached'})
                context = {'experimentJobs': jobs, 'executionToken': 'same-host-token', 'round': 2,
                           'executionSettings': {'materialIds': [material['id']]}}
                node = {'id': 'execution', 'version': 3}
                first = jobs.tools.call('python_run', {'code': 'from pathlib import Path\nPath("measurements.csv").write_text("latency\\n1\\n")'}, node, context, lambda _: None)
                original = (root / first['artifacts'][0]['path']).read_bytes()
                context['executionSettings']['materialIds'] = []
                second = jobs.tools.call('python_run', {'code': 'from pathlib import Path\nassert not Path("inputs/input.txt").exists()\nprint(Path("measurements.csv").read_text())\nPath("measurements.csv").write_text("latency\\n2\\n")'}, node, context, lambda _: None)
                self.assertEqual(second['status'], 'completed')
                self.assertIn('1', second['stdout'])
                self.assertEqual(first['workingDirectory'], second['workingDirectory'])
                self.assertEqual((root / first['artifacts'][0]['path']).read_bytes(), original)
                self.assertNotEqual(first['receipt'], second['receipt'])
                self.assertEqual(jobs.get(second['jobId'])['attempts'][-1]['workingDirectory'], second['workingDirectory'])
                new_context = {**context, 'executionToken': 'next-host-token'}
                third = jobs.tools.call('python_run', {'code': 'from pathlib import Path\nassert not Path("measurements.csv").exists()'}, node, new_context, lambda _: None)
                self.assertEqual(third['status'], 'completed')
                self.assertNotEqual(third['workingDirectory'], first['workingDirectory'])
            finally:
                jobs.close()

    def test_explicit_execution_settings_preserve_standard_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = LocalResearchTools(root)
            result = tools.call('python_run', {'code': 'print("long-configured")', 'timeoutSeconds': 86400}, {'id': 'execution'}, {'executionSettings': {'maxTimeoutSeconds': 86400}}, lambda _: None)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['timeoutSeconds'], 86400)
            self.assertTrue(result['evidence'])
            self.assertEqual(result['environment']['dependencyIsolation'], 'task-local venv')
            self.assertTrue((root / result['environmentPath']).is_file())

    def test_managed_execution_preserves_gate_receipt_and_callbacks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = ExperimentJobs(root)
            try:
                tools = LocalResearchTools(root)
                started, receipts = [], []
                result = tools.call('python_run', {'code': 'from pathlib import Path\nPath("raw.csv").write_text("seed,score\\n1,2\\n")\nprint("managed")'}, {'id': 'execution', 'version': 3}, {'experimentJobs': jobs, 'record_execution_started': started.append, 'record_execution': receipts.append, 'executionToken': 'host-issued'}, lambda _: None)
                self.assertTrue(result.get('jobId'))
                self.assertTrue(started)
                self.assertEqual(receipts[0]['executionToken'], 'host-issued')
                self.assertEqual(result['nodeVersion'], 3)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(tools.call('artifact_read', {'path': result['artifacts'][0]['path']}, {'id': 'execution'}, {}, lambda _: None)['text'].splitlines(), ['seed,score', '1,2'])
                self.assertTrue((root / result['evidence'][0]['locator']).is_file())
            finally:
                jobs.close()

    def test_validated_input_materials_copied_without_becoming_output_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            materials = ResearchMaterials(root / 'experiment-workspace')
            item = materials.add({'name': 'observations.csv', 'text': 'score\n5\n'})
            manifest = {**item, 'path': str(materials.path(item['id']))}
            tools = LocalResearchTools(root / 'runtime')
            result = tools.call('python_run', {'code': 'from pathlib import Path\np=Path("inputs/observations.csv")\nprint(p.read_text())\np.write_text("changed")'}, {'id': 'execution'}, {'inputMaterials': [manifest], 'inputMaterialsRoot': str(materials.root)}, lambda _: None)
            self.assertEqual(result['status'], 'completed')

            self.assertIn('5', result['stdout'])
            self.assertEqual(result['inputs'][0]['sha256'], item['sha256'])
            self.assertEqual(materials.path(item['id']).read_text(), 'score\n5\n')
            self.assertEqual(result['artifacts'], [])
            with self.assertRaises(ValueError):
                tools.call('python_run', {'code': 'print(1)'}, {'id': 'execution'}, {'inputMaterials': [{**manifest, 'sha256': '0'*64}], 'inputMaterialsRoot': str(materials.root)}, lambda _: None)
            result = tools.call('python_run', {'code': 'from pathlib import Path\nassert not Path("inputs/observations.csv").exists()'}, {'id': 'execution'}, {}, lambda _: None)
            self.assertEqual(result['status'], 'completed')

    def test_managed_measurements_pass_existing_csv_gate_and_tampering_is_rejected(self):
        from research_swarm.research_cycle import _experiment_data
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = ExperimentJobs(root)
            try:
                protocol = {'id': 'P-managed', 'replicates': 3, 'validityChecks': ['sum correct'],
                            'outputSchema': {'latency': 'number'}, 'rawData': {'file': 'raw.csv', 'metrics': {'latency': {'column': 'latency', 'statistic': 'median'}}}}
                code = 'import csv,json,time,statistics\nfrom pathlib import Path\nvalues=[]\nfor i in range(3):\n start=time.perf_counter()\n value=sum(range(10000))\n values.append((time.perf_counter()-start)*1000)\nwith open("raw.csv","w",newline="") as f:\n writer=csv.writer(f)\n writer.writerow(["latency"])\n writer.writerows([[v] for v in values])\nPath("metrics.json").write_text(json.dumps({"protocolId":"P-managed","replicates":3,"latency":statistics.median(values),"validation":{"passed":True,"checks":[{"name":"sum correct","passed":value==49995000}]}}))'
                tools = jobs.tools
                execution = tools.call('python_run', {'code': code}, {'id': 'execution'}, {'experimentJobs': jobs, 'executionToken': 'host-token'}, lambda _: None)
                execution['evidenceIds'] = [item['id'] for item in execution['evidence']]
                token = {'toolExecutions': [execution]}
                measured, ids = _experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol)
                self.assertIsNotNone(measured)
                self.assertGreater(measured['latency'], 0)
                self.assertEqual(ids, execution['evidenceIds'])
                raw = next(item for item in execution['artifacts'] if item['name'] == 'raw.csv')
                (root / raw['path']).write_text('latency\n999\n999\n999\n')
                errors = []
                self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol, errors), (None, []))
                self.assertTrue(errors)
            finally:
                jobs.close()



if __name__ == '__main__':
    unittest.main()
