"""Deterministic intent suggestions; explicitly not model inference."""
from __future__ import annotations

import re


RULES = (
    ('review', r'综述|文献|调研|review|survey|literature', '证据检索与文献对比'),
    ('investigation', r'调查|原因|机制|研究|investigat|mechanism|hypothes|research', '问题拆解与主张核验'),
    ('reproduction', r'复现|重现|reproduc|replicat', '复现协议与原始数据核验'),
    ('experimentation', r'实验|测量|基准|消融|experiment|benchmark|ablation', '实验执行与结果分析'),
    ('paper_preparation', r'写作|投稿|(?:撰写|起草|准备|润色|编辑).{0,12}(?:论文|稿件|文章)|'
                         r'(?:论文|稿件).{0,12}(?:撰写|起草|准备|润色|编辑)|'
                         r'\b(?:write|writing|draft|drafting|prepare|preparing|edit|editing|submit|submitting)\b'
                         r'[^\n.!?]{0,40}\b(?:paper|manuscript|publication|article)\b|'
                         r'\b(?:paper|manuscript|publication|article)\b[^\n.!?]{0,40}'
                         r'\b(?:writing|drafting|preparation|editing|submission)\b', '论文表达与来源追踪'),
)


def normalize_plan(capabilities, rationale='', source='model'):
    """Normalize explicit capability intent without accepting invented modules."""
    allowed = {key for key, _, _ in RULES}
    if (not isinstance(capabilities, list) or not capabilities or len(capabilities) > 100
            or any(not isinstance(value, str) or value not in allowed for value in capabilities)):
        raise ValueError('Plan capabilities must be a nonempty list of known capability IDs')
    if not isinstance(rationale, str) or len(rationale) > 10_000:
        raise ValueError('Plan rationale must be bounded text')
    if not isinstance(source, str) or not source or len(source) > 80:
        raise ValueError('Plan source must be bounded text')
    capabilities = [key for key, _, _ in RULES if key in capabilities]
    modules = [{'id': 'module:' + key, 'capability': key, 'title': title, 'enabled': True}
               for key, _, title in RULES if key in capabilities]
    return {'intent': '+'.join(capabilities), 'capabilities': capabilities, 'modules': modules,
            'rationale': rationale, 'source': source}


def infer_plan(text, task_mode='research'):
    if not isinstance(text, str) or len(text) > 200_000 or task_mode not in ('research', 'reproduction'):
        raise ValueError('Invalid plan input')
    capabilities = [key for key, pattern, _ in RULES if re.search(pattern, text, re.I)]
    if task_mode == 'reproduction' and 'reproduction' not in capabilities:
        capabilities.append('reproduction')
    if not capabilities:
        capabilities = ['investigation']
    return normalize_plan(capabilities,
        rationale='根据需求关键词和任务模式生成的确定性建议；需按实际研究范围调整，不代表 AI 推理或科学验证。',
        source='deterministic')
