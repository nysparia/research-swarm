"""Bounded research contracts and shared plan, dispatch and tool policy gates."""
import copy
import json
import math
import re
from decimal import Decimal, InvalidOperation

VERSION = 'conversation_only_v2'
ACTIVE = {'research_starting', 'researching', 'blocked_exception', 'paused', 'interrupted', 'failed'}
FLAGS = (
    'allowPaperSearch', 'allowFullTextDownload', 'allowReplication',
    'allowLocalExperiment', 'allowInstall', 'allowCredentials', 'allowCodeInspection',
)
STAGES = {
    'Retrieval', 'Claim Extraction', 'Evidence Review', 'Epistemic Analysis',
    'Validation Planning', 'Replication', 'Experiment', 'Report',
}
BUDGET_LIMITS = {
    'modelCalls': 30, 'searchCalls': 3, 'toolCalls': 20,
    'nodes': 64, 'seconds': 600, 'parallelism': 1,
}
STAGE_TOOLS = {
    'Retrieval': {'paper_search', 'paper_read', 'paper_download'},
    'Claim Extraction': {'paper_read'},
    'Experiment': {'python_run', 'python_install'},
    'Replication': {'python_run', 'python_install'},
}


class DomainError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def bounded(value, maximum=200000):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise DomainError('invalid_json', 'JSON 格式无效') from None
    if len(raw.encode('utf-8')) > maximum:
        raise DomainError('too_large', '领域数据过大')
    return copy.deepcopy(value)


def text(value, label, maximum=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DomainError('invalid_contract', label + '必须是有界非空字符串')
    return value.strip()


def text_list(value, label, minimum=0, maximum=20):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise DomainError('invalid_contract', label + '必须为有界文本列表')
    return [text(item, label, 4000) for item in value]


def validate_brief(value):
    value = bounded(value)
    allowed = {
        'question', 'objects', 'dimensions', 'deliverables', 'scope',
        'evidencePolicy', 'executionPolicy', 'openQuestions', 'sourceMessageIds', 'plan',
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainError('invalid_contract', 'Brief 含未知字段或宿主控制字段')
    value['question'] = text(value.get('question'), 'question')
    for key in ('objects', 'dimensions', 'deliverables'):
        value[key] = text_list(value.get(key), key, 1, 12)

    scope = value.get('scope')
    if not isinstance(scope, dict):
        raise DomainError('invalid_contract', 'scope 无效')
    for alias, canonical in (('in', 'included'), ('out', 'excluded')):
        if alias in scope:
            item = scope.pop(alias)
            if canonical in scope and scope[canonical] != item:
                raise DomainError('invalid_contract', '范围字段别名冲突')
            scope[canonical] = item
    if set(scope) - {'included', 'excluded', 'optional', 'objectives'}:
        raise DomainError('invalid_contract', 'scope 含未知字段')
    scope.setdefault('optional', [])
    for key in ('included', 'excluded', 'optional'):
        scope[key] = text_list(scope.get(key), 'scope.' + key)
    objectives = scope.get('objectives')
    if not isinstance(objectives, list) or not 1 <= len(objectives) <= 8:
        raise DomainError('invalid_contract', '需要 1 至 8 个明确目标')
    ids = set()
    for objective in objectives:
        if (not isinstance(objective, dict) or set(objective) != {'id', 'description'}
                or not isinstance(objective['id'], str)
                or not re.fullmatch(r'objective:[a-zA-Z0-9_-]{1,40}', objective['id'])):
            raise DomainError('invalid_contract', '目标格式无效')
        if objective['id'] in ids:
            raise DomainError('invalid_contract', '目标 ID 重复')
        ids.add(objective['id'])
        objective['description'] = text(objective['description'], '目标')

    policy = value.get('executionPolicy', {})
    if not isinstance(policy, dict):
        raise DomainError('invalid_policy', '执行权限必须为对象')
    if 'allowExperiment' in policy:
        alias = policy.pop('allowExperiment')
        if 'allowLocalExperiment' in policy and policy['allowLocalExperiment'] != alias:
            raise DomainError('invalid_policy', '实验权限别名冲突')
        policy['allowLocalExperiment'] = alias
    if set(policy) - set(FLAGS) - {'budget'}:
        raise DomainError('invalid_policy', '未知执行权限')
    for flag in FLAGS:
        if type(policy.get(flag, False)) is not bool:
            raise DomainError('invalid_policy', '权限必须是布尔值')
        policy.setdefault(flag, False)
    budget = policy.get('budget', {})
    if budget == 'compact':
        budget = {}
    if not isinstance(budget, dict) or set(budget) - set(BUDGET_LIMITS):
        raise DomainError('invalid_policy', '预算字段无效')
    for key, maximum in BUDGET_LIMITS.items():
        number = budget.get(key, maximum)
        minimum = 1 if key in ('nodes', 'seconds', 'parallelism') else 0
        if type(number) is not int or not minimum <= number <= maximum:
            raise DomainError('invalid_policy', '预算超出上限：' + key)
        budget[key] = number
    policy['budget'] = budget
    value['executionPolicy'] = policy
    if policy['allowCodeInspection']:
        raise DomainError('capability_unavailable', '当前 v2 尚未开放受管代码检查，不能授权任意宿主路径读取')
    if policy['allowInstall'] and not (policy['allowLocalExperiment'] or policy['allowReplication']):
        raise DomainError('invalid_policy', '依赖安装需要明确实验或复现授权')

    evidence = value.get('evidencePolicy', {})
    evidence_fields = {'requireLocatedSources', 'fullTextRequired', 'replicationRequired', 'experimentRequired', 'preferredSources'}
    if not isinstance(evidence, dict) or set(evidence) - evidence_fields:
        raise DomainError('invalid_contract', '未知证据政策')
    for key, default in (('requireLocatedSources', True), ('fullTextRequired', False),
                         ('replicationRequired', False), ('experimentRequired', False)):
        evidence.setdefault(key, default)
        if type(evidence[key]) is not bool:
            raise DomainError('invalid_contract', '证据政策开关必须为布尔值')
    if not evidence['requireLocatedSources']:
        raise DomainError('invalid_contract', '不得降低来源定位规则')
    evidence['preferredSources'] = text_list(evidence.get('preferredSources', []), 'preferredSources', 0, 12)
    if evidence['replicationRequired'] and not policy['allowReplication']:
        raise DomainError('invalid_policy', 'replicationRequired 需要明确复现授权')
    if evidence['experimentRequired'] and not policy['allowLocalExperiment']:
        raise DomainError('invalid_policy', 'experimentRequired 需要明确本机实验授权')
    value['evidencePolicy'] = evidence

    questions = value.get('openQuestions', [])
    if not isinstance(questions, list) or len(questions) > 12:
        raise DomainError('invalid_contract', '澄清项无效')
    for question in questions:
        if not isinstance(question, dict) or set(question) != {'question', 'core'} or type(question['core']) is not bool:
            raise DomainError('invalid_contract', '澄清项需明确 core')
        question['question'] = text(question['question'], '澄清项', 2000)
    value['openQuestions'] = questions
    value['sourceMessageIds'] = text_list(value.get('sourceMessageIds'), 'sourceMessageIds', 1, 500)
    return value


def ready(brief):
    return not any(question['core'] for question in brief['openQuestions'])


def gate(contract, stage, objective_id, tool=None):
    if not isinstance(stage, str) or stage not in STAGES:
        raise DomainError('invalid_plan', '未知研究阶段', 403)
    if not isinstance(objective_id, str) or objective_id not in {objective['id'] for objective in contract['scope']['objectives']}:
        raise DomainError('scope_denied', '目标不属于冻结契约', 403)
    policy = contract['executionPolicy']
    required = {'Retrieval': 'allowPaperSearch', 'Replication': 'allowReplication', 'Experiment': 'allowLocalExperiment'}.get(stage)
    if required and not policy[required]:
        raise DomainError('policy_denied', '冻结契约未授权 ' + stage, 403)
    if tool is not None:
        if not isinstance(tool, str) or tool not in STAGE_TOOLS.get(stage, set()):
            raise DomainError('policy_denied', '阶段工具白名单拒绝调用', 403)
        flag = {'paper_search': 'allowPaperSearch', 'paper_download': 'allowFullTextDownload', 'python_install': 'allowInstall'}.get(tool)
        if flag and not policy[flag]:
            raise DomainError('policy_denied', '工具权限未授权', 403)


def validate_target(target, known_evidence):
    fields = {'paperId', 'evidenceIds', 'metric', 'expected', 'tolerance', 'conditions', 'unit'}
    if not isinstance(target, dict) or set(target) - fields:
        raise DomainError('invalid_output', '复现目标格式无效')
    for key in ('paperId', 'metric', 'expected', 'tolerance', 'conditions'):
        text(target.get(key), 'reproductionTarget.' + key, 4000)
    ids = target.get('evidenceIds')
    if not isinstance(ids, list) or not ids or any(not isinstance(eid, str) or eid not in known_evidence for eid in ids):
        raise DomainError('invalid_output', '复现目标必须引用实际原研究证据')
    try:
        numeric = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
        if any(re.fullmatch(numeric, target[key].strip()) is None for key in ('expected', 'tolerance')):
            raise InvalidOperation()
        expected, tolerance = Decimal(target['expected']), Decimal(target['tolerance'])
        if (not expected.is_finite() or not tolerance.is_finite() or tolerance < 0
                or not all(math.isfinite(float(value)) for value in (expected, tolerance))):
            raise InvalidOperation()
    except (InvalidOperation, ValueError, OverflowError):
        raise DomainError('invalid_output', '复现目标的 expected/tolerance 必须为有限数值字符串') from None
    if 'unit' in target and (not isinstance(target['unit'], str) or len(target['unit']) > 100):
        raise DomainError('invalid_output', '复现目标单位无效')
    return target


def validate_output(value, known_evidence):
    bounded(value)
    allowed = {'summary', 'claims', 'unresolved', 'optionalNextActions', 'exceptionRequest', 'toolCalls', 'validationPlan'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise DomainError('invalid_output', '拒绝未知字段或旧控制协议')
    text(value.get('summary'), 'summary')
    for key in ('claims', 'unresolved', 'optionalNextActions', 'toolCalls'):
        if not isinstance(value.get(key, []), list) or len(value.get(key, [])) > 20:
            raise DomainError('invalid_output', '输出列表无效')
    for key in ('unresolved', 'optionalNextActions'):
        text_list(value.get(key, []), key)
    plans = value.get('validationPlan', [])
    if not isinstance(plans, list) or len(plans) > 8:
        raise DomainError('invalid_output', '验证计划须为最多 8 项的列表')
    for item in plans:
        if not isinstance(item, dict) or set(item) != {'kind', 'claimIds', 'reason'} or item['kind'] not in ('experiment', 'replication'):
            raise DomainError('invalid_output', '验证计划类型或字段无效')
        text_list(item['claimIds'], 'validationPlan.claimIds', 1, 20)
        text(item['reason'], 'validationPlan.reason', 4000)
    for claim in value.get('claims', []):
        allowed_claim = {'statement', 'evidenceIds', 'scope', 'falsification', 'reproductionTarget'}
        if not isinstance(claim, dict) or set(claim) - allowed_claim:
            raise DomainError('invalid_output', '主张不能自行声明已验证')
        text(claim.get('statement'), 'statement')
        for key in ('scope', 'falsification'):
            if key in claim and (not isinstance(claim[key], str) or len(claim[key]) > 12000):
                raise DomainError('invalid_output', '主张边界无效')
        ids = claim.get('evidenceIds', [])
        if not isinstance(ids, list) or any(not isinstance(eid, str) or eid not in known_evidence for eid in ids):
            raise DomainError('invalid_output', '模型不得伪造证据 ID')
        if claim.get('reproductionTarget') is not None:
            validate_target(claim['reproductionTarget'], known_evidence)
    return value
