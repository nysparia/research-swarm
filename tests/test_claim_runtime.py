import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from research_swarm.engine import Engine
from research_swarm.server import export_bundle
from test_research_cycle import CycleRunner, cycle_library as sample_library
from test_workspace import wait_until


class ClaimRuntimeTests(unittest.TestCase):
    def engine(self, runner=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        engine = Engine(sample_library(), Path(directory.name) / 'state.sqlite', runner=runner,
                        workflow='autonomous', max_workers=2)
        self.addCleanup(engine.close)
        return engine

    def run_cycle(self, runner=None, **options):
        runner = runner or CycleRunner()
        engine = self.engine(runner)
        from research_swarm.local_tools import LocalResearchTools
        runner.tools = LocalResearchTools(engine._artifact_root)
        engine.command('start-autonomous', {'researchCycle': True, 'mode': 'llm', **options})
        wait_until(lambda: engine.snapshot()['report'].get('ready') or engine.snapshot()['status'] == 'failed')
        state = engine.snapshot()
        self.assertNotEqual(state['status'], 'failed', [n.get('error') for n in state['nodes']])
        return engine, state

    def test_claim_exists_before_owner_and_workers_run_and_expression_references_it(self):
        observed = []

        class Inspect(CycleRunner):
            def __call__(self, node, context, log):
                if node['input'].get('hypothesisId'):
                    observed.append((copy.deepcopy(node), copy.deepcopy(context.get('claim'))))
                return super().__call__(node, context, log)

        _, state = self.run_cycle(Inspect())
        self.assertTrue(observed)
        for node, claim in observed:
            self.assertIsNotNone(claim)
            self.assertEqual(node['input']['claimId'], claim['id'])
            self.assertEqual(node['input']['claimVersion'], claim['version'])
        claims = state['claimGraph']['claims']
        owner = next(c for c in claims if c['origin']['kind'] == 'hypothesis')
        self.assertEqual(owner['assessment']['status'], 'supported')
        self.assertFalse(owner['assessment']['confirmedByUser'])
        self.assertTrue(any(c.get('claimId') == owner['id'] for c in state['report']['claims']))
        expression = next(e for e in state['claimGraph']['expressions'] if e['id'] == state['report']['expressionId'])
        self.assertIn({'claimId': owner['id'], 'version': 1}, expression['claimRefs'])

    def test_negative_evidence_survives_revision_and_does_not_prove_new_version(self):
        engine, state = self.run_cycle(CycleRunner(refute=True))
        claim = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        old_links = [r for r in state['claimGraph']['relations'] if r['claimId'] == claim['id']]
        self.assertTrue(any(r['polarity'] == 'against' and r['type'] == 'support' for r in old_links))
        impact = engine.command('impact', {'claimId': claim['id'], 'kind': 'modify'})
        self.assertIn(claim['ownerNodeId'], impact['affectedIds'])
        updated = engine.command('intervene', {'claimId': claim['id'], 'kind': 'modify',
            'expectedRevision': impact['revision'], 'text': '仅在低选择性范围内索引降低延迟'})
        revised = next(c for c in updated['claimGraph']['claims'] if c['id'] == claim['id'])
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['assessment']['status'], 'unassessed')
        self.assertEqual(len(revised['versions']), 2)
        for relation in old_links:
            self.assertIn(relation, updated['claimGraph']['relations'])
        active = next(n for n in updated['nodes'] if n['id'] == revised['ownerNodeId'])
        self.assertEqual(active['input']['claimVersion'], 2)

    def test_editorial_intervention_keeps_assessment_and_does_not_rerun_nodes(self):
        engine, state = self.run_cycle()
        claim = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        updated = engine.command('intervene', {'claimId': claim['id'], 'kind': 'modify',
            'expectedRevision': state['revision'], 'text': claim['statement'] + '。'})
        revised = next(c for c in updated['claimGraph']['claims'] if c['id'] == claim['id'])
        self.assertEqual(revised['version'], claim['version'])
        self.assertEqual(revised['assessment'], claim['assessment'])
        self.assertEqual(revised['versions'], claim['versions'])
        self.assertEqual(updated['claimGraph']['relations'], state['claimGraph']['relations'])
        self.assertEqual(len(revised['editorialEdits']), 1)
        self.assertTrue(updated['report']['ready'])
        self.assertEqual([(n['id'], n['version'], n['status']) for n in updated['nodes']],
                         [(n['id'], n['version'], n['status']) for n in state['nodes']])
        old_expression = next(e for e in updated['claimGraph']['expressions'] if e['id'] == state['report']['expressionId'])
        self.assertFalse(old_expression.get('stale', False))

    def test_editorial_signal_does_not_skip_other_intervention_parameters(self):
        from research_swarm.claim_runtime import claim_intervention
        from research_swarm.claims import create_claim
        engine = self.engine()
        claim = create_claim(engine._state, 'A beats B', owner_node_id='central', claim_id='c')
        node = engine._get_node('central')
        node['input']['claimId'] = claim['id']
        for field, value in [('acceptance', 'Stricter checks'), ('experiment', {'id': 'new'}),
                             ('constraints', 'GPU'), ('requirements', []), ('library', {})]:
            with self.subTest(field=field):
                signal = claim_intervention(engine, node, {'claimId': claim['id'], 'kind': 'modify',
                    'text': 'A beats B.', field: value})
                self.assertFalse(signal)
        self.assertEqual(claim['version'], 1)

    def test_reused_hypothesis_id_with_new_statement_creates_semantic_version(self):
        from research_swarm.claim_runtime import bind_hypothesis
        engine = self.engine()
        base = {'statement': 'A beats B', 'falsification': 'No gain', 'reason': 'Test', 'evidenceIds': ['101']}
        initial = bind_hypothesis(engine, base, 'hypothesis-fixture')
        old_version = copy.deepcopy(initial['versions'][0])
        revised = bind_hypothesis(engine, {**base, 'statement': 'A does not beat B'}, 'hypothesis-fixture')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['versions'][0], old_version)
        self.assertEqual(revised['statement'], 'A does not beat B')

    def test_reproduction_target_edit_updates_owner_and_cycle_without_aliasing(self):
        from research_swarm.claim_runtime import bind_hypothesis, claim_intervention
        engine = self.engine()
        original = {'metric': 'score', 'expected': '1', 'tolerance': '.1', 'conditions': 'Dataset X'}
        base = {'statement': 'Reproduce the score', 'falsification': 'Outside tolerance', 'reason': 'Test',
                'evidenceIds': ['101'], 'reproductionTarget': original}
        current = bind_hypothesis(engine, base, 'hypothesis-fixture')
        node = engine._get_node('central')
        current['ownerNodeId'] = node['id']
        node['input']['claimId'] = current['id']
        node['input']['hypothesis'] = copy.deepcopy(base)
        cycle_hypothesis = {**copy.deepcopy(base), 'nodeId': node['id']}
        engine._state['project']['researchCycle'] = {'hypotheses': [cycle_hypothesis]}
        changed = {**original, 'expected': '2'}
        self.assertFalse(claim_intervention(engine, node, {'claimId': current['id'], 'kind': 'modify',
            'text': base['statement'], 'reproductionTarget': changed}))
        self.assertEqual(current['version'], 2)
        self.assertEqual(current['versions'][0]['reproductionTarget'], original)
        self.assertEqual(node['input']['hypothesis']['reproductionTarget'], changed)
        self.assertEqual(cycle_hypothesis['reproductionTarget'], changed)
        changed['expected'] = '99'
        self.assertEqual(current['reproductionTarget']['expected'], '2')
        node['input']['hypothesis']['reproductionTarget']['expected'] = '88'
        self.assertEqual(current['reproductionTarget']['expected'], '2')
        self.assertEqual(cycle_hypothesis['reproductionTarget']['expected'], '2')

    def test_reproduction_mode_changes_context_and_expression_kind(self):
        modes = []

        class Inspect(CycleRunner):
            def __call__(self, node, context, log):
                modes.append(context.get('taskMode'))
                output = super().__call__(node, context, log)
                for hypothesis in output['structured'].get('hypotheses', []):
                    hypothesis['reproductionTarget'] = {'paperId': '1', 'evidenceIds': ['101'],
                        'metric': 'latency', 'expected': '1 ms', 'tolerance': '0.2 ms', 'conditions': '测试样本'}
                return output

        _, state = self.run_cycle(Inspect(experiment=True), taskMode='reproduction')
        self.assertEqual(set(modes), {'reproduction'})
        self.assertEqual(state['project']['taskMode'], 'reproduction')
        expression = next(e for e in state['claimGraph']['expressions'] if e['id'] == state['report']['expressionId'])
        self.assertEqual(expression['kind'], 'reproduction_report')

    def test_claim_confirmation_is_explicit_and_export_contains_graph(self):
        engine, state = self.run_cycle()
        claim = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        confirmed = engine.command('claim-decision', {'claimId': claim['id'],
            'expectedRevision': state['revision'], 'decision': 'confirm', 'note': '同意此范围内的判断',
            'responsibilityAcknowledged': True, 'responsibilityName': '测试审阅者'})
        self.assertTrue(next(c for c in confirmed['claimGraph']['claims'] if c['id'] == claim['id'])['assessment']['confirmedByUser'])
        with zipfile.ZipFile(io.BytesIO(export_bundle(confirmed, engine._artifact_root))) as bundle:
            self.assertIn('claim-graph.json', bundle.namelist())
            self.assertIn('paper.md', bundle.namelist())
            self.assertEqual(json.loads(bundle.read('claim-graph.json'))['schemaVersion'], 1)

    def test_rollback_retains_evidence_and_new_claim_history(self):
        engine, state = self.run_cycle(CycleRunner(refute=True))
        checkpoint = state['checkpoints'][0]['id']
        previous_ids = {c['id'] for c in state['claimGraph']['claims']}
        rolled_back = engine.command('rollback', {'checkpointId': checkpoint})
        self.assertTrue(previous_ids.issubset({c['id'] for c in rolled_back['claimGraph']['claims']}))
        self.assertTrue(all(e in rolled_back['evidence'] for e in state['evidence']))
        self.assertTrue({n['id'] for n in state['nodes']}.issubset({n['id'] for n in rolled_back['nodes']}))
        restored_owner = next(n for n in rolled_back['nodes'] if n['id'] == state['claimGraph']['claims'][0]['ownerNodeId'])
        self.assertFalse(restored_owner['active'])

    def test_changed_background_revises_claim_scope_before_same_hypothesis_is_reused(self):
        class ChangedScope(CycleRunner):
            def __call__(self, node, context, log):
                output = super().__call__(node, context, log)
                if node['input'].get('researchStep') == 'background' and 'GPU' in node['input'].get('description', ''):
                    output['structured']['background']['boundaries'] = 'GPU 大数据'
                return output
        engine, before = self.run_cycle(ChangedScope())
        old = next(c for c in before['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        background = next(n for n in before['nodes'] if n['input'].get('researchStep') == 'background')
        changed = engine.command('intervene', {'nodeId': background['id'], 'kind': 'modify',
            'text': 'GPU 大数据范围', 'expectedRevision': before['revision']})
        engine.command('resume', {})
        wait_until(lambda: engine.snapshot()['report'].get('ready') or engine.snapshot()['status'] == 'failed')
        state = engine.snapshot()
        claim = next(c for c in state['claimGraph']['claims'] if c['id'] == old['id'])
        self.assertEqual(claim['scope'], 'GPU 大数据')
        self.assertEqual(claim['version'], 2)
        self.assertEqual(claim['versions'][0]['scope'], old['scope'])
        self.assertTrue(claim['ownerHistory'])

    def test_owner_cannot_hide_known_counterevidence_by_omitting_its_id(self):
        class CherryPick(CycleRunner):
            def __call__(self, node, context, log):
                output = super().__call__(node, context, log)
                step = node['input'].get('researchStep')
                if step == 'data_source' and node['phase'] != 'aggregate':
                    output['evidenceIds'].append('102')
                    output['structured']['dataAssessment']['evidenceIds'].append('102')
                    output['structured']['evidenceRelations'] = [{'evidenceId': '102', 'type': 'support',
                        'polarity': 'against', 'reason': '相同范围出现反例', 'quality': 'usable'}]
                if step == 'hypothesis' and node['phase'] == 'aggregate':
                    output['structured']['hypothesisVerdict']['evidenceIds'] = ['101']
                    output['structured']['evidenceRelations'] = [r for r in output['structured']['evidenceRelations'] if r['evidenceId'] == '101']
                    output['evidenceIds'] = ['101']
                return output
        _, state = self.run_cycle(CherryPick())
        claim = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        self.assertEqual(claim['assessment']['status'], 'inconclusive')
        self.assertIn('102', claim['assessment']['evidenceIds'])
        self.assertNotEqual(state['project']['researchCycle']['status'], 'converged')

    def test_rerun_invalidates_expression_even_when_statement_version_does_not_change(self):
        engine, state = self.run_cycle()
        claim = next(c for c in state['claimGraph']['claims'] if c['origin']['kind'] == 'hypothesis')
        source = next(n for n in state['nodes'] if n['input'].get('claimId') == claim['id'] and
                      n['input'].get('researchStep') == 'data_source')
        changed = engine.command('intervene', {'nodeId': source['id'], 'kind': 'modify',
            'text': '重新检查这个数据是否适用', 'expectedRevision': state['revision']})
        expression = next(e for e in changed['claimGraph']['expressions'] if e['id'] == state['report']['expressionId'])
        self.assertTrue(expression['stale'])
        self.assertEqual(expression['status'], 'draft')

    def test_changed_reproduction_target_creates_a_version_with_immutable_targets(self):
        from research_swarm.claim_runtime import bind_hypothesis
        engine = self.engine()
        base = {'statement': '复现指标', 'falsification': '超出容差', 'reason': '验证', 'evidenceIds': ['101'],
                'reproductionTarget': {'expected': '1 ms', 'tolerance': '0.2 ms'}}
        first = bind_hypothesis(engine, base, 'hypothesis-fixture')
        revised = bind_hypothesis(engine, {**base, 'reproductionTarget': {'expected': '2 ms', 'tolerance': '0.3 ms'}}, 'hypothesis-fixture')
        self.assertEqual(first['id'], revised['id'])
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['versions'][0]['reproductionTarget']['expected'], '1 ms')
        self.assertEqual(revised['versions'][1]['reproductionTarget']['expected'], '2 ms')
        from research_swarm.claims import revise_claim
        edited = revise_claim(engine._state, revised['id'], '用户细化复现范围')
        self.assertEqual(edited['versions'][2]['reproductionTarget']['expected'], '2 ms')

    def test_next_round_preserves_archived_claim_owners_and_clears_scheduler_view(self):
        engine, before = self.run_cycle()
        prepared = engine.command('next-round', {})
        self.assertNotIn('researchCycle', prepared['project'])
        by_id = {node['id']: node for node in prepared['nodes']}
        for claim in prepared['claimGraph']['claims']:
            self.assertTrue(claim['archived'])
            if claim.get('ownerNodeId'):
                self.assertIn(claim['ownerNodeId'], by_id)
                self.assertFalse(by_id[claim['ownerNodeId']]['active'])
        self.assertTrue(prepared['claimGraph']['expressions'])


if __name__ == '__main__':
    unittest.main()
