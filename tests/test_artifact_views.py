import json
import tempfile
import unittest
from pathlib import Path

from research_swarm.artifact_views import preview_artifact, validate_dashboard, live_dashboard


class ArtifactViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root / 'runs/r1/artifacts').mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        relative = 'runs/r1/artifacts/' + name
        (self.root / relative).write_text(text, encoding='utf-8')
        return relative

    def test_chart_preserves_actual_measurements_without_normalizing_or_guessing_units(self):
        chart = {'version': 1, 'charts': [{'id': 'latency', 'title': 'Measured latency', 'kind': 'bar', 'yLabel': 'milliseconds',
            'series': [{'name': 'baseline', 'points': [{'x': 'A', 'y': 0.001}, {'x': 'B', 'y': -0.02}]}]}]}
        path = self.write('research-dashboard.json', json.dumps(chart))
        result = preview_artifact(self.root, path, {path})
        self.assertEqual(result['kind'], 'chart')
        self.assertEqual(result['dashboard']['charts'][0]['series'][0]['points'][1]['y'], -0.02)
        self.assertEqual(result['dashboard']['charts'][0]['yLabel'], 'milliseconds')

    def test_preview_rejects_unregistered_paths_and_symlink_escapes(self):
        path = self.write('private.txt', 'secret')
        with self.assertRaises(ValueError): preview_artifact(self.root, path, set())
        with self.assertRaises(ValueError): preview_artifact(self.root, '../private.txt', {'../private.txt'})

    def test_nonfinite_and_too_many_measurements_are_not_rendered_as_valid_charts(self):
        for points in ([{'x': 1, 'y': float('nan')}], [{'x': 1, 'y': 2}] * 501):
            with self.assertRaises(ValueError):
                validate_dashboard({'version': 1, 'charts': [{'id': 'x', 'title': 'X', 'kind': 'line', 'series': [{'name': 'a', 'points': points}]}]})

    def test_csv_quotes_missing_values_and_preview_limits_are_preserved(self):
        path = self.write('raw.csv', 'model,accuracy,note\n"model, A",0.9,"a,b"\nB,,missing\n')
        value = preview_artifact(self.root, path, {path})
        self.assertEqual(value['kind'], 'table')
        self.assertEqual(value['rows'][0], ['model, A', '0.9', 'a,b'])
        self.assertEqual(value['rows'][1][1], '')

    def test_code_preview_has_digest_and_explicit_truncation(self):
        path = self.write('code.py', 'print(1)\n' * 18000)
        result = preview_artifact(self.root, path, {path})
        self.assertEqual(result['kind'], 'code')
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['sha256']), 64)

    def test_live_chart_excludes_prior_run_data_and_partial_writes(self):
        directory = self.root / 'laboratory/round-1/node'
        directory.mkdir(parents=True)
        path = directory / 'research-dashboard.json'
        chart = {'version': 1, 'charts': [{'kind': 'line', 'series': [{'points': [{'x': 1, 'y': 0.2}]}]}]}
        path.write_text(json.dumps(chart))
        before = path.stat()
        execution = {'workingDirectory': 'laboratory/round-1/node', 'dashboardBefore': [before.st_size, before.st_mtime_ns]}
        self.assertTrue(live_dashboard(self.root, execution)['pending'])
        path.write_text('{"version":1,')
        self.assertTrue(live_dashboard(self.root, execution)['pending'])
        chart['charts'][0]['series'][0]['points'].append({'x': 2, 'y': 0.4})
        path.write_text(json.dumps(chart))
        value = live_dashboard(self.root, execution)
        self.assertFalse(value['pending'])
        self.assertTrue(value['provisional'])
        self.assertEqual(len(value['dashboard']['charts'][0]['series'][0]['points']), 2)


if __name__ == '__main__': unittest.main()
