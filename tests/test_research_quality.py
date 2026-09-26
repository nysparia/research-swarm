"""Regressions from an actual research run: empty scopes, fanout and execution."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from research_swarm.engine import Engine
from research_swarm.runner import ResearchRunner
from research_swarm.tools import ResearchTools
from test_engine import sample_library


class ResearchQualityTests(unittest.TestCase):
    def test_model_cannot_claim_new_experiment_without_executing(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            class Provider:
                def chat(self, *args, **kwargs):
                    return json.dumps({'summary':'已经实测达到99%并修复所有错误', 'claims':[], 'structured':{}, 'unresolved':[]})
            runner = ResearchRunner(Provider(), Path(temp), local_tools=LocalResearchTools(Path(temp), python_executable=sys.executable))
            result=runner({'id':'unit','kind':'experiment','phase':'execute','input':{}},
                {'library':sample_library(),'mode':'llm','requirements':[],'children':[]},lambda _:None)
            self.assertEqual(result['structured']['status'], 'needs_execution')
            self.assertNotIn('99%', result['summary'])
            self.assertEqual(result['claims'], [])
            self.assertIn('未产生', result['summary'])

    def test_unassigned_empty_ids_inherit_materials_but_explicit_empty_scope_does_not(self):
        runner = ResearchRunner(None, Path('.'))
        context = {'library': sample_library()}
        self.assertEqual(len(runner._papers({'input': {'paperIds': []}}, context)), 2)
        self.assertEqual(runner._papers({'input': {'paperIds': [], 'paperScopeExplicit': True}}, context), [])

    def test_library_search_does_not_report_unrelated_papers_as_matches(self):
        result = ResearchTools(sample_library()).call('paper_search', {'query': 'unrelatedxyz'})
        self.assertEqual(result['papers'], [])

    def test_model_experiment_is_dispatched_to_model_and_local_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            class Provider:
                def chat(self, messages, **kwargs):
                    calls.append(messages)
                    return json.dumps({'summary': '实验需要实际执行', 'claims': [], 'unresolved': []})
            runner = ResearchRunner(Provider(), Path(temp))
            result = runner({'id': 'ex', 'kind': 'experiment', 'phase': 'execute', 'input': {}},
                {'library': sample_library(), 'mode': 'llm', 'requirements': [], 'children': []}, lambda _: None)
            self.assertTrue(calls, 'experiment tasks must not short-circuit to missing_input')
            self.assertEqual(result['structured']['status'], 'needs_execution')

    def test_fanout_at_capacity_executes_existing_nodes_without_repeatable_failure(self):
        def runner(node, context, log):
            result = {'summary': '范围内执行', 'claims': [], 'evidenceIds': [], 'structured': {}, 'unresolved': []}
            if node['phase'] == 'plan':
                result['children'] = [{'title': '子任务' + str(i), 'description': '核验', 'acceptance': '原始结果', 'kind': 'research'} for i in range(4)]
            return result
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(sample_library(), Path(temp)/'state.sqlite', runner=runner, max_workers=3, workflow='autonomous')
            engine.MAX_TASKS = 6
            try:
                engine.command('start-autonomous', {})
                deadline = time.monotonic()+8
                while time.monotonic() < deadline:
                    state = engine.snapshot()
                    if state['status'] in ('completed', 'failed'): break
                    time.sleep(.02)
                self.assertEqual(state['status'], 'completed')
                self.assertLessEqual(sum(n['active'] for n in state['nodes']), 6)
                self.assertTrue(any(n['phase']=='execute' for n in state['nodes'] if n['active']))
                self.assertEqual(state['report']['structured']['quality']['status'], 'incomplete')
                self.assertTrue(state['report']['unresolved'])
            finally: engine.close()

    def test_node_error_and_failed_attempt_are_returned_without_hunting_through_logs(self):
        def fail(*args): raise RuntimeError('specific execution failure')
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(sample_library(), Path(temp)/'state.sqlite', runner=fail, workflow='autonomous')
            try:
                engine.command('start-autonomous', {})
                deadline = time.monotonic()+5
                while time.monotonic() < deadline:
                    state=engine.snapshot()
                    if state['status']=='failed': break
                    time.sleep(.02)
                node=next(n for n in state['nodes'] if n['status']=='failed')
                self.assertIn('specific execution failure', node['error']['message'])
                self.assertTrue(any(h.get('error') for h in engine.node_history(node['id'])))
            finally: engine.close()

    def test_report_marks_unexecuted_experiments_as_incomplete(self):
        def runner(node, context, log):
            result={'summary': '未执行实验', 'claims': [], 'evidenceIds': [], 'structured': {}, 'unresolved': []}
            if node['phase']=='plan':
                result['children']=[{'title':'实验','description':'本机测量','acceptance':'原始指标','kind':'experiment'}]
            elif node['kind']=='experiment': result['structured']={'status':'missing_input'}
            return result
        with tempfile.TemporaryDirectory() as temp:
            engine=Engine(sample_library(),Path(temp)/'state.sqlite',runner=runner,workflow='autonomous')
            try:
                engine.command('start-autonomous',{})
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    state=engine.snapshot()
                    if state['status']=='completed':break
                    time.sleep(.02)
                quality=state['report']['structured']['quality']
                self.assertEqual(quality['status'],'incomplete')
                self.assertEqual(len(quality['unexecutedExperiments']),1)
            finally:engine.close()


class LocalExecutionTests(unittest.TestCase):
    def test_shutdown_drains_local_receipt_and_restart_recovers_an_orphan(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = LocalResearchTools(root, python_executable=sys.executable)
            def runner(node, context, log):
                tool.call('python_run', {'code':'from pathlib import Path\nimport time\nPath("ready.txt").write_text("ready")\ntime.sleep(8)'}, node, context, log)
                return {'summary':'cancelled','claims':[]}
            engine = Engine(sample_library(), root/'state.sqlite', runner=runner, workflow='autonomous')
            engine.command('start-autonomous', {})
            deadline = time.monotonic()+4
            while time.monotonic()<deadline and not list(root.rglob('ready.txt')): time.sleep(.02)
            self.assertTrue(list(root.rglob('ready.txt')))
            started = time.monotonic()
            engine.close()
            self.assertLess(time.monotonic()-started, 3.5)
            restored = Engine(sample_library(), root/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                observations = [h for h in restored.node_history('central') if h.get('type')=='tool-executed']
                self.assertEqual(len(observations), 1)
                self.assertEqual(observations[0]['execution']['status'], 'cancelled')
            finally: restored.close()
            # Simulate a crash between atomic receipt publication and DB commit.
            orphan = root/'runs/local-orphan/receipt.json'
            orphan.parent.mkdir()
            orphan.write_text(json.dumps({'nodeId':'central','nodeVersion':1,'round':1,
                'executionToken':'run:orphan','tool':'python_run','status':'cancelled'}))
            restored = Engine(sample_library(), root/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                entry = next(h for h in restored.node_history('central') if h.get('executionToken')=='run:orphan')
                self.assertFalse(entry['valid'])
                self.assertTrue(entry['recovered'])
            finally: restored.close()

    def test_pause_immediately_followed_by_close_drains_cancelled_receipt(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            tool=LocalResearchTools(root, python_executable=sys.executable)
            original=tool._process
            def delayed_receipt(*args, **kwargs):
                result=original(*args, **kwargs)
                time.sleep(.3)
                return result
            tool._process=delayed_receipt
            def runner(node, context, log):
                tool.call('python_run', {'code':'from pathlib import Path\nimport time\nPath("ready.txt").write_text("ready")\ntime.sleep(8)'}, node, context, log)
                return {'summary':'cancelled','claims':[]}
            engine=Engine(sample_library(),root/'state.sqlite',runner=runner,workflow='autonomous')
            engine.command('start-autonomous',{})
            deadline=time.monotonic()+4
            while time.monotonic()<deadline and not list(root.rglob('ready.txt')):time.sleep(.02)
            self.assertTrue(list(root.rglob('ready.txt')))
            engine.command('pause',{})
            engine.close()
            restored=Engine(sample_library(),root/'state.sqlite',runner=runner,workflow='autonomous')
            try:
                entry=next(h for h in restored.node_history('central') if h.get('type')=='tool-executed')
                self.assertEqual(entry['execution']['status'],'cancelled')
                self.assertFalse(entry['valid'])
            finally:restored.close()

    def test_paused_attempt_keeps_receipt_without_publishing_stale_evidence(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool = LocalResearchTools(Path(temp), python_executable=sys.executable)
            finished = threading.Event()
            def runner(node, context, log):
                result = tool.call('python_run', {'code':'from pathlib import Path\nimport time\nPath("started.txt").write_text("started")\ntime.sleep(8)'}, node, context, log)
                finished.set()
                return {'summary':'旧执行不得进入新结果','claims':[]}
            engine = Engine(sample_library(), Path(temp)/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                engine.command('start-autonomous', {})
                deadline = time.monotonic()+4
                while time.monotonic()<deadline and not list(Path(temp).rglob('started.txt')): time.sleep(.02)
                self.assertTrue(list(Path(temp).rglob('started.txt')))
                engine.command('pause', {})
                self.assertTrue(finished.wait(3))
                state = engine.snapshot()
                entry = next(h for h in engine.node_history('central') if h.get('type')=='tool-executed')
                self.assertFalse(entry['valid'])
                self.assertEqual(entry['execution']['status'], 'cancelled')
                self.assertLess(entry['version'], next(n for n in state['nodes'] if n['id']=='central')['version'])
                self.assertFalse(any(e.get('extractor')=='local_process' for e in state['evidence']))
                self.assertTrue((Path(temp)/entry['execution']['receipt']).is_file())
            finally: engine.close()

    def test_children_are_stopped_even_when_parent_exits_successfully(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool = LocalResearchTools(Path(temp), python_executable=sys.executable)
            code = "import subprocess,sys\nsubprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(1.1);Path('late.txt').write_text('late')\"])"
            result = tool.call('python_run', {'code': code}, {'id': 'unit'}, {'round': 1}, lambda _: None)
            self.assertEqual(result['status'], 'completed')
            time.sleep(1.3)
            self.assertFalse(list(Path(temp).rglob('late.txt')), 'descendants must not outlive their execution receipt')

    def test_completed_observations_survive_later_provider_failure_and_restart(self):
        from research_swarm.local_tools import LocalResearchTools
        from research_swarm.server import export_bundle
        import io, zipfile
        with tempfile.TemporaryDirectory() as temp:
            class Provider:
                count = 0
                def chat(self, *args, **kwargs):
                    self.count += 1
                    if self.count == 1:
                        return json.dumps({'toolCalls':[{'name':'python_run','arguments':{'code':'from pathlib import Path\nPath("metrics.json").write_text("{\\"score\\":1}")'}}]})
                    raise RuntimeError('provider unavailable after experiment')
            runner = ResearchRunner(Provider(), Path(temp), local_tools=LocalResearchTools(Path(temp), python_executable=sys.executable))
            engine = Engine(sample_library(), Path(temp)/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                engine.command('start-autonomous', {'mode':'llm'})
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    state = engine.snapshot()
                    if state['status']=='failed': break
                    time.sleep(.02)
                self.assertEqual(state['status'], 'failed')
                observations = [h for h in engine.node_history('central') if h.get('type')=='tool-executed']
                self.assertEqual(len(observations), 1)
                self.assertTrue(observations[0]['executionToken'])
                self.assertEqual(observations[0]['version'], 1)
                evidence = next(e for e in state['evidence'] if e.get('extractor')=='local_process')
                state['report']['ready'] = True
                archive = zipfile.ZipFile(io.BytesIO(export_bundle(state, Path(temp))))
                self.assertIn(evidence['locator'], archive.namelist())
                self.assertTrue(any(name.endswith('/metrics.json') for name in archive.namelist()))
            finally: engine.close()
            reopened = Engine(sample_library(), Path(temp)/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                self.assertTrue(any(h.get('execution') for h in reopened.node_history('central')))
            finally: reopened.close()

    def test_pause_cancels_process_before_later_writes_and_records_it(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool=LocalResearchTools(Path(temp),python_executable=sys.executable)
            stopped=threading.Event()
            timer=threading.Timer(.5,stopped.set)
            timer.start()
            try:
                result=tool.call('python_run',{'code':'import time\nfrom pathlib import Path\ntime.sleep(5)\nPath("late.txt").write_text("should not happen")'},
                    {'id':'unit'},{'round':1,'cancelled':stopped.is_set},lambda _:None)
            finally:timer.cancel()
            self.assertEqual(result['status'],'cancelled')
            self.assertFalse(list(Path(temp).rglob('late.txt')))

    def test_raw_artifacts_and_script_are_in_export_and_cannot_be_read_outside_runs(self):
        from research_swarm.local_tools import LocalResearchTools
        from research_swarm.server import export_bundle
        import zipfile,io
        with tempfile.TemporaryDirectory() as temp:
            tool=LocalResearchTools(Path(temp),python_executable=sys.executable)
            result=tool.call('python_run',{'code':'from pathlib import Path\nPath("data.json").write_text("{}")\nprint("raw metric")'},
                {'id':'unit'},{'round':1},lambda _:None)
            archive=zipfile.ZipFile(io.BytesIO(export_bundle({'report':{'ready':True},'evidence':result['evidence']},Path(temp))))
            self.assertIn(result['script'],archive.namelist())
            self.assertIn(result['stdoutPath'],archive.namelist())
            self.assertIn(result['artifacts'][0]['path'],archive.namelist())
            self.assertIn('raw metric',tool.call('artifact_read',{'path':result['stdoutPath']},{'id':'unit'},{},lambda _:None)['text'])
            with self.assertRaises(ValueError):
                tool.call('artifact_read',{'path':'../config.local.json'},{'id':'unit'},{},lambda _:None)

    def test_real_python_process_has_receipt_files_and_clean_environment(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'DEEPSEEK_API_KEY':'test-secret-never-inherit'}):
            tool=LocalResearchTools(Path(temp), python_executable=sys.executable)
            result=tool.call('python_run',{'code':'import os,json\nfrom pathlib import Path\nPath("metric.json").write_text("{\\"value\\":42}")\nprint(json.dumps({"value":42,"credentialPresent":bool(os.getenv("DEEPSEEK_API_KEY"))}))'},
                {'id':'unit'},{'round':1},lambda _:None)
            self.assertEqual(result['returnCode'],0)
            self.assertEqual(json.loads(result['stdout'])['credentialPresent'],False)
            evidence=result['evidence'][0]
            receipt=Path(temp)/evidence['locator']
            self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(),evidence['sha256'])
            self.assertTrue(any(a['name']=='metric.json' for a in result['artifacts']))
            self.assertTrue((Path(temp)/result['script']).is_file())

    def test_dependency_caches_cannot_crowd_out_experiment_artifacts(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool=LocalResearchTools(Path(temp), python_executable=sys.executable)
            code='from pathlib import Path\np=Path("pip/cache");p.mkdir(parents=True)\nfor i in range(40):(p/str(i)).write_text("cache")\nPath("z-metrics.json").write_text("{}")'
            result=tool.call('python_run',{'code':code},{'id':'unit'},{'round':1},lambda _:None)
            self.assertEqual([a['name'] for a in result['artifacts']],['z-metrics.json'])

    def test_process_error_and_timeout_are_observations_not_success(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool=LocalResearchTools(Path(temp), python_executable=sys.executable)
            result=tool.call('python_run',{'code':'raise ValueError("broken unit")'}, {'id':'unit'},{'round':1},lambda _:None)
            self.assertNotEqual(result['returnCode'],0)
            self.assertIn('broken unit',result['stderr'])
            result=tool.call('python_run',{'code':'import time; time.sleep(5)','timeoutSeconds':1}, {'id':'unit'},{'round':1},lambda _:None)
            self.assertEqual(result['status'],'timed_out')

    def test_install_rejects_urls_and_command_options(self):
        from research_swarm.local_tools import LocalResearchTools
        with tempfile.TemporaryDirectory() as temp:
            tool=LocalResearchTools(Path(temp), python_executable=sys.executable)
            for package in ['https://example.com/script.whl','--index-url','unknown-package']:
                with self.assertRaises(ValueError):
                    tool.call('python_install',{'packages':[package]}, {'id':'unit'},{'round':1},lambda _:None)
