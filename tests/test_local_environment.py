import tempfile
import unittest
from pathlib import Path

from research_swarm.local_tools import LocalResearchTools


class LocalEnvironmentTests(unittest.TestCase):
    def test_new_deep_task_directory_runs_without_bundled_setuptools(self):
        # Mirrors Windows task paths long enough to fail setuptools' nested test-data extraction.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ('research-' * 7) / ('task-' * 9) / 'runtime'
            root.mkdir(parents=True)
            tool = LocalResearchTools(root)
            result = tool.call('python_run', {'code': 'import sqlite3,sys; print(sqlite3.sqlite_version); print(sys.prefix); print("实验测量日志")', 'timeoutSeconds': 15},
                               {'id': 'deep-path', 'version': 1}, {'round': 1}, lambda _: None)
            self.assertEqual(result['status'], 'completed', result['stderr'])
            self.assertIn('python-env', result['stdout'])
            self.assertIn('实验测量日志', result['stdout'])


if __name__ == '__main__': unittest.main()
