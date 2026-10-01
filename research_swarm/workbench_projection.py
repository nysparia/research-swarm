"""Read-only workbench views of authoritative claim/evidence engine state.

Artifact dependency links describe invalidation, never scientific support.
Only graph assessments supply claim truth. Citations are source references.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .draft_blocks import reconcile_blocks, render_blocks
from .research_plan import infer_plan


def _unique(values):
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v))


def _key(kind, source):
    # Source IDs are already task-local; the per-task store enforces isolation.
    return kind + ':' + str(source)


def _artifact(kind, source, title, content, status='draft', node_ids=(), claim_refs=(),
              evidence_ids=(), dependencies=(), source_revision=1, source_refs=()):
    return {'id': _key(kind, source), 'kind': kind, 'title': str(title or source),
            'status': status, 'content': copy.deepcopy(content), 'nodeIds': _unique(node_ids),
            'claimRefs': copy.deepcopy(list(claim_refs)), 'evidenceIds': _unique(evidence_ids),
            'dependencies': _unique(dependencies), 'sourceRevision': source_revision,
            'sourceRefs': copy.deepcopy(list(source_refs))}


def _source_refs(ids, evidence):
    return [{key: copy.deepcopy(item[key]) for key in ('id', 'type', 'paperId', 'locator', 'path', 'url', 'sha256') if key in item}
            for eid in ids if (item := evidence.get(eid)) is not None]


def _signature(artifact):
    return json.dumps({key: value for key, value in artifact.items()
                       if key not in ('revision', 'dependencyVersions', 'status', 'staleReason')},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def project_workbench(record, state=None, files=(), previous=None):
    """Build a pure projection and preserve invalidated outputs for inspection."""
    if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']:
        raise ValueError('Workbench record requires a task ID')
    if state is not None and not isinstance(state, dict):
        raise ValueError('Invalid engine state')
    previous = previous or {}
    state = state or {}
    document = record.get('document') or {}
    old_blocks = (previous.get('draft') or {}).get('blocks', [])
    if 'draftBlocks' in record:
        blocks = copy.deepcopy(record['draftBlocks'])
        # Rendering validates the explicit authoritative block list.
        render_blocks(blocks)
    else:
        blocks = reconcile_blocks(document.get('markdown', ''), old_blocks, actor='AI')
    draft = {'revision': document.get('revision', 0), 'blocks': blocks}
    plan = copy.deepcopy(record['plan']) if 'plan' in record else infer_plan(render_blocks(blocks), record.get('taskMode', 'research'))
    old = {a['id']: a for a in previous.get('artifacts', [])}
    artifacts = []
    evidence = {e['id']: e for e in state.get('evidence', []) if isinstance(e, dict) and e.get('id')}
    nodes = {n['id']: n for n in state.get('nodes', []) if isinstance(n, dict) and n.get('id')}
    graph = state.get('claimGraph') or {}
    claims = {c['id']: c for c in graph.get('claims', []) if isinstance(c, dict) and c.get('id')}
    node_dependencies = {nid: list((n.get('input') or {}).get('dependsOn') or []) for nid, n in nodes.items()}
    for edge in state.get('edges', []):
        if edge.get('type') in ('dependency', 'depends', 'requires') and not edge.get('informational'):
            if edge.get('target') in node_dependencies:
                node_dependencies[edge['target']].append(edge.get('source'))
    for nid, node in nodes.items():
        output = node.get('output')
        aid = _key('node_output', nid)
        dependencies = [_key('node_output', dep) for dep in _unique(node_dependencies[nid])]
        if output is None:
            if aid in old:
                artifact = copy.deepcopy(old[aid])
                artifact.update(status='stale', staleReason='Source output invalidated or awaiting a new execution', dependencies=dependencies)
                artifacts.append(artifact)
            continue
        if not isinstance(output, dict):
            output = {'summary': str(output)}
        refs = list(output.get('claimRefs') or [])
        refs.extend({'claimId': cid, 'version': c.get('version', 1)} for cid, c in claims.items()
                    if c.get('ownerNodeId') == nid and not any(r.get('claimId') == cid for r in refs))
        ids = _unique(list(output.get('evidenceIds') or []) + list(node.get('evidenceIds') or []))
        artifact = _artifact('node_output', nid, node.get('title'), output, node.get('status', 'draft'),
                             [nid], refs, ids, dependencies, node.get('version', 1), _source_refs(ids, evidence))
        if not node.get('active', True) or (node.get('input') or {}).get('superseded'):
            artifact.update(status='stale', staleReason='Node archived or superseded')
        artifacts.append(artifact)
    for cid, claim in claims.items():
        version = claim.get('version', 1)
        relations = [r for r in graph.get('relations', []) if r.get('claimId') == cid and r.get('claimVersion') == version]
        assessment = claim.get('assessment') or {'status': 'unassessed'}
        ids = _unique(list(assessment.get('evidenceIds') or []) + [r.get('evidenceId') for r in relations]
                      + list((claim.get('origin') or {}).get('evidenceIds') or []))
        owner = claim.get('ownerNodeId') or (claim.get('origin') or {}).get('nodeId')
        dependencies = ([_key('node_output', owner)] if owner else [])
        dependencies.extend(_key('claim', parent) for parent in claim.get('parentClaimIds', []))
        artifact = _artifact('claim', cid, claim.get('statement', cid), dict(claim, relations=relations),
                             'stale' if claim.get('archived') else assessment.get('status', 'unassessed'),
                             [owner] if owner else [], [{'claimId': cid, 'version': version}], ids,
                             dependencies, version, _source_refs(ids, evidence))
        if claim.get('archived'):
            artifact['staleReason'] = 'Claim archived by the engine'
        artifacts.append(artifact)
    for expression in graph.get('expressions', []):
        refs = expression.get('claimRefs') or []
        ids = _unique(eid for ref in refs for eid in (claims.get(ref.get('claimId'), {}).get('assessment') or {}).get('evidenceIds', [])
                      if claims.get(ref.get('claimId'), {}).get('version') == ref.get('version'))
        stale = expression.get('stale') or any(ref.get('claimId') not in claims
            or claims[ref['claimId']].get('archived') or claims[ref['claimId']].get('version', 1) != ref.get('version') for ref in refs)
        artifact = _artifact('expression', expression['id'], expression.get('title'), expression,
                             'stale' if stale else expression.get('status', 'draft'),
                             [claims.get(ref.get('claimId'), {}).get('ownerNodeId') for ref in refs], refs, ids,
                             [_key('claim', ref['claimId']) for ref in refs],
                             expression.get('version', hashlib.sha256(str(expression.get('markdown', '')).encode()).hexdigest()),
                             _source_refs(ids, evidence))
        if stale:
            artifact['staleReason'] = 'Expression references superseded or missing claim versions'
        artifacts.append(artifact)
    if not isinstance(files, (list, tuple)):
        raise ValueError('Files must be an explicit list of output metadata')
    for file in files:
        if not isinstance(file, dict) or not (file.get('id') or file.get('path') or file.get('url')):
            raise ValueError('Output file requires an identity or path')
        source = file.get('id') or file.get('path') or file.get('url')
        artifacts.append(_artifact('file', source, file.get('title') or file.get('name'), file,
                                   file.get('status', 'available'), file.get('nodeIds') or ([file['nodeId']] if file.get('nodeId') else []),
                                   file.get('claimRefs', []), file.get('evidenceIds', []), file.get('dependencies', []),
                                   file.get('sha256') or file.get('revision', 1), [file]))
    for material in record.get('materials') or []:
        if not isinstance(material, dict) or not material.get('id'):
            raise ValueError('Managed material requires an ID')
        artifacts.append(_artifact('material', material['id'], material.get('name') or material.get('title'),
                                   material, 'available', source_revision=material.get('revision', 1), source_refs=[material]))
    for job in record.get('jobs') or []:
        if not isinstance(job, dict) or not job.get('id'):
            raise ValueError('Managed experiment job requires an ID')
        request = job.get('request') or {}
        result = job.get('result') or {}
        node_ids = _unique(list(job.get('nodeIds') or []) +
                           [request.get('nodeId'), result.get('nodeId'), job.get('nodeId')])
        material_ids = request.get('materialIds', job.get('materialIds', []))
        result_evidence = [item for item in result.get('evidence', []) if isinstance(item, dict)]
        evidence_ids = _unique(list(job.get('evidenceIds') or []) + list(result.get('evidenceIds') or [])
                               + [item.get('id') for item in result_evidence])
        output_files = result.get('artifacts', job.get('artifacts', []))
        source_refs = [dict(item, jobId=job['id']) for item in output_files if isinstance(item, dict)]
        source_refs.extend(dict(item, jobId=job['id']) for item in result_evidence)
        source_refs.extend(dict(item, jobId=job['id']) for item in _source_refs(evidence_ids, evidence)
                           if item['id'] not in {e.get('id') for e in result_evidence})
        for key in ('receipt', 'script', 'stdoutPath', 'stderrPath'):
            if isinstance(result.get(key), str) and result[key]:
                source_refs.append({'kind': key, 'path': result[key], 'jobId': job['id']})
        # Receipts for earlier attempts remain traceable even after retry/resume.
        for attempt in job.get('attempts', []):
            receipt = attempt.get('receipt')
            if isinstance(receipt, dict):
                source_refs.append(dict(receipt, kind='receipt', jobId=job['id']))
        artifact = _artifact('experiment_job', job['id'], job.get('title') or job.get('name') or '实验 ' + job['id'],
                             job, job.get('status', 'queued'), node_ids,
                             job.get('claimRefs', []), evidence_ids,
                             [_key('material', mid) for mid in material_ids], job.get('revision', 1), source_refs)
        # Bind managed execution records to their original engine ownership.
        # The raw process result remains a historical fact in content/receipts;
        # no dependency on the owner's output is needed (or safe while running).
        owner_id = request.get('nodeId') or result.get('nodeId') or job.get('nodeId')
        if owner_id:
            owner = nodes.get(owner_id)
            owner_version = request.get('nodeVersion', result.get('nodeVersion', job.get('nodeVersion')))
            job_round = request.get('round', result.get('round', job.get('round')))
            current_round = (state.get('project') or {}).get('round')
            if not owner or not owner.get('active', True) or (owner.get('input') or {}).get('superseded'):
                artifact.update(status='stale', staleReason='Historical experiment: owner is absent or archived')
            elif owner_version is not None and owner.get('version') is not None and owner_version != owner['version']:
                artifact.update(status='stale', staleReason='Historical experiment: owner node version changed')
            elif job_round is not None and current_round is not None and job_round != current_round:
                artifact.update(status='stale', staleReason='Historical experiment: research round changed')
        artifacts.append(artifact)
    by_id = {artifact['id']: artifact for artifact in artifacts}
    if len(by_id) != len(artifacts):
        raise ValueError('Duplicate artifact identities')
    for aid, artifact in old.items():
        if aid not in by_id and artifact['kind'] != 'report':
            preserved = copy.deepcopy(artifact)
            preserved.update(status='stale', staleReason='Source is absent from the current projection')
            artifacts.append(preserved)
            by_id[aid] = preserved
    # Propagate only explicit causal dependencies, never informational comparison.
    for artifact in artifacts:
        prior = old.get(artifact['id'])
        if prior and _signature(prior) == _signature(artifact):
            if prior.get('status') == 'stale':
                artifact.update(status='stale', staleReason=prior.get('staleReason', 'Previously invalidated output'))
            for dep in artifact['dependencies']:
                if dep in old and dep in by_id and _signature(old[dep]) != _signature(by_id[dep]):
                    artifact.update(status='stale', staleReason='An upstream output changed; this output has not been regenerated')
    for _ in range(len(artifacts)):
        changed = False
        for artifact in artifacts:
            if artifact['status'] == 'stale':
                continue
            if any(dep not in by_id or by_id[dep]['status'] == 'stale' for dep in artifact['dependencies']):
                artifact.update(status='stale', staleReason='An upstream dependency is stale or absent')
                changed = True
        if not changed:
            break
    report = _live_report(record, state, artifacts)
    report_artifact = _artifact('report', 'live', record.get('title', 'Research report'), report,
                                'completed' if report['ready'] else 'live',
                                [nid for a in artifacts for nid in a['nodeIds']],
                                [ref for a in artifacts if a['kind'] == 'claim' for ref in a['claimRefs']],
                                [eid for a in artifacts for eid in a['evidenceIds']],
                                [a['id'] for a in artifacts if a['kind'] in ('node_output', 'claim', 'expression')],
                                state.get('revision', document.get('revision', 0)))
    artifacts.append(report_artifact)
    return {'revision': previous.get('revision', 0), 'taskId': record['id'], 'plan': plan,
            'draft': draft, 'artifacts': artifacts, 'report': report,
            'executionSettings': copy.deepcopy(record.get('executionSettings') or {})}


def _live_report(record, state, artifacts):
    original = copy.deepcopy(state.get('report') or {})
    lines = ['# ' + str(record.get('title') or 'Research report'), '',
             '研究状态：' + str(state.get('status') or record.get('phase') or 'draft'),
             '以下为持续更新的工作记录；来源引用不等于科学支持。']
    if original.get('summary'):
        lines.extend(['', str(original['summary'])])
    outputs = [a for a in artifacts if a['kind'] == 'node_output']
    if outputs:
        lines.extend(['', '## 节点输出'])
    for artifact in outputs:
        lines.extend(['', '### ' + artifact['title'] + ' [' + artifact['status'] + ']',
                      str(artifact['content'].get('summary') or artifact['content'].get('markdown') or '')])
    claims = [a for a in artifacts if a['kind'] == 'claim']
    if claims:
        lines.extend(['', '## 主张与评估'])
    for artifact in claims:
        assessment = artifact['content'].get('assessment') or {}
        lines.append('- ' + artifact['title'] + ' [' + str(assessment.get('status', 'unassessed')) +
                     ('; stale' if artifact['status'] == 'stale' else '') + ']')
        if assessment.get('limitations'):
            lines.append('  局限：' + str(assessment['limitations']))
    jobs = [a for a in artifacts if a['kind'] == 'experiment_job']
    if jobs:
        lines.extend(['', '## 实验执行记录', '执行结果是工作记录，科学评估仍由主张与证据图决定。'])
    for artifact in jobs:
        lines.append('- ' + artifact['title'] + ' [' + artifact['status'] + ']')
        error = artifact['content'].get('error') or (artifact['content'].get('result') or {}).get('error')
        if error:
            lines.append('  错误：' + str(error))
    original.update(markdown='\n'.join(lines), ready=bool(original.get('ready')), approved=bool(original.get('approved')),
                    live=True, artifactIds=[a['id'] for a in artifacts])
    return original
