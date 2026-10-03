"""Bind the scheduling tree to persistent research claims and expression records.

Workers produce observations. A separately configured blind judge assesses claims;
paper citations and completed processes are never automatically positive proof.
"""
from __future__ import annotations

import copy

from .claims import (ensure_graph, get_claim, create_claim, add_relation,
                     assess_claim, create_expression, revise_claim)


from .output_protocol import validate_relations


def bind_hypothesis(engine, item, hypothesis_id, parent_claim_ids=None):
    state = engine._state
    graph = ensure_graph(state)
    claim_id = 'claim:' + hypothesis_id.removeprefix('hypothesis-')
    scope = item.get('scope') or (state['project'].get('researchCycle', {}).get('background') or {}).get('boundaries', '')
    claim = next((c for c in graph['claims'] if c['id'] == claim_id and not c.get('archived')), None)
    if claim is None:
        # Reintroduced statements after rollback are new claims, with the old record preserved.
        if any(c['id'] == claim_id for c in graph['claims']):
            claim_id += ':round-' + str(state['project']['round']) + '-' + str(state['revision'])
        claim = create_claim(state, item['statement'], scope=scope,
            falsification=item['falsification'], origin={'kind': 'reproduction' if state['project'].get('taskMode') == 'reproduction' else 'hypothesis',
                'nodeId': 'central', 'hypothesisId': hypothesis_id, 'evidenceIds': item.get('evidenceIds', []),
                'reason': item['reason'], 'reproductionTarget': copy.deepcopy(item.get('reproductionTarget'))},
            parent_claim_ids=item.get('parentClaimIds') or parent_claim_ids, claim_id=claim_id)
    else:
        previous_target = claim.get('reproductionTarget', claim.get('origin', {}).get('reproductionTarget'))
        if (claim['statement'] != item['statement'].strip() or claim['scope'] != scope or
                claim['falsification'] != item['falsification'] or
                previous_target != item.get('reproductionTarget')):
            revise_claim(state, claim['id'], item['statement'], scope=scope,
                         falsification=item['falsification'], actor='AI', reason='上游研究范围变化，重新建立验证边界',
                         reproduction_target=item.get('reproductionTarget'))
        else:
            claim.setdefault('assessmentHistory', []).append(copy.deepcopy(claim['assessment']))
            claim['assessment'] = {'status': 'unassessed', 'reason': '重新启动此主张的取证',
                                   'evidenceIds': [], 'limitations': '', 'confirmedByUser': False}
    claim['archived'] = False
    claim['reproductionTarget'] = copy.deepcopy(item.get('reproductionTarget'))
    # The target is part of the immutable semantic boundary of this version.
    current_version = next(v for v in claim['versions'] if v['version'] == claim['version'])
    current_version.setdefault('reproductionTarget', copy.deepcopy(claim['reproductionTarget']))
    # Sources motivating a hypothesis are not observations confirming it.
    for evidence_id in item.get('evidenceIds', []):
        add_relation(state, claim['id'], evidence_id, reason='提出主张时引用的背景来源；尚未判定支持方向',
                     applicability=claim['scope'])
    return claim


def validate_reproduction(items, evidence):
    for item in items:
        target = item.get('reproductionTarget')
        if not isinstance(target, dict):
            raise ValueError('复现主张必须指定 reproductionTarget，包含论文、报告值、容差和实验条件')
        for key in ('paperId', 'metric', 'expected', 'tolerance', 'conditions'):
            if not isinstance(target.get(key), str) or not target[key].strip():
                raise ValueError('复现目标缺少 ' + key)
        ids = target.get('evidenceIds')
        known = {e['id']: e for e in evidence}
        if not isinstance(ids, list) or not ids or any(not isinstance(eid, str) or eid not in known or
                known[eid].get('paperId') != target['paperId'] or not known[eid].get('locator') for eid in ids):
            raise ValueError('复现目标必须引用该论文实际报告值的可定位证据；缺材料请先取证')


def output_relations(state, node, output, phase):
    claim_id = node['input'].get('claimId')
    if not claim_id:
        return
    claim = get_claim(state, claim_id)
    if node['input'].get('claimVersion') != claim['version']:
        raise ValueError('主张已修订，拒绝将旧版本的任务结果写入新版本')
    evidence = {e['id']: e for e in state['evidence']}
    explicit = output.get('structured', {}).get('evidenceRelations', [])
    validate_relations(explicit, set(evidence), {'id': claim_id, 'version': claim['version']})
    review = output.get('structured', {}).get('review', {})
    from .semantic_review import relation_gaps, review_admitted
    reviewed = review_admitted(review, 'judge')
    accepted = set(output.get('evidenceIds', []))
    for relation in explicit:
        if relation.get('claimId', claim_id) != claim_id or relation.get('claimVersion', claim['version']) != claim['version']:
            raise ValueError('节点不能将取证结果归到其他主张或版本')
        proposed = relation.get('polarity', 'unresolved')
        gaps = relation_gaps(relation, evidence[relation['evidenceId']], claim,
                             state['project'].get('researchEvidenceApprovals'), state['project'].get('round'))
        if relation['type'] == 'support' and proposed != 'unresolved' and not reviewed:
            gaps.append('生产节点的方向未经过独立裁判复核')
        link = add_relation(state, claim_id, relation['evidenceId'], relation_type=relation['type'],
            polarity='unresolved' if gaps else proposed, reason=relation['reason'],
            applicability=relation.get('applicability', ''),
            quality=relation.get('quality', 'limited') if relation['evidenceId'] in accepted else 'unusable')
        link.update({key: copy.deepcopy(relation.get(key)) for key in ('quote', 'locator', 'rule', 'confidence')})
        link.update(proposedPolarity=proposed, semanticGate={'passed': not gaps, 'issues': gaps}, review=copy.deepcopy(review))
        output['unresolved'].extend(gap for gap in gaps if gap not in output['unresolved'])
    represented = {r['evidenceId'] for r in explicit}
    for eid in accepted - represented:
        add_relation(state, claim_id, eid, reason='执行节点回传的材料；等待独立裁判判定支持方向',
                     applicability=claim['scope'])
    verdict = output.get('structured', {}).get('hypothesisVerdict')
    if not verdict or phase != 'aggregate' or node['id'] != claim['ownerNodeId']:
        return
    ids = verdict.get('evidenceIds', [])
    # A verdict alone cannot manufacture a directional evidence relationship.
    directed = {r['evidenceId'] for r in explicit if r['type'] == 'support'}
    for eid in ids:
        if eid not in directed:
            add_relation(state, claim_id, eid, polarity='unresolved', reason=verdict['reason'],
                         applicability=claim['scope'], quality='limited')
        if verdict.get('limitations'):
            add_relation(state, claim_id, eid, relation_type='qualify',
                         reason=verdict['limitations'], applicability=claim['scope'])
    current = [r for r in state['claimGraph']['relations'] if r['claimId'] == claim_id and
               r['claimVersion'] == claim['version'] and r['type'] == 'support' and
               r['quality'] != 'unusable' and r.get('semanticGate', {}).get('passed') and
               review_admitted(r.get('review', {}), 'judge') and evidence.get(r['evidenceId'], {}).get('locator')]
    directions = {r['polarity'] for r in current}
    status = 'mixed' if 'mixed' in directions or {'for', 'against'}.issubset(directions) else verdict['status']
    if status == 'mixed':
        ids = list(dict.fromkeys(ids + [r['evidenceId'] for r in current if r['polarity'] != 'unresolved']))
    if status == 'supported' and 'for' not in directions or status == 'refuted' and 'against' not in directions:
        status = 'inconclusive'
    covered = {r['evidenceId'] for r in current if r['polarity'] != 'unresolved'}
    omitted_counterevidence = [r['evidenceId'] for r in state['claimGraph']['relations'] if
        r['claimId'] == claim_id and r['claimVersion'] == claim['version'] and
        r.get('proposedPolarity', r['polarity']) in ('against', 'mixed') and
        r['evidenceId'] not in {item['evidenceId'] for item in explicit}]
    if status == 'supported' and omitted_counterevidence:
        status = 'inconclusive'
        ids = list(dict.fromkeys(ids + omitted_counterevidence))
    if not reviewed or (status in ('supported', 'refuted') and not set(ids).issubset(covered)):
        status = 'inconclusive'
    assess_claim(state, claim_id, status, verdict['reason'], evidence_ids=ids,
                 limitations=verdict.get('limitations', ''))
    claim['assessment']['review'] = copy.deepcopy(review) or {'status': 'unreviewed', 'independent': False, 'role': 'main', 'needsHumanReview': True}
    if review.get('reviewLevel') == 'same_model':
        notice = '同一模型分角色复核，仅为候选判断，尚无独立科学验证。'
        if notice not in claim['assessment']['limitations']:
            claim['assessment']['limitations'] += '；' + notice
    if status in ('mixed', 'inconclusive') and verdict['status'] != 'inconclusive':
        verdict['status'] = 'inconclusive'
        verdict['evidenceIds'] = ids
        output['evidenceIds'] = list(dict.fromkeys(output['evidenceIds'] + ids))
        verdict['reason'] += '；独立复核或证据门尚未支持定论，保留冲突与缺口。'
        output['summary'] = verdict['reason']
        output['claims'] = []
        claim['assessment']['reason'] = verdict['reason']
        for h in state['project'].get('researchCycle', {}).get('hypotheses', []):
            if h.get('claimId') == claim_id:
                h.update(status='inconclusive', verdict=copy.deepcopy(verdict))


def claim_intervention(engine, node, payload):
    """Return true only for an editorial owner edit requiring no task rerun."""
    claim_id = payload.get('claimId') or node['input'].get('claimId')
    if not claim_id:
        return
    claim = get_claim(engine._state, claim_id)
    kind = payload['kind']
    if node['id'] != claim['ownerNodeId']:
        return
    if kind == 'modify':
        previous_version = claim['version']
        target = {'reproduction_target': payload['reproductionTarget']} if 'reproductionTarget' in payload else {}
        claim = revise_claim(engine._state, claim_id, payload['text'], scope=payload.get('scope'),
                             falsification=payload.get('falsification'), reason=payload['text'], **target)
        current_target = claim.get('reproductionTarget', claim.get('origin', {}).get('reproductionTarget'))
        node['title'] = claim['statement'][:120]
        node['input']['claimVersion'] = claim['version']
        node['input']['hypothesis'] = {**node['input'].get('hypothesis', {}),
            'statement': claim['statement'], 'scope': claim['scope'], 'falsification': claim['falsification'],
            'reproductionTarget': copy.deepcopy(current_target)}
        for hypothesis in engine._state['project'].get('researchCycle', {}).get('hypotheses', []):
            if hypothesis.get('nodeId') == node['id']:
                hypothesis.update(statement=claim['statement'], scope=claim['scope'],
                                  falsification=claim['falsification'], claimVersion=claim['version'],
                                  reproductionTarget=copy.deepcopy(current_target))
        editorial_fields = {'claimId', 'nodeId', 'kind', 'text', 'scope', 'falsification',
                            'reproductionTarget', 'expectedRevision'}
        return claim['version'] == previous_version and set(payload).issubset(editorial_fields)
    if kind == 'reject':
        claim.setdefault('decisions', []).append({'actor': 'user', 'decision': 'reject',
            'version': claim['version'], 'reason': payload['text'], 'at': engine._cycle_now()})


def invalidate_claims(state, affected, reason):
    graph = ensure_graph(state)
    claim_ids = {n['input'].get('claimId') for n in state['nodes'] if n['id'] in affected}
    for claim in graph['claims']:
        if claim['id'] in claim_ids and not claim.get('archived'):
            claim.setdefault('assessmentHistory', []).append(copy.deepcopy(claim['assessment']))
            claim['assessment'] = {'status': 'unassessed', 'reason': reason, 'evidenceIds': [],
                                   'limitations': '', 'confirmedByUser': False}
    for expression in graph['expressions']:
        if any(ref['claimId'] in claim_ids for ref in expression.get('claimRefs', [])):
            expression.update(stale=True, status='draft', staleReason=reason)


def report_from_claims(state):
    """Expression is a versioned view of claims, not a second source of truth."""
    graph = ensure_graph(state)
    report = state['report']
    active = {n['id'] for n in state['nodes'] if n.get('active') and not n['input'].get('superseded')}
    selected = [c for c in graph['claims'] if c.get('ownerNodeId') in active and not c.get('archived')]
    conclusion = report.get('structured', {}).get('researchConclusion')
    if state['project'].get('researchCycle') and conclusion is not None:
        admitted = set(conclusion.get('evidenceIds', []))
        selected = [c for c in selected if set(c['assessment'].get('evidenceIds', [])).issubset(admitted)]
    if not state['project'].get('researchCycle'):
        # Material-audit mode also records its findings as provisional claims.
        for candidate in report.get('claims', []):
            claim_id = candidate.get('claimId') or 'claim:result:' + candidate['id']
            claim = next((c for c in graph['claims'] if c['id'] == claim_id), None)
            if claim is None:
                claim = create_claim(state, candidate['text'], origin={'kind': 'finding',
                    'nodeId': candidate.get('nodeId'), 'evidenceIds': candidate.get('evidenceIds', [])},
                    owner_node_id=candidate.get('nodeId'), claim_id=claim_id)
                for eid in candidate.get('evidenceIds', []):
                    add_relation(state, claim_id, eid, reason='资料核验候选；尚未判定支持方向')
            if candidate.get('limitations') and not claim['assessment'].get('limitations'):
                claim['assessment']['limitations'] = candidate['limitations']
            if claim not in selected:
                selected.append(claim)
    rows = []
    refs = []
    lines = ['# ' + state['project']['title'], '', '当前为可审阅的研究草稿。用户确认记录不等同外部科学验证。', '',
             '## 问题与研究范围', '', state['project'].get('description', ''), '', '## 主张与证据', '']
    status_names = {'supported': '模型复核认为支持（候选）', 'refuted': '模型复核认为反对（候选）', 'mixed': '证据存在冲突',
                    'unassessed': '尚待论证', 'inconclusive': '证据尚不足'}
    for claim in selected:
        assessment = claim['assessment']
        links = [r for r in graph['relations'] if r['claimId'] == claim['id'] and r['claimVersion'] == claim['version']]
        ids = list(dict.fromkeys(r['evidenceId'] for r in links))
        refs.append({'claimId': claim['id'], 'version': claim['version']})
        rows.append({'id': claim['id'], 'claimId': claim['id'], 'claimVersion': claim['version'],
            'text': claim['statement'], 'nodeId': claim.get('ownerNodeId'), 'evidenceIds': ids,
            'status': 'confirmed' if assessment.get('confirmedByUser') else 'candidate',
            'assessmentStatus': assessment['status'], 'limitations':
                ('无证据：该主张尚待验证。' if not ids else '') + assessment.get('limitations', ''),
            'review': copy.deepcopy(assessment.get('review', {}))})
        lines.extend(['### ' + claim['statement'], '', f"主张 `{claim['id']}` · 版本 {claim['version']}", '',
            '判断：' + status_names[assessment['status']], '', '范围：' + (claim['scope'] or '尚未明确'), '',
            '可证伪条件：' + (claim['falsification'] or '尚未明确'), '', assessment.get('reason', ''), ''])
        if not links:
            lines.extend(['**无证据**：不能作为已验证结论。', ''])
        elif assessment['status'] in ('unassessed', 'inconclusive'):
            lines.extend(['**证据不足**：有关联材料，但不能据此宣布主张成立。', ''])
        for relation in links:
            direction = '细化条件' if relation['type'] == 'qualify' else {
                'for': '支持', 'against': '反对', 'mixed': '混合', 'unresolved': '方向未定'}[relation['polarity']]
            lines.append(f"- {direction} · {relation['evidenceId']} · {relation['reason']}")
        lines.extend(['', '局限：' + (assessment.get('limitations') or '待进一步核验'), ''])
    lines.extend(['## 未决问题', ''] + ['- ' + issue for issue in report.get('unresolved', [])])
    expression = create_expression(state, 'reproduction_report' if state['project'].get('taskMode') == 'reproduction' else 'paper',
        state['project']['title'], '\n'.join(lines), refs)
    report.update(claims=rows, expressionId=expression['id'], claimRefs=refs)
