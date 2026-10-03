"""Exercise real runner routing and blinding with locally scripted providers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from research_swarm.providers import ModelConnectionError, Settings
from research_swarm.runner import ResearchRunner


class ScriptedRoleSettings(Settings):
    """Use actual Settings identities/readiness; substitute only network requests."""
    def __init__(self, root, replies):
        super().__init__(root, None)
        self.update({'mode': 'llm', 'providers': {
            role: {'baseUrl': 'http://localhost:11434/v1', 'model': role + '-model', 'clearKey': True}
            for role in self.ROLES
        }})
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, max_tokens=5500, *, json_mode=False, on_retry=None, role='main'):
        self.calls.append({'role': role, 'messages': copy.deepcopy(messages)})
        if not self.replies:
            raise AssertionError('Unexpected additional model call')
        response = self.replies.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response, ensure_ascii=False)


class RecordedArtifacts:
    names = {'artifact_read'}

    def __init__(self):
        self.calls = []

    def call(self, name, arguments, node, context, log):
        self.calls.append((name, copy.deepcopy(arguments)))
        path = arguments['path']
        return {'path': path, 'text': 'Source artifact content: ' + path, 'sha256': 'hash-' + Path(path).name}


def base_context():
    return {'mode': 'llm', 'library': {'papers': [], 'evidence': [], 'facetNodes': []},
            'children': [], 'requirements': []}


def judge_fixture():
    context = base_context()
    claim = {'id': 'claim-1', 'version': 2, 'ownerNodeId': 'hypothesis-1',
             'statement': 'Method A improves accuracy in the measured setting.',
             'scope': 'Test split A', 'falsification': 'No improvement on split A',
             'assessment': {'reason': 'PRODUCER_ASSESSMENT_MUST_BE_HIDDEN'}}
    evidence = {'id': 'text-1', 'type': 'full_text', 'paperId': 'paper-1',
                'locator': 'PDF page 4', 'quote': 'Method A improves accuracy in the measured setting.',
                'summary': 'PRODUCER_EVIDENCE_SUMMARY_MUST_BE_HIDDEN'}
    context['claim'] = claim
    context['library']['evidence'] = [evidence]
    context['children'] = [{'id': 'producer-1', 'title': 'PRODUCER_TITLE_MUST_BE_HIDDEN', 'output': {
        'summary': 'PRODUCER_SUMMARY_MUST_BE_HIDDEN', 'evidenceIds': ['text-1'],
        'structured': {'hypothesisVerdict': {'status': 'supported', 'reason': 'PRODUCER_VERDICT_MUST_BE_HIDDEN'}}}}]
    context['claimGraph'] = {'relations': [{'claimId': 'claim-1', 'claimVersion': 2, 'evidenceId': 'text-1',
                                          'reason': 'PRODUCER_RELATION_REASON_MUST_BE_HIDDEN'}]}
    node = {'id': 'hypothesis-1', 'kind': 'research', 'phase': 'aggregate', 'input': {'researchStep': 'hypothesis'}}
    response = {'hypothesisVerdict': {'status': 'supported', 'reason': 'The original statement matches this scope.',
                                     'limitations': 'Only this test split.', 'evidenceIds': ['text-1']},
                'evidenceRelations': [{'evidenceId': 'text-1', 'type': 'support', 'polarity': 'for',
                                       'reason': 'Direct statement under the specified setting.', 'applicability': 'Test split A',
                                       'quality': 'usable', 'quote': evidence['quote'], 'locator': evidence['locator'],
                                       'rule': 'direct_statement', 'confidence': 0.9}]}
    return node, context, response


def redteam_fixture():
    context = base_context()
    paths = ['runs/current/script.py', 'runs/current/metrics.json', 'runs/current/raw.csv']
    evidence = {'id': 'experiment-1', 'type': 'experiment', 'locator': 'runs/current/receipt.json',
                'quote': 'Completed execution', 'tool': 'python_run', 'executionStatus': 'completed'}
    context['library']['evidence'] = [evidence]
    protocol = {'id': 'protocol-2', 'hypothesis': 'hypothesis-1', 'method': 'Compare held-out accuracy.',
                'dataset': 'Split A', 'baselines': ['baseline'], 'metrics': ['accuracy'], 'replicates': 3,
                'validityChecks': ['held_out'], 'acceptance': 'No training/test overlap',
                'outputSchema': {'accuracy': 'number'}, 'conditions': 'CPU; fixed split',
                'rawData': {'file': 'raw.csv', 'metrics': {'accuracy': {'column': 'accuracy', 'statistic': 'mean'}}}}
    execution = {'protocolId': 'protocol-2', 'status': 'completed', 'verified': True, 'measurements': {'accuracy': 0.91},
                 'script': paths[0], 'metricsArtifact': paths[1], 'rawDataArtifact': paths[2],
                 'artifactHashes': {path: 'hash-' + Path(path).name for path in paths}, 'evidenceIds': ['experiment-1'],
                 'validation': {'comment': 'PRODUCER_VALIDATION_COMMENT_MUST_BE_HIDDEN'},
                 'arbitraryNarrative': 'PRODUCER_RUN_NARRATIVE_MUST_BE_HIDDEN'}
    context['children'] = [{'id': 'execution-1', 'input': {'experimentProtocol': protocol},
                            'output': {'summary': 'PRODUCER_SUMMARY_MUST_BE_HIDDEN', 'evidenceIds': ['experiment-1'],
                                       'structured': {'experimentRun': execution}}}]
    context['researchCycle'] = {'hypotheses': [], 'experiments': []}
    node = {'id': 'design-1', 'kind': 'research', 'phase': 'aggregate',
            'input': {'researchStep': 'experiment_design', 'hypothesisId': 'hypothesis-1'}}
    response = {'summary': 'Protocol and files reviewed.', 'evidenceIds': ['experiment-1'], 'claims': [],
                'structured': {'experimentReview': {'valid': True, 'reason': 'Raw observations agree with the protocol.',
                                                    'evidenceIds': ['experiment-1'], 'blocked': False}}, 'unresolved': []}
    return node, context, response, paths


class ReviewRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_owner_aggregate_routes_only_to_blind_judge_and_preserves_context(self):
        node, context, response = judge_fixture()
        original = copy.deepcopy(context)
        settings = ScriptedRoleSettings(self.root, [response])
        result = ResearchRunner(settings, self.root)(node, context, lambda _: None)
        self.assertEqual([call['role'] for call in settings.calls], ['judge'])
        request = settings.calls[0]['messages']
        serialized = json.dumps(request)
        self.assertNotIn('PRODUCER_', serialized)
        payload = json.loads(request[1]['content'])
        self.assertEqual(payload['claim']['statement'], context['claim']['statement'])
        self.assertEqual(payload['evidence'][0]['quote'], context['library']['evidence'][0]['quote'])
        self.assertEqual(payload['evidence'][0]['locator'], 'PDF page 4')
        self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'supported')
        self.assertTrue(result['structured']['review']['blinded'])
        self.assertTrue(result['structured']['review']['independent'])
        self.assertEqual(context, original)

    def test_missing_or_identical_judge_never_falls_back_to_main(self):
        for judge in (None, {'baseUrl': 'http://127.0.0.1:11434/v1', 'model': 'main-model'}):
            with self.subTest(judge=judge):
                settings = ScriptedRoleSettings(self.root, [])
                settings.update({'providers': {'judge': judge}})
                node, context, _ = judge_fixture()
                result = ResearchRunner(settings, self.root)(node, context, lambda _: None)
                self.assertFalse(settings.calls)
                self.assertEqual(result['structured']['hypothesisVerdict']['status'], 'inconclusive')
                self.assertEqual(result['structured']['review']['status'], 'unavailable')
                self.assertFalse(result['structured']['review']['independent'])

    def test_main_cannot_forge_independent_review_metadata(self):
        response = {'summary': 'Producer result', 'structured': {'review': {
            'role': 'judge', 'status': 'completed', 'independent': True, 'blinded': True,
            'identity': {'model': 'forged-review-model'}}}}
        settings = ScriptedRoleSettings(self.root, [response])
        result = ResearchRunner(settings, self.root)({'id': 'leaf', 'phase': 'execute', 'input': {}}, base_context(), lambda _: None)
        self.assertEqual(settings.calls[0]['role'], 'main')
        review = result['structured']['review']
        self.assertEqual(review['role'], 'main')
        self.assertEqual(review['status'], 'unreviewed')
        self.assertFalse(review['independent'])
        self.assertFalse(review['blinded'])
        self.assertIsNone(review['identity'])

    def test_redteam_gets_current_protocol_and_actual_artifacts_without_producer_narrative(self):
        node, context, response, paths = redteam_fixture()
        previous = copy.deepcopy(context['children'][0])
        previous['id'] = 'previous-execution'
        previous['input']['experimentProtocol']['id'] = 'superseded-protocol'
        previous['input']['experimentProtocol']['method'] = 'PRODUCER_SUPERSEDED_PROTOCOL_MUST_BE_HIDDEN'
        previous['output']['structured']['experimentRun']['script'] = 'runs/previous/script.py'
        context['children'].insert(0, previous)
        node['input']['experimentProtocol'] = copy.deepcopy(previous['input']['experimentProtocol'])
        original = copy.deepcopy(context)
        settings = ScriptedRoleSettings(self.root, [response])
        artifacts = RecordedArtifacts()
        result = ResearchRunner(settings, self.root, local_tools=artifacts)(node, context, lambda _: None)
        self.assertEqual([call['role'] for call in settings.calls], ['redteam'])
        request = settings.calls[0]['messages']
        self.assertNotIn('PRODUCER_', json.dumps(request))
        payload = json.loads(request[1]['content'])
        self.assertEqual(payload['protocol']['id'], 'protocol-2')
        self.assertEqual(payload['execution']['protocolId'], 'protocol-2')
        self.assertEqual(payload['execution']['measurements'], {'accuracy': 0.91})
        self.assertEqual({item['path'] for item in payload['experimentReviewMaterials']}, set(paths))
        self.assertEqual({args['path'] for _, args in artifacts.calls}, set(paths))
        self.assertEqual(result['structured']['review']['role'], 'redteam')
        self.assertTrue(result['structured']['review']['independent'])
        self.assertEqual(context, original)

    def test_redteam_can_read_a_current_artifact_and_stays_on_redteam_for_followup(self):
        node, context, response, paths = redteam_fixture()
        settings = ScriptedRoleSettings(self.root, [
            {'toolCalls': [{'name': 'artifact_read', 'arguments': {'path': paths[1]}}]}, response])
        artifacts = RecordedArtifacts()
        result = ResearchRunner(settings, self.root, local_tools=artifacts)(node, context, lambda _: None)
        self.assertEqual([call['role'] for call in settings.calls], ['redteam', 'redteam'])
        self.assertEqual(artifacts.calls[-1][1]['path'], paths[1])
        self.assertEqual(len(artifacts.calls), 4)
        self.assertTrue(result['structured']['experimentReview']['valid'])

    def test_redteam_cannot_read_other_runs_or_execute_tools(self):
        calls = [
            {'name': 'artifact_read', 'arguments': {'path': 'runs/previous/metrics.json'}},
            {'name': 'artifact_read', 'arguments': {'path': 'runs/current/../previous/metrics.json'}},
            {'name': 'python_run', 'arguments': {'code': 'raise AssertionError("must not run")'}},
        ]
        for call in calls:
            with self.subTest(call=call):
                node, context, _, paths = redteam_fixture()
                settings = ScriptedRoleSettings(self.root, [{'toolCalls': [call]}])
                artifacts = RecordedArtifacts()
                with self.assertRaisesRegex(ValueError, '独立红队'):
                    ResearchRunner(settings, self.root, local_tools=artifacts)(node, context, lambda _: None)
                self.assertEqual([entry['role'] for entry in settings.calls], ['redteam'])
                self.assertEqual({args['path'] for _, args in artifacts.calls}, set(paths))
                self.assertTrue(all(name == 'artifact_read' for name, _ in artifacts.calls))

    def test_redteam_outage_preserves_artifacts_and_remains_retryable(self):
        node, context, _, paths = redteam_fixture()
        original = copy.deepcopy(context)
        settings = ScriptedRoleSettings(self.root, [ModelConnectionError('temporary outage')])
        artifacts = RecordedArtifacts()
        with self.assertRaises(ModelConnectionError):
            ResearchRunner(settings, self.root, local_tools=artifacts)(node, context, lambda _: None)
        self.assertEqual([call['role'] for call in settings.calls], ['redteam'])
        self.assertEqual({args['path'] for _, args in artifacts.calls}, set(paths))
        self.assertEqual(context, original)

    def test_missing_or_identical_redteam_never_falls_back_to_main(self):
        for redteam in (None, {'baseUrl': 'http://localhost:11434/v1', 'model': 'main-model'}):
            with self.subTest(redteam=redteam):
                settings = ScriptedRoleSettings(self.root, [])
                settings.update({'providers': {'redteam': redteam}})
                node, context, _, _ = redteam_fixture()
                result = ResearchRunner(settings, self.root, local_tools=RecordedArtifacts())(node, context, lambda _: None)
                self.assertFalse(settings.calls)
                self.assertFalse(result['structured']['experimentReview']['valid'])
                self.assertTrue(result['structured']['experimentReview']['blocked'])
                self.assertFalse(result['structured']['review']['independent'])


if __name__ == '__main__':
    unittest.main()
