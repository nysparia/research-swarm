import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research_swarm.experiment_jobs import ExperimentJobs
from research_swarm.research_materials import atomic_json
from test_experiment_jobs import wait_job


class ReceiptFailureTests(unittest.TestCase):
    def test_receipt_failure_is_visible_and_does_not_kill_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            result = {'status': 'completed', 'returnCode': 0, 'stdout': 'finished', 'stderr': '', 'artifacts': []}
            with patch.object(ExperimentJobs, '_execute', side_effect=lambda *args: (dict(result), None)):
                jobs = ExperimentJobs(Path(directory))
                try:
                    with patch('research_swarm.experiment_jobs.atomic_json', side_effect=OSError('disk denied')):
                        first = jobs.submit({'code': 'print(1)'})
                        failed = wait_job(jobs, first['id'], timeout=1)
                    self.assertEqual(failed['status'], 'failed')
                    self.assertIn('disk denied', failed['result']['error'])
                    self.assertEqual(failed['attempts'][-1]['status'], 'failed')
                    self.assertNotIn('receipt', failed['attempts'][-1])
                    self.assertFalse(failed['result'].get('evidence'))
                    second = jobs.submit({'code': 'print(2)'})
                    self.assertEqual(wait_job(jobs, second['id'])['status'], 'completed')
                finally:
                    jobs.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows long-path regression')
    def test_atomic_json_does_not_hit_legacy_limit_for_temporary_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            while len(str(target)) < 215:
                target = target / ('nested-' + 'a' * 13)
            target.mkdir(parents=True)
            target = target / 'receipt.json'
            self.assertLess(len(str(target)), 260)
            self.assertGreater(len(str(target)) + 42, 260)
            atomic_json(target, {'status': 'completed'})
            self.assertEqual(json.loads(target.read_text('utf-8')), {'status': 'completed'})
