import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from research_swarm.claim_runtime import validate_relations
from research_swarm.semantic_review import blind_payload, relation_gaps, review_claim
from scripts.evaluate_semantics import (case_inputs, compare_human_labels, compare_predictions,
                                        host_evaluation, live_record, load_dataset, main,
                                        score_predictions)


def source():
    return {'id': 'e1', 'type': 'full_text', 'quote': 'A beats B on Dataset X.', 'locator': 'paper.pdf#page=3'}


def claim():
    return {'id': 'c1', 'version': 1, 'statement': 'A beats B on Dataset X.', 'scope': 'Dataset X',
            'falsification': 'A does not beat B on Dataset X.'}


def relation():
    return {'evidenceId': 'e1', 'type': 'support', 'polarity': 'for', 'quality': 'usable',
            'reason': 'Explicit comparison in the stated scope', 'applicability': 'Dataset X',
            'quote': source()['quote'], 'locator': source()['locator'], 'rule': 'direct_statement', 'confidence': .9}


def context():
    return {'claim': claim(), 'library': {'evidence': [source()]},
            'children': [{'output': {'evidenceIds': ['e1']}}], 'claimGraph': {'relations': []}}


def verdict():
    return {'hypothesisVerdict': {'status': 'supported', 'reason': 'Direct source support',
                                  'limitations': 'Dataset X only', 'evidenceIds': ['e1']},
            'evidenceRelations': [relation()]}


class Reviewer:
    def __init__(self, output=None, ready=True, independent=True, error=None):
        self.output = output if output is not None else verdict()
        self.ready = ready
        self.independent = independent
        self.error = error
        self.calls = []

    def role_status(self, role):
        return {'ready': self.ready, 'independentFromMain': self.independent,
                'identity': {'baseUrl': 'https://review.example/v1', 'model': 'review-model'}}

    def chat(self, messages, **kwargs):
        self.calls.append((copy.deepcopy(messages), kwargs))
        if self.error:
            raise self.error
        return json.dumps(self.output)

    @staticmethod
    def safe_error(error):
        return str(error)


class SemanticReviewTests(unittest.TestCase):
    def test_blind_payload_omits_producer_interpretations_and_preserves_counterevidence(self):
        data = context()
        data['claim']['assessment'] = {'reason': 'PRODUCER_VERDICT'}
        data['claim']['origin'] = {'reason': 'PRODUCER_ORIGIN'}
        data['children'][0]['output'].update(summary='PRODUCER_SUMMARY',
            structured={'hypothesisVerdict': {'reason': 'PRODUCER_REASON'}})
        data['library']['evidence'][0]['summary'] = 'PRODUCER_EVIDENCE_SUMMARY'
        data['library']['evidence'].extend([
            {'id': 'e2', 'quote': 'A fails on X.', 'type': 'full_text', 'locator': 'paper.pdf#page=8'},
            {'id': 'e3', 'quote': 'Unreturned evidence', 'type': 'full_text', 'locator': 'page 9'}])
        data['claimGraph']['relations'] = [{'claimId': 'c1', 'claimVersion': 1, 'evidenceId': 'e2',
                                          'reason': 'PRODUCER_COUNTER_INTERPRETATION', 'polarity': 'against'}]
        data['evidenceApprovals'] = {'e2': {'measurements': {'score': 0}}, 'e3': {'measurements': {'score': 1}}}
        payload = blind_payload({}, data)
        self.assertNotIn('PRODUCER_', json.dumps(payload))
        self.assertEqual({e['id'] for e in payload['evidence']}, {'e1', 'e2'})
        self.assertEqual(set(payload['verifiedMeasurements']), {'e2'})
        self.assertEqual(payload['claim']['statement'], claim()['statement'])

    def test_missing_or_same_judge_is_explicitly_inconclusive_without_call(self):
        for reviewer in (Reviewer(ready=False), Reviewer(independent=False)):
            result = review_claim(reviewer, {}, context(), lambda _: None)
            self.assertEqual(reviewer.calls, [])
            self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'inconclusive')
            self.assertFalse(result['structured']['review']['independent'])
            self.assertTrue(result['structured']['review']['needsHumanReview'])

    def test_judge_failure_is_inconclusive_and_preserves_input_evidence(self):
        data = context()
        before = copy.deepcopy(data)
        result = review_claim(Reviewer(error=RuntimeError('Unavailable')), {}, data, lambda _: None)
        self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'inconclusive')
        self.assertEqual(result['structured']['review']['status'], 'unavailable')
        self.assertEqual(data, before)

    def test_anchored_source_is_admitted_with_explicit_judge_metadata(self):
        reviewer = Reviewer()
        result = review_claim(reviewer, {}, context(), lambda _: None)
        self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'supported')
        self.assertTrue(result['structured']['review']['independent'])
        self.assertTrue(result['structured']['review']['blinded'])
        self.assertEqual(reviewer.calls[0][1]['role'], 'judge')
        self.assertTrue(result['structured']['evidenceRelations'][0]['semanticGate']['passed'])

    def test_missing_fragment_locator_or_rule_downgrades_instead_of_accepting(self):
        for field, invalid in [('quote', ''), ('quote', 'Fabricated text'), ('locator', ''),
                               ('locator', 'paper.pdf#page=99'), ('rule', 'citation_count')]:
            with self.subTest(field=field, invalid=invalid):
                output = verdict()
                output['evidenceRelations'][0][field] = invalid
                result = review_claim(Reviewer(output), {}, context(), lambda _: None)
                self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'inconclusive')
                self.assertEqual(result['structured']['evidenceRelations'][0]['polarity'], 'unresolved')
                self.assertTrue(result['unresolved'])

    def test_evidence_type_and_nontext_source_are_guarded(self):
        self.assertEqual(relation_gaps(relation(), source(), claim()), [])
        for kind in ('abstract', 'experiment', 'fulltext', None):
            with self.subTest(kind=kind):
                self.assertTrue(relation_gaps(relation(), {**source(), 'type': kind}, claim()))
        for value in (None, 123, ['A beats B on Dataset X.'], {'text': 'A beats B'}):
            with self.subTest(value=value):
                self.assertTrue(relation_gaps(relation(), {**source(), 'quote': value}, claim()))

    def test_confidence_requires_finite_number_and_low_confidence_is_sampled(self):
        for confidence in (True, False, '0.9', None, -1, 1.1, float('nan'), float('inf')):
            with self.subTest(confidence=confidence):
                self.assertTrue(relation_gaps({**relation(), 'confidence': confidence}, source(), claim()))
        output = verdict()
        output['evidenceRelations'][0]['confidence'] = .4
        result = review_claim(Reviewer(output), {}, context(), lambda _: None)
        self.assertTrue(result['structured']['review']['needsHumanReview'])
        self.assertEqual(result['structured']['review']['samplingReason'], 'low_confidence')

    def test_same_evidence_cannot_be_attached_in_opposite_directions(self):
        with self.assertRaises(ValueError):
            validate_relations([relation(), {**relation(), 'polarity': 'against'}], {'e1'})
        validate_relations([{**relation(), 'polarity': 'mixed'}], {'e1'})

    def test_paper_report_is_not_reproduction_measurement(self):
        target = {'metric': 'accuracy', 'expected': '.9', 'tolerance': '.01', 'conditions': 'Dataset X'}
        self.assertTrue(relation_gaps(relation(), source(), {**claim(), 'reproductionTarget': target}))

    def test_replication_requires_approved_completed_measurement_and_matching_conditions(self):
        evidence = {**source(), 'type': 'experiment', 'tool': 'python_run', 'executionStatus': 'completed'}
        candidate = {**relation(), 'rule': 'verified_measurement'}
        target = {'metric': 'score', 'expected': '1', 'tolerance': '.1', 'conditions': 'Dataset X'}
        current = {**claim(), 'reproductionTarget': target}
        approval = {'artifactHashes': {'metrics.json': 'abc'}, 'measurements': {'score': 1.05}, 'conditions': 'Dataset X',
                    'review': {'role': 'redteam', 'status': 'completed', 'independent': True}}
        self.assertEqual(relation_gaps(candidate, evidence, current, {'e1': approval}), [])
        for bad in ({}, {'e1': {**approval, 'conditions': 'Dataset Y'}},
                    {'e1': {**approval, 'measurements': {'other_metric': 1.05}}},
                    {'e1': {**approval, 'artifactHashes': {}}},
                    {'e1': {**approval, 'review': {}}},
                    {'e1': {**approval, 'review': {'role': 'redteam', 'status': 'completed', 'independent': False}}}):
            with self.subTest(approval=bad):
                self.assertTrue(relation_gaps(candidate, evidence, current, bad))
        self.assertTrue(relation_gaps(candidate, {**evidence, 'executionStatus': 'failed'}, current, {'e1': approval}))

    def test_replication_tolerance_rejects_outside_values_and_zero_tolerance_epsilon(self):
        evidence = {**source(), 'type': 'experiment', 'tool': 'python_run', 'executionStatus': 'completed'}
        candidate = {**relation(), 'rule': 'verified_measurement'}
        target = {'metric': 'score', 'expected': '1', 'tolerance': '.1', 'conditions': 'Dataset X'}
        approval = {'artifactHashes': {'metrics.json': 'abc'}, 'measurements': {'score': 2}, 'conditions': 'Dataset X',
                    'review': {'role': 'redteam', 'status': 'completed', 'independent': True}}
        current = {**claim(), 'reproductionTarget': target}
        self.assertTrue(relation_gaps(candidate, evidence, current, {'e1': approval}))
        self.assertEqual(relation_gaps({**candidate, 'polarity': 'against'}, evidence, current, {'e1': approval}), [])
        approval['measurements']['score'] = 1.1
        self.assertEqual(relation_gaps(candidate, evidence, current, {'e1': approval}), [],
                         'A value exactly at a declared decimal tolerance is admissible')
        approval['round'] = 1
        self.assertTrue(relation_gaps(candidate, evidence, current, {'e1': approval}, current_round=2))
        self.assertEqual(relation_gaps(candidate, evidence, current, {'e1': approval}, current_round=1), [])
        self.assertTrue(relation_gaps(candidate, evidence, {**current, 'reproductionTarget': {**target, 'conditions': None}},
                                     {'e1': {**approval, 'conditions': None}}))
        current['reproductionTarget'] = {**target, 'expected': '0', 'tolerance': '0'}
        approval['measurements']['score'] = 1e-13
        self.assertTrue(relation_gaps(candidate, evidence, current, {'e1': approval}))
        for field, value in [('expected', True), ('tolerance', '-1'), ('expected', '1 ms'), ('tolerance', 'nan')]:
            with self.subTest(field=field, value=value):
                current['reproductionTarget'] = {**target, field: value}
                self.assertTrue(relation_gaps(candidate, evidence, current, {'e1': approval}))


class SemanticBenchmarkTests(unittest.TestCase):
    def test_corpus_has_fifty_distinct_traps_and_controls_with_auditable_host_results(self):
        cases = load_dataset()['cases']
        traps = [c for c in cases if c['id'].startswith('trap-')]
        self.assertGreaterEqual(len(traps), 50)
        self.assertEqual(len({c['category'] for c in traps}), len(traps))
        self.assertTrue(any(c['expectedPolarity'] == 'for' for c in cases))
        self.assertTrue(any(c['expectedPolarity'] == 'against' for c in cases))
        host = host_evaluation(cases)
        self.assertEqual(host['contractMatches'], len(cases))
        # Correct anchoring does not imply semantic support. Keep this gap visible.
        self.assertGreater(host['nonSupportingAdmittedAsSupport'], 0)
        self.assertEqual(len(host['records']), len(cases))

    def test_live_scoring_is_blind_and_routes_only_to_requested_role(self):
        case = load_dataset()['cases'][0]
        _, evidence, _, _ = case_inputs(case)
        reviewer = Reviewer({'polarity': 'unresolved', 'reason': 'Association is not causation',
                             'quote': evidence['quote'], 'locator': evidence['locator'],
                             'rule': 'direct_statement', 'confidence': .95})
        record = live_record(reviewer, 'redteam', case)
        self.assertEqual(record['polarity'], 'unresolved')
        self.assertEqual(reviewer.calls[0][1]['role'], 'redteam')
        request = reviewer.calls[0][0][1]['content']
        self.assertNotIn('expectedPolarity', request)
        self.assertNotIn('candidatePolarity', request)
        self.assertNotIn('association_vs_causation', request)

    def test_partial_scoring_uses_only_completed_matched_cases_and_reports_missing(self):
        cases = [{'id': 'a', 'expectedPolarity': 'unresolved'}, {'id': 'b', 'expectedPolarity': 'for'},
                 {'id': 'c', 'expectedPolarity': 'against'}]
        predictions = [{'caseId': 'a', 'polarity': 'for'}, {'caseId': 'b', 'polarity': 'for'},
                       {'caseId': 'c', 'status': 'error'}, {'caseId': 'unknown', 'polarity': 'for'}]
        score = score_predictions(cases, predictions)
        self.assertEqual(score['completedMatchedCases'], 2)
        self.assertEqual(score['falseSupportRate'], 1)
        self.assertEqual(score['agreementWithSyntheticLabels'], .5)
        self.assertEqual(score['missingOrFailedCaseIds'], ['c'])
        self.assertEqual(score['unknownPredictionCaseIds'], ['unknown'])

    def test_flip_and_human_agreement_use_intersection_and_never_invent_zero_case_rates(self):
        first = [{'caseId': 'a', 'polarity': 'for'}, {'caseId': 'b', 'polarity': 'unresolved'}]
        second = [{'caseId': 'a', 'polarity': 'against'}, {'caseId': 'c', 'polarity': 'for'}]
        result = compare_predictions(first, second, ['a', 'b', 'c'])
        self.assertEqual((result['matchedCases'], result['flipCount'], result['flipRate']), (1, 1, 1))
        self.assertIsNone(compare_predictions(first, second, ['d'])['flipRate'])
        labels = {'independent': True, 'annotator': 'Expert 1', 'labelerType': 'expert', 'records': second}
        calibrated = compare_human_labels(first, labels, ['a', 'b', 'c'])
        self.assertEqual(calibrated['matchedCases'], 1)
        self.assertEqual(calibrated['expertAgreement'], 0)
        with self.assertRaises(ValueError):
            compare_human_labels(first, {**labels, 'independent': False}, ['a'])
        with self.assertRaises(ValueError):
            score_predictions([{'id': 'a', 'expectedPolarity': 'for'}], first + first)

    def test_comparison_discloses_identity_and_excludes_credentials(self):
        entries = [{'caseId': 'a', 'polarity': 'for'}]
        a = {'identity': {'baseUrl': 'http://user:secret@localhost:11434/v1?key=secret',
                          'model': 'a', 'apiKey': 'secret'}, 'records': entries}
        b = {'identity': {'baseUrl': 'http://localhost:1234/v1', 'model': 'b'}, 'records': entries}
        result = compare_predictions(a, b, ['a'])
        self.assertTrue(result['differentConfiguredIdentities'])
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['reviewerIdentities'][0]['baseUrl'], 'http://localhost:11434/v1')
        self.assertFalse(compare_predictions(a, a, ['a'])['differentConfiguredIdentities'])
        self.assertIsNone(compare_predictions(entries, entries, ['a'])['differentConfiguredIdentities'])

    def test_offline_cli_writes_report_without_any_model_request(self):
        with tempfile.TemporaryDirectory() as directory, patch('scripts.evaluate_semantics.Settings') as settings:
            settings.ROLES = ('main', 'judge', 'redteam')
            path = Path(directory) / 'report.json'
            self.assertEqual(main(['--output', str(path)]), 0)
            result = json.loads(path.read_text(encoding='utf-8'))
            settings.assert_not_called()
            self.assertIsNone(result['modelReview'])
            self.assertEqual(result['hostGate']['evaluated'], 65)
            self.assertIn('not independent expert', result['warning'])
            self.assertEqual(len(result['cases']), 65)
            self.assertEqual(len(result['datasetSha256']), 64)
            self.assertEqual(result['dataset'], 'semantic-evidence.json')


if __name__ == '__main__':
    unittest.main()
