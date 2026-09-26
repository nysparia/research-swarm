"""Bounded, passive previews of registered experiment files. No model-made metrics."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
from pathlib import Path

MAX_FILE = 4 * 1024 * 1024
MAX_TEXT = 100000


def validate_dashboard(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('charts'), list) or len(value['charts']) > 8:
        raise ValueError('图表文件需要 version=1 和最多 8 个 charts')
    charts = []
    for index, chart in enumerate(value['charts']):
        if not isinstance(chart, dict) or chart.get('kind') not in ('bar', 'line'):
            raise ValueError('图表只支持 bar 或 line')
        series = chart.get('series')
        if not isinstance(series, list) or not 1 <= len(series) <= 6:
            raise ValueError('每张图需要 1 至 6 个测量序列')
        normalized = []
        for item in series:
            if not isinstance(item, dict) or not isinstance(item.get('points'), list) or not 1 <= len(item['points']) <= 500:
                raise ValueError('每个测量序列需要 1 至 500 个点')
            points = []
            for point in item['points']:
                if not isinstance(point, dict) or not isinstance(point.get('x'), (str, int, float)) or isinstance(point.get('x'), bool):
                    raise ValueError('图表 x 必须为测量值或类别名')
                if isinstance(point['x'], (int, float)) and not math.isfinite(point['x']):
                    raise ValueError('图表不能包含非有限数值')
                if type(point.get('y')) not in (float, int) or not math.isfinite(point['y']):
                    raise ValueError('图表 y 必须为有限数值，缺测不能填成 0')
                points.append({'x': point['x'] if not isinstance(point['x'], str) else point['x'][:120], 'y': point['y']})
            normalized.append({'name': str(item.get('name', '测量'))[:120], 'points': points})
        charts.append({'id': str(chart.get('id', f'chart-{index + 1}'))[:120], 'title': str(chart.get('title', '实验测量'))[:200],
                       'kind': chart['kind'], 'series': normalized, 'xLabel': str(chart.get('xLabel', ''))[:120],
                       'yLabel': str(chart.get('yLabel', ''))[:120], 'note': str(chart.get('note', ''))[:2000]})
    return {'version': 1, 'charts': charts}


def preview_artifact(root, relative, registered):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if relative not in registered or not path.is_relative_to(root / 'runs') or not path.is_file() or path.is_symlink():
        raise ValueError('只能预览已登记的本课题实验产物')
    if path.stat().st_size > MAX_FILE:
        raise ValueError('产物超过 4 MiB，请下载后查看完整内容')
    raw = path.read_bytes()
    result = {'name': path.name, 'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'truncated': False}
    suffix = path.suffix.lower()
    image_type = ('image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if raw.startswith(b'\xff\xd8\xff')
                  else 'image/webp' if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' else None)
    if image_type:
        return {**result, 'kind': 'image', 'dataUrl': 'data:' + image_type + ';base64,' + base64.b64encode(raw).decode('ascii')}
    if suffix not in ('.py', '.r', '.js', '.ts', '.json', '.csv', '.tsv', '.md', '.tex', '.bib', '.txt', '.log', '.svg'):
        return {**result, 'kind': 'binary', 'text': '此文件为二进制产物，请下载查看。'}
    text = raw.decode('utf-8-sig', errors='replace')
    result.update(kind='code' if suffix in ('.py', '.r', '.js', '.ts') else 'text', text=text[:MAX_TEXT], truncated=len(text) > MAX_TEXT)
    if suffix in ('.csv', '.tsv'):
        reader = csv.reader(io.StringIO(text), delimiter='\t' if suffix == '.tsv' else ',')
        columns = next(reader, [])
        rows, count, wide = [], 0, len(columns) > 30
        for row in reader:
            count += 1
            wide = wide or len(row) > 30
            if count <= 300:
                rows.append(row[:30])
        result.update(kind='table', columns=columns[:30], rows=rows, rowCount=count, truncated=count > 300 or wide)
    if suffix == '.json':
        try:
            value = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非有限 JSON 数值')))
            result['kind'] = 'json'
            if isinstance(value, dict) and 'charts' in value:
                result.update(kind='chart', dashboard=validate_dashboard(value))
        except ValueError as exc:
            result['warning'] = '结构化预览未通过校验：' + str(exc)[:300]
    return result


def live_dashboard(root, execution):
    root = Path(root).resolve()
    directory = (root / str(execution.get('workingDirectory', ''))).resolve()
    path = directory / 'research-dashboard.json'
    if not directory.is_relative_to(root / 'laboratory') or not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root / 'laboratory'):
        return {'pending': True}
    info = path.stat()
    if [info.st_size, info.st_mtime_ns] == execution.get('dashboardBefore') or info.st_size > MAX_FILE:
        return {'pending': True}
    try:
        raw = path.read_bytes()
        dashboard = validate_dashboard(json.loads(raw))
    except (OSError, ValueError):
        return {'pending': True}
    return {'pending': False, 'kind': 'chart', 'dashboard': dashboard, 'provisional': True,
            'sha256': hashlib.sha256(raw).hexdigest(), 'name': path.name}
