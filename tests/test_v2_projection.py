import copy
import json
import unittest
from unittest.mock import patch

from research_swarm.v2_projection import WORKFLOW_VERSION, build_brief_view, project_task, render_brief


AT = '2026-10-03T10:00:00+00:00'
LATER = '2026-10-03T10:01:00+00:00'
PUBLIC = {'mode': 'llm', 'capabilities': {'modelReady': True}}


def contract(message_id='message-1', question='比较现有方法的证据与限制'):
    return {
        'question': question,
        'objects': ['现有方法'],
        'dimensions': ['可定位证据'],
        'deliverables': ['保留未决事项的研究报告'],
        'scope': {
            'included': ['文献证据'],
            'excluded': ['未授权的实验'],
            'optional': [],
            'objectives': [{'id': 'objective:1', 'description': '核对证据来源'}],
        },
        'evidencePolicy': {'requireLocatedSources': True},
        'executionPolicy': {
            'allowPaperSearch': True,
            'allowFullTextDownload': False,
            'allowReplication': False,
            'allowLocalExperiment': False,
            'allowCodeInspection': False,
            'budget': {'seconds': 600},
        },
        'openQuestions': [],
        'sourceMessageIds': [message_id],
    }


def snapshot(with_run=True):
    value = contract()
    result = {
        'task': {'id': 'task-1', 'workflow': WORKFLOW_VERSION, 'title': '证据研究',
                 'draft': 1, 'confirmed': 1, 'generation': 1, 'created': AT},
        'briefs': [{'version': 1, 'content': value, 'createdAt': AT}],
        'messages': [{'id': 'message-1', 'role': 'user', 'content': value['question'],
                      'intent': 'draft', 'at': AT}],
        'runs': [],
        'nodes': [],
        'exceptions': [],
        'cursor': 3,
    }
    if with_run:
        result['runs'].append({
            'runId': 'run-1', 'briefVersion': 1, 'status': 'research_starting',
            'revision': 1, 'epoch': 1, 'contract': copy.deepcopy(value),
            'settings': {'mode': 'evidence'}, 'createdAt': AT, 'startedAt': 1791021600,
            'usage': {}, 'report': None,
        })
        result['nodes'] = [
            {'id': 'run-1:o0-0', 'runId': 'run-1', 'briefVersion': 1, 'stage': 'Retrieval',
             'objectiveId': 'objective:1', 'dependencies': [], 'status': 'pending', 'token': None},
            {'id': 'run-1:o0-1', 'runId': 'run-1', 'briefVersion': 1, 'stage': 'Claim Extraction',
             'objectiveId': 'objective:1', 'dependencies': ['o0-0'], 'status': 'pending', 'token': None},
            {'id': 'run-1:report', 'runId': 'run-1', 'briefVersion': 1, 'stage': 'Report',
             'objectiveId': 'objective:1', 'dependencies': ['o0-1'], 'status': 'pending', 'token': None},
        ]
        result['cursor'] = 4
    return result


def scientific_state(node_id='run-1:o0-1', suffix='1'):
    evidence_id = 'evidence-' + suffix
    claim_id = 'claim-' + suffix
    return {
        'project': {'taskMode': 'research'},
        'papers': [{'id': 'paper-' + suffix, 'title': '实际检索资料', 'year': None}],
        'evidence': [{'id': evidence_id, 'paperId': 'paper-' + suffix, 'quote': '原文',
                      'locator': 'paper.pdf#page=2', 'type': 'full_text', 'confidence': 0.9}],
        'claimGraph': {
            'schemaVersion': 1,
            'claims': [{
                'id': claim_id, 'statement': '仅为候选主张', 'scope': '给定条件',
                'falsification': '相反观测', 'ownerNodeId': node_id,
                'parentClaimIds': [], 'version': 1, 'versions': [],
                'origin': {'kind': 'v2', 'evidenceIds': [evidence_id]},
                'assessment': {'status': 'inconclusive', 'reason': '样本有限',
                               'evidenceIds': [evidence_id], 'limitations': '未复现'},
            }],
            'relations': [{'id': 'relation-' + suffix, 'claimId': claim_id, 'claimVersion': 1,
                           'evidenceId': evidence_id, 'type': 'support', 'polarity': 'unresolved'}],
            'expressions': [],
            'materials': [],
        },
    }


def complete(value):
    data = scientific_state()
    report = {
        'runId': 'run-1', 'briefVersion': 1, 'ready': True, 'approved': False,
        'summary': '流程结束，科学结论仍有限制。',
        'claims': [{'claimId': 'claim-1', 'claimVersion': 1, 'text': '仅为候选主张',
                    'status': 'inconclusive', 'evidenceIds': ['evidence-1'], 'limitations': '未复现'}],
        'unresolved': ['尚需复现'], 'optionalNextActions': ['以后讨论扩展范围'],
        'state': data,
        'stageSummaries': [{'stage': 'Claim Extraction', 'objectiveId': 'objective:1', 'summary': '保留候选'}],
    }
    for node in value['nodes']:
        node.update(status='completed', attempt=1, result={'state': copy.deepcopy(data), 'summary': node['stage']})
    value['nodes'][-1]['result'] = copy.deepcopy(report)
    value['runs'][-1].update(status='completed', revision=7, finishedAt=LATER, report=report)
    value['cursor'] = 10
    return value


class BriefProjectionTests(unittest.TestCase):
    def test_empty_and_pending_draft_status(self):
        value = snapshot(False)
        value['task'].update(draft=0, confirmed=None)
        value['briefs'] = []
        value['messages'] = []
        empty = project_task(value, {})
        self.assertEqual(empty['phase'], 'empty')
        self.assertEqual(empty['brief']['status'], 'empty')
        self.assertIsNone(empty['state'])
        self.assertEqual(empty['document']['markdown'], '')
        self.assertEqual(empty['workbench']['draft']['blocks'], [])
        self.assertEqual(empty['workbench']['artifacts'], [])
        self.assertFalse(empty['modelReady'])
        value['messages'].append({'id': 'pending', 'role': 'user', 'intent': 'draft', 'content': '问题', 'at': AT})
        pending = project_task(value, PUBLIC)
        self.assertEqual(pending['phase'], 'requirements')
        self.assertTrue(pending['document']['polishing'])
        self.assertEqual(pending['brief']['status'], 'requirements_clarifying')

    def test_clarification_readiness_and_question_types(self):
        value = snapshot(False)
        value['briefs'][0]['content']['openQuestions'] = [{'question': '确认对象？', 'core': True}]
        detail = project_task(value, PUBLIC)
        self.assertEqual(detail['brief']['status'], 'requirements_clarifying')
        self.assertEqual(detail['document']['questions'], ['确认对象？'])
        self.assertEqual(detail['brief']['clarificationQuestions'], [{'question': '确认对象？', 'core': True}])
        self.assertFalse(detail['document']['polishing'])
        value['briefs'][0]['content']['openQuestions'][0]['core'] = False
        self.assertEqual(build_brief_view(value)['status'], 'requirements_ready')

    def test_scope_diff_preserves_version_content_and_pending_semantics(self):
        value = snapshot(False)
        draft = copy.deepcopy(value['briefs'][0])
        draft['version'] = 2
        draft['content']['question'] = '调整后的范围'
        draft['content']['sourceMessageIds'].append('message-2')
        value['briefs'].append(draft)
        value['task']['draft'] = 2
        value['messages'].append({'id': 'message-2', 'role': 'user', 'intent': 'revise_scope', 'content': '调整范围', 'at': LATER})
        result = build_brief_view(value)
        self.assertEqual(result['draft'], draft)
        self.assertEqual(result['confirmedBrief'], value['briefs'][0])
        self.assertEqual(result['confirmedVersion'], 1)
        self.assertEqual(result['scopeDiff'], ['question', 'sourceMessageIds'])
        self.assertFalse(result['understandingPending'])
        value['messages'].append({'id': 'ask', 'role': 'user', 'intent': 'ask', 'content': '进展？', 'at': LATER})
        self.assertFalse(build_brief_view(value)['understandingPending'])
        value['messages'].append({'id': 'message-3', 'role': 'user', 'intent': 'next_round', 'content': '新问题', 'at': LATER})
        self.assertTrue(build_brief_view(value)['understandingPending'])
        result['draft']['content']['objects'].append('仅修改返回值')
        self.assertNotIn('仅修改返回值', draft['content']['objects'])

    def test_markdown_matches_existing_brief_rendering(self):
        brief = snapshot(False)['briefs'][0]
        content = brief['content']
        expected = [
            '# 研究契约（只读）', '', content['question'], '', 'Brief 版本：1',
            '', '## objects', '- 现有方法', '', '## dimensions', '- 可定位证据',
            '', '## deliverables', '- 保留未决事项的研究报告',
            '', '## scope', '```json', json.dumps(content['scope'], ensure_ascii=False, indent=2), '```',
            '', '## executionPolicy', '```json', json.dumps(content['executionPolicy'], ensure_ascii=False, indent=2), '```',
        ]
        self.assertEqual(render_brief(brief), '\n'.join(expected))
        self.assertEqual(render_brief(None), '')


class TaskProjectionTests(unittest.TestCase):
    def test_running_task_has_complete_legacy_state_and_actual_dag(self):
        value = snapshot()
        result = project_task(value, PUBLIC)
        state = result['state']
        required = {'revision', 'project', 'stage', 'paused', 'status', 'requirements', 'nodes',
                    'edges', 'papers', 'facetNodes', 'evidence', 'checkpoints', 'activeCheckpointId',
                    'activities', 'report', 'history'}
        self.assertTrue(required.issubset(state))
        self.assertEqual(state['revision'], value['cursor'])
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['stage'], 0)
        self.assertEqual([node['id'] for node in state['nodes']], [node['id'] for node in value['nodes']])
        self.assertEqual([(edge['source'], edge['target']) for edge in state['edges']],
                         [('run-1:o0-0', 'run-1:o0-1'), ('run-1:o0-1', 'run-1:report')])
        self.assertEqual(state['project']['description'], value['runs'][0]['contract']['question'])
        self.assertEqual(state['project']['mode'], 'evidence')
        self.assertFalse(state['report']['ready'])
        self.assertEqual(state['report']['claims'], [])
        self.assertNotIn('researchCycle', state)
        self.assertNotIn('researchDecision', state['project'])
        self.assertEqual(state['checkpoints'], [])
        self.assertIsNone(state['activeCheckpointId'])

    def test_all_research_node_fields_and_error_normalization(self):
        value = snapshot()
        value['runs'][0].update(status='failed', revision=3)
        value['nodes'][0].update(status='failed', attempt=1, result=None, error='真实执行错误',
                                 startedAt=AT, finishedAt=LATER)
        value['nodes'][1]['stage'] = 'Validation Planning'
        result = project_task(value, PUBLIC)
        required = {'id', 'parentId', 'title', 'role', 'kind', 'phase', 'status', 'progress',
                    'input', 'output', 'logs', 'sourceNodeId', 'requirementIds', 'evidenceIds',
                    'startedAt', 'finishedAt', 'elapsedMs', 'version', 'active'}
        for node in result['state']['nodes']:
            self.assertTrue(required.issubset(node))
            self.assertIsInstance(node['input'], dict)
            self.assertIsInstance(node['logs'], list)
            self.assertIsInstance(node['version'], int)
            self.assertIsInstance(node['active'], bool)
        first, planning, report = result['state']['nodes']
        self.assertEqual(first['error']['message'], '真实执行错误')
        self.assertEqual(first['elapsedMs'], 60000)
        self.assertEqual(first['version'], 1)
        self.assertIsNone(first['output'])
        self.assertIsNone(planning['startedAt'])
        self.assertEqual(planning['elapsedMs'], 0)
        self.assertEqual(planning['phase'], 'plan')
        self.assertEqual(report['phase'], 'aggregate')
        self.assertEqual(result['error'], '真实执行错误')

    def test_run_status_mapping_preserves_exact_workflow_status(self):
        cases = {
            'research_starting': ('researching', 'running', False),
            'researching': ('researching', 'running', False),
            'blocked_exception': ('researching', 'waiting_user', True),
            'paused': ('researching', 'waiting_user', True),
            'interrupted': ('researching', 'waiting_user', True),
            'failed': ('failed', 'failed', False),
            'completed': ('completed', 'completed', False),
            'cancelled': ('completed', 'idle', True),
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                value = snapshot()
                value['runs'][0]['status'] = status
                result = project_task(value, PUBLIC)
                self.assertEqual((result['phase'], result['state']['status'], result['state']['paused']), expected)
                self.assertEqual(result['workflowStatus']['run'], status)
                self.assertEqual(result['task']['phase'], expected[0])
                self.assertFalse(result['state']['report']['ready'])

    def test_current_node_results_merge_latest_state_per_objective(self):
        value = snapshot()
        value['runs'][0]['status'] = 'researching'
        value['nodes'][0].update(status='completed', result={'state': scientific_state(suffix='old')})
        value['nodes'][1].update(status='completed', result={'state': scientific_state(suffix='new'), 'summary': '实际输出'})
        other = copy.deepcopy(value['nodes'][0])
        other.update(id='run-1:o1-0', objectiveId='objective:2', result={'state': scientific_state('run-1:o1-0', 'other')})
        value['nodes'].append(other)
        value['runs'][0]['contract']['scope']['objectives'].append({'id': 'objective:2', 'description': '第二目标'})
        state = project_task(value, PUBLIC)['state']
        self.assertEqual([row['id'] for row in state['papers']], ['paper-new', 'paper-other'])
        self.assertEqual([row['id'] for row in state['claimGraph']['claims']], ['claim-new', 'claim-other'])
        self.assertEqual(state['papers'][0]['year'], '')
        self.assertIsInstance(state['papers'][0]['facetNodeIds'], list)
        self.assertIsInstance(state['papers'][0]['facts'], list)
        self.assertEqual(state['evidence'][0]['extractor'], '')
        self.assertEqual(state['nodes'][1]['output']['summary'], '实际输出')
        self.assertEqual(state['nodes'][1]['evidenceIds'], ['evidence-new'])

    def test_finished_report_state_is_authoritative_and_not_modified(self):
        value = complete(snapshot())
        value['nodes'][0]['result']['state'] = scientific_state(suffix='obsolete')
        before = copy.deepcopy(value)
        result = project_task(value, PUBLIC)
        self.assertEqual(value, before)
        self.assertEqual(result['research']['report'], before['runs'][0]['report'])
        self.assertEqual(result['runs'][0]['report']['state'], before['runs'][0]['report']['state'])
        self.assertEqual([row['id'] for row in result['state']['papers']], ['paper-1'])
        self.assertEqual(len(result['state']['nodes']), 3)
        self.assertNotIn('nodes', result['research']['report']['state'])
        self.assertNotIn('report', result['research']['report']['state'])
        self.assertEqual(result['state']['report']['claims'][0]['assessmentStatus'], 'inconclusive')
        self.assertEqual(result['state']['report']['claims'][0]['status'], 'candidate')
        result['state']['papers'][0]['title'] = '只修改显示数据'
        result['state']['claimGraph']['claims'][0]['assessment']['status'] = 'supported'
        self.assertEqual(result['research']['report']['state'], before['runs'][0]['report']['state'])
        self.assertEqual(value, before)

    def test_scientific_support_is_not_turned_into_user_approval(self):
        value = complete(snapshot())
        value['runs'][0]['report']['claims'][0]['status'] = 'supported'
        result = project_task(value, PUBLIC)
        self.assertEqual(result['research']['report']['claims'][0]['status'], 'supported')
        self.assertEqual(result['state']['report']['claims'][0]['assessmentStatus'], 'supported')
        self.assertEqual(result['state']['report']['claims'][0]['status'], 'candidate')
        self.assertFalse(result['state']['report']['approved'])
        self.assertFalse(result['workbench']['report']['approved'])

    def test_new_run_does_not_display_old_nodes_exceptions_or_science(self):
        value = complete(snapshot())
        value['exceptions'] = [{'id': 'old-exception', 'runId': 'run-1', 'status': 'resolved', 'message': '旧异常'}]
        newer = snapshot()
        new_run = newer['runs'][0]
        new_run.update(runId='run-2', briefVersion=2, createdAt=LATER)
        value['runs'].append(new_run)
        new_nodes = newer['nodes']
        for node in new_nodes:
            node['runId'] = 'run-2'
            node['id'] = node['id'].replace('run-1:', 'run-2:')
            node['briefVersion'] = 2
        # Fully qualified dependencies work, but an old-run reference cannot cross the boundary.
        new_nodes[1]['dependencies'] = ['run-2:o0-0', 'run-1:o0-1']
        value['nodes'].extend(new_nodes)
        value['exceptions'].append({'id': 'current-exception', 'runId': 'run-2', 'status': 'open', 'message': '本轮异常'})
        result = project_task(value, PUBLIC)
        self.assertEqual(result['task']['round'], 2)
        self.assertEqual(result['research']['run']['runId'], 'run-2')
        self.assertTrue(all(row['runId'] == 'run-2' for row in result['research']['nodes']))
        self.assertTrue(all(row['runId'] == 'run-2' for row in result['exceptions']))
        self.assertEqual(result['state']['papers'], [])
        self.assertEqual(result['state']['evidence'], [])
        self.assertEqual(result['state']['report']['claims'], [])
        self.assertFalse(result['workbench']['report']['ready'])
        self.assertTrue(all(row['runId'] == 'run-2' for row in result['workbench']['artifacts']))
        self.assertNotIn('run-1', json.dumps(result['state'], ensure_ascii=False))
        self.assertEqual(len(result['runs']), 2)
        self.assertTrue(result['runs'][0]['report']['ready'])
        self.assertEqual(result['runs'][0]['round'], 1)
        self.assertEqual(result['runs'][0]['at'], LATER)
        self.assertEqual(result['runs'][0]['summary'], value['runs'][0]['report']['summary'])

    def test_frozen_run_contract_is_not_replaced_by_new_draft(self):
        value = snapshot()
        newer = copy.deepcopy(value['briefs'][0])
        newer['version'] = 2
        newer['content']['question'] = '下一轮的新问题'
        newer['content']['executionPolicy']['allowLocalExperiment'] = True
        value['briefs'].append(newer)
        value['task']['draft'] = 2
        result = project_task(value, PUBLIC)
        self.assertIn('下一轮的新问题', result['document']['markdown'])
        self.assertEqual(result['workbench']['draft']['revision'], 2)
        self.assertNotIn('下一轮的新问题', result['state']['project']['description'])
        self.assertEqual(result['workbench']['plan']['intent'], value['runs'][0]['contract']['question'])
        self.assertNotIn('experimentation', result['workbench']['plan']['capabilities'])
        self.assertEqual(result['workbench']['executionSettings']['revision'], 1)

    def test_workbench_fields_artifacts_references_and_report_content(self):
        result = project_task(complete(snapshot()), PUBLIC)
        workbench = result['workbench']
        self.assertIsInstance(workbench['plan']['intent'], str)
        self.assertIsInstance(workbench['plan']['capabilities'], list)
        self.assertTrue(workbench['draft']['blocks'][0]['locked'])
        self.assertEqual(workbench['draft']['blocks'][0]['content'], result['document']['markdown'])
        required = {'id', 'kind', 'title', 'status', 'content', 'revision', 'sourceRevision',
                    'nodeIds', 'claimRefs', 'evidenceIds', 'dependencies', 'sourceRefs'}
        ids = {item['id'] for item in workbench['artifacts']}
        for artifact in workbench['artifacts']:
            self.assertTrue(required.issubset(artifact))
            self.assertIsInstance(artifact['content'], dict)
            self.assertIsInstance(artifact['revision'], int)
            self.assertTrue(set(artifact['dependencies']).issubset(ids))
            for key in ('nodeIds', 'claimRefs', 'evidenceIds', 'dependencies', 'sourceRefs'):
                self.assertIsInstance(artifact[key], list)
        claim = next(item for item in workbench['artifacts'] if item['kind'] == 'claim')
        self.assertEqual(claim['status'], 'inconclusive')
        self.assertEqual(claim['nodeIds'], ['run-1:o0-1'])
        self.assertEqual(claim['claimRefs'], [{'claimId': 'claim-1', 'version': 1}])
        self.assertEqual(claim['sourceRefs'][0]['locator'], 'paper.pdf#page=2')
        self.assertEqual(claim['dependencies'], ['node_output:run-1:o0-1'])
        self.assertTrue(workbench['report']['ready'])
        self.assertIn('尚需复现', workbench['report']['markdown'])
        self.assertIn('状态：inconclusive', workbench['report']['markdown'])
        self.assertIn('report:live', ids)
        self.assertEqual(result['artifacts'][0]['url'], '/api/tasks/task-1/export')

    def test_pending_nodes_have_context_artifacts_but_no_fabricated_outputs(self):
        result = project_task(snapshot(), PUBLIC)
        artifacts = result['workbench']['artifacts']
        self.assertEqual(len([item for item in artifacts if item['kind'] == 'node_state']), 3)
        self.assertFalse(any(item['kind'] == 'node_output' for item in artifacts))
        self.assertFalse(any(item['kind'] == 'claim' for item in artifacts))
        self.assertFalse(result['workbench']['report']['ready'])
        self.assertEqual(result['artifacts'], [])
        self.assertIsNone(artifacts[0]['content']['output'])

    def test_artifact_revisions_follow_commits_not_polling_elapsed_time_or_leases(self):
        value = snapshot()
        value['runs'][0].update(status='researching', revision=2)
        value['nodes'][0].update(status='running', attempt=1, elapsedMs=10, leaseUntil=100, token='token-1')
        first = project_task(value, PUBLIC)['workbench']['artifacts']
        value['nodes'][0].update(elapsedMs=5000, leaseUntil=200, token='token-2')
        value['cursor'] += 1
        self.assertEqual(project_task(value, PUBLIC)['workbench']['artifacts'], first)
        value['runs'][0]['revision'] = 3
        value['nodes'][0].update(status='completed', result={'summary': '实际取得资料', 'state': scientific_state()})
        second = project_task(value, PUBLIC)['workbench']['artifacts']
        current = next(item for item in second if item['id'] == first[0]['id'])
        self.assertEqual(current['revision'], 3)
        self.assertNotEqual(current['sourceRevision'], first[0]['sourceRevision'])
        self.assertTrue(any(item['kind'] == 'node_output' for item in second))

    def test_projection_is_pure_and_preserves_v2_metadata(self):
        value = complete(snapshot())
        before = copy.deepcopy(value)
        public = copy.deepcopy(PUBLIC)
        with patch('builtins.open', side_effect=AssertionError('file access')), \
                patch('pathlib.Path.write_text', side_effect=AssertionError('file write')), \
                patch('threading.Thread.start', side_effect=AssertionError('worker start')), \
                patch('queue.Queue.put', side_effect=AssertionError('queue write')):
            first = project_task(value, public)
            second = project_task(value, public)
        self.assertEqual(first, second)
        self.assertEqual(value, before)
        self.assertEqual(public, PUBLIC)
        self.assertEqual(first['workflowVersion'], WORKFLOW_VERSION)
        self.assertEqual(first['workbench']['workflowVersion'], WORKFLOW_VERSION)
        self.assertTrue(first['capabilities']['conversationOnly'])
        self.assertTrue(first['capabilities']['documentReadOnly'])
        self.assertFalse(first['capabilities']['codeInspection'])
        self.assertFalse(first['capabilities']['materialsImport'])
        self.assertTrue(first['document']['readOnly'])
        self.assertIsNone(first['document']['error'])
        self.assertTrue(first['modelReady'])
        self.assertEqual(first['workbench']['sourceEventId'], value['cursor'])
        self.assertEqual(first['workbench']['cursorKind'], 'domain_event')
        self.assertEqual(first['task']['updatedAt'], LATER)
        self.assertEqual(first['messages'][0]['kind'], 'requirements')
        json.dumps(first, ensure_ascii=False, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
