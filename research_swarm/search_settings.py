"""Validated, non-secret literature budgets shared by API, runner and UI."""
from __future__ import annotations

import copy
from datetime import datetime, timezone


PROFILES = {
    'standard': {'queryCount': 6, 'perQuery': 20, 'candidateLimit': 150,
                 'maxRequests': 36, 'seedCount': 3, 'neighborsPerSeed': 10,
                 'citationDepth': 1, 'maxSeconds': 120, 'maxCalls': 8},
    'deep': {'queryCount': 18, 'perQuery': 30, 'candidateLimit': 500,
             'maxRequests': 100, 'seedCount': 5, 'neighborsPerSeed': 20,
             'citationDepth': 1, 'maxSeconds': 300, 'maxCalls': 16},
}
LIMITS = {'queryCount': (1, 24), 'perQuery': (1, 100), 'candidateLimit': (10, 1000),
          'maxRequests': (3, 200), 'seedCount': (0, 10), 'neighborsPerSeed': (1, 50),
          'citationDepth': (0, 1), 'maxSeconds': (10, 600), 'maxCalls': (1, 40),
          'yearFrom': (1900, 2100)}
SOURCES = ('openalex', 'arxiv', 'semantic_scholar')
KEY_ENVS = {'openalex': 'OPENALEX_API_KEY', 'semantic_scholar': 'SEMANTIC_SCHOLAR_API_KEY'}


def normalize_search(value=None):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('论文检索设置必须为对象')
    if set(value) - (set(LIMITS) | {'profile', 'sources', 'crossrefFallback'}):
        raise ValueError('未知的论文检索设置')
    profile = value.get('profile', 'standard')
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('检索档位须为 standard 或 deep')
    result = dict(PROFILES[profile], profile=profile, sources=list(SOURCES),
                  crossrefFallback=True, yearFrom=max(1900, datetime.now(timezone.utc).year - 5))
    result.update(copy.deepcopy(value))
    for name, (low, high) in LIMITS.items():
        item = result[name]
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise ValueError(f'{name} 须为 {low}–{high} 的整数')
    if (not isinstance(result['sources'], list) or not result['sources'] or
            any(not isinstance(s, str) or s not in SOURCES for s in result['sources']) or
            len(set(result['sources'])) != len(result['sources'])):
        raise ValueError('请选择不重复的 OpenAlex、arXiv 或 Semantic Scholar 来源')
    if not isinstance(result['crossrefFallback'], bool):
        raise ValueError('Crossref 回退开关须为布尔值')
    return result


def update_search(previous, changes):
    if not isinstance(changes, dict):
        raise ValueError('论文检索设置必须为对象')
    # Switching presets resets their budgets; explicit overrides still win.
    base = normalize_search(previous)
    if 'profile' in changes and (not isinstance(changes['profile'], str) or changes['profile'] not in PROFILES):
        raise ValueError('检索档位须为 standard 或 deep')
    if changes.get('profile', base['profile']) != base['profile']:
        base.update(PROFILES.get(changes['profile'], {}))
    return normalize_search(dict(base, **changes))
