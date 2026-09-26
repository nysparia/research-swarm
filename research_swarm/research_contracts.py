"""Shared research result contracts without a manuscript UI or secondary runtime."""
import json


BUDGETS = {'compact': {'maxTasks': 48, 'maxIterations': 3, 'maxDepth': 4, 'maxParallel': 3},
           'team': {'maxTasks': 160, 'maxIterations': 6, 'maxDepth': 5, 'maxParallel': 6},
           'swarm': {'maxTasks': 240, 'maxIterations': 8, 'maxDepth': 5, 'maxParallel': 8}}
SECTION_IDS = {'abstract', 'introduction', 'related_work', 'method', 'experiments', 'results', 'discussion'}


def validate_structured_result(structured, valid_evidence):
    sections = structured.get('paperSections', [])
    if not isinstance(sections, list) or len(sections) > len(SECTION_IDS):
        raise ValueError('paperSections 必须为最多七个章节的列表')
    seen = set()
    for section in sections:
        if not isinstance(section, dict) or section.get('id') not in SECTION_IDS or section['id'] in seen:
            raise ValueError('论文章节 ID 无效或重复')
        seen.add(section['id'])
        if not isinstance(section.get('markdown'), str) or not 1 <= len(section['markdown'].strip()) <= 20000:
            raise ValueError('论文章节需要有效的 Markdown 正文')
        ids = section.get('evidenceIds', [])
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or set(ids) - valid_evidence:
            raise ValueError('论文章节引用了不存在的证据')
    decision = structured.get('researchDecision')
    if decision is not None:
        if not isinstance(decision, dict) or not isinstance(decision.get('question'), str) or not decision['question'].strip():
            raise ValueError('研究决策需要明确的问题')
        options = decision.get('options')
        if not isinstance(options, list) or not 2 <= len(options) <= 3:
            raise ValueError('研究决策需要两到三个选项')
        for option in options:
            if not isinstance(option, dict) or any(not isinstance(option.get(k), str) or not option[k].strip() for k in ('label', 'effect')):
                raise ValueError('决策选项需要 label 与 effect')
        if len(json.dumps(decision, ensure_ascii=False)) > 10000:
            raise ValueError('研究决策内容过长')
    design = structured.get('experimentDesign')
    if design is not None and (not isinstance(design, dict) or len(json.dumps(design)) > 24000):
        raise ValueError('实验协议必须为有限大小的对象')
    return structured


def decision_message(decision):
    options = '\n'.join(f"{i+1}. {option['label']}：{option['effect']}" for i, option in enumerate(decision['options']))
    return decision['question'] + '\n' + options + '\n请在下方输入框回复选项编号或你的判断；如需重写需求，请以「修改需求：」开头。'
