"""Evaluate the terminology decision protocol; live inference is explicit opt-in.

No Tavily search is performed. These annotated scenarios do not certify an
LLM's knowledge boundaries, source interpretation, or scientific correctness.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_swarm.concept_search import normalize_concepts, normalize_identity
from research_swarm.providers import Settings, parse_json_object
from research_swarm.requirement_concepts import PROTOCOL


def decision(result, case):
    action = result.get('action')
    if action == 'resolve_concepts':
        concepts = normalize_concepts(result.get('concepts'), case['input'], '')
        terms = {c['term'].casefold() for c in concepts}
        if case.get('terms') and not terms.intersection(t.casefold() for t in case['terms']):
            return 'wrong_term'
        return 'lookup'
    if action != 'draft':
        return 'invalid'
    resolutions = result.get('conceptResolutions', [])
    if not isinstance(resolutions, list):
        return 'invalid'
    pending = [r for r in resolutions if isinstance(r, dict) and r.get('status') == 'unresolved'
               and normalize_identity(r, case['input'], '').get('core', r.get('core')) is True]
    if pending:
        expected = {t.casefold() for t in case.get('terms', [])}
        if expected and not any(str(r.get('term', '')).casefold() in expected for r in pending):
            return 'wrong_term'
        return 'clarify'
    return 'skip'


def evaluate(cases, settings=None):
    rows = []
    for case in cases:
        row = {**case, 'status': 'not_run', 'decision': None, 'passed': None}
        if settings:
            try:
                result = parse_json_object(settings.chat([
                    {'role': 'system', 'content': '你是研究需求协作者。本次评估只返回 action、concepts 或 conceptResolutions，省略长需求正文。搜索可用，但不执行搜索。' + PROTOCOL},
                    {'role': 'user', 'content': case['input']}], max_tokens=1800, json_mode=True))
                outcome = decision(result, case)
                row.update(status='evaluated', decision=outcome, passed=outcome in case['expected'], response=result)
            except Exception as error:
                row.update(status='error', error=settings.safe_error(error), passed=False)
        rows.append(row)
    evaluated = [row for row in rows if row['status'] == 'evaluated']
    return {'scope': 'concept_decision_protocol_only', 'liveInference': settings is not None, 'tavilyRequests': 0,
            'caseCount': len(rows), 'evaluatedCount': len(evaluated),
            'passedCount': sum(row['passed'] is True for row in evaluated),
            'falseSearchCount': sum(row['decision'] == 'lookup' and row['expected'] == ['skip'] for row in evaluated) if evaluated else None,
            'missedLookupCount': sum(row['expected'] == ['lookup'] and row['decision'] != 'lookup' for row in evaluated) if evaluated else None,
            'ambiguousGuessedCount': sum(row['category'] == 'ambiguous' and row['decision'] == 'skip' for row in evaluated) if evaluated else None,
            'errorCount': sum(row['status'] == 'error' for row in rows),
            'notice': '默认不调用模型；未运行不等于通过。合成与人工标注样例仅用于提示词回归，不能证明模型知识边界或消歧始终可靠。', 'cases': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=ROOT/'benchmarks/concept-cases.json')
    parser.add_argument('--live', action='store_true', help='Explicitly call the configured main model; may incur charges')
    parser.add_argument('--state-dir', type=Path, help='Existing configuration directory, required for --live')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    if not isinstance(cases, list) or not cases or len({c['id'] for c in cases}) != len(cases):
        parser.error('cases must be a nonempty list with unique IDs')
    settings = None
    if args.live:
        if not args.state_dir or not (args.state_dir/'config.local.json').is_file():
            parser.error('--live requires --state-dir with an existing config.local.json')
        settings = Settings(args.state_dir, None)
        if settings.public()['mode'] != 'llm' or not settings.public()['capabilities']['modelReady']:
            parser.error('configure and enable the main model first')
    report = evaluate(cases, settings)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, ensure_ascii=False, indent=2))
    return 1 if report['errorCount'] or any(r['passed'] is False for r in report['cases']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
