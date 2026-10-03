"""Shared pure scientific protocol and verdict checks; no lifecycle dependency."""
from pathlib import Path

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


def validate_verdict(value, known):
    _text(value, ('reason', 'limitations'))
    ids = _ids(value, known)
    if value.get('status') not in ('supported', 'refuted', 'inconclusive'):
        raise ValueError('猜想论证状态必须为 supported/refuted/inconclusive')
    if value['status'] != 'inconclusive' and not ids:
        raise ValueError('无证据不能宣布猜想被支持或证伪')


import csv
import hashlib
import json
import math
import statistics

def validate_measurements(artifact_root, executions, protocol, errors=None):
    errors = errors if errors is not None else []
    executions = [r for r in executions if r.get('tool') == 'python_run' and r.get('status') == 'completed']
    if not executions: errors.append('本次节点没有成功的 python_run 凭据')
    for execution in reversed(executions):
        candidates = [a for a in execution.get('artifacts') or [] if Path(a.get('path', '')).name == 'metrics.json']
        if not candidates: errors.append('成功进程未保存 metrics.json 产物')
        for artifact in candidates:
            path = (artifact_root / str(artifact.get('path', ''))).resolve()
            if path.name != 'metrics.json' or not path.is_relative_to(artifact_root / 'runs') or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
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
                raw_path = (artifact_root / raw_artifact['path']).resolve()
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
                script_path = (artifact_root / str(execution.get('script', ''))).resolve()
                if not script_path.is_relative_to(artifact_root / 'runs') or not script_path.is_file(): raise ValueError('执行脚本缺失或越界')
                if hashlib.sha256(script_path.read_bytes()).hexdigest() != execution.get('scriptSha256'): raise ValueError('执行脚本内容哈希与执行凭据不一致')
                hashes = {execution['script']: execution['scriptSha256'], artifact['path']: artifact['sha256'], raw_artifact['path']: raw_artifact['sha256']}
                data['_provenance'] = {'script': execution['script'], 'metricsArtifact': artifact['path'], 'rawDataArtifact': raw_artifact['path'], 'artifactHashes': hashes}
                ids = execution.get('evidenceIds', [])
                if ids: return data, ids
                errors.append('成功进程缺少可定位的执行凭据')
            except (ValueError, KeyError, TypeError, OSError) as exc:
                errors.append(str(artifact.get('path', 'metrics.json')) + '：' + str(exc))
    return None, []
