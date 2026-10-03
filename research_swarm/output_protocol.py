"""One output contract for producers, engine admission and evidence judges."""
import json


UNSCOPED = object()


class OutputValidationError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(json.dumps(issues, ensure_ascii=False))


def validation_issues(error):
    return getattr(error, 'issues', [{'code': 'invalid_output', 'path': '$', 'message': str(error)}])


def binding_for(node):
    data = node.get('input') or {}
    return {'id': data['claimId'], 'version': data.get('claimVersion')} if data.get('claimId') else None


def validate_relations(items, known, binding=UNSCOPED):
    issues, supports = [], {}

    def issue(code, path, message, **details):
        issues.append(dict(code=code, path=path, message=message, **details))

    if not isinstance(items, list) or len(items) > 100:
        raise OutputValidationError([{'code': 'relation_list', 'path': 'structured.evidenceRelations',
                                      'message': 'evidenceRelations 必须是最多 100 项的列表'}])
    if binding is None and items:
        raise OutputValidationError([{'code': 'relations_without_claim', 'path': 'structured.evidenceRelations',
            'message': '当前节点未绑定主张。evidenceRelations 必须省略或为空；将支持、反证和限制保留在阶段结果、候选观察的 text/limitations 与 unresolved 中，并保留实际 evidenceIds。'}])
    for index, item in enumerate(items):
        path = f'structured.evidenceRelations[{index}]'
        if not isinstance(item, dict):
            issue('relation_object', path, '证据关系必须是对象', index=index)
            continue
        eid = item.get('evidenceId')
        detail = {'index': index, 'evidenceId': eid}
        if not isinstance(eid, str) or eid not in known:
            issue('unknown_evidence', path + '.evidenceId', '证据关系必须引用实际 evidenceId', **detail)
        cid, version = item.get('claimId'), item.get('claimVersion')
        if binding is not UNSCOPED:
            cid = item.get('claimId', binding['id'])
            version = item.get('claimVersion', binding['version'])
            if cid != binding['id'] or version != binding['version'] or type(version) is not int:
                issue('claim_binding', path, '节点不能将取证结果归到其他主张或版本',
                      expectedClaimId=binding['id'], expectedClaimVersion=binding['version'], **detail)
        if cid is not None and not isinstance(cid, str):
            issue('claim_id', path + '.claimId', 'claimId 必须是字符串', **detail)
        if version is not None and (type(version) is not int or version < 1):
            issue('claim_version', path + '.claimVersion', 'claimVersion 必须是正整数', **detail)
        kind, polarity = item.get('type'), item.get('polarity', 'unresolved')
        if kind not in ('support', 'qualify'):
            issue('relation_type', path + '.type', '证据关系只支持 support 或 qualify', **detail)
        if polarity not in ('for', 'against', 'mixed', 'unresolved'):
            issue('relation_polarity', path + '.polarity', 'support 方向必须为 for/against/mixed/unresolved', **detail)
        if kind == 'qualify' and polarity != 'unresolved':
            issue('qualify_polarity', path + '.polarity', 'qualify 记录适用条件，不使用正反方向；应为 unresolved', **detail)
        if item.get('quality', 'limited') not in ('usable', 'limited', 'unusable'):
            issue('relation_quality', path + '.quality', '证据质量必须为 usable/limited/unusable', **detail)
        if not isinstance(item.get('reason'), str) or not item['reason'].strip():
            issue('relation_reason', path + '.reason', '证据关系必须说明本证据与主张的关系', **detail)
        if kind == 'support' and isinstance(eid, str) and (cid is None or isinstance(cid, str)) and (version is None or type(version) is int):
            key = (cid, version, eid)
            if key in supports:
                previous_index, previous_polarity = supports[key]
                issue('duplicate_support', path, '同一主张版本的同一证据只能有一条 support；正反含义并存时请解释并返回一条 mixed，不能删除反证',
                      previousIndex=previous_index, polarities=[previous_polarity, polarity], claimId=cid, claimVersion=version, **detail)
            else:
                supports[key] = (index, polarity)
    if issues:
        raise OutputValidationError(issues)


def validate_node_output(value, known, node=None):
    """Collect independent envelope, reference, stage and relation errors together."""
    from .research_contracts import validate_structured_result
    from .research_cycle import validate_output
    issues = []

    def check(fn):
        try:
            fn()
        except ValueError as error:
            issues.extend(validation_issues(error))

    if not isinstance(value, dict):
        raise OutputValidationError([{'code': 'output_object', 'path': '$', 'message': '节点输出必须是对象'}])
    if not isinstance(value.get('summary'), str) or not value['summary'].strip():
        issues.append({'code': 'summary_required', 'path': 'summary', 'message': '节点输出缺少 summary'})
    for field in ('claims', 'evidenceIds', 'unresolved'):
        if not isinstance(value.get(field, []), list):
            issues.append({'code': 'output_list', 'path': field, 'message': field + ' 必须为列表'})
    structured = value.get('structured', {})
    if not isinstance(structured, dict):
        issues.append({'code': 'structured_object', 'path': 'structured', 'message': 'structured 必须是对象'})
        structured = {}
    check(lambda: validate_structured_result(structured, known))
    binding = binding_for(node) if node is not None else UNSCOPED
    check(lambda: validate_relations(structured.get('evidenceRelations', []), known, binding))
    if node and node.get('input', {}).get('researchStep'):
        try:
            validate_output(node['input']['researchStep'], node.get('phase'), structured,
                            known, node['input'].get('hypothesisId'))
        except ValueError as error:
            message = str(error)
            import re
            field = re.search(r'研究阶段缺少 (\w+) 对象', message)
            issues.append({'code': 'stage_field_missing' if field else 'stage_output',
                           'path': 'structured.' + field[1] if field else 'structured', 'message': message})

    def references(owner, path):
        ids = owner.get('evidenceIds', [])
        if not isinstance(ids, list):
            issues.append({'code': 'evidence_list', 'path': path, 'message': '证据引用必须是 ID 列表'})
        else:
            for i, eid in enumerate(ids):
                if str(eid) not in known:
                    issues.append({'code': 'unknown_evidence', 'path': f'{path}[{i}]', 'evidenceId': eid,
                                   'message': '引用了不存在的证据'})
    references(value, 'evidenceIds')
    if isinstance(value.get('claims', []), list):
        for i, candidate in enumerate(value.get('claims', [])):
            if not isinstance(candidate, dict) or not isinstance(candidate.get('text'), str) or not candidate['text'].strip():
                issues.append({'code': 'candidate_text', 'path': f'claims[{i}]', 'message': '候选判断缺少文本'})
                continue
            references(candidate, f'claims[{i}].evidenceIds')
            if node and node.get('input', {}).get('researchStep') and not candidate.get('evidenceIds'):
                issues.append({'code': 'candidate_source', 'path': f'claims[{i}].evidenceIds', 'message': '研究判断必须有来源；猜想放 hypotheses，缺口放 unresolved'})
            if binding is not UNSCOPED and candidate.get('claimId') is not None:
                if binding is None or candidate.get('claimId') != binding['id'] or candidate.get('claimVersion', binding['version']) != binding['version'] or type(candidate.get('claimVersion', binding['version'])) is not int:
                    issues.append({'code': 'claim_binding', 'path': f'claims[{i}]', 'message': '候选观察不能伪造主张或引用其他主张版本'})
            elif binding is None and candidate.get('claimVersion') is not None:
                issues.append({'code': 'claim_binding', 'path': f'claims[{i}].claimVersion', 'message': '无主张节点不填写 claimVersion'})
    if issues:
        raise OutputValidationError(issues)


def relation_prompt(binding):
    if binding is None:
        return ('\n当前节点没有绑定持久化主张。claims 仅为有来源的候选观察，可用 text、evidenceIds、limitations 保留支持、反证与条件；'
                '不得填写 claimId/claimVersion，不代表已作科学判断。structured.evidenceRelations 必须省略或为 []。'
                '阶段结果和 unresolved 中保留矛盾与缺口，不得为通过校验删除反证。')
    return ('\n当前主张由宿主绑定：' + json.dumps(binding, ensure_ascii=False) + '。evidenceRelations 中可省略 claimId/claimVersion，由宿主绑定；若填写必须完全匹配。'
            '同一主张版本、同一 evidenceId 只能有一条 support。不同证据可以分别 support+for 和 support+against；'
            '同一证据含正反信息时使用一条 support+mixed，并在 reason 解释两面，不能丢弃反证。'
            '可另附 qualify+unresolved 表达适用条件；qualify 不得使用其他方向。'
            '格式示例：[{"evidenceId":"实际ID","type":"support","polarity":"mixed","reason":"支持方面及相反方面的依据",'
            '"applicability":"条件","quality":"limited"},{"evidenceId":"同一实际ID","type":"qualify","polarity":"unresolved",'
            '"reason":"适用范围限制","applicability":"条件","quality":"limited"}]。生产节点给候选观察，方向由独立裁判复核。')


def counterevidence_ids(value, known):
    """Remember real adverse/qualifying sources across a formatting repair."""
    structured = value.get('structured') if isinstance(value, dict) else None
    items = structured.get('evidenceRelations') if isinstance(structured, dict) else None
    if not isinstance(items, list):
        return set()
    return {r['evidenceId'] for r in items if isinstance(r, dict) and isinstance(r.get('evidenceId'), str)
            and r['evidenceId'] in known and (r.get('type') == 'qualify' or r.get('polarity') in ('against', 'mixed'))}


def validate_retained_sources(value, required):
    """Do not let format repair silently drop a known counter-source citation."""
    cited = set()
    def visit(item):
        if isinstance(item, dict):
            if isinstance(item.get('evidenceId'), str):
                cited.add(item['evidenceId'])
            if isinstance(item.get('evidenceIds'), list):
                cited.update(str(e) for e in item['evidenceIds'])
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    missing = required - cited
    if missing:
        raise OutputValidationError([{'code': 'counterevidence_dropped', 'path': 'evidenceIds', 'evidenceId': eid,
            'message': '纠正格式不能删去已引用的反证/条件来源；请保留其引用并解释内容和局限'} for eid in sorted(missing)])
