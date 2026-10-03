import copy
import json
import tempfile
import unittest
from pathlib import Path

from research_swarm.claim_runtime import validate_relations
from research_swarm.output_protocol import OutputValidationError, validate_node_output
from research_swarm.runner import ResearchRunner, validate_result
from research_swarm.engine import Engine
from test_runner import LIBRARY
from test_engine import sample_library


def relation(**updates):
    return dict(evidenceId='10', type='support', polarity='for', reason='source states result',
                quality='limited', **{}) | updates


NODE = {'id': 'topic', 'version': 2, 'kind': 'research', 'phase': 'execute', 'input': {'researchStep': 'topic'}}


def output():
    return {'summary': '研究接口优势与过度自信风险', 'evidenceIds': ['10', '20'],
            'claims': [{'id': 'observation', 'text': '存在过度自信反例', 'evidenceIds': ['20'], 'limitations': '摘要级证据'}],
            'structured': {'researchTopic': {'title': '校准边界', 'question': '何时过度自信？',
                                           'rationale': '支持与反证并存，须核实范围', 'evidenceIds': ['10', '20']}},
            'unresolved': ['反证与支持的协议尚待核验']}


class Provider:
    def __init__(self, replies):
        self.replies = replies
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        response = self.replies.pop(0)
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


class ProtocolTests(unittest.TestCase):
    def test_stage_forbids_relations_but_preserves_candidate_observations(self):
        value = output()
        validate_node_output(value, {'10', '20'}, NODE)
        value['structured']['evidenceRelations'] = [relation()]
        with self.assertRaises(OutputValidationError) as caught:
            validate_node_output(value, {'10', '20'}, NODE)
        self.assertEqual(caught.exception.issues[0]['code'], 'relations_without_claim')
        self.assertIn('保留', str(caught.exception))

    def test_multiple_independent_errors_are_reported_together(self):
        with self.assertRaises(OutputValidationError) as caught:
            validate_relations([relation(), relation(polarity='against'),
                                relation(type='qualify', polarity='mixed'), relation(evidenceId='missing', quality='bad', reason='')], {'10'})
        codes = {i['code'] for i in caught.exception.issues}
        self.assertEqual(codes, {'duplicate_support', 'qualify_polarity', 'unknown_evidence', 'relation_quality', 'relation_reason'})
        conflict = next(i for i in caught.exception.issues if i['code'] == 'duplicate_support')
        self.assertEqual(conflict['previousIndex'], 0)
        self.assertEqual(conflict['index'], 1)
        self.assertEqual(conflict['polarities'], ['for', 'against'])
        self.assertEqual(conflict['evidenceId'], '10')

    def test_scope_and_qualifiers(self):
        validate_relations([relation(claimId='a', claimVersion=1), relation(claimId='b', claimVersion=1, polarity='against'),
                            relation(claimId='a', claimVersion=2, polarity='against')], {'10'})
        validate_relations([relation(polarity='mixed'), relation(type='qualify', polarity='unresolved')], {'10'}, {'id': 'a', 'version': 1})
        with self.assertRaises(OutputValidationError):
            validate_relations([relation(), relation()], {'10'})

    def test_bound_identity_and_version_cannot_be_overridden(self):
        for bad in (relation(claimId='b'), relation(claimVersion=2), relation(claimVersion=True), relation(evidenceId='missing')):
            with self.subTest(bad=bad), self.assertRaises(OutputValidationError):
                validate_relations([bad], {'10'}, {'id': 'a', 'version': 1})

    def test_candidate_omitted_version_inherits_host_and_boolean_is_rejected(self):
        node = {'phase': 'execute', 'input': {'claimId': 'a', 'claimVersion': 2}}
        value = {'summary': 'observation', 'claims': [{'text': 'source observation', 'claimId': 'a', 'evidenceIds': ['10']}]}
        normalized = validate_result(value, LIBRARY, node)
        self.assertEqual(normalized['claims'][0]['claimVersion'], 2)
        validate_node_output(normalized, {'10'}, node)
        node['input']['claimVersion'] = 1
        value['claims'][0]['claimVersion'] = True
        with self.assertRaises(OutputValidationError):
            validate_result(value, LIBRARY, node)

    def test_no_unhashable_fields_crash_error_collection(self):
        with self.assertRaises(OutputValidationError):
            validate_relations([relation(evidenceId=[], claimId={}, claimVersion=[])], {'10'})

    def test_engine_admission_uses_stage_contract(self):
        # Use the real admission validator without starting a scheduler.
        engine = Engine.__new__(Engine)
        engine._state = {'evidence': copy.deepcopy(LIBRARY['evidence'])}
        value = output()
        value['structured']['evidenceRelations'] = [relation()]
        with self.assertRaises(OutputValidationError):
            engine._validated_output(NODE, value)

    def test_diagnostic_history_reference_survives_restart_without_becoming_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'state.sqlite'
            engine = Engine(sample_library(), db)
            token = {'id': 'run:diagnostic-test', 'nodeId': 'central', 'version': 1}
            engine._record_diagnostic(token, {'diagnosticRef': 'diagnostics/example.json', 'attemptCount': 2,
                                             'validationErrors': [{'code': 'example'}], 'status': 'failed'})
            with engine._condition:
                engine._history('execution-failed', nodeId='central', version=1, executionToken=token['id'], error='example')
                engine._commit()
            engine.close()
            restored = Engine(sample_library(), db)
            try:
                history = restored.node_history('central')
                self.assertEqual(history[0]['diagnosticRef'], 'diagnostics/example.json')
                self.assertEqual(history[0]['attemptCount'], 2)
                self.assertEqual(len(restored.snapshot()['evidence']), 2)
            finally:
                restored.close()


class RepairTests(unittest.TestCase):
    def run_replies(self, replies, callback=None, provider=None, node=None, context=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = provider or Provider(replies)
        runner = ResearchRunner(settings, Path(temp.name))
        ctx = {'library': copy.deepcopy(LIBRARY), 'mode': 'llm', 'researchCycle': {'status': 'running'},
               'executionToken': 'run:fixture', 'record_diagnostic': callback or (lambda _: None)} | (context or {})
        return runner, settings, node or copy.deepcopy(NODE), ctx, Path(temp.name)

    def test_stage_repair_keeps_counterevidence_and_records_attempts(self):
        bad = output(); bad['structured']['evidenceRelations'] = [relation(polarity='against')]
        entries = []
        runner, provider, node, ctx, root = self.run_replies([bad, output()], entries.append)
        result = runner(node, ctx, lambda _: None)
        self.assertEqual(result['claims'][0]['text'], '存在过度自信反例')
        self.assertEqual(result['claims'][0]['evidenceIds'], ['20'])
        self.assertIn('反证', provider.messages[1][-1]['content'])
        record = json.loads((root / entries[0]['diagnosticRef']).read_text('utf-8'))
        self.assertEqual([a['status'] for a in record['attempts']], ['invalid', 'validated'])
        self.assertFalse(record['eligibleAsEvidence'])
        self.assertNotIn('diagnosticRef', result)
        system = provider.messages[0][0]['content']
        for irrelevant in ('rawData.metrics', 'metrics.json', 'python_install', 'replicates', 'evidenceRelations 返回'):
            self.assertNotIn(irrelevant, system)
        self.assertEqual(json.loads(provider.messages[0][1]['content'])['library']['evidence'], LIBRARY['evidence'])

    def test_two_repairs_allowed_when_errors_improve(self):
        bad = output(); bad['structured']['evidenceRelations'] = [relation()]
        bad['claims'][0]['evidenceIds'] = ['missing']
        second = output(); second['claims'][0]['evidenceIds'] = ['missing']
        runner, provider, node, ctx, _ = self.run_replies([bad, second, output()])
        runner(node, ctx, lambda _: None)
        self.assertEqual(len(provider.messages), 3)
        repair = provider.messages[1][-1]['content']
        self.assertIn('relations_without_claim', repair)
        self.assertIn('unknown_evidence', repair)

    def test_repeated_errors_stop_early_and_all_attempts_survive(self):
        bad = output(); bad['structured']['evidenceRelations'] = [relation()]
        entries = []
        runner, provider, node, ctx, root = self.run_replies([bad, bad, output()], entries.append)
        with self.assertRaises(OutputValidationError):
            runner(node, ctx, lambda _: None)
        self.assertEqual(len(provider.messages), 2)
        record = json.loads((root / entries[0]['diagnosticRef']).read_text('utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(len(record['attempts']), 2)
        self.assertTrue(all(a['response'] and a['validationErrors'] for a in record['attempts']))

    def test_maximum_two_repairs_even_when_errors_change(self):
        values = []
        for eid in ('unknown1', 'unknown2', 'unknown3', '10'):
            v = output(); v['claims'][0]['evidenceIds'] = [eid]; values.append(v)
        runner, provider, node, ctx, _ = self.run_replies(values)
        with self.assertRaises(OutputValidationError):
            runner(node, ctx, lambda _: None)
        self.assertEqual(len(provider.messages), 3)

    def test_json_parse_errors_use_same_repair_and_diagnostics(self):
        runner, provider, node, ctx, _ = self.run_replies(['broken json', output()])
        runner(node, ctx, lambda _: None)
        self.assertEqual(len(provider.messages), 2)

    def test_summary_only_reply_gets_exact_stage_template_in_repair(self):
        runner, provider, node, ctx, _ = self.run_replies([{'summary': 'incomplete summary'}, output()])
        runner(node, ctx, lambda _: None)
        repair = provider.messages[1][-1]['content']
        self.assertIn('stage_field_missing', repair)
        self.assertIn('structured.researchTopic', repair)
        self.assertIn('"structured":{"researchTopic"', repair)
        self.assertIn('300', repair)

    def test_repair_cannot_succeed_by_deleting_counterevidence_reference(self):
        bad = output(); bad['structured']['evidenceRelations'] = [relation(evidenceId='20', polarity='against')]
        erased = output(); erased['evidenceIds'] = ['10']; erased['claims'] = []
        erased['structured']['researchTopic']['evidenceIds'] = ['10']
        runner, provider, node, ctx, _ = self.run_replies([bad, erased, output()])
        result = runner(node, ctx, lambda _: None)
        self.assertEqual(len(provider.messages), 3)
        self.assertIn('counterevidence_dropped', provider.messages[2][-1]['content'])
        self.assertIn('20', result['evidenceIds'])

    def test_diagnostics_redact_secrets_without_truncating_response(self):
        provider = Provider([output()])
        secret = 'custom-private-credential'
        provider.data = {'providers': {'main': {'apiKey': secret}}}
        provider.role_status = lambda _: {'identity': {'model': 'frozen-model'}}
        node = copy.deepcopy(NODE)
        node['input']['description'] = secret + ' Bearer other-private-token apiKey="some-other-key"'
        provider.replies[0]['summary'] = secret + ' sk-secret-password ' + '长' * 1000
        entries = []
        runner, _, node, ctx, root = self.run_replies([], entries.append, provider, node)
        runner(node, ctx, lambda _: None)
        saved = (root / entries[0]['diagnosticRef']).read_text('utf-8')
        for value in (secret, 'other-private-token', 'some-other-key', 'sk-secret-password'):
            self.assertNotIn(value, saved)
        self.assertIn('长' * 1000, saved)
        self.assertIn('frozen-model', saved)


if __name__ == '__main__':
    unittest.main()
