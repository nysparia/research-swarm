import tempfile
import unittest
from pathlib import Path

from research_swarm.engine import Engine
from research_swarm.claims import create_claim, assess_claim
from test_engine import sample_library


class ResponsibilityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = Engine(sample_library(), Path(directory.name) / 'state.sqlite')
        self.addCleanup(self.engine.close)

    def test_unsigned_and_partial_checkpoint_confirmation_are_atomic(self):
        state = self.engine.snapshot()
        payload = {'id': state['activeCheckpointId'], 'decision': 'confirm'}
        for acknowledgement in ({}, {'responsibilityAcknowledged': True},
                                 {'responsibilityAcknowledged': 'true', 'responsibilityName': '审阅者'},
                                 {'responsibilityAcknowledged': True, 'responsibilityName': ' '}):
            with self.subTest(acknowledgement=acknowledgement), self.assertRaisesRegex(ValueError, '签名'):
                self.engine.command('checkpoint', {**payload, **acknowledgement})
            self.assertEqual(self.engine.snapshot(), state)

    def test_signed_confirmation_is_audited_but_not_scientific_validation(self):
        state = self.engine.snapshot()
        result = self.engine.command('checkpoint', {'id': state['activeCheckpointId'], 'decision': 'confirm',
            'expectedRevision': state['revision'], 'responsibilityAcknowledged': True, 'responsibilityName': '审阅者'})
        record = result['checkpoints'][0]
        self.assertEqual(record['responsibilityName'], '审阅者')
        self.assertIs(record['scientificValidation'], False)
        event = next(e for e in result['history'] if e['type'] == 'checkpoint-confirm')
        self.assertTrue(event['responsibilityAcknowledged'])

    def test_stale_signature_cannot_confirm_changed_state(self):
        state = self.engine.snapshot()
        with self.assertRaisesRegex(ValueError, '版本'):
            self.engine.command('checkpoint', {'id': state['activeCheckpointId'], 'decision': 'confirm',
                'expectedRevision': state['revision'] - 1, 'responsibilityAcknowledged': True, 'responsibilityName': '审阅者'})

    def test_claim_decision_requires_separate_signature(self):
        claim = create_claim(self.engine._state, '待核验主张')
        assess_claim(self.engine._state, claim['id'], 'inconclusive', '证据不足')
        payload = {'claimId': claim['id'], 'decision': 'confirm', 'expectedRevision': self.engine.snapshot()['revision']}
        with self.assertRaisesRegex(ValueError, '签名'):
            self.engine.command('claim-decision', payload)
        state = self.engine.command('claim-decision', {**payload, 'responsibilityAcknowledged': True, 'responsibilityName': '审阅者'})
        updated = next(c for c in state['claimGraph']['claims'] if c['id'] == claim['id'])
        self.assertEqual(updated['assessment']['status'], 'inconclusive')
        self.assertFalse(updated['decisions'][-1]['scientificValidation'])


if __name__ == '__main__':
    unittest.main()
