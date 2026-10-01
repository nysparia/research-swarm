"""Persistent claim, evidence and expression records for the research workspace.

The caller holds the engine lock. This module deliberately does not infer truth
from citations, source counts, or an execution's success.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone


STATUSES = {'unassessed', 'supported', 'refuted', 'mixed', 'inconclusive'}
POLARITIES = {'for', 'against', 'mixed', 'unresolved'}
QUALITIES = {'usable', 'limited', 'unusable'}
EXPRESSION_KINDS = {'paper', 'reproduction_report'}
_UNCHANGED = object()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(prefix):
    return prefix + ':' + uuid.uuid4().hex


def _assessment():
    return {'status': 'unassessed', 'reason': '', 'evidenceIds': [],
            'limitations': '', 'confirmedByUser': False}


def _material(graph, kind, source_id, source, material_id=None):
    key = material_id or 'material:' + kind + ':' + str(source_id)
    material = next((m for m in graph['materials'] if m['id'] == key), None)
    if material is None:
        material = {'id': key, 'kind': kind, 'sourceId': source_id}
        graph['materials'].append(material)
    # Metadata is a view of the source record; preserve material identity.
    material.update({k: copy.deepcopy(v) for k, v in source.items()
                     if k in ('title', 'path', 'url', 'sha256', 'type', 'locator', 'paperId')})
    return material


def ensure_graph(state):
    state.setdefault('project', {}).setdefault('taskMode', 'research')
    migrating_legacy = 'claimGraph' not in state
    graph = state.setdefault('claimGraph', {'schemaVersion': 1, 'claims': [],
        'relations': [], 'expressions': [], 'materials': []})
    if graph.get('schemaVersion') != 1:
        raise ValueError('Unsupported claim graph schema')
    for key in ('claims', 'relations', 'expressions', 'materials'):
        graph.setdefault(key, [])
    for paper in state.get('papers', []):
        if paper.get('id') is not None:
            _material(graph, 'paper', paper['id'], paper)
    for evidence in state.get('evidence', []):
        if evidence.get('id') is not None and not evidence.get('paperId'):
            kind = ('experiment' if evidence.get('type') == 'experiment' else
                    'dataset' if evidence.get('type') in ('dataset', 'data') else 'evidence')
            # Keep the original material ID if an older graph used kind=evidence.
            _material(graph, kind, evidence['id'], evidence,
                      material_id='material:evidence:' + str(evidence['id']))

    cycle = state.get('project', {}).get('researchCycle') or {}
    hypotheses = cycle.get('hypotheses', [])
    nodes = state.get('nodes', [])
    for h in hypotheses:
        old_id = h.get('id')
        if not old_id:
            continue
        claim_id = h.get('claimId') or 'claim:' + str(old_id).removeprefix('hypothesis-')
        claim = next((c for c in graph['claims'] if c['id'] == claim_id), None)
        needs_import = claim is None
        if needs_import:
            claim = create_claim(state, h.get('statement') or h.get('text') or str(old_id),
                scope=h.get('scope', ''), falsification=h.get('falsification', ''),
                origin={'kind': 'hypothesis', 'nodeId': h.get('nodeId'),
                        'hypothesisId': old_id, 'evidenceIds': list(h.get('evidenceIds') or [])},
                owner_node_id=h.get('nodeId'), claim_id=claim_id, actor='migration')
        h['claimId'] = claim_id
        h.setdefault('claimVersion', claim['version'])
        if needs_import:
            for eid in h.get('evidenceIds') or []:
                _legacy_relation(state, claim_id, eid, 'unresolved')
            verdict = h.get('verdict') or {}
            direction = {'supported': 'for', 'refuted': 'against', 'mixed': 'mixed'}.get(verdict.get('status'))
            if direction:
                for eid in verdict.get('evidenceIds') or []:
                    _legacy_relation(state, claim_id, eid, direction,
                                     reason=verdict.get('reason', ''))
            if verdict.get('status') in STATUSES:
                verdict_ids = list(verdict.get('evidenceIds') or [])
                located = {e.get('id') for e in state.get('evidence', [])
                           if str(e.get('locator') or '').strip()}
                status = verdict['status']
                if status in ('supported', 'refuted', 'mixed') and (
                        not verdict_ids or not set(verdict_ids).issubset(located)):
                    status = 'inconclusive'
                claim['assessment'] = {'status': status, 'reason': verdict.get('reason', ''),
                    'evidenceIds': verdict_ids,
                    'limitations': verdict.get('limitations', ''), 'confirmedByUser': False}

    # Report rows are a view. Import them only for an envelope predating claimGraph;
    # later model output must pass the explicit claim API to enter the graph.
    for row in (state.get('report') or {}).get('claims', []) if migrating_legacy else []:
        if not isinstance(row, dict) or not (row.get('id') or row.get('claimId')):
            continue
        claim_id = row.get('claimId') or 'claim:result:' + str(row['id'])
        legacy_row = not row.get('claimId') or not any(c['id'] == claim_id for c in graph['claims'])
        if not any(c['id'] == claim_id for c in graph['claims']):
            create_claim(state, row.get('text') or row.get('statement') or str(row['id']),
                origin={'kind': 'legacy', 'nodeId': row.get('nodeId'),
                        'evidenceIds': list(row.get('evidenceIds') or [])},
                owner_node_id=row.get('nodeId'), claim_id=claim_id, actor='migration')
        row['claimId'] = claim_id
        row.setdefault('claimVersion', get_claim(state, claim_id)['version'])
        if legacy_row:
            for eid in row.get('evidenceIds') or []:
                _legacy_relation(state, claim_id, eid, 'unresolved')

    by_id = {n.get('id'): n for n in nodes}
    for h in hypotheses:
        if h.get('nodeId') in by_id and h.get('claimId'):
            owner_input = by_id[h['nodeId']].setdefault('input', {})
            owner_input.setdefault('claimId', h['claimId'])
            owner_input.setdefault('claimVersion', h['claimVersion'])
    # Stored node order need not be parent first.
    for _ in nodes:
        changed = False
        for node in nodes:
            parent = by_id.get(node.get('parentId'))
            if parent and parent.get('input', {}).get('claimId'):
                target = node.setdefault('input', {})
                if not target.get('claimId'):
                    target['claimId'] = parent['input']['claimId']
                    target['claimVersion'] = parent['input']['claimVersion']
                    changed = True
                elif target.get('claimVersion') is None and target['claimId'] == parent['input']['claimId']:
                    target['claimVersion'] = parent['input']['claimVersion']
                    changed = True
        if not changed:
            break
    return graph


def _legacy_relation(state, claim_id, evidence_id, polarity, reason=''):
    graph = state['claimGraph']
    if not any(e.get('id') == evidence_id for e in state.get('evidence', [])):
        return
    if any(r['claimId'] == claim_id and r['evidenceId'] == evidence_id and
           r['polarity'] == polarity and r['claimVersion'] == get_claim(state, claim_id)['version']
           for r in graph['relations']):
        return
    add_relation(state, claim_id, evidence_id, polarity=polarity,
                 reason=reason, quality='limited')


def get_claim(state, claim_id):
    graph = ensure_graph(state) if 'claimGraph' not in state else state['claimGraph']
    for claim in graph['claims']:
        if claim['id'] == claim_id:
            return claim
    raise ValueError('Unknown claim: ' + str(claim_id))


def create_claim(state, statement, scope='', falsification='', origin=None,
                 owner_node_id=None, parent_claim_ids=None, claim_id=None, actor='AI'):
    graph = ensure_graph(state) if 'claimGraph' not in state else state['claimGraph']
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError('Claim statement is required')
    claim_id = claim_id or _id('claim')
    if any(c['id'] == claim_id for c in graph['claims']):
        raise ValueError('Duplicate claim ID: ' + claim_id)
    parents = list(parent_claim_ids or [])
    if any(not any(c['id'] == p for c in graph['claims']) for p in parents):
        raise ValueError('Unknown parent claim')
    at = _now()
    version = {'version': 1, 'statement': statement.strip(), 'scope': scope or '',
        'falsification': falsification or '', 'actor': actor, 'reason': '', 'at': at}
    claim = {'id': claim_id, 'statement': version['statement'], 'scope': version['scope'],
        'falsification': version['falsification'], 'origin': copy.deepcopy(origin or {'kind': 'user'}),
        'ownerNodeId': owner_node_id, 'parentClaimIds': parents, 'version': 1,
        'versions': [version], 'assessment': _assessment(), 'createdAt': at,
        'updatedAt': at, 'archived': False}
    graph['claims'].append(claim)
    return claim


def revise_claim(state, claim_id, statement, scope=None, falsification=None,
                 actor='user', reason='', reproduction_target=_UNCHANGED):
    claim = get_claim(state, claim_id)
    if claim.get('archived'):
        raise ValueError('Archived claim cannot be revised')
    if not isinstance(statement, str) or not statement.strip():
        raise ValueError('Claim statement is required')
    at = _now()
    claim.setdefault('assessmentHistory', []).append(copy.deepcopy(claim['assessment']))
    claim['version'] = max(v['version'] for v in claim['versions']) + 1
    claim['statement'] = statement.strip()
    claim['scope'] = claim['scope'] if scope is None else scope
    claim['falsification'] = claim['falsification'] if falsification is None else falsification
    if reproduction_target is not _UNCHANGED:
        claim['reproductionTarget'] = copy.deepcopy(reproduction_target)
    claim['versions'].append({'version': claim['version'], 'statement': claim['statement'],
        'scope': claim['scope'], 'falsification': claim['falsification'],
        'reproductionTarget': copy.deepcopy(claim.get('reproductionTarget', claim.get('origin', {}).get('reproductionTarget'))),
        'actor': actor, 'reason': reason, 'at': at})
    claim['assessment'] = _assessment()
    claim['updatedAt'] = at
    for expression in state['claimGraph']['expressions']:
        if any(ref['claimId'] == claim_id for ref in expression['claimRefs']):
            expression['status'] = 'draft'
            expression['stale'] = True
    return claim


def add_relation(state, claim_id, evidence_id, relation_type='support',
                 polarity='unresolved', reason='', applicability='', quality='limited',
                 claim_version=None):
    claim = get_claim(state, claim_id)
    if claim.get('archived'):
        raise ValueError('Archived claim cannot receive evidence')
    if claim_version is not None and claim_version != claim['version']:
        raise ValueError('Evidence must target the current claim version')
    evidence = next((e for e in state.get('evidence', []) if e.get('id') == evidence_id), None)
    if evidence is None:
        raise ValueError('Unknown evidence: ' + str(evidence_id))
    if relation_type not in ('support', 'qualify') or polarity not in POLARITIES or quality not in QUALITIES:
        raise ValueError('Invalid evidence relationship')
    if relation_type == 'qualify' and polarity != 'unresolved':
        raise ValueError('Qualifying evidence has no support direction')
    for existing in state['claimGraph']['relations']:
        if (existing['claimId'], existing['claimVersion'], existing['evidenceId'], existing['type'],
                existing['polarity'], existing['reason'], existing['applicability'], existing['quality']) == (
                claim_id, claim['version'], evidence_id, relation_type, polarity, reason, applicability, quality):
            return existing
    group = _source_group(evidence)
    relation = {'id': _id('relation'), 'claimId': claim_id,
        'claimVersion': claim['version'], 'evidenceId': evidence_id,
        'type': relation_type, 'polarity': polarity, 'reason': reason,
        'applicability': applicability, 'quality': quality,
        'sourceGroup': group, 'createdAt': _now()}
    state['claimGraph']['relations'].append(relation)
    return relation


def _source_group(evidence):
    if evidence.get('paperId'):
        return 'paper:' + str(evidence['paperId'])
    for key in ('sourceId', 'runId', 'receiptId', 'receiptPath'):
        if evidence.get(key):
            return 'source:' + str(evidence[key])
    locator = str(evidence.get('locator') or '').replace('\\', '/')
    parts = [part for part in locator.split('/') if part]
    if evidence.get('type') == 'experiment' and len(parts) >= 3 and parts[0] == 'runs':
        # metrics, raw data and receipt from one execution share provenance.
        return 'run:' + '/'.join(parts[:2])
    if locator:
        return 'source:' + locator
    return 'source:' + str(evidence.get('id'))


def assess_claim(state, claim_id, status, reason, evidence_ids=None,
                 limitations='', confirmed_by_user=False):
    claim = get_claim(state, claim_id)
    if claim.get('archived') or status not in STATUSES:
        raise ValueError('Invalid claim assessment')
    ids = list(dict.fromkeys(evidence_ids or []))
    known = {e.get('id'): e for e in state.get('evidence', [])}
    if any(eid not in known for eid in ids):
        raise ValueError('Assessment cites unknown evidence')
    if status in ('supported', 'refuted', 'mixed'):
        if not ids:
            raise ValueError('Decisive assessment requires evidence')
        relations = [r for r in state['claimGraph']['relations']
                     if r['claimId'] == claim_id and r['claimVersion'] == claim['version']
                     and r['type'] == 'support'
                     and r['quality'] in ('usable', 'limited') and r['polarity'] != 'unresolved'
                     and bool(str(known.get(r['evidenceId'], {}).get('locator') or '').strip())]
        if not relations:
            raise ValueError('Decisive assessment requires current directional located evidence')
        directions = {r['polarity'] for r in relations}
        if status == 'supported' and (directions != {'for'}):
            raise ValueError('Support assessment conflicts with evidence direction')
        if status == 'refuted' and (directions != {'against'}):
            raise ValueError('Refutation assessment conflicts with evidence direction')
        if status == 'mixed' and 'mixed' not in directions and directions != {'for', 'against'}:
            raise ValueError('Mixed assessment requires conflicting evidence')
        covered = {r['evidenceId'] for r in relations}
        if not set(ids).issubset(covered):
            raise ValueError('Assessment includes evidence without usable directional relation')
    claim.setdefault('assessmentHistory', []).append(copy.deepcopy(claim['assessment']))
    claim['assessment'] = {'status': status, 'reason': reason, 'evidenceIds': ids,
        'limitations': limitations, 'confirmedByUser': bool(confirmed_by_user)}
    claim['updatedAt'] = _now()
    return claim


def create_expression(state, kind, title, markdown, claim_refs, confirmed=False):
    graph = ensure_graph(state)
    if kind not in EXPRESSION_KINDS or not isinstance(claim_refs, list):
        raise ValueError('Invalid expression')
    refs = []
    for ref in claim_refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('version'), int):
            raise ValueError('Invalid claim reference')
        claim = get_claim(state, ref.get('claimId'))
        if not any(v['version'] == ref['version'] for v in claim['versions']):
            raise ValueError('Unknown claim version')
        refs.append({'claimId': claim['id'], 'version': ref['version']})
    at = _now()
    expression = {'id': _id('expression'), 'kind': kind, 'title': title,
        'markdown': markdown, 'claimRefs': refs,
        'status': 'confirmed' if confirmed else 'draft', 'createdAt': at,
        'updatedAt': at}
    graph['expressions'].append(expression)
    return expression


def preserve_history(restored_state, previous_state):
    """Retain later records while the restored snapshot selects current versions."""
    # Relations are useful only while their exact source observations remain
    # addressable. Preserve missing records without replacing checkpoint data.
    for key in ('papers', 'evidence'):
        target = restored_state.setdefault(key, [])
        known = {item.get('id') for item in target}
        for source in previous_state.get(key, []):
            if source.get('id') is not None and source['id'] not in known:
                target.append(copy.deepcopy(source))
                known.add(source['id'])
    retained_nodes = {node['id'] for node in restored_state.get('nodes', [])}
    for node in previous_state.get('nodes', []):
        if node['id'] not in retained_nodes:
            archived_node = copy.deepcopy(node)
            archived_node['active'] = False
            archived_node.setdefault('input', {})['superseded'] = True
            archived_node['archivedByRollback'] = True
            restored_state.setdefault('nodes', []).append(archived_node)
    restored = ensure_graph(restored_state)
    previous = ensure_graph(previous_state)
    by_id = {c['id']: c for c in restored['claims']}
    for future in previous['claims']:
        current = by_id.get(future['id'])
        if current is None:
            archived = copy.deepcopy(future)
            archived['archived'] = True
            restored['claims'].append(archived)
            by_id[archived['id']] = archived
            continue
        known_versions = {v['version'] for v in current['versions']}
        current['versions'].extend(copy.deepcopy(v) for v in future['versions']
                                   if v['version'] not in known_versions)
        current['versions'].sort(key=lambda v: v['version'])
        for field in ('assessmentHistory', 'decisions'):
            existing = current.setdefault(field, [])
            for entry in future.get(field, []):
                if entry not in existing:
                    existing.append(copy.deepcopy(entry))
    reconsider = set()
    for key in ('relations', 'materials', 'expressions'):
        known = {item['id'] for item in restored[key]}
        for item in previous[key]:
            if item['id'] not in known:
                preserved = copy.deepcopy(item)
                if key == 'expressions':
                    preserved.update(stale=True, status='draft', staleReason='回滚后保留的历史表达')
                if key == 'relations':
                    claim = by_id.get(preserved['claimId'])
                    if claim and not claim.get('archived') and claim['version'] == preserved['claimVersion']:
                        reconsider.add(claim['id'])
                restored[key].append(preserved)
                known.add(item['id'])
    for claim_id in reconsider:
        claim = by_id[claim_id]
        claim.setdefault('assessmentHistory', []).append(copy.deepcopy(claim['assessment']))
        claim['assessment'] = {**_assessment(), 'reason': '回滚保留了后来取得的证据，需要重新论证当前版本'}
    for expression in restored['expressions']:
        if any(ref['claimId'] not in by_id or
               ref['claimId'] in reconsider or
               by_id[ref['claimId']].get('archived') or
               ref['version'] != by_id[ref['claimId']]['version']
               for ref in expression.get('claimRefs', [])):
            expression['status'] = 'draft'
            expression['stale'] = True
    return restored
