"""Pure v2 read models for the task, legacy graph and workbench interfaces.

The store snapshot is authoritative. This module neither executes a stage nor
creates scientific findings, approvals, checkpoints or legacy control protocols.
Artifact versions use the persisted run revision, never a clock or a lease.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any


WORKFLOW_VERSION = 'conversation_only_v2'

_SCOPE_INTENTS = {'draft', 'revise_scope', 'next_round'}
_NODE_STATUSES = {'pending', 'running', 'completed', 'failed', 'waiting_user'}
_ASSESSMENTS = {'unassessed', 'supported', 'refuted', 'mixed', 'inconclusive'}
_RUN_STATUSES = {
    'research_starting': 'running',
    'researching': 'running',
    'blocked_exception': 'waiting_user',
    'paused': 'waiting_user',
    'interrupted': 'waiting_user',
    'failed': 'failed',
    'completed': 'completed',
    'cancelled': 'idle',
}


def build_brief_view(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Match the application's draft/confirmation semantics without store access."""
    task = snapshot['task']
    latest = next((b for b in snapshot['briefs'] if b['version'] == task['draft']), None)
    confirmed = next((b for b in snapshot['briefs'] if b['version'] == task['confirmed']), None)
    message = next((m for m in reversed(snapshot['messages'])
                    if m['role'] == 'user' and m.get('intent') in _SCOPE_INTENTS), None)
    pending = bool(message and (not latest or message['id'] not in latest['content']['sourceMessageIds']))
    questions = latest['content']['openQuestions'] if latest else []
    if not latest and not pending:
        status = 'empty'
    elif latest and not any(q['core'] for q in questions) and not pending:
        status = 'requirements_ready'
    else:
        status = 'requirements_clarifying'
    difference = []
    if latest and confirmed and latest['version'] != confirmed['version']:
        difference = [key for key in latest['content']
                      if latest['content'][key] != confirmed['content'].get(key)]
    return copy.deepcopy({
        'draft': latest,
        'confirmedBrief': confirmed,
        'confirmedVersion': task['confirmed'],
        'status': status,
        'understandingPending': pending,
        'scopeDiff': difference,
        'clarificationQuestions': questions,
    })


def render_brief(brief: dict[str, Any] | None) -> str:
    """Render the existing read-only Markdown contract, not a generated report."""
    if not brief:
        return ''
    value = brief['content']
    lines = ['# 研究契约（只读）', '', value['question'], '', f"Brief 版本：{brief['version']}"]
    for key in ('objects', 'dimensions', 'deliverables'):
        lines.extend(['', '## ' + key])
        lines.extend('- ' + item for item in value[key])
    for key in ('scope', 'executionPolicy'):
        lines.extend(['', '## ' + key, '```json',
                      json.dumps(value[key], ensure_ascii=False, indent=2), '```'])
    return '\n'.join(lines)


def _unique(values):
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _merge_rows(states, key):
    rows = {}
    for state in states:
        for row in state.get(key, []):
            rows[row['id']] = copy.deepcopy(row)
    return list(rows.values())


def _scientific_state(run, nodes):
    report_state = (run.get('report') or {}).get('state')
    if isinstance(report_state, dict):
        return copy.deepcopy(report_state)
    # Results are cumulative within each objective. Only its latest committed
    # state is current; merging every stage would resurrect superseded claims.
    latest = {}
    for node in nodes:
        value = (node.get('result') or {}).get('state')
        if node['status'] == 'completed' and isinstance(value, dict):
            latest[node['objectiveId']] = value
    states = list(latest.values())
    state = {key: _merge_rows(states, key)
             for key in ('papers', 'facetNodes', 'evidence', 'activities', 'checkpoints')}
    graphs = [value['claimGraph'] for value in states if isinstance(value.get('claimGraph'), dict)]
    if graphs:
        state['claimGraph'] = {
            'schemaVersion': graphs[-1].get('schemaVersion', 1),
            **{key: _merge_rows(graphs, key) for key in ('claims', 'relations', 'expressions', 'materials')},
        }
    state['history'] = [copy.deepcopy(item) for value in states for item in value.get('history', [])]
    return state


def _paper(row):
    defaults = {
        'title': '', 'abstract': '', 'year': '', 'venue': '', 'doi': '',
        'authors': [], 'codeUrl': '', 'pdfAvailable': False, 'pdfPath': None,
        'score': 0, 'scores': {}, 'reason': '', 'reproducibility': '',
        'workerStatus': 'pending', 'facetNodeIds': [], 'evidenceIds': [],
        'facts': [], 'scoreBasis': [], 'feedback': None,
    }
    value = {**defaults, **copy.deepcopy(row)}
    # Library metadata can have a SQL NULL year; the frontend expects text or a number.
    if value['year'] is None:
        value['year'] = ''
    return value


def _legacy_claim(row):
    value = copy.deepcopy(row)
    value.setdefault('id', value.get('claimId', ''))
    value.setdefault('text', value.get('statement', ''))
    value.setdefault('evidenceIds', [])
    status = value.get('status', 'candidate')
    if status in _ASSESSMENTS:
        value['assessmentStatus'] = status
    # Scientific assessment is not a user confirmation. Preserve it separately
    # instead of relabelling supported/refuted as confirmed/rejected.
    value['status'] = status if status in ('candidate', 'confirmed', 'rejected') else 'candidate'
    return value


def _report_view(report):
    value = copy.deepcopy({key: item for key, item in (report or {}).items() if key != 'state'})
    value.setdefault('summary', '')
    value.setdefault('unresolved', [])
    value.setdefault('approved', False)
    value.setdefault('ready', False)
    value['claims'] = [_legacy_claim(row) for row in value.get('claims', [])]
    return value


def _dependencies(nodes, run_id):
    ids = {node['id'] for node in nodes}
    result = {}
    for node in nodes:
        dependencies = []
        for source in node.get('dependencies', []):
            qualified = source if source in ids else run_id + ':' + source
            if qualified in ids and qualified != node['id']:
                dependencies.append(qualified)
        result[node['id']] = _unique(dependencies)
    return result


def _elapsed(node):
    if isinstance(node.get('elapsedMs'), (int, float)):
        return max(0, node['elapsedMs'])
    if node.get('startedAt') and node.get('finishedAt'):
        try:
            start = datetime.fromisoformat(node['startedAt'].replace('Z', '+00:00'))
            finish = datetime.fromisoformat(node['finishedAt'].replace('Z', '+00:00'))
            return max(0, int((finish - start).total_seconds() * 1000))
        except (TypeError, ValueError):
            pass
    # The v2 store currently does not timestamp each node. Zero means no
    # recorded duration, not an estimate based on the run's start or its lease.
    return 0


def _project_node(node, run, dependencies):
    result = node.get('result')
    output = None
    evidence_ids = []
    if isinstance(result, dict):
        output = copy.deepcopy({key: value for key, value in result.items() if key != 'state'})
        evidence_ids = _unique(list(result.get('evidenceIds', [])) +
                               [row['id'] for row in (result.get('state') or {}).get('evidence', [])])
        output['evidenceIds'] = evidence_ids
        if 'claims' in output:
            output['claims'] = [_legacy_claim(row) for row in output['claims']]
    error = node.get('error')
    if isinstance(error, str):
        error = {'message': error}
    else:
        error = copy.deepcopy(error)
    status = node['status']
    if status not in _NODE_STATUSES:
        status = 'waiting_user' if status in ('paused', 'interrupted', 'blocked_exception') else 'pending'
    stage = node['stage']
    objective = next((item for item in run['contract']['scope']['objectives']
                      if item['id'] == node['objectiveId']), {})
    phase = 'aggregate' if stage == 'Report' else 'plan' if stage == 'Validation Planning' else 'execute'
    return {
        'id': node['id'],
        'parentId': dependencies[0] if dependencies else None,
        'title': node.get('title') or stage,
        'role': stage,
        'kind': stage.lower().replace(' ', '_'),
        'phase': phase,
        'status': status,
        'progress': 100 if node['status'] == 'completed' else 0,
        'input': {
            'runId': run['runId'],
            'briefVersion': run['briefVersion'],
            'objectiveId': node['objectiveId'],
            'description': objective.get('description', ''),
            'stage': stage,
            'dependsOn': dependencies,
        },
        'output': output,
        'logs': copy.deepcopy(node.get('logs') or []),
        'sourceNodeId': None,
        'requirementIds': [node['objectiveId']],
        'evidenceIds': evidence_ids,
        'startedAt': node.get('startedAt'),
        'finishedAt': node.get('finishedAt'),
        'elapsedMs': _elapsed(node),
        'version': node.get('version', node.get('attempt', 0)),
        'active': True,
        'error': error,
        'runId': run['runId'],
        'briefVersion': run['briefVersion'],
        'stage': stage,
        'executionStatus': node['status'],
    }


def _state_view(snapshot, run, nodes, provider_public):
    state = _scientific_state(run, nodes)
    contract = run['contract']
    dependencies = _dependencies(nodes, run['runId'])
    projected = [_project_node(node, run, dependencies[node['id']]) for node in nodes]
    project = state.get('project') or {}
    mode = run.get('settings', {}).get('mode', provider_public.get('mode', 'evidence'))
    state.update({
        'revision': snapshot['cursor'],
        'project': {
            'id': snapshot['task']['id'],
            'title': snapshot['task']['title'],
            'description': contract['question'],
            'round': len(snapshot['runs']),
            'sourcePath': project.get('sourcePath', state.get('sourcePath', '')),
            'mode': mode if mode in ('llm', 'evidence') else 'evidence',
            'taskMode': project.get('taskMode', 'research'),
            'runId': run['runId'],
            'briefVersion': run['briefVersion'],
        },
        'stage': sum(node['status'] == 'completed' for node in nodes),
        'paused': run['status'] in ('paused', 'interrupted', 'blocked_exception', 'cancelled'),
        'status': _RUN_STATUSES.get(run['status'], 'idle'),
        'nodes': projected,
        'edges': [
            {'source': source, 'target': node['id'],
             'type': 'return' if node['stage'] == 'Report' else 'decompose',
             'reason': '冻结计划中的阶段依赖'}
            for node in nodes for source in dependencies[node['id']]
        ],
        'requirements': [
            {'id': item['id'], 'description': item['description'],
             'acceptance': '\n'.join(contract['deliverables']),
             'constraints': '\n'.join(contract['scope'].get('excluded', [])),
             'version': run['briefVersion'],
             'sourceNodeIds': [node['id'] for node in nodes if node['objectiveId'] == item['id']]}
            for item in contract['scope']['objectives']
        ],
        'papers': [_paper(row) for row in state.get('papers', [])],
        'evidence': [
            {'paperId': '', 'quote': '', 'locator': '', 'type': '', 'confidence': 0,
             'extractor': '', **row}
            for row in state.get('evidence', [])
        ],
        'report': _report_view(run.get('report')),
    })
    for key in ('facetNodes', 'checkpoints', 'activities', 'history'):
        state.setdefault(key, [])
    state.setdefault('activeCheckpointId', None)
    if isinstance(state.get('claimGraph'), dict):
        state['claimGraph'].setdefault('schemaVersion', 1)
        for key in ('claims', 'relations', 'expressions', 'materials'):
            state['claimGraph'].setdefault(key, [])
    state.pop('researchCycle', None)
    return state


def _plan(contract):
    policy = contract.get('executionPolicy', {})
    capabilities = []
    if policy.get('allowPaperSearch'):
        capabilities.append('review')
    if contract:
        capabilities.append('investigation')
    if policy.get('allowReplication'):
        capabilities.append('reproduction')
    if policy.get('allowLocalExperiment'):
        capabilities.append('experimentation')
    return {'intent': contract.get('question', ''), 'capabilities': capabilities,
            'modules': [{'id': 'module:' + item, 'capability': item, 'title': item, 'enabled': True}
                        for item in capabilities], 'source': 'brief'}


def _source_refs(ids, evidence):
    fields = ('id', 'type', 'paperId', 'locator', 'path', 'url', 'sha256')
    return [{key: copy.deepcopy(evidence[eid][key]) for key in fields if key in evidence[eid]}
            for eid in ids if eid in evidence]


def _artifact(kind, identifier, title, content, status, run, node_ids=(),
              claim_refs=(), evidence_ids=(), dependencies=(), source_refs=()):
    return {
        'id': identifier,
        'kind': kind,
        'title': title,
        'status': status,
        'content': copy.deepcopy(content),
        # Nodes do not have an independent commit revision. The run revision
        # advances on claim/finish/retry; attempt alone misses completion.
        'revision': run['revision'],
        'sourceRevision': f"{run['runId']}:{run['briefVersion']}:{run['revision']}",
        'nodeIds': _unique(node_ids),
        'claimRefs': copy.deepcopy(list(claim_refs)),
        'evidenceIds': _unique(evidence_ids),
        'dependencies': _unique(dependencies),
        'sourceRefs': copy.deepcopy(list(source_refs)),
        'runId': run['runId'],
        'briefVersion': run['briefVersion'],
    }


def _report_markdown(run, state):
    report = state['report']
    if isinstance(report.get('markdown'), str):
        return report['markdown']
    lines = ['# ' + run['contract']['question'], '', '运行状态：' + run['status']]
    if report['summary']:
        lines.extend(['', report['summary']])
    for claim in report['claims']:
        lines.extend(['', '## ' + claim['text'],
                      '状态：' + claim.get('assessmentStatus', claim['status']),
                      '限制：' + claim.get('limitations', ''),
                      '证据：' + ', '.join(claim['evidenceIds'])])
    summaries = report.get('stageSummaries')
    if summaries is None:
        summaries = [{'stage': node['title'], 'summary': node['output'].get('summary', '')}
                     for node in state['nodes'] if node['output'] is not None]
    for item in summaries:
        if item.get('summary'):
            lines.extend(['', '### ' + item['stage'], item['summary']])
    if report['unresolved']:
        lines.extend(['', '## 未决事项', *['- ' + item for item in report['unresolved']]])
    if report.get('optionalNextActions'):
        lines.extend(['', '## 可选后续事项', *['- ' + item for item in report['optionalNextActions']]])
    return '\n'.join(lines)


def _workbench_artifacts(run, state):
    artifacts = []
    nodes = {node['id']: node for node in state['nodes']}
    evidence = {row['id']: row for row in state['evidence']}
    graph = state.get('claimGraph') or {}
    claims = {row['id']: row for row in graph.get('claims', [])}

    def node_source(node_id):
        kind = 'node_output' if nodes[node_id]['output'] is not None else 'node_state'
        return kind + ':' + node_id

    for node in nodes.values():
        refs = [{'claimId': claim['id'], 'version': claim['version']}
                for claim in claims.values() if claim.get('ownerNodeId') == node['id']]
        sources = _source_refs(node['evidenceIds'], evidence)
        content = {key: value for key, value in node.items() if key != 'elapsedMs'}
        artifacts.append(_artifact(
            'node_state', 'node_state:' + node['id'], node['title'], content, node['status'], run,
            [node['id']], refs, node['evidenceIds'],
            ['node_state:' + dep for dep in node['input']['dependsOn']], sources))
        if node['output'] is not None:
            artifacts.append(_artifact(
                'node_output', 'node_output:' + node['id'], node['title'], node['output'],
                node['status'], run, [node['id']], refs, node['evidenceIds'],
                [node_source(dep) for dep in node['input']['dependsOn']], sources))
    for claim in claims.values():
        relations = [row for row in graph.get('relations', [])
                     if row['claimId'] == claim['id'] and row['claimVersion'] == claim['version']]
        assessment = claim.get('assessment') or {}
        ids = _unique(list(assessment.get('evidenceIds', [])) +
                      list((claim.get('origin') or {}).get('evidenceIds', [])) +
                      [row['evidenceId'] for row in relations])
        owner = claim.get('ownerNodeId')
        dependencies = [node_source(owner)] if owner in nodes else []
        dependencies.extend('claim:' + parent for parent in claim.get('parentClaimIds', []) if parent in claims)
        artifacts.append(_artifact(
            'claim', 'claim:' + claim['id'], claim['statement'], dict(claim, relations=relations),
            'stale' if claim.get('archived') else assessment.get('status', 'unassessed'), run,
            [owner] if owner in nodes else [], [{'claimId': claim['id'], 'version': claim['version']}],
            ids, dependencies, _source_refs(ids, evidence)))
    for expression in graph.get('expressions', []):
        refs = expression.get('claimRefs', [])
        owners = [claims[ref['claimId']].get('ownerNodeId') for ref in refs if ref['claimId'] in claims]
        ids = _unique(eid for ref in refs if ref['claimId'] in claims
                      and claims[ref['claimId']]['version'] == ref['version']
                      for eid in claims[ref['claimId']].get('assessment', {}).get('evidenceIds', []))
        artifacts.append(_artifact(
            'expression', 'expression:' + expression['id'], expression.get('title', expression['id']),
            expression, expression.get('status', 'draft'), run,
            [owner for owner in owners if owner in nodes], refs, ids,
            ['claim:' + ref['claimId'] for ref in refs if ref['claimId'] in claims], _source_refs(ids, evidence)))
    report = {**copy.deepcopy(state['report']), 'markdown': _report_markdown(run, state),
              'artifactIds': [item['id'] for item in artifacts]}
    ids = _unique(eid for claim in report['claims'] for eid in claim['evidenceIds'])
    refs = [{'claimId': claim['claimId'], 'version': claim['claimVersion']}
            for claim in report['claims'] if 'claimId' in claim and 'claimVersion' in claim]
    artifacts.append(_artifact(
        'report', 'report:live', run['contract']['question'], report,
        'completed' if report['ready'] else run['status'], run, list(nodes), refs, ids,
        report['artifactIds'], _source_refs(ids, evidence)))
    return artifacts, report


def project_task(snapshot: dict[str, Any], provider_public: dict[str, Any]) -> dict[str, Any]:
    """Return a detached TaskDetail using only one authoritative store snapshot."""
    snapshot = copy.deepcopy(snapshot)
    task = snapshot['task']
    brief = build_brief_view(snapshot)
    run = snapshot['runs'][-1] if snapshot['runs'] else None
    nodes = [node for node in snapshot['nodes'] if run and node['runId'] == run['runId']]
    exceptions = [item for item in snapshot['exceptions'] if run and item['runId'] == run['runId']]
    if run:
        # The legacy completed phase means the round ended, not scientific success.
        phase = 'completed' if run['status'] in ('completed', 'cancelled') else (
            'failed' if run['status'] == 'failed' else 'researching')
    else:
        phase = 'empty' if brief['status'] == 'empty' else 'requirements'
    raw_report = (run or {}).get('report')
    research = {
        'run': run,
        'runs': snapshot['runs'],
        'nodes': nodes,
        'exceptions': exceptions,
        'report': raw_report if raw_report is not None else {
            'ready': False, 'approved': False, 'claims': [], 'unresolved': [], 'summary': ''},
    }
    state = _state_view(snapshot, run, nodes, provider_public) if run else None
    markdown = render_brief(brief['draft'])
    contract = run['contract'] if run else (brief['draft'] or {}).get('content', {})
    artifacts, report = _workbench_artifacts(run, state) if run else (
        [], {'markdown': '', 'ready': False, 'approved': False, 'artifactIds': []})
    workbench = {
        'schemaVersion': 2,
        'workflowVersion': WORKFLOW_VERSION,
        'taskId': task['id'],
        'revision': snapshot['cursor'],
        'sourceEventId': snapshot['cursor'],
        'cursorKind': 'domain_event',
        'plan': _plan(contract),
        'draft': {
            'revision': task['draft'],
            'blocks': [{'id': 'brief:' + str(task['draft']), 'kind': 'requirements',
                        'title': '研究契约（只读）', 'content': markdown,
                        'source': 'brief', 'locked': True}] if brief['draft'] else [],
        },
        'artifacts': artifacts,
        'report': report,
        'executionSettings': {
            'revision': run['briefVersion'] if run else task['draft'],
            'maxTimeoutSeconds': min(90, contract.get('executionPolicy', {}).get('budget', {}).get('seconds', 0)),
            'materialIds': [],
        },
        'requirements': copy.deepcopy(state['requirements']) if state else [],
        'relations': [],
        'events': [],
        'research': research,
    }
    messages = []
    for message in snapshot['messages']:
        kind = 'requirements' if message.get('intent') in _SCOPE_INTENTS else 'progress'
        messages.append(dict(message, kind=kind))
    runs = []
    for index, item in enumerate(snapshot['runs'], 1):
        runs.append(dict(item, round=index, at=item.get('finishedAt') or item['createdAt'],
                         summary=(item.get('report') or {}).get('summary', ''),
                         mode=item.get('settings', {}).get('mode', 'evidence')))
    timestamps = [task['created']] + [message['at'] for message in messages]
    timestamps.extend(item['createdAt'] for item in snapshot['briefs'])
    timestamps.extend(item.get('finishedAt') or item['createdAt'] for item in snapshot['runs'])
    error = next((node['error']['message'] for node in reversed(state['nodes'])
                  if node['status'] == 'failed' and node['error']), None) if state else None
    return {
        'task': {'id': task['id'], 'title': task['title'], 'phase': phase,
                 'workflowVersion': WORKFLOW_VERSION, 'updatedAt': max(timestamps),
                 'round': len(snapshot['runs']) or 1},
        'taskMode': state['project']['taskMode'] if state else 'research',
        'workflowVersion': WORKFLOW_VERSION,
        'capabilities': {'conversationOnly': True, 'documentReadOnly': True,
                         'codeInspection': False, 'materialsImport': False},
        'workflowStatus': {'requirements': brief['status'], 'run': run['status'] if run else None},
        'brief': brief,
        'research': research,
        'exceptions': exceptions,
        'phase': phase,
        'document': {'markdown': markdown, 'revision': task['draft'], 'readOnly': True,
                     'polishing': brief['understandingPending'], 'polishedFrom': None,
                     'questions': [item['question'] for item in brief['clarificationQuestions']],
                     'source': 'brief', 'error': None},
        'messages': messages,
        'state': state,
        'error': error,
        'artifacts': [{'name': '研究报告与数据.zip', 'kind': 'archive',
                       'url': f"/api/tasks/{task['id']}/export"}] if raw_report and raw_report.get('ready') else [],
        'runs': runs,
        'workbench': workbench,
        'interactions': [],
        'modelReady': provider_public.get('capabilities', {}).get('modelReady', False),
    }
