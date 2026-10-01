"""Contract tests for the persisted claim graph."""

import copy
import unittest

from research_swarm.claims import (add_relation, assess_claim, create_claim,
                                   create_expression, ensure_graph, get_claim,
                                   preserve_history, revise_claim)


def state():
    return {
        'project': {'researchCycle': {'hypotheses': []}},
        'nodes': [], 'papers': [{'id': 'p1', 'title': 'Source'}],
        'evidence': [
            {'id': 'e1', 'paperId': 'p1', 'locator': 'abstract', 'quote': 'Positive', 'type': 'abstract'},
            {'id': 'e2', 'paperId': 'p1', 'locator': 'page 4', 'quote': 'Negative', 'type': 'fulltext'},
            {'id': 'e3', 'locator': 'runs/a/metrics.json', 'quote': 'Measured', 'type': 'experiment'},
            {'id': 'e4', 'paperId': 'p1', 'locator': '', 'quote': 'Unlocated', 'type': 'abstract'},
        ], 'report': {'claims': []},
    }


class ClaimDomainTests(unittest.TestCase):
    def test_migration_is_stable_and_preserves_legacy_ids_and_direction(self):
        s = state()
        s['project']['researchCycle']['hypotheses'] = [{
            'id': 'hypothesis-abc', 'statement': 'A beats B', 'scope': 'Dataset X',
            'falsification': 'No gain', 'nodeId': 'owner', 'evidenceIds': ['e1'],
            'verdict': {'status': 'refuted', 'reason': 'Counterexample', 'evidenceIds': ['e2']}}]
        s['nodes'] = [
            {'id': 'owner', 'parentId': 'central', 'input': {'hypothesisId': 'hypothesis-abc'}},
            {'id': 'child', 'parentId': 'owner', 'input': {}},
        ]
        first = copy.deepcopy(ensure_graph(s))
        self.assertEqual(first['schemaVersion'], 1)
        self.assertEqual(len(first['claims']), 1)
        claim = first['claims'][0]
        self.assertEqual(claim['id'], 'claim:abc')
        self.assertEqual(claim['ownerNodeId'], 'owner')
        self.assertEqual(claim['assessment']['status'], 'refuted')
        self.assertEqual({(r['evidenceId'], r['polarity']) for r in first['relations']},
                         {('e1', 'unresolved'), ('e2', 'against')})
        self.assertEqual(s['nodes'][1]['input']['claimId'], claim['id'])
        self.assertEqual(s['nodes'][1]['input']['claimVersion'], 1)
        self.assertEqual(s['project']['researchCycle']['hypotheses'][0]['claimId'], claim['id'])
        self.assertEqual(ensure_graph(s), first)

    def test_canonical_hypothesis_does_not_restore_old_verdict_after_revision(self):
        s = state()
        s['project']['researchCycle']['hypotheses'] = [{
            'id': 'hypothesis-abc', 'statement': 'First', 'evidenceIds': ['e1'],
            'verdict': {'status': 'supported', 'reason': 'Old result', 'evidenceIds': ['e1']}}]
        ensure_graph(s)
        revise_claim(s, 'claim:abc', 'Second')
        self.assertEqual(get_claim(s, 'claim:abc')['assessment']['status'], 'unassessed')
        before = copy.deepcopy(s['claimGraph']['relations'])
        ensure_graph(s)
        self.assertEqual(s['claimGraph']['relations'], before)
        self.assertEqual(get_claim(s, 'claim:abc')['assessment']['status'], 'unassessed')
        self.assertEqual(s['project']['researchCycle']['hypotheses'][0]['claimVersion'], 1)

    def test_legacy_descendants_bind_even_when_stored_before_parent(self):
        s = state()
        s['project']['researchCycle']['hypotheses'] = [{'id': 'hypothesis-x',
            'statement': 'X', 'nodeId': 'owner'}]
        s['nodes'] = [{'id': 'grandchild', 'parentId': 'child', 'input': {}},
                      {'id': 'child', 'parentId': 'owner', 'input': {}},
                      {'id': 'owner', 'parentId': 'central', 'input': {}}]
        ensure_graph(s)
        self.assertEqual(s['nodes'][0]['input']['claimId'], 'claim:x')
        self.assertEqual(s['nodes'][0]['input']['claimVersion'], 1)

    def test_legacy_verdict_without_located_evidence_is_inconclusive(self):
        s = state()
        s['project']['researchCycle']['hypotheses'] = [{
            'id': 'hypothesis-x', 'statement': 'X',
            'verdict': {'status': 'supported', 'reason': 'Old conclusion',
                        'evidenceIds': ['e4']}}]
        ensure_graph(s)
        self.assertEqual(get_claim(s, 'claim:x')['assessment']['status'], 'inconclusive')
        self.assertEqual(s['project']['researchCycle']['hypotheses'][0]['verdict']['status'], 'supported')

    def test_migration_refreshes_materials_without_duplicate_claims(self):
        s = state()
        ensure_graph(s)
        s['papers'].append({'id': 'p2', 'title': 'Later'})
        s['evidence'].append({'id': 'e5', 'paperId': 'p2', 'locator': 'p. 2', 'type': 'fulltext'})
        graph = ensure_graph(s)
        self.assertIn(('paper', 'p2'), {(m['kind'], m['sourceId']) for m in graph['materials']})
        self.assertEqual(len({m['id'] for m in graph['materials']}), len(graph['materials']))

    def test_canonical_report_readback_does_not_invent_unresolved_relations(self):
        s = state()
        create_claim(s, 'Finding', claim_id='c')
        add_relation(s, 'c', 'e1', polarity='for', quality='usable')
        s['report']['claims'] = [{'id': 'c', 'claimId': 'c', 'claimVersion': 1,
                                  'text': 'Finding', 'evidenceIds': ['e1']}]
        ensure_graph(s)
        self.assertEqual([(r['evidenceId'], r['polarity']) for r in s['claimGraph']['relations']],
                         [('e1', 'for')])

    def test_new_report_rows_do_not_create_phantom_claims(self):
        s = state()
        ensure_graph(s)
        s['report']['claims'] = [{'id': 'model-output', 'text': 'Unreviewed synthesis',
                                  'evidenceIds': ['e1']}]
        ensure_graph(s)
        self.assertEqual(s['claimGraph']['claims'], [])
        self.assertEqual(s['claimGraph']['relations'], [])
        self.assertNotIn('claimId', s['report']['claims'][0])

    def test_initial_legacy_report_rows_migrate_once(self):
        s = state()
        s['report']['claims'] = [{'id': 'old', 'text': 'Old finding', 'evidenceIds': ['e1']}]
        ensure_graph(s)
        self.assertEqual(get_claim(s, 'claim:result:old')['statement'], 'Old finding')
        self.assertEqual(len(s['claimGraph']['relations']), 1)
        ensure_graph(s)
        self.assertEqual(len(s['claimGraph']['relations']), 1)

    def test_relations_keep_negative_and_qualifying_evidence_and_group_metadata(self):
        s = state()
        claim = create_claim(s, 'A beats B', claim_id='c')
        positive = add_relation(s, 'c', 'e1', polarity='for', quality='usable')
        negative = add_relation(s, 'c', 'e2', polarity='against', quality='limited')
        qualifier = add_relation(s, 'c', 'e3', relation_type='qualify', reason='Only small data')
        self.assertEqual(positive['sourceGroup'], negative['sourceGroup'])
        self.assertEqual(qualifier['polarity'], 'unresolved')
        self.assertEqual({r['polarity'] for r in s['claimGraph']['relations']},
                         {'for', 'against', 'unresolved'})
        self.assertEqual(claim['version'], 1)
        with self.assertRaises(ValueError):
            add_relation(s, 'c', 'missing')
        with self.assertRaises(ValueError):
            add_relation(s, 'c', 'e1', relation_type='qualify', polarity='for')
        with self.assertRaises(ValueError):
            add_relation(s, 'c', 'e1', quality='disqualified')

    def test_experiment_and_dataset_materials_share_actual_run_sources(self):
        s = state()
        s['evidence'].extend([
            {'id': 'metric', 'type': 'experiment', 'locator': 'runs/run-1/metrics.json'},
            {'id': 'raw', 'type': 'experiment', 'locator': 'runs/run-1/raw.csv'},
            {'id': 'failed', 'type': 'experiment', 'locator': 'runs/run-2/receipt.json',
             'executionStatus': 'failed'},
            {'id': 'data', 'type': 'dataset', 'locator': 'datasets/a.csv'},
        ])
        create_claim(s, 'A beats B', claim_id='c')
        a = add_relation(s, 'c', 'metric', polarity='for')
        b = add_relation(s, 'c', 'raw', polarity='against')
        c = add_relation(s, 'c', 'failed', polarity='against')
        self.assertEqual(a['sourceGroup'], b['sourceGroup'])
        self.assertNotEqual(a['sourceGroup'], c['sourceGroup'])
        materials = {m['sourceId']: m for m in s['claimGraph']['materials']}
        self.assertEqual(materials['metric']['kind'], 'experiment')
        self.assertEqual(materials['data']['kind'], 'dataset')
        self.assertEqual(materials['metric']['id'], 'material:evidence:metric')

    def test_assessment_requires_current_directional_located_usable_evidence(self):
        s = state()
        create_claim(s, 'A beats B', claim_id='c')
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'supported', 'Looks good')
        assess_claim(s, 'c', 'inconclusive', 'No evidence yet')
        add_relation(s, 'c', 'e1', polarity='unresolved')
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'supported', 'Looks good', ['e1'])
        add_relation(s, 'c', 'e4', polarity='for', quality='usable')
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'supported', 'Looks good', ['e4'])
        add_relation(s, 'c', 'e1', polarity='for', quality='usable')
        assessed = assess_claim(s, 'c', 'supported', 'Located support', ['e1'])
        self.assertEqual(assessed['assessment']['evidenceIds'], ['e1'])
        self.assertFalse(assessed['assessment']['confirmedByUser'])
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'refuted', 'Wrong direction', ['e1'])

    def test_conflicting_directions_require_mixed_assessment(self):
        s = state()
        create_claim(s, 'A beats B', claim_id='c')
        add_relation(s, 'c', 'e1', polarity='for', quality='usable')
        add_relation(s, 'c', 'e2', polarity='against', quality='usable')
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'supported', 'Ignores counterevidence', ['e1', 'e2'])
        self.assertEqual(assess_claim(s, 'c', 'mixed', 'Conflict', ['e1', 'e2'])
                         ['assessment']['status'], 'mixed')

    def test_revision_is_append_only_and_old_relations_cannot_assess_new_version(self):
        s = state()
        create_claim(s, 'First', scope='All', falsification='No effect', claim_id='c')
        add_relation(s, 'c', 'e1', polarity='for', quality='usable')
        assess_claim(s, 'c', 'supported', 'Positive', ['e1'])
        old = copy.deepcopy(get_claim(s, 'c')['versions'][0])
        revised = revise_claim(s, 'c', 'Second', actor='user', reason='Narrow claim')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['versions'][0], old)
        self.assertEqual(revised['assessment']['status'], 'unassessed')
        self.assertEqual(s['claimGraph']['relations'][0]['claimVersion'], 1)
        with self.assertRaises(ValueError):
            assess_claim(s, 'c', 'supported', 'Stale', ['e1'])
        with self.assertRaises(ValueError):
            add_relation(s, 'c', 'e2', claim_version=1)

    def test_expression_refs_must_resolve_exact_versions(self):
        s = state()
        create_claim(s, 'First', claim_id='c')
        revise_claim(s, 'c', 'Second')
        expression = create_expression(s, 'paper', 'Draft', 'Body',
                                       [{'claimId': 'c', 'version': 1}])
        self.assertEqual(expression['status'], 'draft')
        self.assertEqual(expression['claimRefs'], [{'claimId': 'c', 'version': 1}])
        with self.assertRaises(ValueError):
            create_expression(s, 'paper', 'Bad', 'Body', [{'claimId': 'c', 'version': 3}])
        with self.assertRaises(ValueError):
            create_expression(s, 'poster', 'Bad', 'Body', [])

    def test_rollback_preserves_future_history_without_exposing_it_as_current(self):
        s = state()
        create_claim(s, 'First', claim_id='c')
        checkpoint = copy.deepcopy(s)
        revise_claim(s, 'c', 'Future')
        add_relation(s, 'c', 'e2', polarity='against')
        create_claim(s, 'Only in future', claim_id='future')
        create_expression(s, 'paper', 'Future report', 'Body', [{'claimId': 'c', 'version': 2}])
        preserve_history(checkpoint, s)
        restored = get_claim(checkpoint, 'c')
        self.assertEqual(restored['version'], 1)
        self.assertEqual(restored['statement'], 'First')
        self.assertEqual([v['version'] for v in restored['versions']], [1, 2])
        self.assertTrue(get_claim(checkpoint, 'future')['archived'])
        self.assertIn('e2', {r['evidenceId'] for r in checkpoint['claimGraph']['relations']})
        self.assertEqual(len(checkpoint['claimGraph']['expressions']), 1)
        self.assertTrue(checkpoint['claimGraph']['expressions'][0]['stale'])
        preserve_history(checkpoint, s)
        self.assertEqual(len(checkpoint['claimGraph']['claims']), 2)

    def test_rollback_keeps_new_negative_and_failed_observations_addressable(self):
        s = state()
        create_claim(s, 'First', claim_id='c')
        checkpoint = copy.deepcopy(s)
        s['papers'].append({'id': 'p2', 'title': 'Later paper'})
        s['evidence'].extend([
            {'id': 'later-negative', 'paperId': 'p2', 'type': 'fulltext',
             'locator': 'p. 7', 'quote': 'Counterexample'},
            {'id': 'later-failed', 'type': 'experiment', 'locator': 'runs/fail/receipt.json',
             'executionStatus': 'failed', 'quote': 'Timeout'},
        ])
        add_relation(s, 'c', 'later-negative', polarity='against')
        add_relation(s, 'c', 'later-failed', polarity='against', quality='unusable')
        preserve_history(checkpoint, s)
        self.assertEqual({e['id'] for e in checkpoint['evidence'] if e['id'].startswith('later-')},
                         {'later-negative', 'later-failed'})
        self.assertEqual(next(p for p in checkpoint['papers'] if p['id'] == 'p2')['title'],
                         'Later paper')
        self.assertEqual({r['evidenceId'] for r in checkpoint['claimGraph']['relations']},
                         {'later-negative', 'later-failed'})
        self.assertEqual(next(e for e in checkpoint['evidence'] if e['id'] == 'later-failed')
                         ['executionStatus'], 'failed')


if __name__ == '__main__':
    unittest.main()
