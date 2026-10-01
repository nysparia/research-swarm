import copy
import tempfile
import threading
import unittest
from pathlib import Path

from research_swarm.draft_blocks import patch_blocks, reconcile_blocks, render_blocks
from research_swarm.research_plan import infer_plan
from research_swarm.workbench_store import WorkbenchStore


def record():
    return {'id': 'task-one', 'title': 'Test', 'phase': 'researching', 'taskMode': 'research',
            'document': {'markdown': '# Scope\nCompare methods\n\n## Constraint\nKeep raw data', 'revision': 3}}


def state():
    return {'revision': 10, 'status': 'running', 'nodes': [
        {'id': 'n1', 'title': 'First', 'version': 1, 'status': 'completed', 'active': True,
         'input': {}, 'output': {'summary': 'Original finding', 'evidenceIds': ['e1'], 'claims': []}},
        {'id': 'n2', 'title': 'Dependent', 'version': 1, 'status': 'completed', 'active': True,
         'input': {'dependsOn': ['n1']}, 'output': {'summary': 'Second finding', 'evidenceIds': []}},
        {'id': 'other', 'title': 'Sibling', 'version': 1, 'status': 'completed', 'active': True,
         'input': {}, 'output': {'summary': 'Unaffected', 'evidenceIds': []}}],
        'edges': [{'source': 'n1', 'target': 'other', 'type': 'compare', 'informational': True}],
        'evidence': [{'id': 'e1', 'type': 'fulltext', 'locator': 'paper.pdf#page=4'}],
        'claimGraph': {'claims': [{'id': 'c1', 'version': 1, 'statement': 'Candidate',
                                 'ownerNodeId': 'n1', 'assessment': {'status': 'unassessed', 'evidenceIds': ['e1']}}],
                       'relations': [], 'expressions': [{'id': 'x1', 'kind': 'paper', 'title': 'Draft',
                        'markdown': '# Paper', 'claimRefs': [{'claimId': 'c1', 'version': 1}], 'status': 'draft'}]},
        'report': {'summary': '', 'ready': False, 'approved': False}}


class DraftTests(unittest.TestCase):
    def test_moved_sections_preserve_identity_and_fences_are_not_sections(self):
        old = reconcile_blocks('# One\na\n\n## Two\n```python\n# code\n```')
        self.assertEqual(len(old), 2)
        new = reconcile_blocks('## Two\n```python\n# code\n```\n\n# One\na', old)
        self.assertEqual([b['id'] for b in new], [old[1]['id'], old[0]['id']])

    def test_user_patch_is_protected_from_ai_rewrites_and_omission(self):
        old = reconcile_blocks('# Scope\nAI scope\n\n## Limit\nAI limit')
        changed = patch_blocks(old, [{'op': 'update', 'id': old[1]['id'], 'changes': {'content': '## Limit\nUser limit'}}])
        self.assertTrue(changed[1]['locked'])
        new = reconcile_blocks('# Scope\nNew scope\n\n## Limit\nAI replacement', changed)
        self.assertEqual(new[1]['content'], '## Limit\nUser limit')
        omitted = reconcile_blocks('# Scope\nNew scope', changed)
        self.assertIn('User limit', render_blocks(omitted))
        self.assertEqual(old[1]['content'], '## Limit\nAI limit')

    def test_patch_is_atomic_and_rejects_unknown_fields_ids_and_duplicate_ids(self):
        blocks = reconcile_blocks('# Scope\nBody')
        before = copy.deepcopy(blocks)
        for operation in ({'op': 'update', 'id': blocks[0]['id'], 'mystery': True},
                          {'op': 'remove', 'id': 'missing'},
                          {'op': 'add', 'block': dict(blocks[0])}):
            with self.assertRaises(ValueError):
                patch_blocks(blocks, [operation])
        self.assertEqual(blocks, before)

    def test_plan_composes_requested_capabilities_with_honest_fallback(self):
        plan = infer_plan('综述并调查原因，复现基线、做实验，最后准备论文')
        self.assertEqual(set(plan['capabilities']), {'review', 'investigation', 'reproduction', 'experimentation', 'paper_preparation'})
        self.assertEqual(plan['source'], 'deterministic')
        self.assertTrue(plan['modules'])
        self.assertIn('reproduction', infer_plan('', 'reproduction')['capabilities'])

    def test_paper_mentions_do_not_imply_manuscript_preparation(self):
        for text in ('复现论文', '找三篇论文', 'reproduce this paper', 'find papers about graph networks'):
            with self.subTest(text=text):
                self.assertNotIn('paper_preparation', infer_plan(text)['capabilities'])
        for text in ('准备论文', '论文写作', '撰写稿件', 'write a paper', 'prepare a manuscript'):
            with self.subTest(text=text):
                self.assertIn('paper_preparation', infer_plan(text)['capabilities'])

    def test_normalize_model_plan_is_whitelisted_unique_and_canonical(self):
        from research_swarm.research_plan import normalize_plan
        model = normalize_plan(['paper_preparation', 'review', 'review'], rationale='Explicit writing request')
        self.assertEqual(model['capabilities'], ['review', 'paper_preparation'])
        self.assertEqual(model['intent'], 'review+paper_preparation')
        self.assertEqual([m['id'] for m in model['modules']], ['module:review', 'module:paper_preparation'])
        self.assertEqual(model['source'], 'model')
        self.assertEqual(model['rationale'], 'Explicit writing request')
        for invalid in ([], ['unknown'], ['review', None], 'review', {'review': True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_plan(invalid)
        with self.assertRaises(ValueError):
            normalize_plan(['review'], rationale={'unexpected': True})


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'workbench.sqlite3'
        self.store = WorkbenchStore(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_unchanged_sync_replays_durably_without_duplicate_events(self):
        first = self.store.sync(record(), state())
        second = self.store.sync(record(), state())
        self.assertEqual(first, second)
        events = self.store.events()
        self.assertEqual(events[-1]['id'], first['revision'])
        self.store.close()
        self.store = WorkbenchStore(self.path)
        self.assertEqual(self.store.snapshot(), first)
        self.assertEqual(self.store.events(after=events[0]['id']), events[1:])
        self.store.append_event('proposal.created', {'id': 'p1'})
        self.assertGreater(self.store.snapshot()['revision'], first['revision'])

    def test_explicit_draft_and_plan_are_authoritative_and_task_isolation_enforced(self):
        rec = record()
        rec['draftBlocks'] = reconcile_blocks('# Explicit\nKeep me', actor='user')
        rec['plan'] = {'intent': 'custom', 'capabilities': ['review'], 'modules': [], 'rationale': 'user'}
        result = self.store.sync(rec)
        self.assertEqual(result['draft'], {'revision': 3, 'blocks': rec['draftBlocks']})
        self.assertEqual(result['plan'], rec['plan'])
        other = dict(rec, id='task-two')
        with self.assertRaises(ValueError):
            self.store.sync(other)

    def test_pending_node_context_is_versioned_without_fabricating_outputs_or_clock_churn(self):
        current = state()
        current['nodes'][0].update(status='running', output=None, elapsedMs=10,
                                   input={'description': 'Measure latency'}, logs=[])
        first = self.store.sync(record(), current)
        node = self.store.artifact('node_state:n1')
        self.assertEqual(node['nodeIds'], ['n1'])
        self.assertIsNone(node['content']['output'])
        self.assertEqual(node['dependencies'], [])
        self.assertNotIn('node_output:n1', [a['id'] for a in first['artifacts']])
        current['revision'] += 1
        current['nodes'][0]['elapsedMs'] = 2000
        current['nodes'][2]['output']['summary'] = 'An unrelated result'
        self.store.sync(record(), current)
        self.assertEqual(self.store.artifact('node_state:n1')['revision'], node['revision'])
        current['nodes'][0]['logs'].append({'message': 'Starting CPU measurement'})
        self.store.sync(record(), current)
        changed = self.store.artifact('node_state:n1')
        self.assertEqual(changed['revision'], node['revision'] + 1)
        self.assertEqual(self.store.artifact('node_state:n1', node['revision'])['content']['logs'], [])
        current['nodes'][0]['active'] = False
        self.store.sync(record(), current)
        self.assertEqual(self.store.artifact('node_state:n1')['status'], 'stale')

    def test_live_report_citations_preserve_unassessed_truth(self):
        result = self.store.sync(record(), state(), [{'path': 'runs/one/raw.csv', 'name': 'raw', 'sha256': 'abc'}])
        claim = next(a for a in result['artifacts'] if a['kind'] == 'claim')
        self.assertEqual(claim['status'], 'unassessed')
        self.assertEqual(claim['nodeIds'], ['n1'])
        self.assertEqual(claim['content']['assessment']['status'], 'unassessed')
        self.assertTrue(result['report']['markdown'])
        self.assertFalse(result['report']['ready'])
        self.assertIn('paper.pdf#page=4', str(claim['sourceRefs']))
        self.assertTrue(any(a['kind'] == 'file' for a in result['artifacts']))

    def test_stale_propagation_preserves_versions_and_unrelated_siblings(self):
        old = self.store.sync(record(), state())
        n1 = next(a for a in old['artifacts'] if a['nodeIds'] == ['n1'] and a['kind'] == 'node_output')
        old_revision = n1['revision']
        changed = state()
        changed['nodes'][0].update(version=2, status='pending', output=None)
        changed['claimGraph']['claims'][0].update(version=2, statement='Revised candidate')
        new = self.store.sync(record(), changed)
        by_id = {a['id']: a for a in new['artifacts']}
        self.assertEqual(by_id[n1['id']]['status'], 'stale')
        dependent = next(a for a in new['artifacts'] if a['nodeIds'] == ['n2'])
        sibling = next(a for a in new['artifacts'] if a['nodeIds'] == ['other'])
        self.assertEqual(dependent['status'], 'stale')
        self.assertEqual(sibling['status'], 'completed')
        self.assertEqual(next(a for a in new['artifacts'] if a['id'] == 'expression:x1')['status'], 'stale')
        self.assertEqual(self.store.artifact(n1['id'], old_revision)['content']['summary'], 'Original finding')
        self.assertEqual(len(self.store.artifact_versions(n1['id'])), 2)

    def test_concurrent_sync_is_serialized_and_deduplicated(self):
        failures = []
        barrier = threading.Barrier(8)
        def save():
            try:
                barrier.wait()
                self.store.sync(record(), state())
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=save) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(self.store.events()), 1)
        for artifact in self.store.snapshot()['artifacts']:
            self.assertEqual(len(self.store.artifact_versions(artifact['id'])), 1)

    def test_removed_outputs_remain_stale_and_invalid_cursors_rejected(self):
        first = self.store.sync(record(), state())
        later = state()
        later['nodes'] = []
        snapshot = self.store.sync(record(), later)
        old_node = next(a for a in first['artifacts'] if a['kind'] == 'node_output')
        self.assertEqual(self.store.artifact(old_node['id'])['status'], 'stale')
        self.assertEqual(snapshot['revision'], self.store.events()[-1]['id'])
        with self.assertRaises(ValueError):
            self.store.events(after=-1)

    def test_jobs_materials_and_execution_configuration_are_live_non_scientific_outputs(self):
        rec = record()
        rec.update(jobs=[{'id': 'job1', 'revision': 2, 'status': 'failed', 'title': 'Actual job',
                          'materialIds': ['m1'], 'error': 'process exited 1', 'artifacts': []}],
                   materials=[{'id': 'm1', 'revision': 1, 'name': 'input.csv', 'sha256': 'abcd'}],
                   executionSettings={'timeoutSeconds': 3600, 'resource': {'gpu': False}})
        snapshot = self.store.sync(rec, state())
        job = next(a for a in snapshot['artifacts'] if a['kind'] == 'experiment_job')
        material = next(a for a in snapshot['artifacts'] if a['kind'] == 'material')
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['dependencies'], [material['id']])
        self.assertEqual(snapshot['executionSettings'], rec['executionSettings'])
        self.assertIn('process exited 1', snapshot['report']['markdown'])
        self.assertEqual(next(a for a in snapshot['artifacts'] if a['kind'] == 'claim')['status'], 'unassessed')
        self.assertEqual(self.store.sync(rec, state())['revision'], snapshot['revision'])

    def test_actual_nested_job_schema_projects_anchors_receipts_files_and_history(self):
        rec = record()
        rec['materials'] = [{'id': 'm1', 'revision': 1, 'name': 'input.csv', 'sha256': 'input-hash'}]
        rec['jobs'] = [{'id': 'j1', 'revision': 3, 'status': 'completed',
                        'request': {'nodeId': 'n1', 'nodeVersion': 1, 'materialIds': ['m1'], 'protocolId': 'p1'},
                        'result': {'status': 'completed', 'nodeId': 'n1', 'receipt': 'jobs/j1/attempt-1/receipt.json',
                                   'stdoutPath': 'jobs/j1/attempt-1/stdout.txt',
                                   'artifacts': [{'name': 'raw.csv', 'path': 'jobs/j1/attempt-1/artifacts/raw.csv', 'sha256': 'raw-hash'}],
                                   'evidence': [{'id': 'experiment:local:abc', 'type': 'experiment',
                                                'locator': 'jobs/j1/attempt-1/receipt.json', 'executionStatus': 'completed'}]}}]
        snapshot = self.store.sync(rec, state())
        job = self.store.artifact('experiment_job:j1')
        self.assertEqual(job['nodeIds'], ['n1'])
        self.assertEqual(job['dependencies'], ['material:m1'])
        self.assertIn('material:m1', [a['id'] for a in snapshot['artifacts']])
        self.assertEqual(job['evidenceIds'], ['experiment:local:abc'])
        self.assertTrue(any(ref.get('sha256') == 'raw-hash' and ref['jobId'] == 'j1' for ref in job['sourceRefs']))
        self.assertTrue(any(ref.get('path') == 'jobs/j1/attempt-1/receipt.json' for ref in job['sourceRefs']))
        self.assertTrue(any(ref.get('id') == 'experiment:local:abc' for ref in job['sourceRefs']))
        rec['jobs'][0]['revision'] = 4
        rec['jobs'][0]['status'] = 'failed'
        rec['jobs'][0]['result']['status'] = 'failed'
        self.store.sync(rec, state())
        history = self.store.artifact_versions('experiment_job:j1')
        self.assertEqual([v['status'] for v in history], ['completed', 'failed'])
        self.assertEqual(history[0]['content']['result']['status'], 'completed')

    def test_job_projection_preserves_process_truth_but_invalidates_old_engine_ownership(self):
        from research_swarm.workbench_projection import project_workbench
        rec = record()
        rec['jobs'] = [{'id': 'j1', 'revision': 3, 'status': 'completed',
                        'request': {'nodeId': 'n1', 'nodeVersion': 1, 'round': 1, 'materialIds': []},
                        'result': {'status': 'completed', 'receipt': 'jobs/j1/receipt.json'}}]
        current = state()
        current['project'] = {'round': 1}
        # A running owner has no result yet; this is not a stale-output dependency.
        current['nodes'][0].update(status='running', output=None)
        job = next(a for a in project_workbench(rec, current)['artifacts'] if a['kind'] == 'experiment_job')
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['dependencies'], [])
        for label, mutate in (
            ('changed owner version', lambda s: s['nodes'][0].update(version=2)),
            ('changed round', lambda s: s['project'].update(round=2)),
            ('archived owner', lambda s: s['nodes'][0].update(active=False)),
            ('absent owner', lambda s: s.update(nodes=[]))):
            with self.subTest(reason=label):
                changed = copy.deepcopy(current)
                mutate(changed)
                job = next(a for a in project_workbench(rec, changed)['artifacts'] if a['kind'] == 'experiment_job')
                self.assertEqual(job['status'], 'stale')
                self.assertEqual(job['content']['status'], 'completed')
                self.assertEqual(job['content']['result']['receipt'], 'jobs/j1/receipt.json')
                self.assertTrue(job['staleReason'])
        standalone = copy.deepcopy(rec)
        standalone['jobs'][0]['request'].pop('nodeId')
        standalone['jobs'][0]['status'] = 'failed'
        current['project']['round'] = 2
        job = next(a for a in project_workbench(standalone, current)['artifacts'] if a['kind'] == 'experiment_job')
        self.assertEqual(job['status'], 'failed')

    def test_regenerated_downstream_can_recover_after_upstream_changes(self):
        self.store.sync(record(), state())
        changed = state()
        changed['nodes'][0].update(version=2)
        changed['nodes'][0]['output']['summary'] = 'Updated finding'
        snapshot = self.store.sync(record(), changed)
        self.assertEqual(next(a for a in snapshot['artifacts'] if a['nodeIds'] == ['n2'])['status'], 'stale')
        changed['nodes'][1].update(version=2)
        changed['nodes'][1]['output']['summary'] = 'Regenerated downstream'
        snapshot = self.store.sync(record(), changed)
        self.assertEqual(next(a for a in snapshot['artifacts'] if a['nodeIds'] == ['n2'])['status'], 'completed')

    def test_duplicate_titles_keep_exact_moved_identity(self):
        previous = reconcile_blocks('## Same\nfirst\n\n## Same\nsecond')
        blocks = reconcile_blocks('## Same\nsecond\n\n## Same\nfirst', previous)
        self.assertEqual([b['id'] for b in blocks], [previous[1]['id'], previous[0]['id']])

    def test_two_connections_serialize_changes_and_reject_non_json_atomically(self):
        other = WorkbenchStore(self.path)
        errors = []
        barrier = threading.Barrier(2)
        def save(store):
            try:
                barrier.wait()
                store.sync(record(), state())
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=save, args=(store,)) for store in (self.store, other)]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(len(self.store.events()), 1)
            previous = self.store.snapshot()
            malformed = state()
            malformed['nodes'][0]['output']['summary'] = {'invalid'}
            with self.assertRaises(TypeError):
                self.store.sync(record(), malformed)
            self.assertEqual(self.store.snapshot(), previous)
            self.assertEqual(len(self.store.events()), 1)
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
