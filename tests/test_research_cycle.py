import copy
import tempfile
import unittest
from pathlib import Path

from research_swarm.engine import Engine
from research_swarm.local_tools import LocalResearchTools
from test_engine import sample_library as base_library
from test_workspace import wait_until


def result(structured, evidence=None, summary='阶段结果'):
    return {'summary': summary, 'evidenceIds': evidence or [], 'claims': [], 'structured': structured, 'unresolved': []}


def cycle_library():
    """Synthetic full-text observations for orchestration, not semantic evaluation."""
    library = base_library()
    for evidence in library['evidence']:
        evidence.update(type='full_text', locator='fixture page 1', quote='人工编写的调度测试观察，不代表真实科研结论。')
    return library


sample_library = cycle_library


def topic_candidates(evidence_id='101'):
    return [dict(id='T'+str(i), title=title, question=question, researchGap=gap,
                 rationale='已有观察尚不能回答这个问题', evidenceIds=[evidence_id],
                 minimalStudy='固定其余变量后比较条件', feasibility='CPU 小规模可行', limitations='仅合成调度测试证据')
            for i, (title, question, gap) in enumerate([
                ('索引选择性边界', '选择性如何影响查询延迟？', '缺少不同选择性的可比数据'),
                ('索引维护代价', '更新频率如何影响总成本？', '缺少写入成本观察'),
                ('缓存调节效应', '冷热缓存是否改变索引收益？', '未控制缓存条件')], 1)]


class CycleRunner:
    def __init__(self, experiment=False, refute=False, new_hypothesis=False):
        self.calls = []; self.experiment = experiment; self.refute = refute; self.new_hypothesis = new_hypothesis
        self.failed_once = False

    def __call__(self, node, context, log):
        step = node['input'].get('researchStep')
        self.calls.append((step, node['phase'], copy.deepcopy(context.get('children', []))))
        if step == 'background':
            return result({'background': {'context': '背景', 'boundaries': 'CPU 小数据', 'relatedFields': ['database']}})
        if step == 'literature':
            self.assert_background = context['upstreamResults'][0]['output']['structured']['background']
            return result({'literatureReview': {'summary': '已有数据', 'gaps': ['未知规模效应'], 'evidenceIds': ['101']}}, ['101'])
        if step == 'topic':
            topic = {'title': '索引收益条件', 'question': '在什么条件下有效', 'evidenceIds': ['101'], 'rationale': '需要比较选择性'}
            mode = node['input'].get('topicMode', 'explore')
            if mode in ('direct', 'reproduction'):
                return result({'selectedTopic' if mode == 'direct' else 'researchTopic': topic}, ['101'])
            candidates = topic_candidates()
            structured = {'topicCandidates': candidates}
            if mode == 'delegate':
                structured['topicRecommendation'] = {'candidateId': candidates[1]['id'], 'reason': '该缺口在当前资源下更易取得数据'}
            return result(structured, ['101'])
        if step == 'hypothesis_generation':
            return result({'hypotheses': [{'id': 'H1', 'statement': '索引降低查询延迟', 'falsification': '相同数据下未改善', 'reason': '检验已有观察', 'evidenceIds': ['101']}]}, ['101'])
        if step == 'hypothesis' and node['phase'] != 'aggregate':
            return result({'dataRequests': [{'metric': '查询延迟', 'definition': '相同查询的 p95 ms', 'purpose': '比较有无索引', 'acceptance': '同数据、重复测量、原始计时', 'scope': '本机 CPU'}]})
        if step == 'data_request' and node['phase'] != 'aggregate':
            return result({'dataDemand': node['input']['dataDemand']})
        if step == 'data_source' and node['phase'] != 'aggregate':
            return result({'dataAssessment': {'sufficient': not self.experiment, 'reason': '有对应实测' if not self.experiment else '现有数据不可比', 'evidenceIds': [] if self.experiment else ['101']}} , [] if self.experiment else ['101'])
        if step == 'experiment_design':
            protocol = {'id': 'P2' if self.failed_once else 'P1', 'hypothesis': 'H1', 'method': '同数据有无索引',
                'dataset': '本机合成数据', 'baselines': ['scan'], 'metrics': ['p95 ms'], 'replicates': 5,
                'validityChecks': ['相同结果', '固定种子'], 'acceptance': '保留逐次计时', 'outputSchema': {'latency': 'number'},
                'rawData': {'file': 'measurements.csv', 'metrics': {'latency': {'column': 'latency', 'statistic': 'median'}}}}
            protocol['hypothesis'] = node['input']['hypothesisId']
            if node['phase'] == 'aggregate':
                latest = context['children'][-1]['output']['structured']['experimentRun']
                if latest.get('verified'):
                    for path in (latest['script'], latest['metricsArtifact'], latest['rawDataArtifact']):
                        self.tools.call('artifact_read', {'path': path}, node, context, log)
                return result({'experimentReview': {'valid': latest['status'] == 'completed', 'reason': '已检查协议与数据', 'evidenceIds': latest.get('evidenceIds', [])}, 'experimentProtocol': protocol,
                    'review': {'role': 'redteam', 'independent': True, 'status': 'completed', 'identity': {'model': 'test-double-redteam'}}}, latest.get('evidenceIds', []))
            return result({'experimentProtocol': protocol})
        if step == 'experiment_execution':
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError('测试实验：数据形状错误，需要设计节点修改')
            import json
            fixture = {'protocolId': node['input']['experimentProtocol']['id'], 'latency': 1.0, 'replicates': 5,
                       'validation': {'passed': True, 'checks': [{'name': name, 'passed': True} for name in node['input']['experimentProtocol']['validityChecks']]}}
            code = ('import csv,json,time,statistics\nfrom pathlib import Path\n'
                    'values=[]\nfor _ in range(5):\n start=time.perf_counter()\n sum(range(1000))\n values.append((time.perf_counter()-start)*1000)\n'
                    'with open("measurements.csv","w",newline="") as f:\n w=csv.writer(f)\n w.writerow(["latency"])\n w.writerows([[v] for v in values])\n'
                    'data=' + repr(fixture) + '\ndata["latency"]=statistics.median(values)\nPath("metrics.json").write_text(json.dumps(data))\nprint("fixture measured process completed")')
            execution = self.tools.call('python_run', {'code': code}, node, context, log)
            ids = [e['id'] for e in execution['evidence']]
            return result({'experimentRun': {'status': 'completed', 'protocolId': node['input']['experimentProtocol']['id'], 'evidenceIds': ids, 'measurements': {'latency': 1.0}, 'validation': {'passed': True, 'checks': ['boundary fixture']}}}, ids)
        if step in ('data_source', 'data_request'):
            ids = [e for child in context['children'] for e in child['output']['evidenceIds']]
            return result({'evidenceResponse': {'sufficient': bool(ids), 'evidenceIds': ids, 'reason': '子结果已返回'}}, ids)
        if step == 'hypothesis':
            ids = [e for child in context['children'] for e in child['output']['evidenceIds']]
            known = {e['id']: e for e in context['library']['evidence']}
            relations = [{'evidenceId': eid, 'type': 'support', 'polarity': 'against' if self.refute else 'for',
                'reason': '仅验证调度边界的测试替身', 'quality': 'usable', 'quote': known[eid]['quote'],
                'locator': known[eid]['locator'], 'rule': 'verified_measurement' if known[eid]['type'] == 'experiment' else 'direct_statement',
                'confidence': .9} for eid in ids]
            return result({'hypothesisVerdict': {'status': 'inconclusive' if not ids else 'refuted' if self.refute else 'supported', 'evidenceIds': ids, 'reason': '数据与预先定义的判定标准比较', 'limitations': '仅测试范围'},
                'evidenceRelations': relations, 'review': {'role': 'judge', 'independent': True, 'status': 'completed', 'identity': {'model': 'test-double-judge'}}}, ids)
        if step == 'synthesis':
            completed = context['researchCycle']['hypotheses']
            ids = list(dict.fromkeys(e for h in completed for e in (h.get('verdict') or {}).get('evidenceIds', [])))
            hypotheses = []
            if self.new_hypothesis and len(completed) == 1:
                hypotheses = [{'id': 'H2', 'statement': '规模改变收益', 'falsification': '不同规模收益不变', 'reason': '初次结果提出新问题', 'evidenceIds': ['101']}]
            return result({'researchConclusion': {'converged': not hypotheses, 'reason': '范围内的猜想都已检验', 'evidenceIds': ids}, 'hypotheses': hypotheses}, ids)
        raise AssertionError(f'unexpected research step {step}/{node["phase"]}')


class ResearchCycleTests(unittest.TestCase):
    def run_cycle(self, runner):
        temp = tempfile.TemporaryDirectory()
        engine = Engine(sample_library(), Path(temp.name) / 'state.sqlite', runner=runner, workflow='autonomous', max_workers=3)
        runner.tools = LocalResearchTools(Path(temp.name))
        self.addCleanup(temp.cleanup); self.addCleanup(engine.close)
        self._last_engine = engine
        engine.command('start-autonomous', {'mode': 'llm', 'researchCycle': True, 'topicMode': 'direct', 'budgetTier': 'swarm'})
        wait_until(lambda: engine.snapshot()['report'].get('ready') or engine.snapshot()['status'] == 'failed', timeout=5)
        return engine.snapshot()

    def test_existing_data_returns_without_creating_an_experiment_and_top_stages_are_ordered(self):
        runner = CycleRunner()
        state = self.run_cycle(runner)
        self.assertIn('researchCycle', state['project'])
        self.assertEqual(state['project']['researchCycle']['status'], 'converged')
        self.assertEqual([s for s, _, _ in runner.calls[:3]], ['background', 'literature', 'topic'])
        self.assertEqual(state['project']['researchCycle']['hypotheses'][0]['status'], 'supported')
        self.assertFalse(any(n['kind'] == 'experiment' for n in state['nodes']))

    def test_execution_problem_is_reported_to_designer_then_new_protocol_is_executed(self):
        runner = CycleRunner(experiment=True, refute=True)
        state = self.run_cycle(runner)
        self.assertEqual(state['project'].get('researchCycle', {}).get('status'), 'converged')
        self.assertTrue(runner.failed_once)
        designs = [children for step, phase, children in runner.calls if step == 'experiment_design' and phase == 'aggregate']
        self.assertEqual(designs[0][-1]['output']['structured']['experimentRun']['status'], 'problem')
        self.assertEqual(len(designs), 2)
        self.assertEqual(state['project']['researchCycle']['hypotheses'][0]['status'], 'refuted')
        self.assertTrue(any(h['type'] == 'experiment-redesigned' for h in state['history']))

    def test_returned_data_can_generate_another_hypothesis(self):
        state = self.run_cycle(CycleRunner(new_hypothesis=True))
        self.assertEqual(len(state['project'].get('researchCycle', {}).get('hypotheses', [])), 2)
        self.assertEqual(state['project']['researchCycle']['status'], 'converged')

    def test_returned_data_can_request_more_evidence_for_the_same_hypothesis(self):
        class MoreEvidence(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'synthesis' and context['researchCycle']['iteration'] == 1:
                    hypothesis = context['researchCycle']['hypotheses'][0]
                    value['structured']['researchConclusion']['converged'] = False
                    value['structured']['evidenceRevisions'] = [{'hypothesisId': hypothesis['id'], 'reason': '还需验证另一个规模'}]
                return value
        runner = MoreEvidence(); state = self.run_cycle(runner)
        self.assertEqual(len(state['project']['researchCycle']['hypotheses']), 1)
        self.assertEqual(sum(s == 'hypothesis' and phase != 'aggregate' for s, phase, _ in runner.calls), 2)
        self.assertEqual(state['project']['researchCycle']['iteration'], 2)
        self.assertEqual(state['project']['researchCycle']['status'], 'converged')

    def test_compact_budget_still_allows_full_delegation_depth(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(sample_library(), Path(temp) / 'state.sqlite', workflow='autonomous')
            try:
                engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct', 'budgetTier': 'compact'})
                self.assertGreaterEqual(engine._limit('maxDepth'), 5)
            finally:
                engine.close()

    def test_background_changes_invalidate_its_sequential_consumers(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Engine(sample_library(), Path(temp) / 'state.sqlite', workflow='autonomous')
            try:
                engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct'})
                stages = {n['input'].get('researchStep'): n for n in engine.snapshot()['nodes']}
                affected = engine.command('impact', {'nodeId': stages['background']['id'], 'kind': 'modify'})
                self.assertIn(stages['literature']['id'], affected['affectedIds'])
                self.assertIn(stages['topic']['id'], affected['affectedIds'])
            finally:
                engine.close()

    def test_modified_background_regenerates_hypotheses_instead_of_reusing_old_children(self):
        runner = CycleRunner()
        state = self.run_cycle(runner); engine = self._last_engine
        background = next(n for n in state['nodes'] if n['input'].get('researchStep') == 'background')
        old = state['project']['researchCycle']['hypotheses'][0]['nodeId']
        engine.command('intervene', {'expectedRevision': state['revision'], 'nodeId': background['id'], 'kind': 'modify', 'text': '改成另一个边界'})
        engine.command('resume', {})
        wait_until(lambda: engine.snapshot()['report'].get('ready'))
        state = engine.snapshot()
        self.assertEqual(sum(s == 'hypothesis_generation' for s, _, _ in runner.calls), 2)
        self.assertFalse(next(n for n in state['nodes'] if n['id'] == old)['active'])
        self.assertNotEqual(state['project']['researchCycle']['hypotheses'][0]['nodeId'], old)

    def test_global_requirement_edit_keeps_the_typed_research_cycle(self):
        runner = CycleRunner(); state = self.run_cycle(runner); engine = self._last_engine
        engine.command('intervene', {'expectedRevision': state['revision'], 'nodeId': 'central', 'kind': 'modify',
                                    'text': '新的研究需求', 'requirements': [{'id': 'new', 'description': '新的方向', 'acceptance': '数据证据', 'constraints': 'CPU'}]})
        self.assertEqual(engine._get_node('central')['input']['researchStep'], 'hypothesis_generation')
        self.assertEqual(engine.snapshot()['project']['researchCycle']['hypotheses'], [])
        engine.command('resume', {})
        wait_until(lambda: engine.snapshot()['report'].get('ready'))
        self.assertEqual(sum(s == 'background' for s, _, _ in runner.calls), 2)

    def test_returned_evidence_is_narrowed_at_every_level(self):
        class Narrow(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                step = node['input'].get('researchStep')
                if step == 'data_source' and node['phase'] != 'aggregate':
                    value['structured']['dataAssessment']['evidenceIds'] = ['101', '102']
                if step == 'data_request' and node['phase'] == 'aggregate':
                    value['structured']['evidenceResponse']['evidenceIds'] = ['101']
                if step == 'hypothesis' and node['phase'] == 'aggregate':
                    value['structured']['hypothesisVerdict']['evidenceIds'] = ['102']
                return value
        state = self.run_cycle(Narrow())
        self.assertEqual(state['project']['researchCycle']['dataRequests'][0]['evidenceIds'], ['101'])
        self.assertEqual(state['project']['researchCycle']['hypotheses'][0]['status'], 'inconclusive')

    def test_deepen_creates_a_typed_research_branch_instead_of_ignored_generic_work(self):
        runner = CycleRunner(); state = self.run_cycle(runner); engine = self._last_engine
        state = engine.command('pause', {})
        engine.command('intervene', {'expectedRevision': state['revision'], 'nodeId': 'central', 'kind': 'deepen', 'text': '进一步研究查询规模效应'})
        pending = [n for n in engine.snapshot()['nodes'] if n['active'] and n['status'] == 'pending' and n['id'] != 'central']
        self.assertTrue(pending)
        self.assertTrue(all(n['input'].get('researchStep') for n in pending))
        engine.command('resume', {})
        wait_until(lambda: engine.snapshot()['report'].get('ready'))
        # Boundary runner proposes only its old H1; ignored/duplicate work cannot count as convergence.
        self.assertEqual(engine.snapshot()['project']['researchCycle']['status'], 'inconclusive')

    def test_topic_choice_resumes_the_cycle_without_repeating_topic_research(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = CycleRunner()
            engine = Engine(sample_library(), Path(temp) / 'state.sqlite', runner=runner, workflow='autonomous')
            try:
                engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'explore', 'paperResearch': True, 'mode': 'llm'})
                wait_until(lambda: bool(engine.snapshot()['project']['researchCycle'].get('topicSelection')))
                state = engine.snapshot(); decision = state['project']['researchCycle']['topicSelection']
                self.assertEqual(engine._get_node(decision['nodeId'])['input']['researchStep'], 'topic')
                engine.command('topic-selection', {'expectedRevision': state['revision'], 'mode': 'candidate', 'candidateId': 'T1'})
                wait_until(lambda: engine.snapshot()['report'].get('ready'))
                self.assertEqual(sum(s == 'topic' for s, _, _ in runner.calls), 1)
                self.assertEqual(engine.snapshot()['project']['researchCycle']['status'], 'converged')
            finally:
                engine.close()

    def test_claimed_process_success_without_real_data_never_becomes_convergence(self):
        class NoData(CycleRunner):
            def __call__(self, node, context, log):
                if node['input'].get('researchStep') == 'experiment_execution':
                    self.failed_once = True
                    return result({'experimentRun': {'status': 'completed', 'protocolId': node['input']['experimentProtocol']['id'],
                        'measurements': {'latency': 0.001}, 'validation': {'passed': True}, 'evidenceIds': []}})
                return super().__call__(node, context, log)
        state = self.run_cycle(NoData(experiment=True))
        self.assertEqual(state['project']['researchCycle']['status'], 'inconclusive')
        self.assertEqual(state['project']['researchCycle']['hypotheses'][0]['status'], 'inconclusive')
        self.assertEqual(len(state['project']['researchCycle']['experiments']), 4)
        self.assertTrue(state['report']['unresolved'])
        self.assertTrue(all(e['status'] == 'problem' for e in state['project']['researchCycle']['experiments']))

    def test_convergence_requires_sources_and_bound_experiment_contracts(self):
        from research_swarm.research_cycle import validate_output, validate_protocol
        for value in ({'hypothesisVerdict': {'status': 'supported', 'reason': '我认为', 'limitations': '未知', 'evidenceIds': []}},
                      {'hypothesisVerdict': {'status': 'refuted', 'reason': '证伪', 'limitations': '未知', 'evidenceIds': ['made-up']}}):
            with self.assertRaises(ValueError):
                validate_output('hypothesis', 'aggregate', value, {'101'})
        with self.assertRaises(ValueError):
            validate_protocol({'id': 'P1', 'method': 'run something'})

    def test_experiment_artifact_requires_current_protocol_schema_and_each_validity_check(self):
        from research_swarm.research_cycle import _experiment_data
        import json
        import hashlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / 'runs' / 'one' / 'metrics.json'; path.parent.mkdir(parents=True)
            token = {'toolExecutions': [{'tool': 'python_run', 'status': 'completed', 'evidenceIds': ['receipt'],
                                        'artifacts': [{'path': 'runs/one/metrics.json'}]}]}
            raw = path.parent / 'raw.csv'; raw.write_text('score\n1\n1\n1\n', encoding='utf-8')
            token['toolExecutions'][0]['script'] = 'runs/one/experiment.py'
            script = path.parent / 'experiment.py'; script.write_text('# boundary fixture', encoding='utf-8')
            token['toolExecutions'][0]['scriptSha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
            token['toolExecutions'][0]['artifacts'].append({'path': 'runs/one/raw.csv', 'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()})
            protocol = {'id': 'P1', 'replicates': 3, 'validityChecks': ['same result'], 'outputSchema': {'score': 'number', 'raw': 'array'},
                        'rawData': {'file': 'raw.csv', 'metrics': {'score': {'column': 'score', 'statistic': 'median'}}}}
            good = {'protocolId': 'P1', 'replicates': 3, 'score': 1.0, 'raw': [1, 2, 3],
                    'validation': {'passed': True, 'checks': [{'name': 'same result', 'passed': True}]}}
            for bad in (dict(good, protocolId='old'), dict(good, raw='not raw data'),
                        dict(good, validation={'passed': True, 'checks': []}),
                        dict(good, validation={'passed': True, 'checks': [{'name': 'same result', 'passed': False}]})):
                path.write_text(json.dumps(bad), encoding='utf-8')
                token['toolExecutions'][0]['artifacts'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol), (None, []))
            path.write_text(json.dumps(good), encoding='utf-8')
            token['toolExecutions'][0]['artifacts'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol)[1], ['receipt'])
            path.write_text(json.dumps(dict(good, score=99)), encoding='utf-8')
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol), (None, []))
            token['toolExecutions'][0]['artifacts'][0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol), (None, []))

    def test_grouped_raw_data_is_recomputed_per_baseline_and_reports_specific_mismatches(self):
        from research_swarm.research_cycle import _experiment_data
        import json
        import hashlib
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run = root / 'runs' / 'one'; run.mkdir(parents=True)
            raw = run / 'raw.csv'; raw.write_text('path,latency\nscan,10\nindex,1\nscan,12\nindex,2\nscan,14\nindex,3\n', encoding='utf-8')
            script = run / 'experiment.py'; script.write_text('# boundary fixture', encoding='utf-8')
            metrics = run / 'metrics.json'; metrics.write_text(json.dumps({'protocolId': 'P1', 'replicates': 3,
                'index_latency': 2, 'scan_latency': 12, 'validation': {'passed': True, 'checks': [{'name': 'correct result', 'passed': True}]}}), encoding='utf-8')
            token = {'toolExecutions': [{'tool': 'python_run', 'status': 'completed', 'evidenceIds': ['receipt'],
                'script': 'runs/one/experiment.py', 'scriptSha256': hashlib.sha256(script.read_bytes()).hexdigest(),
                'artifacts': [{'path': 'runs/one/' + p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in (raw, metrics)]}]}
            protocol = {'id': 'P1', 'replicates': 3, 'validityChecks': ['correct result'],
                'outputSchema': {'index_latency': 'number', 'scan_latency': 'number'}, 'rawData': {'file': 'raw.csv', 'metrics': {
                    'index_latency': {'column': 'latency', 'statistic': 'median', 'where': {'path': 'index'}},
                    'scan_latency': {'column': 'latency', 'statistic': 'median', 'where': {'path': 'scan'}}}}}
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol)[1], ['receipt'])
            protocol['rawData']['metrics']['index_latency'].pop('where')
            errors = []
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol, errors), (None, []))
            self.assertTrue(any('index_latency' in e and '中位' in e for e in errors), errors)
            protocol['rawData']['metrics']['index_latency']['where'] = {'path': 'absent'}
            errors = []
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=root), {}, token, protocol, errors), (None, []))
            self.assertTrue(any('原始测量不足' in e for e in errors), errors)

    def test_failed_experiment_receipt_cannot_be_used_as_existing_sufficient_data(self):
        class FailedReceipt(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'data_source' and node['phase'] != 'aggregate':
                    value['structured']['dataAssessment']['evidenceIds'] = ['bad-receipt']
                return value
        runner = FailedReceipt()
        with tempfile.TemporaryDirectory() as temp:
            library = sample_library()
            library['evidence'].append({'id': 'bad-receipt', 'type': 'experiment', 'executionStatus': 'failed', 'locator': 'missing', 'quote': 'unrelated'})
            engine = Engine(library, Path(temp) / 'state.sqlite', runner=runner, workflow='autonomous')
            runner.tools = LocalResearchTools(Path(temp))
            try:
                engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct', 'mode': 'llm'})
                wait_until(lambda: engine.snapshot()['report'].get('ready'))
                self.assertNotIn('bad-receipt', engine.snapshot()['project']['researchCycle']['dataRequests'][0]['evidenceIds'])
            finally: engine.close()

    def test_unresolved_child_obligation_prevents_convergence(self):
        class Unresolved(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'hypothesis' and node['phase'] == 'aggregate':
                    value['unresolved'] = ['必要反例仍未测试']
                return value
        state = self.run_cycle(Unresolved())
        self.assertEqual(state['project']['researchCycle']['status'], 'inconclusive')
        self.assertTrue(any('必要反例' in issue for issue in state['report']['unresolved']))

    def test_editing_background_supersedes_a_pending_topic_choice(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = CycleRunner(); engine = Engine(sample_library(), Path(temp)/'state.sqlite', runner=runner, workflow='autonomous')
            try:
                engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'explore', 'paperResearch': True, 'mode': 'llm'})
                wait_until(lambda: bool(engine.snapshot()['project']['researchCycle'].get('topicSelection')))
                state = engine.snapshot(); old_decision = state['project']['researchCycle']['topicSelection']['id']
                background = next(n for n in state['nodes'] if n['input'].get('researchStep') == 'background')
                engine.command('intervene', {'expectedRevision': state['revision'], 'nodeId': background['id'], 'kind': 'modify', 'text': '新的边界'})
                self.assertFalse(engine.snapshot()['project']['researchCycle'].get('topicSelection'))
                engine.command('resume', {})
                wait_until(lambda: bool(engine.snapshot()['project']['researchCycle'].get('topicSelection')))
                self.assertNotEqual(engine.snapshot()['project']['researchCycle']['topicSelection']['id'], old_decision)
            finally: engine.close()

    def test_designer_cannot_accept_tampered_artifacts(self):
        class Tampered(CycleRunner):
            def __call__(self, node, context, log):
                if node['input'].get('researchStep') == 'experiment_design' and node['phase'] == 'aggregate':
                    run = context['children'][-1]['output']['structured']['experimentRun']
                    if run.get('verified'):
                        (self.tools.root / run['rawDataArtifact']).write_text('latency\n999\n999\n999\n999\n999\n', encoding='utf-8')
                return super().__call__(node, context, log)
        state = self.run_cycle(Tampered(experiment=True))
        self.assertNotEqual(state['project']['researchCycle']['status'], 'converged')
        self.assertFalse(any(e.get('reviewStatus') == 'accepted' for e in state['project']['researchCycle']['experiments']))

    def test_approved_evidence_requires_all_registered_artifacts_when_reused(self):
        from research_swarm.research_cycle import _existing_sources
        runner = CycleRunner(experiment=True); state = self.run_cycle(runner); engine = self._last_engine
        demand = state['project']['researchCycle']['dataRequests'][0]
        self.assertTrue(_existing_sources(engine, demand['evidenceIds'], demand))
        node = next(n for n in state['nodes'] if (n.get('output') or {}).get('structured', {}).get('experimentRun', {}).get('verified'))
        (runner.tools.root / node['output']['structured']['experimentRun']['rawDataArtifact']).unlink()
        self.assertFalse(_existing_sources(engine, demand['evidenceIds'], demand))

    def test_large_raw_csv_can_be_reviewed_with_hash_verified_bounded_preview(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw = root/'runs'/'large'/'raw.csv'; raw.parent.mkdir(parents=True)
            raw.write_text('a,b\n' + '1.234567890123456,2.345678901234567\n'*10000, encoding='utf-8')
            tools = LocalResearchTools(root)
            result = tools.call('artifact_read', {'path': 'runs/large/raw.csv', 'preview': True}, {'id': 'designer'}, {}, lambda _: None)
            self.assertTrue(result['preview'])
            self.assertEqual(result['rowCount'], 10000)
            self.assertEqual(result['sha256'], hashlib.sha256(raw.read_bytes()).hexdigest())
            self.assertLess(len(result['text']), 5000)

    def test_large_metrics_json_has_explicit_hash_verified_review_preview(self):
        import hashlib
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root/'runs'/'large'/'metrics.json'; path.parent.mkdir(parents=True)
            data = {'protocolId': 'P1', 'score': 1.2, 'replicates': 5,
                    'validation': {'passed': True, 'checks': [{'name': 'same result', 'passed': True}]},
                    'observations': list(range(60000))}
            path.write_text(json.dumps(data), encoding='utf-8')
            result = LocalResearchTools(root).call('artifact_read', {'path': 'runs/large/metrics.json', 'preview': True}, {'id': 'designer'}, {}, lambda _: None)
            self.assertTrue(result['preview'])
            self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn('protocolId', result['text'])
            self.assertIn('60000', result['text'])
            self.assertIn('仅预览', result['text'])
            self.assertLess(len(result['text']), 5000)

    def test_execution_receipt_binds_the_script_before_launch_and_rejects_self_modification(self):
        import hashlib
        from types import SimpleNamespace
        from research_swarm.research_cycle import _experiment_data
        with tempfile.TemporaryDirectory() as temp:
            tools = LocalResearchTools(Path(temp))
            code = ('import json,csv\nfrom pathlib import Path\n'
                    'Path(__file__).write_text("# replaced after launch")\n'
                    'Path("raw.csv").write_text("score\\n1\\n1\\n1\\n")\n'
                    'Path("metrics.json").write_text(json.dumps({"protocolId":"P1","score":1,"replicates":3,"validation":{"passed":True,"checks":[{"name":"same result","passed":True}]}}))')
            result = tools.call('python_run', {'code': code}, {'id': 'executor'}, {}, lambda _: None)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['scriptSha256'], hashlib.sha256(code.encode('utf-8')).hexdigest())
            self.assertTrue(result['scriptChanged'])
            token = {'toolExecutions': [dict(result, evidenceIds=[e['id'] for e in result['evidence']])]}
            protocol = {'id': 'P1', 'replicates': 3, 'validityChecks': ['same result'], 'outputSchema': {'score': 'number'},
                        'rawData': {'file': 'raw.csv', 'metrics': {'score': {'column': 'score', 'statistic': 'median'}}}}
            errors = []
            self.assertEqual(_experiment_data(SimpleNamespace(_artifact_root=Path(temp)), {}, token, protocol, errors), (None, []))
            self.assertTrue(any('脚本' in e and '哈希' in e for e in errors), errors)

    def test_manuscript_results_cannot_bypass_the_bound_evidence_chain(self):
        class PaperBypass(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'hypothesis' and node['phase'] == 'aggregate':
                    value['structured']['paperSections'] = [{'id': 'results', 'markdown': '无关证据推导出的结果', 'evidenceIds': ['102']}]
                return value
        state = self.run_cycle(PaperBypass())
        node = next(n for n in state['nodes'] if n['input'].get('researchStep') == 'hypothesis')
        self.assertFalse(node['output']['structured'].get('paperSections'))
        self.assertEqual(state['project']['researchCycle']['status'], 'inconclusive')

    def test_hypothesis_rebuild_discards_node_less_capacity_demands(self):
        runner = CycleRunner(); state = self.run_cycle(runner); engine = self._last_engine
        state = engine.command('pause', {})
        h = state['project']['researchCycle']['hypotheses'][0]
        with engine._condition:
            old = copy.deepcopy(engine._state['project']['researchCycle']['dataRequests'][0])
            old.update(id='capacity-blocked', nodeId=None, status='blocked', evidenceIds=[])
            engine._state['project']['researchCycle']['dataRequests'].append(old)
            engine._state['project']['researchCycle']['hypotheses'][0]['dataRequestIds'].append(old['id'])
        state = engine.snapshot()
        engine.command('intervene', {'expectedRevision': state['revision'], 'nodeId': h['nodeId'], 'kind': 'modify', 'text': '改过的猜想'})
        self.assertFalse(engine.snapshot()['project']['researchCycle']['dataRequests'])

    def test_synthesis_cannot_converge_using_unrelated_known_evidence(self):
        class Unrelated(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'synthesis':
                    value['structured']['researchConclusion']['evidenceIds'] = ['102']
                    value['claims'] = [{'text': '与本数据链无关的判断', 'evidenceIds': ['102']}]
                return value
        state = self.run_cycle(Unrelated())
        self.assertEqual(state['project']['researchCycle']['status'], 'inconclusive')
        self.assertFalse(state['report']['claims'])

    def test_duplicate_hypothesis_is_not_misreported_as_exhausted_budget(self):
        class Duplicate(CycleRunner):
            def __call__(self, node, context, log):
                value = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'synthesis':
                    h = context['researchCycle']['hypotheses'][0]
                    value['structured']['hypotheses'] = [{k: h[k] for k in ('id', 'statement', 'falsification', 'reason', 'evidenceIds')}]
                return value
        state = self.run_cycle(Duplicate())
        self.assertEqual(state['project']['researchCycle']['status'], 'inconclusive')
        self.assertEqual(len(state['project']['researchCycle']['hypotheses']), 1)

    def test_stored_outputs_contain_host_verified_results_and_research_inputs(self):
        runner = CycleRunner(experiment=True)
        state = self.run_cycle(runner)
        # The durable output and visible node use the same post-validation result.
        engine = self._last_engine
        successful = next(n for n in state['nodes'] if n['input'].get('researchStep') == 'experiment_execution'
                          and n['output']['structured']['experimentRun'].get('verified'))
        stored = engine.node_history(successful['id'])[-1]
        self.assertTrue(stored['output']['structured']['experimentRun']['verified'])
        self.assertIn('researchCycle', stored['context'])
        self.assertEqual(successful['evidenceIds'], stored['output']['evidenceIds'])

    def test_restart_preserves_guess_and_evidence_decomposition(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.sqlite'
            engine = Engine(sample_library(), path, workflow='autonomous')
            state = engine.command('start-autonomous', {'researchCycle': True, 'topicMode': 'direct', 'budgetTier': 'swarm'})
            engine.close()
            engine = Engine(sample_library(), path, workflow='autonomous')
            try:
                restored = engine.snapshot()
                self.assertTrue(restored['paused'])
                self.assertEqual(restored['project']['researchCycle'], state['project']['researchCycle'])
                self.assertTrue(any(n['input'].get('dependsOn') for n in restored['nodes']))
            finally: engine.close()


if __name__ == '__main__':
    unittest.main()
