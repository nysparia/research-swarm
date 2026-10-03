"""Candidate topics and explicit selection, separate from research strategy decisions."""
import copy
import re
import unicodedata


FIELDS = ('id', 'title', 'question', 'researchGap', 'rationale', 'minimalStudy', 'feasibility', 'limitations')


def normalize_intent(mode, quote, user_text):
    """Generated requirements are not evidence that a user specified/delegated a topic."""
    if mode not in ('direct', 'delegate') or not isinstance(quote, str):
        return 'explore', ''
    quote = quote.strip()
    if len(quote) < 4 or quote not in user_text:
        return 'explore', ''
    if mode == 'direct' and not re.search(r'课题[是为：:]|只研究|具体研究|指定|固定|在.{1,60}(上|下).{0,30}(比较|验证|评估|测量)|specifically|fixed|compare.+on ', quote, re.I):
        return 'explore', ''
    if mode == 'delegate' and (re.search(r'不要|不能|不允许|不得|别替|do not|don.t', quote, re.I)
            or not re.search(r'(你|系统|平台|自动|帮我|替我).{0,20}(选|决定)|choose.{0,30}(for me|yourself)', quote, re.I)):
        return 'explore', ''
    return mode, quote


def message_action(text, candidates):
    """Only unambiguous selections commit. Questions stay in topic discussion."""
    value = text.strip()
    custom = re.fullmatch(r'(自定义(?:课题)?|课题改为|我(?:想|要)研究)[：:\s]*(.+)', value, re.S)
    if custom:
        return {'mode': 'custom', 'customText': custom[2].strip()}
    match = re.fullmatch(r'(?:我选|选择|选)?\s*(?:第)?([1-5一二三四五])(?:个|项)?[。.!！]?', value)
    if match:
        number = '一二三四五'.find(match[1]) + 1 if match[1] in '一二三四五' else int(match[1])
        if number > len(candidates):
            raise ValueError('请选择当前列表中的课题编号')
        return {'mode': 'candidate', 'candidateId': candidates[number-1]['id']}
    for candidate in candidates:
        if value in (candidate['title'], candidate['id'], '选择' + candidate['title'], '我选' + candidate['title']):
            return {'mode': 'candidate', 'candidateId': candidate['id']}
    return None


def pending(project):
    return ((project.get('researchCycle') or {}).get('topicSelection') or {}).get('status') == 'pending'


def _signature(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def validate_topics(structured, known, mode='explore'):
    def check(item, fields):
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip() for k in fields):
            raise ValueError('课题缺少必要字段：' + ', '.join(fields))
        if any(len(item[k]) > 4000 for k in fields):
            raise ValueError('课题字段过长，请精炼说明')
        ids = item.get('evidenceIds')
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in known for i in ids):
            raise ValueError('每个课题必须关联实际证据，不能使用空或虚构 evidenceIds')

    if structured.get('researchDecision') or structured.get('hypotheses'):
        raise ValueError('选题阶段只提出课题，不提出研究策略决策或提前生成猜想')
    if mode == 'direct':
        check(structured.get('selectedTopic'), ('title', 'question', 'rationale'))
        return
    items = structured.get('topicCandidates')
    if not isinstance(items, list) or not 3 <= len(items) <= 5:
        raise ValueError('topicCandidates 必须包含 3 至 5 个不同的候选课题')
    seen = {k: set() for k in ('id', 'title', 'question')}
    for item in items:
        check(item, FIELDS)
        for key in seen:
            signature = _signature(item[key])
            if not signature or signature in seen[key]:
                raise ValueError('候选课题重复：' + key + ' 必须不同，不能只改写标题')
            seen[key].add(signature)
    if structured.get('selectedTopic') or structured.get('topicSelection'):
        raise ValueError('候选阶段不能自行确认课题；只在 delegate 模式返回 topicRecommendation')
    if mode == 'delegate':
        recommendation = structured.get('topicRecommendation')
        if (not isinstance(recommendation, dict) or not isinstance(recommendation.get('candidateId'), str) or recommendation['candidateId'] not in {i['id'] for i in items}
                or not isinstance(recommendation.get('reason'), str) or not recommendation['reason'].strip()):
            raise ValueError('委托选题必须用 topicRecommendation 引用候选 ID 并说明 reason')


def topic_message(cycle):
    selection = cycle.get('topicSelection') or {}
    if selection.get('status') == 'selected':
        topic = selection['selectedTopic']
        reason = selection.get('reason', '')
        return '已确定研究课题：' + topic['title'] + '\n\n' + topic['question'] + ('\n\n' + reason if reason else '') + '\n\n接下来围绕此课题提出猜想并取得证据。'
    rows = ['根据当前文献与证据缺口，整理了以下候选课题。请选一个继续，也可以编辑候选或提出自己的课题。']
    for i, item in enumerate(cycle.get('topicCandidates', []), 1):
        rows.append(f"{i}. **{item['title']}**\n\n   {item['question']}\n\n   研究缺口：{item['researchGap']}")
    return '\n\n'.join(rows)


def select(engine, payload):
    project = engine._state['project']
    cycle = project.get('researchCycle') or {}
    if type(payload.get('expectedRevision')) is not int or payload['expectedRevision'] != engine._state['revision']:
        raise ValueError('选题状态版本已变化，请重新查看候选课题')
    if not pending(project):
        raise ValueError('当前没有待选择的课题，或课题已经确定')
    if set(payload) - {'expectedRevision', 'mode', 'candidateId', 'customText', 'baseCandidateId'}:
        raise ValueError('未知选题参数')
    mode = payload.get('mode')
    if mode not in ('candidate', 'custom', 'edited'):
        raise ValueError('请选择候选课题、编辑候选或自定义课题')
    items = {i['id']: i for i in cycle['topicCandidates']}
    source_ids = []
    if mode == 'candidate':
        if not isinstance(payload.get('candidateId'), str) or payload['candidateId'] not in items or payload.get('customText') or payload.get('baseCandidateId'):
            raise ValueError('请选择当前列表中的一个候选课题')
        topic = copy.deepcopy(items[payload['candidateId']])
        source_ids = [topic['id']]
    else:
        value = payload.get('customText')
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 8000 or payload.get('candidateId'):
            raise ValueError('自定义课题需要 1 至 8000 字的文本')
        if mode == 'edited':
            if not isinstance(payload.get('baseCandidateId'), str) or payload['baseCandidateId'] not in items:
                raise ValueError('编辑的候选课题已变化')
            source_ids = [payload['baseCandidateId']]
        elif payload.get('baseCandidateId'):
            raise ValueError('自定义课题不能携带候选 ID')
        # Original sources remain with the candidates; they do not automatically prove a new topic.
        topic = {'title': value.strip().splitlines()[0][:120], 'question': value.strip(),
                 'rationale': '用户指定的研究课题；后续需按此问题重新核对证据适用性。', 'evidenceIds': []}
    selection = {**cycle['topicSelection'], 'status': 'selected', 'mode': mode,
                 'selectedTopic': topic, 'sourceCandidateIds': source_ids,
                 'at': engine._cycle_now(), 'actor': 'user'}
    for key in ('candidateId', 'baseCandidateId', 'customText'):
        if key in payload:
            selection[key] = payload[key]
    cycle.update(topicSelection=selection, topic=copy.deepcopy(topic), stage='hypothesis_generation', status='running')
    engine._history('topic-selected', selection=copy.deepcopy(selection))
    engine._activity('user', '已确定研究课题：' + topic['title'])
    engine._resume({})
