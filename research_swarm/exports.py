"""Research report bundles from persisted state and registered artifacts."""
from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path


def export_bundle(state: dict, artifact_root: Path) -> bytes:
    state = copy.deepcopy(state)
    from .claims import ensure_graph
    graph = ensure_graph(state)
    report = state.get('report', {})
    if not report.get('approved') and not report.get('ready'):
        raise ValueError('请先完成最终输出确认，再导出研究结果')
    project = state.get('project', {})
    decision = '用户已确认本轮输出。确认表示用户决策记录，不等同外部科学验证。' if report.get('approved') else '本轮执行已结束。以下区分已取得证据、实测结果与尚未验证的候选；报告可导出不代表所有研究验收项已完成。'
    lines = ['# ' + project.get('title', '科研结果'), '', f'研究轮次：{project.get("round", 1)}', f'运行模式：{"已有数据核验" if project.get("mode") == "evidence" else "模型科研运行"}', '', decision, '', report.get('summary', ''), '', '## 结论与证据', '']
    evidence = {e['id']: e for e in state.get('evidence', [])}
    papers = {p['id']: p for p in state.get('papers', [])}
    for claim in report.get('claims', []):
        lines.extend(['### ' + claim.get('text', ''), '', f'状态：{claim.get("status", "candidate")}', ''])
        if claim.get('claimId'):
            lines.extend([f"主张：{claim['claimId']} · 版本 {claim.get('claimVersion', '?')} · 判断 {claim.get('assessmentStatus', 'unassessed')}", ''])
            for relation in graph['relations']:
                if relation['claimId'] == claim['claimId'] and relation['claimVersion'] == claim.get('claimVersion'):
                    lines.append(f"- 证据关系 {relation['type']} / {relation['polarity']} · {relation['evidenceId']}：{relation['reason']}")
        ids = claim.get('evidenceIds', [])
        if not ids:
            lines.extend(['**无证据**：此项不能作为已验证结论。', ''])
        for eid in ids:
            e = evidence.get(eid)
            if not e:
                lines.append(f'- 证据 {eid}：缺失，需重新核验。')
                continue
            p = papers.get(e.get('paperId'), {})
            lines.extend([f'- 证据 {eid} · {p.get("title", "实验产物")} · {e.get("locator", "未定位")} · 类型 {e.get("type", "unknown")}', '', '> ' + e.get('quote', '').replace('\n', '\n> '), ''])
        if claim.get('limitations'):
            lines.extend(['限制：' + claim['limitations'], ''])
    lines.extend(['## 未决问题', ''] + ['- ' + str(x) for x in report.get('unresolved', [])])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.md', '\n'.join(lines).encode('utf-8'))
        archive.writestr('research-data.json', json.dumps(state, ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('evidence.json', json.dumps(state.get('evidence', []), ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('claim-graph.json', json.dumps(graph, ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('materials.json', json.dumps(graph['materials'], ensure_ascii=False, indent=2).encode('utf-8'))
        expression = next((e for e in graph['expressions'] if e['id'] == report.get('expressionId')), None)
        if expression:
            archive.writestr('reproduction-report.md' if expression['kind'] == 'reproduction_report' else 'paper.md',
                             expression['markdown'].encode('utf-8'))
        archive.writestr('activity.json', json.dumps(state.get('activities', []), ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('execution-history.json', json.dumps(state.get('executionHistory', []), ensure_ascii=False, indent=2).encode('utf-8'))
        artifact_root = Path(artifact_root).resolve()
        included = set()
        recorded = [{'type': 'experiment', 'extractor': 'local_process', 'locator': entry['execution']['receipt']}
                    for entry in state.get('history', []) if entry.get('type') == 'tool-executed'
                    and (entry.get('execution') or {}).get('receipt')]
        for e in [*evidence.values(), *recorded]:
            if e.get('type') == 'experiment':
                path = (artifact_root / e.get('locator', '')).resolve()
                if path.is_relative_to(artifact_root / 'runs') and path.is_file():
                    related = list(path.parent.rglob('*')) if e.get('extractor') == 'local_process' else [path]
                    for file in related:
                        if file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(artifact_root/'runs') and file.stat().st_size<=50*1024*1024:
                            relative=file.relative_to(artifact_root).as_posix()
                            if relative not in included:
                                included.add(relative)
                                archive.write(file,relative)
    return stream.getvalue()
