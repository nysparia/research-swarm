"""Persistent coauthorship and projections of real research outputs.

Presence of a citation or successful process is never a verdict on a claim.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone


SECTIONS = [('abstract', '摘要'), ('introduction', '引言与研究问题'), ('related_work', '相关工作'),
            ('method', '方法'), ('experiments', '实验设置'), ('results', '结果与分析'), ('discussion', '讨论与局限')]
ROLES = {'literature': '文献研究员', 'method': '方法研究员', 'baseline': '基线工程师',
         'experiment': '实验工程师', 'statistics': '统计分析员', 'reviewer': '独立审稿人', 'writer': '论文作者',
         'problem': '问题定义研究员', 'novelty': '创新性核查员', 'dataset': '数据工程师', 'reproduction': '复现工程师',
         'ablation': '消融研究员', 'robustness': '稳健性研究员', 'systems': '系统测量工程师',
         'visualization': '科研可视化工程师', 'falsification': '反例研究员'}
BUDGETS = {'compact': {'maxTasks': 48, 'maxIterations': 3, 'maxDepth': 4, 'maxParallel': 3},
           'team': {'maxTasks': 160, 'maxIterations': 6, 'maxDepth': 5, 'maxParallel': 6},
           'swarm': {'maxTasks': 240, 'maxIterations': 8, 'maxDepth': 5, 'maxParallel': 8}}


def now():
    return datetime.now(timezone.utc).isoformat()


def new_paper():
    return {'revision': 0, 'topics': [], 'selectedTopicId': None, 'decisions': [], 'approvedRevision': None, 'budgetTier': 'swarm',
            'sections': [{'id': key, 'title': title, 'markdown': '', 'revision': 0, 'author': 'AI',
                          'evidenceIds': [], 'sourceNodeIds': [], 'sourceVersions': {}, 'suggestion': None}
                         for key, title in SECTIONS], 'seenOutputs': {}}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]


def validate_topics(value):
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError('选题候选必须为最多三项的列表')
    result = []
    for item in value:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                                            for k in ('title', 'question', 'hypothesis', 'contribution', 'feasibility', 'firstExperiment')):
            raise ValueError('选题缺少研究问题、假设、贡献候选、可行性或首个实验')
        topic = {k: item[k].strip()[:2000] for k in ('title', 'question', 'hypothesis', 'contribution', 'feasibility', 'firstExperiment')}
        topic['id'] = 'topic-' + fingerprint(topic)
        result.append(topic)
    return result


def validate_paper_output(structured, valid_evidence):
    sections = structured.get('paperSections', [])
    if not isinstance(sections, list) or len(sections) > len(SECTIONS):
        raise ValueError('paperSections 必须为最多七个章节的列表')
    seen = set()
    for section in sections:
        if not isinstance(section, dict) or section.get('id') not in dict(SECTIONS) or section['id'] in seen:
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
            if not isinstance(option, dict) or any(not isinstance(option.get(k), str) or not option[k].strip()
                                                  for k in ('label', 'effect')):
                raise ValueError('决策选项需要 label 与 effect')
        if len(json.dumps(decision, ensure_ascii=False)) > 10000:
            raise ValueError('研究决策内容过长')
    design = structured.get('experimentDesign')
    if design is not None and (not isinstance(design, dict) or len(json.dumps(design)) > 24000):
        raise ValueError('实验协议必须为有限大小的对象')
    return structured


def edit_section(paper, section_id, payload):
    section = next((s for s in paper['sections'] if s['id'] == section_id), None)
    if not section:
        raise ValueError('论文章节不存在')
    if payload.get('expectedRevision') != section['revision']:
        raise ValueError('论文章节版本已变化，已保留你的编辑，请重新同步')
    text = payload.get('markdown')
    if not isinstance(text, str) or len(text) > 60000:
        raise ValueError('论文章节不能超过 60000 字符')
    if payload.get('acceptSuggestion'):
        suggestion = section.get('suggestion')
        if not suggestion or payload.get('suggestionId') != suggestion['id']:
            raise ValueError('AI 建议已变化，请重新查看')
    section.setdefault('history', []).append({k: copy.deepcopy(section[k]) for k in
        ('markdown', 'revision', 'author', 'evidenceIds', 'sourceNodeIds', 'sourceVersions')})
    if payload.get('acceptSuggestion'):
        section.update({k: copy.deepcopy(suggestion[k]) for k in ('markdown', 'evidenceIds', 'sourceNodeIds', 'sourceVersions')})
        section['round'] = suggestion.get('round', 1)
    else:
        section['markdown'] = text
        # The previous citations remain traceable, but have not been verified against the user's new text.
    section.update(author='user', revision=section['revision'] + 1, updatedAt=now(), suggestion=None)
    paper['revision'] += 1
    paper['approvedRevision'] = None
    paper['decisions'].append({'id': 'edit-' + fingerprint([section_id, paper['revision'], now()]), 'kind': 'writing',
        'title': '修改了「' + section['title'] + '」', 'answer': '采用 AI 建议' if payload.get('acceptSuggestion') else '保存了你的文字',
        'at': now(), 'actor': 'user', 'sectionId': section_id, 'effect': '你的版本成为正文；后续 AI 修改只作为建议。'})


def source_signature(paper, state):
    state = state or {}
    nodes = {node['id']: node for node in state.get('nodes', [])}
    evidence = {item['id']: item for item in state.get('evidence', [])}
    sources = []
    for section in paper['sections']:
        for node_id in section['sourceVersions']:
            node = nodes.get(node_id, {})
            sources.append((section['id'], node_id, node.get('version'), node.get('active'), bool(node.get('output'))))
        for evidence_id in section['evidenceIds']:
            sources.append((section['id'], evidence_id, evidence.get(evidence_id)))
    return fingerprint([state.get('project', {}).get('round', 1), sources,
                        state.get('project', {}).get('researchDecision')])


def sync_paper(paper, state):
    if not state:
        return False
    changed = False
    valid_evidence = {e['id'] for e in state.get('evidence', [])}
    for node in sorted(state.get('nodes', []), key=lambda n: (n['id'] == 'central', n.get('finishedAt') or '')):
        output = node.get('output')
        if not node.get('active') or not output:
            continue
        key = f"{state.get('project', {}).get('round', 1)}:{node['id']}:{node.get('version', 1)}"
        signature = fingerprint(output)
        if paper['seenOutputs'].get(key) == signature:
            continue
        structured = output.get('structured') or {}
        try:
            validate_paper_output(structured, valid_evidence)
        except ValueError:
            # Legacy/custom outputs stay inspectable, never promoted to manuscript.
            paper['seenOutputs'][key] = signature
            changed = True
            continue
        for update in structured.get('paperSections', []):
            section = next(s for s in paper['sections'] if s['id'] == update['id'])
            proposal = {'id': fingerprint([key, update]), 'markdown': update['markdown'],
                        'evidenceIds': list(dict.fromkeys(update.get('evidenceIds', []))),
                        'sourceNodeIds': [node['id']], 'sourceVersions': {node['id']: node.get('version', 1)},
                        'round': state.get('project', {}).get('round', 1), 'at': now()}
            if section['author'] == 'user':
                section['suggestion'] = proposal
            else:
                section.update(proposal, id=section['id'], revision=section['revision'] + 1, updatedAt=now())
                section['suggestion'] = None
        paper['seenOutputs'][key] = signature
        changed = True
    signature = source_signature(paper, state)
    if signature != paper.get('sourceSignature'):
        paper['sourceSignature'] = signature
        changed = True
    if changed:
        paper['revision'] += 1
        paper['approvedRevision'] = None
    return changed


def paper_view(paper, state):
    result = copy.deepcopy(paper)
    result.pop('seenOutputs', None)
    result.pop('sourceSignature', None)
    state = state or {}
    nodes = {n['id']: n for n in state.get('nodes', [])}
    evidence = {e['id']: e for e in state.get('evidence', [])}
    for section in result['sections']:
        section.pop('history', None)
        section['stale'] = any(i not in nodes or not nodes[i].get('active') or nodes[i].get('version') != v
                               for i, v in section['sourceVersions'].items()) or bool(section.get('sourceNodeIds') and
                               section.get('round', 1) != state.get('project', {}).get('round', 1))
        section['evidenceStatus'] = 'unverified' if section['author'] == 'user' else 'linked' if section['evidenceIds'] else 'unsupported'
    claims, experiments, seen = [], [], set()
    for node in nodes.values():
        if not node.get('active'):
            continue
        output = node.get('output') or {}
        for claim in output.get('claims', []):
            marker = (claim.get('text'), tuple(sorted(claim.get('evidenceIds', []))))
            if marker in seen:
                continue
            seen.add(marker)
            ids = [i for i in claim.get('evidenceIds', []) if i in evidence]
            claims.append({**claim, 'id': node['id'] + ':' + str(claim.get('id', len(claims))), 'nodeId': node['id'],
                           'evidenceIds': ids, 'support': 'linked' if ids else 'unsupported',
                           'experimentEvidenceIds': [i for i in ids if evidence[i].get('type') == 'experiment']})
        if node.get('kind') == 'experiment':
            receipts = []
            for entry in state.get('history', []):
                run = entry.get('execution', {})
                if entry.get('type') == 'tool-executed' and entry.get('valid') is True and run.get('nodeId') == node['id'] and run.get('nodeVersion') == node.get('version') and run.get('round') == state.get('project', {}).get('round', 1) and run.get('tool') == 'python_run':
                    receipts.append(run)
            completed = any(r.get('status') == 'completed' for r in receipts)
            structured = output.get('structured') or {}
            experiments.append({'nodeId': node['id'], 'title': node['title'], 'status': node.get('status'),
                'executionStatus': 'executed' if completed else 'failed' if receipts else 'unexecuted',
                'design': structured.get('experimentDesign') or node.get('input', {}).get('experimentDesign') or {},
                'summary': output.get('summary', ''), 'unresolved': output.get('unresolved', []),
                'claimIds': [c['id'] for c in claims if c['nodeId'] == node['id']], 'receipts': receipts})
    result['claims'], result['experiments'] = claims, experiments
    result['pendingDecision'] = state.get('project', {}).get('researchDecision')
    result['issues'] = ([f'「{s["title"]}」待撰写' for s in result['sections'] if not s['markdown'].strip()] +
                        [f'「{s["title"]}」来源已改变，需重新核验' for s in result['sections'] if s['stale']] +
                        [f'「{s["title"]}」正文尚未关联证据' for s in result['sections'] if s['markdown'].strip() and not s['evidenceIds']] +
                        ([f'{sum(c["support"] == "unsupported" for c in claims)} 条候选论点没有证据'] if any(c['support'] == 'unsupported' for c in claims) else []) +
                        [f'「{e["title"]}」尚无本轮成功执行记录' for e in experiments if e['executionStatus'] != 'executed'] +
                        state.get('report', {}).get('unresolved', []))
    result['approved'] = (paper.get('approvedRevision') == paper['revision'] and
                          paper.get('sourceSignature') == source_signature(paper, state))
    result['coverage'] = {'written': sum(bool(s['markdown'].strip()) for s in result['sections']), 'total': len(SECTIONS),
                          'linkedClaims': sum(bool(c['evidenceIds']) for c in claims), 'claims': len(claims),
                          'executedExperiments': sum(e['executionStatus'] == 'executed' for e in experiments), 'experiments': len(experiments)}
    return result


def _tex(text):
    # Plain text export deliberately escapes all TeX commands supplied by a model/user.
    replacements = {'\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$', '&': r'\&', '#': r'\#',
                    '_': r'\_', '%': r'\%', '~': r'\textasciitilde{}', '^': r'\textasciicircum{}'}
    return ''.join(replacements.get(c, c) for c in str(text))


def export_paper(paper, state, title):
    view = paper_view(paper, state)
    markdown = f'# {title}\n\n> 论文草稿 · ' + ('用户已确认此版本' if view['approved'] else '待用户审阅') + '。证据引用不代表论点已成立；实验执行不代表假设已通过。\n'
    latex = ['\\documentclass[UTF8]{ctexart}', '\\usepackage{hyperref}', '\\begin{document}',
             '\\title{' + _tex(title) + '}', '\\author{Human--AI research collaboration}', '\\maketitle',
             _tex('论文草稿。证据及实验结果仍需审阅。')]
    for section in view['sections']:
        body = section['markdown'].strip() or '待撰写。'
        sources = '、'.join(section['evidenceIds']) or '无证据引用 / 待验证'
        note = ('来源已变化，需重新核验。' if section['stale'] else '') + '证据：' + sources
        markdown += f'\n## {section["title"]}\n\n{body}\n\n> {note}\n'
        latex += ['\\section{' + _tex(section['title']) + '}', _tex(body), '\n' + _tex(note)]
    markdown += '\n## 未决问题\n\n' + '\n'.join('- ' + i for i in view['issues'])
    bib = []
    for index, reference in enumerate((state or {}).get('papers', []), 1):
        authors = reference.get('authors') or []
        if isinstance(authors, list):
            authors = ' and '.join(str(a) for a in authors)
        bib.append('@misc{ref' + str(index) + ',\n  title={' + _tex(reference.get('title', '')) + '},\n  author={' + _tex(authors) + '},\n  year={' + _tex(reference.get('year', '')) + '},\n  doi={' + _tex(reference.get('doi', '')) + '}\n}')
    latex += ['\\nocite{*}', '\\bibliographystyle{plain}', '\\bibliography{references}', '\\end{document}']
    return {'paper/manuscript.md': markdown, 'paper/manuscript.tex': '\n\n'.join(latex),
            'paper/references.bib': '\n\n'.join(bib),
            'paper/research-ledger.json': json.dumps(view, ensure_ascii=False, indent=2),
            'paper/evidence.json': json.dumps((state or {}).get('evidence', []), ensure_ascii=False, indent=2),
            'paper/README.md': '论文草稿与研究账本\n\nmanuscript.md 可直接阅读；manuscript.tex 为安全转义后的文字源文件，可用 XeLaTeX 与 BibTeX 编译。未经本机编译为 PDF。references.bib 来自实际论文库，不保证每篇都被正文使用。\n\n研究账本保留候选论点、实验协议、执行凭据与用户决策。正文按章节关联证据；引用存在不等于证据支持该段全部表述。用户编辑后需要重新核对引用。未完成项不会自动变成结论。\n'}
