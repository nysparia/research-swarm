"""Typed research delegation, evidence return and designer-owned experiment repair.

This module controls transitions; models propose hypotheses, protocols and judgments.
Convergence is checked separately from ending a bounded run.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path


LABELS = {'background': '扩充研究背景', 'literature': '研究现有文献与数据', 'topic': '凝练具体课题',
          'hypothesis_generation': '提出可证伪猜想', 'hypothesis': '猜想与重新论证',
          'data_request': '明确需要什么数据', 'data_source': '判断从哪里取得数据',
          'experiment_design': '设计与修订实验', 'experiment_execution': '执行实验并报告问题',
          'synthesis': '汇总论证并判断收敛'}
PROTOCOL_FIELDS = ('id', 'hypothesis', 'method', 'dataset', 'baselines', 'metrics', 'replicates',
                   'validityChecks', 'acceptance', 'outputSchema', 'rawData')


def _object(owner, key):
    value = owner.get(key)
    if not isinstance(value, dict):
        raise ValueError(f'研究阶段缺少 {key} 对象')
    return value


def _text(owner, keys):
    for key in keys:
        if not isinstance(owner.get(key), str) or not owner[key].strip():
            raise ValueError(f'研究数据缺少 {key}')


def _ids(owner, known):
    ids = owner.get('evidenceIds', [])
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or set(ids) - known:
        raise ValueError('研究数据引用了不存在的证据')
    return list(dict.fromkeys(ids))


def validate_hypotheses(items, known, required=False):
    if not isinstance(items, list) or len(items) > 6 or (required and not items):
        raise ValueError('需要一到六个具体猜想；综合阶段可以不提出新猜想')
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('猜想必须是对象')
        _text(item, ('id', 'statement', 'falsification', 'reason'))
        _ids(item, known)
        if item['id'] in seen:
            raise ValueError('同一轮猜想 ID 不能重复')
        seen.add(item['id'])


def validate_protocol(protocol):
    if not isinstance(protocol, dict) or any(k not in protocol for k in PROTOCOL_FIELDS):
        raise ValueError('实验协议必须包含假设、方法、数据、基线、指标、重复次数、有效性检查、验收与输出结构')
    _text(protocol, ('id', 'hypothesis', 'method', 'dataset', 'acceptance'))
    for key in ('baselines', 'metrics', 'validityChecks'):
        if not isinstance(protocol[key], list) or not protocol[key] or any(not isinstance(v, str) or not v.strip() for v in protocol[key]):
            raise ValueError('实验协议的 ' + key + ' 必须是非空文本列表')
    if type(protocol['replicates']) is not int or not 1 <= protocol['replicates'] <= 10000:
        raise ValueError('实验重复次数必须是正整数')
    if not isinstance(protocol['outputSchema'], dict) or not protocol['outputSchema']:
        raise ValueError('实验必须声明 metrics.json 的指标结构')
    if any(not isinstance(k, str) or not k.strip() or v not in ('number', 'integer', 'string', 'boolean', 'array', 'object')
           for k, v in protocol['outputSchema'].items()):
        raise ValueError('实验输出结构只支持 number/integer/string/boolean/array/object 字段类型')
    raw = _object(protocol, 'rawData'); _text(raw, ('file',))
    if Path(raw['file']).name != raw['file'] or not raw['file'].endswith('.csv'):
        raise ValueError('原始数据必须为本次产物中的 CSV 文件名')
    metrics = _object(raw, 'metrics')
    numeric = {k for k, v in protocol['outputSchema'].items() if v in ('number', 'integer')}
    if not numeric or set(metrics) != numeric:
        raise ValueError('每个数值指标必须声明原始 CSV 列及统计方法')
    for spec in metrics.values():
        if not isinstance(spec, dict): raise ValueError('原始指标映射必须为对象')
        _text(spec, ('column', 'statistic'))
        if spec['statistic'] not in ('mean', 'median', 'min', 'max', 'p95', 'sum', 'count'):
            raise ValueError('原始指标统计仅支持 mean/median/min/max/p95/sum/count')
        if 'where' in spec and (not isinstance(spec['where'], dict) or not spec['where'] or
                any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) for k, v in spec['where'].items())):
            raise ValueError('原始指标 where 必须声明 CSV 列名与精确匹配的文本值')


def validate_output(step, phase, structured, known, hypothesis_id=None):
    if step not in LABELS:
        raise ValueError('未知研究阶段')
    if step == 'background':
        _text(_object(structured, 'background'), ('context', 'boundaries'))
    elif step == 'literature':
        value = _object(structured, 'literatureReview'); _text(value, ('summary',)); _ids(value, known)
    elif step == 'topic':
        value = _object(structured, 'researchTopic'); _text(value, ('title', 'question', 'rationale')); _ids(value, known)
    elif step == 'hypothesis_generation':
        validate_hypotheses(structured.get('hypotheses'), known, required=True)
    elif step == 'hypothesis' and phase != 'aggregate':
        requests = structured.get('dataRequests')
        if not isinstance(requests, list) or not 1 <= len(requests) <= 6:
            raise ValueError('每个猜想必须索求一到六项明确数据')
        for demand in requests:
            if not isinstance(demand, dict): raise ValueError('数据需求必须为对象')
            _text(demand, ('metric', 'definition', 'purpose', 'acceptance', 'scope'))
    elif step == 'data_request' and phase != 'aggregate':
        _text(_object(structured, 'dataDemand'), ('metric', 'definition', 'purpose', 'acceptance', 'scope'))
    elif step == 'data_source' and phase != 'aggregate':
        value = _object(structured, 'dataAssessment'); _text(value, ('reason',)); ids = _ids(value, known)
        if type(value.get('sufficient')) is not bool or (value['sufficient'] and not ids):
            raise ValueError('足够的数据支撑必须附实际证据；缺数据须 sufficient=false')
    elif step == 'experiment_design':
        if phase == 'aggregate':
            value = _object(structured, 'experimentReview'); _text(value, ('reason',)); _ids(value, known)
            if type(value.get('valid')) is not bool:
                raise ValueError('设计节点必须判断实验有效性，而不是假设是否获胜')
            if not value['valid'] and not value.get('blocked'):
                validate_protocol(structured.get('experimentProtocol'))
        else:
            validate_protocol(structured.get('experimentProtocol'))
    elif step == 'experiment_execution':
        value = _object(structured, 'experimentRun'); _ids(value, known)
        if value.get('status') not in ('completed', 'problem'):
            raise ValueError('执行节点需要报告 completed 或 problem')
        if value['status'] == 'completed':
            _text(value, ('protocolId',))
            if not isinstance(value.get('measurements'), dict) or not isinstance(value.get('validation'), dict):
                raise ValueError('实验执行结果需要实测指标与有效性检查')
        elif not isinstance(value.get('problem'), dict) or not value['problem'].get('message'):
            raise ValueError('实验问题必须带具体错误信息')
    elif step in ('data_request', 'data_source'):
        value = _object(structured, 'evidenceResponse'); _text(value, ('reason',)); ids = _ids(value, known)
        if type(value.get('sufficient')) is not bool or (value['sufficient'] and not ids):
            raise ValueError('逐级回传需要明确是否满足数据需求及其来源')
    elif step == 'hypothesis':
        value = _object(structured, 'hypothesisVerdict'); _text(value, ('reason', 'limitations')); ids = _ids(value, known)
        if value.get('status') not in ('supported', 'refuted', 'inconclusive'):
            raise ValueError('猜想论证状态必须为 supported/refuted/inconclusive')
        if value['status'] != 'inconclusive' and not ids:
            raise ValueError('无证据不能宣布猜想被支持或证伪')
    elif step == 'synthesis':
        value = _object(structured, 'researchConclusion'); _text(value, ('reason',)); _ids(value, known)
        if type(value.get('converged')) is not bool:
            raise ValueError('综合节点必须明确研究是否收敛')
        validate_hypotheses(structured.get('hypotheses', []), known)
        revisions = structured.get('evidenceRevisions', [])
        if not isinstance(revisions, list) or len(revisions) > 6:
            raise ValueError('补充取证需要最多六条猜想 ID 与具体缺口')
        for revision in revisions:
            if not isinstance(revision, dict): raise ValueError('补充取证必须为对象')
            _text(revision, ('hypothesisId', 'reason'))
    if hypothesis_id and structured.get('experimentProtocol') and structured['experimentProtocol']['hypothesis'] != hypothesis_id:
        raise ValueError('实验协议必须绑定当前节点的 hypothesisId')


def _spawn(engine, parent, step, title, data=None, kind='research'):
    if sum(n['active'] for n in engine._state['nodes']) >= engine._limit('maxTasks') or engine._depth(parent) >= engine._limit('maxDepth'):
        return None
    child = {'title': title[:120], 'description': title + '\n' + (parent['input'].get('description') or ''),
             'acceptance': '按研究阶段协议回传可定位证据与未满足条件；不编造实测或把进程完成当作假设成立。',
             'constraints': parent['input'].get('constraints', ''), 'kind': kind, 'researchStep': step,
             **copy.deepcopy(data or {})}
    engine._validate_children(parent, [child]); engine._add_children(parent, [child])
    node = engine._children(parent['id'])[-1]
    for source in node['input'].get('dependsOn', []):
        engine._state['edges'].append({'source': source, 'target': node['id'], 'type': 'dependency', 'reason': '前序研究结果是本任务的输入'})
    node.update(phase='execute', role=LABELS[step] + ' agent')
    parent.update(status='pending', phase='aggregate', progress=35)
    engine._history('research-delegated', parentId=parent['id'], nodeId=node['id'], step=step,
                    demand=copy.deepcopy(node['input']))
    return node


def start(engine):
    engine._state['project'].setdefault('researchBudget', {})['maxDepth'] = max(5, engine._limit('maxDepth'))
    cycle = {'status': 'running', 'stage': 'background', 'iteration': 1, 'hypotheses': [],
             'dataRequests': [], 'experiments': [], 'background': None, 'literatureReview': None, 'topic': None,
             'unresolved': []}
    engine._state['project']['researchCycle'] = cycle
    root = engine._get_node('central'); root['input']['researchStep'] = 'hypothesis_generation'
    background = _spawn(engine, root, 'background', '扩充问题的上下文、边界与相关领域')
    literature = _spawn(engine, root, 'literature', '检索文献与已有数据，查清现状及证据缺口', {'dependsOn': [background['id']]})
    _spawn(engine, root, 'topic', '根据文献研究凝练具体课题', {'dependsOn': [literature['id']]})


def _done(engine, node, output):
    node.update(status='completed', progress=100, finishedAt=engine._cycle_now())
    engine._activity('AI', LABELS[node['input']['researchStep']] + '已回传：' + output['summary'][:500], node)
    engine._history('research-returned', nodeId=node['id'], parentId=node['parentId'],
                    step=node['input']['researchStep'], evidenceIds=output['evidenceIds'])


def _hypotheses(engine, items, origin=None):
    cycle = engine._state['project']['researchCycle']; root = engine._get_node('central')
    added = 0
    for item in items:
        signature = hashlib.sha256((item['statement'] + '\n' + item['falsification']).encode()).hexdigest()[:20]
        if any(h['signature'] == signature for h in cycle['hypotheses']):
            continue
        hypothesis_id = 'hypothesis-' + signature
        from .claim_runtime import bind_hypothesis
        parent_claim = (origin or {}).get('input', {}).get('originClaimId')
        claim = bind_hypothesis(engine, item, hypothesis_id, [parent_claim] if parent_claim else None)
        topic_nodes = [n['id'] for n in engine._children('central') if n['input'].get('researchStep') == 'topic']
        node = _spawn(engine, root, 'hypothesis', item['statement'], {'hypothesisId': hypothesis_id,
            'claimId': claim['id'], 'claimVersion': claim['version'], 'hypothesis': item, 'dependsOn': topic_nodes})
        if node is None:
            cycle['unresolved'].append('任务预算不足，猜想尚未验证：' + item['statement']); continue
        if claim.get('ownerNodeId') and claim['ownerNodeId'] != node['id']:
            claim.setdefault('ownerHistory', []).append({'nodeId': claim['ownerNodeId'],
                'version': claim['version'], 'at': engine._cycle_now()})
        claim['ownerNodeId'] = node['id']
        node['role'] = '主张研究负责人'
        cycle['hypotheses'].append({**copy.deepcopy(item), 'id': hypothesis_id, 'signature': signature,
                                   'claimId': claim['id'], 'claimVersion': claim['version'],
                                   'nodeId': node['id'], 'status': 'awaiting_evidence', 'verdict': None,
                                   'iteration': cycle['iteration'], 'dataRequestIds': []})
        added += 1
    root['input']['researchStep'] = 'synthesis'
    return added


def _experiment_data(engine, node, token, protocol, errors=None):
    errors = errors if errors is not None else []
    executions = [r for r in token.get('toolExecutions', []) if r.get('tool') == 'python_run' and r.get('status') == 'completed']
    if not executions: errors.append('本次节点没有成功的 python_run 凭据')
    for execution in reversed(executions):
        candidates = [a for a in execution.get('artifacts') or [] if Path(a.get('path', '')).name == 'metrics.json']
        if not candidates: errors.append('成功进程未保存 metrics.json 产物')
        for artifact in candidates:
            path = (engine._artifact_root / str(artifact.get('path', ''))).resolve()
            if path.name != 'metrics.json' or not path.is_relative_to(engine._artifact_root / 'runs') or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                errors.append('metrics.json 缺失、越界或超过 4 MiB')
                continue
            try:
                content = path.read_bytes()
                if artifact.get('sha256') != hashlib.sha256(content).hexdigest(): raise ValueError('metrics.json 内容哈希与执行凭据不一致')
                def reject_constant(value): raise ValueError('实验数据含非有限数值')
                data = json.loads(content.decode('utf-8'), parse_constant=reject_constant)
                if not isinstance(data, dict): raise ValueError('metrics.json 必须为对象')
                if data.get('protocolId') != protocol['id']: raise ValueError('metrics.json protocolId 与本次实验协议不匹配')
                missing = set(protocol['outputSchema']) - set(data)
                if missing: raise ValueError('metrics.json 缺少指标字段：' + ', '.join(sorted(missing)))
                if not isinstance(data.get('validation'), dict) or data['validation'].get('passed') is not True:
                    raise ValueError('metrics.json validation.passed 未通过')
                if type(data.get('replicates')) is not int or data['replicates'] < protocol['replicates']:
                    raise ValueError('metrics.json 实际重复次数不足')
                checks = data['validation'].get('checks')
                if not isinstance(checks, list) or any(not isinstance(c, dict) or c.get('passed') is not True for c in checks):
                    raise ValueError('metrics.json 有效性检查未逐项通过')
                missing = set(protocol['validityChecks']) - {c.get('name') for c in checks if isinstance(c.get('name'), str)}
                if missing: raise ValueError('metrics.json 缺少有效性检查：' + ', '.join(sorted(missing)))
                for key, type_name in protocol['outputSchema'].items():
                    value = data[key]
                    if type_name == 'number' and (type(value) not in (float, int) or not math.isfinite(value)):
                        raise ValueError('metrics.json ' + key + ' 不是有限数值')
                    if type_name != 'number' and type(value) is not {'integer': int, 'string': str, 'boolean': bool, 'array': list, 'object': dict}.get(type_name):
                        raise ValueError('metrics.json ' + key + ' 类型与输出结构不一致')
                raw = protocol['rawData']
                raw_artifact = next((a for a in execution.get('artifacts', []) if Path(a.get('path', '')).name == raw['file']), None)
                if not raw_artifact: raise ValueError('本次进程未保存协议指定的 ' + raw['file'])
                raw_path = (engine._artifact_root / raw_artifact['path']).resolve()
                if not raw_path.is_relative_to(path.parent) or not raw_path.is_file() or raw_path.stat().st_size > 4*1024*1024:
                    raise ValueError('原始 CSV 缺失、越界或超过 4 MiB')
                raw_bytes = raw_path.read_bytes()
                if raw_artifact.get('sha256') != hashlib.sha256(raw_bytes).hexdigest(): raise ValueError('原始 CSV 内容哈希与执行凭据不一致')
                reader = csv.DictReader(raw_bytes.decode('utf-8-sig').splitlines()); rows = list(reader)
                if len(rows) < data['replicates']: raise ValueError('CSV 原始测量不足')
                for key, spec in raw['metrics'].items():
                    where = spec.get('where', {})
                    if ({spec['column']} | set(where)) - set(reader.fieldnames or []):
                        raise ValueError('原始 CSV 缺少指标 ' + key + ' 的测量列或分组列')
                    selected = [row for row in rows if all(row.get(k) == v for k, v in where.items())]
                    values = [float(row[spec['column']]) for row in selected if (row.get(spec['column']) or '').strip()]
                    if len(values) < data['replicates'] or not all(math.isfinite(v) for v in values): raise ValueError(key + ' 原始测量不足或含非有限值')
                    aggregates = {'mean': statistics.mean, 'median': statistics.median, 'min': min, 'max': max,
                                  'p95': lambda vs: sorted(vs)[math.ceil(len(vs)*.95)-1], 'sum': sum, 'count': len}
                    actual = aggregates[spec['statistic']](values)
                    if not math.isclose(actual, data[key], rel_tol=1e-6, abs_tol=1e-9):
                        statistic = '中位数 median' if spec['statistic'] == 'median' else spec['statistic']
                        raise ValueError(f'{key} 的 {statistic} 与原始测量不匹配：CSV 重算={actual:g}，metrics.json={data[key]:g}；检查 column/where 分组映射')
                script_path = (engine._artifact_root / str(execution.get('script', ''))).resolve()
                if not script_path.is_relative_to(engine._artifact_root / 'runs') or not script_path.is_file(): raise ValueError('执行脚本缺失或越界')
                if hashlib.sha256(script_path.read_bytes()).hexdigest() != execution.get('scriptSha256'): raise ValueError('执行脚本内容哈希与执行凭据不一致')
                hashes = {execution['script']: execution['scriptSha256'], artifact['path']: artifact['sha256'], raw_artifact['path']: raw_artifact['sha256']}
                data['_provenance'] = {'script': execution['script'], 'metricsArtifact': artifact['path'], 'rawDataArtifact': raw_artifact['path'], 'artifactHashes': hashes}
                ids = execution.get('evidenceIds', [])
                if ids: return data, ids
                errors.append('成功进程缺少可定位的执行凭据')
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(str(artifact.get('path', 'metrics.json')) + '：' + str(exc))
    return None, []


def _block(output, message):
    output['unresolved'].append(message)
    output['structured']['researchBlocked'] = message


def _bind_return(output, ids):
    """An aggregation cannot introduce an empirical claim outside its returned data."""
    available = set(ids)
    rejected = [c for c in output['claims'] if not set(c['evidenceIds']).issubset(available)]
    if rejected:
        output['claims'] = [c for c in output['claims'] if c not in rejected]
        _block(output, '未采纳超出本数据回传链的论断；需另行索求对应证据。')
    sections = output['structured'].get('paperSections', [])
    rejected_sections = [section for section in sections if set(section.get('evidenceIds', [])) - available or
                         (section.get('id') == 'results' and not section.get('evidenceIds'))]
    if rejected_sections:
        output['structured']['paperSections'] = [section for section in sections if section not in rejected_sections]
        _block(output, '未采纳缺少本回传链证据的论文段落；结果写作需绑定实际数据。')
    output['evidenceIds'] = list(dict.fromkeys(ids))
    return not rejected and not rejected_sections


def _artifacts_intact(engine, hashes):
    if not hashes: return False
    for relative, expected in hashes.items():
        path = (engine._artifact_root / relative).resolve()
        try:
            if not path.is_relative_to(engine._artifact_root / 'runs') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        except OSError: return False
    return True


def _existing_sources(engine, ids, demand):
    by_id = {e['id']: e for e in engine._state['evidence']}
    for eid in ids:
        evidence = by_id[eid]
        if not evidence.get('locator') or not evidence.get('quote'): return False
        if evidence.get('type') == 'experiment':
            approval = engine._state['project'].get('researchEvidenceApprovals', {}).get(eid)
            receipt = (engine._artifact_root / evidence['locator']).resolve()
            if (evidence.get('executionStatus') != 'completed' or not approval or
                    not receipt.is_relative_to(engine._artifact_root / 'runs') or not receipt.is_file() or
                    hashlib.sha256(receipt.read_bytes()).hexdigest() != evidence.get('sha256')):
                return False
            if any(approval['dataDemand'].get(k) != demand.get(k) for k in ('metric', 'definition', 'acceptance', 'scope')):
                return False
            if not _artifacts_intact(engine, approval.get('artifactHashes')): return False
    return True


def accept(engine, node, output, token):
    """Return True when this controller owns the accepted node's transitions."""
    cycle = engine._state['project'].get('researchCycle')
    step = node['input'].get('researchStep')
    if not cycle or step not in LABELS:
        return False
    structured = output['structured']; phase = token['phase']
    cycle['stage'] = step
    hid = node['input'].get('hypothesisId')
    hypothesis = next((h for h in cycle['hypotheses'] if h['id'] == hid), None)
    demand = next((d for d in cycle['dataRequests'] if d['id'] == node['input'].get('demandId')), None)
    if step in ('background', 'literature', 'topic'):
        key = {'background': 'background', 'literature': 'literatureReview', 'topic': 'researchTopic'}[step]
        cycle['topic' if step == 'topic' else key] = copy.deepcopy(structured[key])
    elif step == 'hypothesis_generation':
        if _hypotheses(engine, structured['hypotheses'], node):
            if node['id'] == 'central': return True
            _done(engine, node, output); return True
        _block(output, '没有可执行的新猜想；需要重新界定课题或增加研究资源。')
        cycle['unresolved'].append('追加研究方向尚未产生新的可执行猜想：' + node['title'])
    elif step == 'hypothesis' and phase != 'aggregate':
        for index, request in enumerate(structured['dataRequests'], 1):
            did = hid + ':demand:' + str(index) + ':' + str(node['version'])
            child = _spawn(engine, node, 'data_request', '我需要什么：' + request['metric'],
                           {'hypothesisId': hid, 'demandId': did, 'dataDemand': request})
            cycle['dataRequests'].append({**copy.deepcopy(request), 'id': did, 'hypothesisId': hid,
                                         'nodeId': child['id'] if child else None, 'status': 'pending' if child else 'blocked', 'evidenceIds': []})
            hypothesis['dataRequestIds'].append(did)
        if engine._children(node['id']): return True
        _block(output, '没有资源继续明确数据需求，猜想仍未验证。')
        hypothesis['status'] = 'inconclusive'
    elif step == 'data_request' and phase != 'aggregate':
        demand.update(copy.deepcopy(structured['dataDemand']))
        if _spawn(engine, node, 'data_source', '我需要找谁：' + demand['metric'],
                  {'hypothesisId': hid, 'demandId': demand['id'], 'dataDemand': structured['dataDemand']}): return True
        demand['status'] = 'blocked'; _block(output, '数据来源节点预算不足。')
    elif step == 'data_source' and phase != 'aggregate':
        assessment = structured['dataAssessment']
        if engine._state['project'].get('taskMode') == 'reproduction' and assessment['sufficient']:
            approved = engine._state['project'].get('researchEvidenceApprovals', {})
            if not assessment['evidenceIds'] or any(eid not in approved for eid in assessment['evidenceIds']):
                assessment.update(sufficient=False, evidenceIds=[], reason='论文报告值只能作为复现目标；当前课题还需实际执行并复核复现实验。')
        if assessment['sufficient'] and not _existing_sources(engine, assessment['evidenceIds'], node['input']['dataDemand']):
            assessment.update(sufficient=False, evidenceIds=[], reason='当前引用缺少有效的来源或实验复核，需取得可核验数据。')
        if assessment['sufficient']:
            output['evidenceIds'] = list(dict.fromkeys(output['evidenceIds'] + assessment['evidenceIds']))
            demand.update(status='satisfied', evidenceIds=assessment['evidenceIds'], source='existing')
        else:
            demand['status'] = 'needs_experiment'
            if _spawn(engine, node, 'experiment_design', '设计实验取得：' + demand['metric'],
                      {'hypothesisId': hid, 'demandId': demand['id'], 'dataDemand': node['input']['dataDemand'], 'attempt': 1}): return True
            demand['status'] = 'blocked'; _block(output, '证据不足且无资源继续设计实验。')
    elif step == 'experiment_design':
        latest = engine._children(node['id'])[-1] if engine._children(node['id']) else None
        review = structured.get('experimentReview', {})
        valid_execution = bool(latest and (latest.get('output') or {}).get('structured', {}).get('experimentRun', {}).get('verified'))
        if phase == 'aggregate' and review.get('valid') and valid_execution:
            ids = latest['output']['evidenceIds']
            if not ids or not review['evidenceIds'] or not set(review['evidenceIds']).issubset(set(ids)):
                raise ValueError('实验复核必须引用当前实验返回的实际凭据')
            run = latest['output']['structured']['experimentRun']
            required = {run['script'], run['metricsArtifact'], run['rawDataArtifact']}
            observed = {r['path']: r['sha256'] for r in token.get('artifactReads', [])}
            if not _artifacts_intact(engine, run['artifactHashes']):
                raise ValueError('实验产物在执行后发生变化或缺失，当前数据不得验收')
            if required - set(observed) or any(observed.get(path) != run['artifactHashes'][path] for path in required):
                raise ValueError('设计节点尚未读取当前实验的脚本、实测指标与原始数据')
            output['evidenceIds'] = ids
            demand.update(status='satisfied', evidenceIds=ids, source='experiment')
            _bind_return(output, ids)
            structured['experimentVerified'] = {'protocolId': latest['input']['experimentProtocol']['id'], 'evidenceIds': ids}
            experiment = next(e for e in cycle['experiments'] if e['nodeId'] == latest['id'])
            experiment.update(reviewStatus='accepted', reviewReason=review['reason'])
            for e in engine._state['evidence']:
                if e['id'] in ids:
                    approval = {'protocolId': run['protocolId'], 'designNodeId': node['id'], 'executionNodeId': latest['id'], 'dataDemand': copy.deepcopy(node['input']['dataDemand']), 'artifactHashes': copy.deepcopy(run['artifactHashes'])}
                    e['researchValidation'] = approval
                    engine._state['project'].setdefault('researchEvidenceApprovals', {})[e['id']] = approval
        else:
            attempt = int(node['input'].get('attempt', 0)) + (1 if phase == 'aggregate' else 0)
            if review.get('blocked') or attempt > 4 or not structured.get('experimentProtocol'):
                demand['status'] = 'blocked'; _block(output, '实验未通过协议复核或修订预算已用完；数据不能支持结论。')
                output['claims'] = []; output['evidenceIds'] = []
                if latest:
                    experiment = next(e for e in cycle['experiments'] if e['nodeId'] == latest['id'])
                    experiment.update(reviewStatus='rejected', reviewReason=review.get('reason', '未通过复核'))
            else:
                protocol = copy.deepcopy(structured['experimentProtocol'])
                if phase == 'aggregate':
                    old = latest['input']['experimentProtocol']
                    latest['input']['superseded'] = True
                    experiment = next(e for e in cycle['experiments'] if e['nodeId'] == latest['id'])
                    experiment.update(reviewStatus='superseded', reviewReason=review.get('reason', '修订实验'))
                    if protocol['id'] == old['id']:
                        protocol['id'] = old['id'] + ':revision:' + str(attempt)
                    engine._history('experiment-redesigned', nodeId=node['id'], previousExecutionId=latest['id'],
                                    problem=copy.deepcopy(latest['output']), protocol=protocol, attempt=attempt)
                node['input']['attempt'] = attempt
                node['input']['experimentProtocol'] = protocol
                execution = _spawn(engine, node, 'experiment_execution', '执行实验：' + demand['metric'] + f'（方案 {attempt}）',
                    {'hypothesisId': hid, 'demandId': demand['id'], 'experimentProtocol': protocol, 'experimentDesign': protocol,
                     'dataDemand': node['input']['dataDemand'], 'attempt': attempt}, kind='experiment')
                if execution:
                    cycle['experiments'].append({'nodeId': execution['id'], 'designNodeId': node['id'], 'hypothesisId': hid,
                                                'demandId': demand['id'], 'protocolId': protocol['id'], 'attempt': attempt, 'status': 'pending'})
                    return True
                demand['status'] = 'blocked'; _block(output, '没有资源执行修订后的实验。')
    elif step == 'experiment_execution':
        run = structured['experimentRun']; protocol = node['input']['experimentProtocol']
        errors = []
        data, ids = _experiment_data(engine, node, token, protocol, errors) if run['status'] == 'completed' else (None, [])
        if data is not None and run['protocolId'] == protocol['id']:
            run.update(verified=True, measurements={k: data[k] for k in protocol['outputSchema']}, validation=data['validation'], evidenceIds=ids, **data['_provenance'])
            output['evidenceIds'] = ids
        else:
            run.update(status='problem', verified=False, problem=run.get('problem') or {'kind': 'invalid_data',
                'message': '；'.join(errors[:3]) or '本次实验数据未满足当前协议；退回设计节点。'})
            structured['unverifiedSummary'] = output['summary']
            output['summary'] = '实验数据未通过验收，已回传设计节点。' + run['problem']['message']
            output['claims'] = []; _block(output, run['problem']['message'])
        _bind_return(output, ids)
        experiment = next(e for e in cycle['experiments'] if e['nodeId'] == node['id'])
        experiment.update(status=run['status'], evidenceIds=output['evidenceIds'], problem=run.get('problem'))
        if run['status'] == 'problem':
            engine._history('experiment-problem-reported', nodeId=node['id'], designNodeId=node['parentId'], problem=run['problem'])
    elif step in ('data_request', 'data_source'):
        response = structured['evidenceResponse']
        ids = set(e for child in engine._children(node['id']) for e in (child.get('output') or {}).get('evidenceIds', []))
        if demand['status'] != 'satisfied' or not set(response['evidenceIds']).issubset(ids):
            response.update(sufficient=False, evidenceIds=[])
        _bind_return(output, response['evidenceIds'] if response['sufficient'] else [])
        demand.update(evidenceIds=list(output['evidenceIds']), status='satisfied' if response['sufficient'] else 'blocked')
        if not response['sufficient']: demand['status'] = 'blocked'; _block(output, response['reason'])
    elif step == 'hypothesis':
        verdict = structured['hypothesisVerdict']
        demands = [d for d in cycle['dataRequests'] if d['id'] in hypothesis['dataRequestIds']]
        available = set(e for child in engine._children(node['id'])
                        for e in (child.get('output') or {}).get('evidenceIds', []))
        if not demands or any(d['status'] != 'satisfied' for d in demands) or not set(verdict['evidenceIds']).issubset(available):
            verdict.update(status='inconclusive', evidenceIds=[])
            output['claims'] = []; _block(output, '数据需求尚未满足，不能将猜想论证为成立或证伪。')
        _bind_return(output, verdict['evidenceIds'])
        hypothesis.update(status=verdict['status'], verdict=copy.deepcopy(verdict))
        engine._history('hypothesis-reconsidered', hypothesisId=hid, verdict=copy.deepcopy(verdict))
    elif step == 'synthesis':
        new = structured.get('hypotheses', [])
        revisions = structured.get('evidenceRevisions', [])
        revised_ids = {r['hypothesisId'] for r in revisions}
        if revised_ids - {h['id'] for h in cycle['hypotheses']}:
            raise ValueError('补充取证引用了不存在的猜想')
        budget_exhausted = bool((new or revisions) and cycle['iteration'] >= engine._limit('maxIterations'))
        if (new or revisions) and cycle['iteration'] < engine._limit('maxIterations'):
            cycle['iteration'] += 1
            added = _hypotheses(engine, new, node) if new else 0
            for revision in revisions:
                h = next(h for h in cycle['hypotheses'] if h['id'] == revision['hypothesisId'])
                target = engine._get_node(h['nodeId'])
                prior = copy.deepcopy(target['output'])
                engine._invalidate([target['id']] + engine._descendants(target['id']), revision['reason'])
                rebuild(engine, target, 'modify', h['statement'])
                target['input'].update(priorResults=prior, researchFeedback=revision['reason'])
                target['input']['description'] += '\n本次需补充取证：' + revision['reason']
                engine._history('hypothesis-evidence-reopened', hypothesisId=h['id'], reason=revision['reason'], iteration=cycle['iteration'])
            if added or revisions:
                node.update(status='pending', phase='aggregate', progress=40)
                return True
            budget_exhausted = bool(cycle['unresolved'])
        conclusion = structured['researchConclusion']
        available = {e for h in cycle['hypotheses'] for e in (h.get('verdict') or {}).get('evidenceIds', [])}
        valid_sources = bool(conclusion['evidenceIds']) and set(conclusion['evidenceIds']).issubset(available)
        valid_claims = _bind_return(output, conclusion['evidenceIds'] if valid_sources else [])
        if not valid_sources:
            conclusion['evidenceIds'] = []
            _block(output, '综合结论必须来自猜想论证实际回传的数据证据。')
        complete = bool(cycle['hypotheses']) and all(h['status'] in ('supported', 'refuted') for h in cycle['hypotheses'])
        complete = complete and not cycle['unresolved'] and not new and not revisions
        obligations = [f'{n["title"]}：{issue}' for n in engine._state['nodes']
                       if n['active'] and n['id'] != 'central' and not n['input'].get('superseded')
                       for issue in (n.get('output') or {}).get('unresolved', [])]
        output['unresolved'].extend(issue for issue in obligations if issue not in output['unresolved'])
        converged = complete and valid_sources and valid_claims and not output['unresolved'] and conclusion['converged']
        cycle['status'] = 'converged' if converged else 'budget_exhausted' if budget_exhausted else 'inconclusive'
        if not converged:
            _block(output, '研究尚未收敛：仍有证据缺口、未执行的新猜想或需要重新界定的范围。')
        structured['researchConclusion']['converged'] = converged
        _finish(engine, node, output, cycle)
        return True
    _done(engine, node, output)
    if node['id'] == 'central':
        cycle['status'] = 'inconclusive'; _finish(engine, node, output, cycle)
    return True


def _finish(engine, node, output, cycle):
    _done(engine, node, output)
    output['structured']['researchCycle'] = copy.deepcopy(cycle)
    unresolved = list(dict.fromkeys(output['unresolved'] + cycle['unresolved']))
    engine._state['report'] = {'summary': output['summary'], 'claims': output['claims'], 'evidenceIds': output['evidenceIds'],
        'structured': copy.deepcopy(output['structured']), 'unresolved': unresolved, 'approved': False,
        'ready': not bool(engine._state['project'].get('researchDecision'))}
    engine._state.update(paused=True, stage=8)
    engine._activity('system', '研究已收敛，候选结论与证据待用户审阅。' if cycle['status'] == 'converged' else '本轮暂时结束，研究未收敛；已保留证据缺口和实验问题。')


def report_failure(engine, node, token, error):
    if not engine._state['project'].get('researchCycle') or node['input'].get('researchStep') != 'experiment_execution':
        return False
    node['output'] = {'summary': '实验执行遇到问题，已上报设计节点：' + str(error), 'evidenceIds': [], 'claims': [],
        'structured': {'experimentRun': {'status': 'problem', 'verified': False,
            'problem': {'kind': type(error).__name__, 'message': str(error)}, 'evidenceIds': []}}, 'unresolved': [str(error)]}
    node.update(status='completed', progress=100)
    experiment = next((e for e in engine._state['project']['researchCycle']['experiments'] if e['nodeId'] == node['id']), None)
    if experiment: experiment.update(status='problem', problem={'kind': type(error).__name__, 'message': str(error)})
    engine._new_outputs.append({'id': token['id'], 'nodeId': node['id'], 'version': node['version'], 'phase': token['phase'],
        'sourceNodeId': node['sourceNodeId'], 'at': engine._cycle_now(), 'input': copy.deepcopy(token['input']),
        'context': copy.deepcopy(token['context']), 'output': copy.deepcopy(node['output'])})
    engine._history('experiment-problem-reported', nodeId=node['id'], designNodeId=node['parentId'],
                    problem=node['output']['structured']['experimentRun']['problem'])
    return True


def invalidate(engine, affected):
    cycle = engine._state['project'].get('researchCycle')
    if not cycle: return
    cycle.update(status='running', stage='hypothesis')
    approvals = engine._state['project'].get('researchEvidenceApprovals', {})
    engine._state['project']['researchEvidenceApprovals'] = {eid: value for eid, value in approvals.items()
        if value['executionNodeId'] not in affected and value['designNodeId'] not in affected}
    for hypothesis in cycle['hypotheses']:
        if hypothesis['nodeId'] in affected:
            hypothesis.update(status='awaiting_evidence', verdict=None)
    for demand in cycle['dataRequests']:
        if demand['nodeId'] in affected: demand.update(status='pending', evidenceIds=[])
    for experiment in cycle['experiments']:
        if experiment['nodeId'] in affected: experiment['status'] = 'pending'


def insert(engine, origin, payload):
    root = engine._get_node('central')
    data = {key: copy.deepcopy(payload[key]) for key in ('allowNewSearch', 'searchBudgetId', 'retrieval', 'paperIds') if key in payload}
    data.update(originNodeId=origin['id'], originClaimId=origin['input'].get('claimId'),
                originResult=copy.deepcopy(origin.get('output')), userInstruction=payload['text'])
    if not _spawn(engine, root, 'hypothesis_generation', '按用户指定方向深化研究：' + payload['text'], data):
        engine._state['project']['researchCycle']['unresolved'].append('新增方向因资源不足尚未执行：' + payload['text'])


def rebuild(engine, node, kind, text, full_reset=False):
    """Regenerate changed demands; aggregation of old descendants is not a rerun."""
    cycle = engine._state['project'].get('researchCycle')
    if not cycle: return
    step = node['input'].get('researchStep')
    if kind == 'reject':
        for h in cycle['hypotheses']:
            if h['nodeId'] == node['id']: h.update(status='inconclusive', verdict=None)
        for d in cycle['dataRequests']:
            if d['nodeId'] == node['id'] or (d['nodeId'] and not engine._get_node(d['nodeId'])['active']):
                d.update(status='blocked', evidenceIds=[])
        return
    if full_reset or node['id'] == 'central':
        engine._history('research-cycle-replaced', reason=text, previous=copy.deepcopy(cycle))
        for old in engine._state['nodes']:
            if old['id'] != 'central': old['active'] = False
        start(engine)
        return
    if step in ('background', 'literature', 'topic'):
        archived = {n['id'] for n in engine._state['nodes'] if n['input'].get('researchStep') not in ('background', 'literature', 'topic') and n['id'] != 'central'}
        cycle.update(hypotheses=[], dataRequests=[], experiments=[], unresolved=[], iteration=1)
        for key in ({'background': ('background', 'literatureReview', 'topic'), 'literature': ('literatureReview', 'topic'), 'topic': ('topic',)}[step]):
            cycle[key] = None
        root = engine._get_node('central'); root['input']['researchStep'] = 'hypothesis_generation'; root['phase'] = 'aggregate'
    else:
        archived = set(engine._descendants(node['id']))
        cycle['dataRequests'] = [d for d in cycle['dataRequests'] if d['nodeId'] not in archived and
                                 not (step == 'hypothesis' and d['hypothesisId'] == node['input']['hypothesisId'])]
        cycle['experiments'] = [e for e in cycle['experiments'] if e['nodeId'] not in archived]
        for h in cycle['hypotheses']:
            h['dataRequestIds'] = [d['id'] for d in cycle['dataRequests'] if d['hypothesisId'] == h['id']]
        if 'dataDemand' in node['input']: node['input']['dataDemand']['userInstruction'] = text
        if step == 'experiment_design':
            node['input']['attempt'] = 1
            node['input'].pop('experimentProtocol', None)
        if step == 'hypothesis':
            node['input']['hypothesis']['statement'] = text
            h = next(h for h in cycle['hypotheses'] if h['nodeId'] == node['id'])
            h['statement'] = text
            h['signature'] = hashlib.sha256((text + '\n' + h['falsification']).encode()).hexdigest()[:20]
    for old in engine._state['nodes']:
        if old['id'] in archived: old['active'] = False
    node['phase'] = 'execute'; node['input']['userInstruction'] = text
    engine._history('research-branch-rebuilt', nodeId=node['id'], archivedIds=list(archived), reason=text)
