"""Requirement impact and revision, using the existing engine dependency graph."""
import copy

from .claims import get_claim, revise_claim


def impact(engine, values):
    requirements = engine._validated_requirements(values)
    old = {r['id']: r for r in engine._state['requirements']}
    new = {r['id']: r for r in requirements}
    changed = {rid for rid in set(old) | set(new) if old.get(rid) != new.get(rid)}
    global_change = set(old) != set(new) or any(old[r].get('constraints') != new[r].get('constraints')
                                                for r in set(old) & set(new))
    nodes = [n for n in engine._state['nodes'] if n['active'] and not n['input'].get('superseded')]
    owners = [n for n in nodes if n['id'] != 'central' and set(n.get('requirementIds', [])) & changed]
    # Missing attribution cannot justify declaring any branch unaffected.
    if changed and (global_change or not owners):
        affected = engine._affected('central', 'modify')
        full_reset = True
    else:
        affected = list(dict.fromkeys(i for n in owners for i in engine._affected(n['id'], 'modify')))
        full_reset = False
    return {'revision': engine._state['revision'], 'affectedIds': affected,
            'changedRequirementIds': sorted(changed), 'fullReset': full_reset,
            'requirements': requirements}


def apply(engine, payload):
    if payload.get('expectedRevision') != engine._state['revision']:
        raise ValueError('研究状态版本已变化，请重新预览草稿修改')
    change = impact(engine, payload.get('requirements'))
    if not change['changedRequirementIds']:
        return
    reason = payload.get('reason') or '研究需求条目已修改'
    if change['fullReset']:
        engine._intervene({'nodeId': 'central', 'kind': 'modify', 'text': reason,
                           'requirements': change['requirements'], 'expectedRevision': payload['expectedRevision']})
        return
    affected = change['affectedIds']
    engine._state['requirements'] = change['requirements']
    root = engine._get_node('central')
    for key in ('description', 'acceptance', 'constraints'):
        root['input'][key] = '；'.join(r[key] for r in change['requirements'])
    root['requirementIds'] = [r['id'] for r in change['requirements']]
    by_id = {r['id']: r for r in change['requirements']}
    revised_claims = set()
    for node in engine._state['nodes']:
        if node['id'] not in affected:
            continue
        cid = node['input'].get('claimId')
        if cid and cid not in revised_claims:
            claim = get_claim(engine._state, cid)
            if not claim.get('archived'):
                scope = claim['scope'].split('\n\n当前需求边界：', 1)[0]
                boundary = '\n'.join(by_id[r]['description'] for r in node.get('requirementIds', []) if r in by_id)
                if boundary:
                    revise_claim(engine._state, cid, claim['statement'], scope=scope+'\n\n当前需求边界：'+boundary,
                                 actor='user', reason=reason)
                revised_claims.add(cid)
    for node in engine._state['nodes']:
        cid = node['input'].get('claimId')
        if node['id'] in affected and cid in revised_claims:
            node['input']['claimVersion'] = get_claim(engine._state, cid)['version']
    engine._invalidate(affected, reason, supersede_decision=True)
    engine._clear_report_gate()
    engine._state['stage'] = 6
    engine._state['paused'] = engine._manual_paused or (
        engine._autonomous() and not engine._state['project'].get('researchStarted'))
    engine._history('requirements-revised', reason=reason, requirements=copy.deepcopy(change['requirements']),
                    changedRequirementIds=change['changedRequirementIds'], affectedIds=affected)
    engine._activity('user', '已更新需求条目并重新调度相关分支：' + reason)
