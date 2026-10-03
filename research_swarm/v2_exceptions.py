"""Host-admitted exceptions; model suggestions never grant pause or permissions."""
import json

from .v2_contracts import DomainError, bounded, gate, text
from .v2_store import encode, uid, now

TYPES = {'scope_change', 'missing_core_input', 'identity_ambiguity', 'credential_required',
         'cost_or_resource_required', 'irreversible_action'}
HOST_ERRORS = {
    'credential_required': {'credential_required'},
    'missing_core_input': {'missing_core_input'},
    'identity_ambiguity': {'identity_ambiguity'},
    'cost_or_resource_required': {'resource_unavailable', 'budget_exhausted'},
    'irreversible_action': {'irreversible_action'},
}
OPTION_LABELS = {
    'dismiss': '保留为研究记录，继续主线',
    'revise_scope': '暂停并重新确认范围或资源预算',
    'cancel_run': '取消本次运行，保留记录',
    'retry': '外部条件已修复，准备显式恢复',
}


def host_type(error):
    if isinstance(error, DomainError):
        return next((kind for kind, codes in HOST_ERRORS.items() if error.code in codes), None)
    return None


def accept(store, node, request, host_error=None):
    request = bounded(request)
    allowed = {'type', 'summary', 'message', 'impact', 'blocking', 'options'}
    if (not isinstance(request, dict) or set(request) - allowed
            or not isinstance(request.get('type'), str) or request['type'] not in TYPES):
        raise DomainError('invalid_exception', '未知异常类型或字段')
    if 'blocking' in request and type(request['blocking']) is not bool:
        raise DomainError('invalid_exception', 'blocking 必须是布尔值，且仅表示申请')
    message = text(request.get('summary') or request.get('message'), '异常摘要', 2000)
    impact = request.get('impact', '')
    if not isinstance(impact, str) or len(impact) > 4000:
        raise DomainError('invalid_exception', '异常影响说明无效')
    with store.transaction() as db:
        run, _ = store.guard(db, node)
        gate(run['contract'], node['stage'], node['objectiveId'])
        blocking = host_error is not None and host_type(host_error) == request['type']
        if node['stage'] in ('Experiment', 'Replication'):
            key = 'experimentRequired' if node['stage'] == 'Experiment' else 'replicationRequired'
            blocking = blocking and run['contract']['evidencePolicy'].get(key, False)
        model_connection = getattr(host_error, 'resource', None) == 'model_connection'
        if request['type'] == 'credential_required' and not model_connection and not run['contract']['executionPolicy']['allowCredentials']:
            blocking = False
        options = ['cancel_run', 'revise_scope'] if blocking else ['dismiss', 'revise_scope']
        if blocking and host_error.code in ('credential_required', 'resource_unavailable', 'missing_core_input'):
            options.append('retry')
        content = {
            'type': request['type'], 'objectiveId': node['objectiveId'], 'nodeId': node['id'],
            'blocking': bool(blocking), 'message': message, 'impact': impact,
            'options': [{'id': option, 'label': OPTION_LABELS[option]} for option in options],
            'createdAt': now(), 'resolution': None,
            'cause': {'code': host_error.code, 'resource': getattr(host_error, 'resource', None)} if host_error else None,
        }
        exception_id = uid()
        db.execute('INSERT INTO exceptions VALUES(?,?,?,?,?)', (
            exception_id, node['runId'], 1, 'open' if blocking else 'optional', encode(content)))
        if blocking:
            db.execute("UPDATE runs SET status='blocked_exception', epoch=epoch+1, revision=revision+1 WHERE id=?", (node['runId'],))
            db.execute("UPDATE nodes SET status='interrupted', token=NULL WHERE run=? AND status='running'", (node['runId'],))
        else:
            db.execute('UPDATE runs SET revision=revision+1 WHERE id=?', (node['runId'],))
        store.event(db, 'exception.accepted', {'exceptionId': exception_id, 'runId': node['runId'], 'blocking': bool(blocking)})
        return dict(content, id=exception_id, revision=1, runId=node['runId'])


def resolve(store, exception_id, payload):
    if set(payload) - {'runId', 'revision', 'runRevision', 'requestId', 'optionId'}:
        raise DomainError('invalid_resolution', '异常解决不得携带权限或范围变更')
    with store.transaction() as db:
        prior, digest = store.replay(db, 'resolve:' + exception_id, payload.get('requestId'), payload)
        if prior:
            return prior
        row = db.execute('SELECT * FROM exceptions WHERE id=?', (exception_id,)).fetchone()
        if not row or row['run'] != payload.get('runId'):
            raise DomainError('exception_not_found', '异常与运行不匹配', 404)
        run = store._run(db.execute('SELECT * FROM runs WHERE id=?', (row['run'],)).fetchone())
        if (any(type(payload.get(key)) is not int or payload[key] < 0 for key in ('revision', 'runRevision'))
                or row['revision'] != payload['revision'] or run['revision'] != payload['runRevision']
                or row['status'] not in ('open', 'optional')):
            raise DomainError('stale_exception', '异常或运行版本已变化', 409)
        value = json.loads(row['content'])
        option = payload.get('optionId')
        if not isinstance(option, str) or option not in {item['id'] for item in value['options']}:
            raise DomainError('invalid_resolution', '不是宿主提供的处理选项')
        value['resolution'] = {'optionId': option, 'at': now()}
        db.execute("UPDATE exceptions SET status='resolved', revision=revision+1, content=? WHERE id=?", (encode(value), exception_id))
        if option == 'cancel_run':
            db.execute("UPDATE runs SET status='cancelled', epoch=epoch+1, revision=revision+1 WHERE id=?", (row['run'],))
        elif option in ('revise_scope', 'retry'):
            # Resolving records the user's action; resuming execution is a separate explicit operation.
            db.execute("UPDATE runs SET status='paused', epoch=epoch+1, revision=revision+1 WHERE id=? AND status NOT IN ('completed','cancelled')", (row['run'],))
        else:
            db.execute('UPDATE runs SET revision=revision+1 WHERE id=?', (row['run'],))
        if option in ('cancel_run', 'revise_scope', 'retry'):
            db.execute("UPDATE nodes SET status='interrupted', token=NULL WHERE run=? AND status='running'", (row['run'],))
        store.event(db, 'exception.resolved', {'runId': row['run'], 'exceptionId': exception_id, 'optionId': option})
        next_action = {'revise_scope': 'messages:revise_scope', 'retry': 'research/actions:resume'}.get(option)
        result = {'exceptionId': exception_id, 'resolved': True, 'nextAction': next_action}
        db.execute('INSERT INTO requests VALUES(?,?,?,?)', ('resolve:' + exception_id, payload['requestId'], digest, encode(result)))
        return result
