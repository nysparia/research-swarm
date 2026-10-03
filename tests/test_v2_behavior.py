"""Contract-level behavior through the public task API and real v2 scheduler."""
import copy
import io
import json
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from research_swarm.v2_contracts import DomainError
from research_swarm.v2_tools import RunTools
from research_swarm.workspace import WorkspaceApplication


def request_brief():
    return {
        'question': 'Jev 的架构与原理是什么？', 'objects': ['Jev'],
        'dimensions': ['架构', '原理'], 'deliverables': ['研究报告', '证据与限制'],
        'scope': {'included': ['Jev 架构与原理'], 'excluded': ['成本', '计费', '新机制'],
                  'objectives': [{'id': 'objective:1', 'description': '解释 Jev 架构与原理'}]},
        'executionPolicy': {'allowPaperSearch': True}, 'openQuestions': [],
    }


EVIDENCE = {'id': 'e1', 'paperId': 'p1', 'type': 'full_text', 'locator': 'page 1',
            'quote': 'Jev separates preparation from execution.'}
SOURCE = {'library': {'papers': [{'id': 'p1', 'title': 'Synthetic Jev architecture',
                                  'abstract': '', 'evidenceIds': ['e1']}], 'evidence': [EVIDENCE]},
          'retrieval': {}}
PUBLIC = {'mode': 'llm', 'capabilities': {'modelReady': True}, 'search': {}}
REVIEW = {'ready': True, 'independentFromMain': True, 'reviewPolicy': 'independent',
          'identity': {'baseUrl': 'https://judge.invalid', 'model': 'test-judge'}}


class V2BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ws = WorkspaceApplication(Path(__file__).resolve().parents[1] / 'vendor' / 'ai-access',
                                       Path(self.temp.name), import_existing=False)
        self.addCleanup(self.ws.close)
        self.task = self.ws.post('/api/tasks', {'workflowVersion': 'conversation_only_v2'})['task']['id']
        self.base = '/api/tasks/' + self.task
        self.app = self.ws._v2_apps[self.task]

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            detail = self.ws.detail(self.task)
            if predicate(detail):
                return detail
            time.sleep(.01)
        self.fail('v2 workflow did not reach the expected state')

    def finish(self):
        return self.wait(lambda detail: detail['research']['run'] and
                         detail['research']['run']['status'] not in ('research_starting', 'researching'))

    def prepare(self, value=None):
        self.ws.post(self.base + '/messages', {'text': '研究 Jev 架构与原理，不研究成本。',
                                              'brief': value or request_brief()})
        self.ws.post(self.base + '/brief/confirm', {'expectedBriefVersion': 1})

    def model(self, messages, **kwargs):
        payload = json.loads(messages[-1]['content'])
        if kwargs.get('role') == 'judge':
            return json.dumps({
                'hypothesisVerdict': {'status': 'supported', 'reason': '合成来源直接说明架构分离。',
                                      'limitations': '仅限单一合成测试资料，不代表真实 Jev 产品。', 'evidenceIds': ['e1']},
                'evidenceRelations': [{'evidenceId': 'e1', 'type': 'support', 'polarity': 'for',
                                      'reason': '来源直接陈述', 'applicability': '合成测试', 'quality': 'usable',
                                      'quote': EVIDENCE['quote'], 'locator': EVIDENCE['locator'],
                                      'rule': 'direct_statement', 'confidence': .95}],
            })
        if 'stage' not in payload:
            if 'previousBrief' in payload:
                return json.dumps({'brief': request_brief(), 'summary': '仅研究 Jev 架构与原理，不执行实验。'})
            return '这只是对既有结果的解释，不增加成本研究。'
        stage = payload['stage']
        result = {'summary': 'Jev 将准备与执行分离；该描述只来自本例合成资料。',
                  'claims': [{'statement': EVIDENCE['quote'], 'evidenceIds': ['e1']}]
                  if stage == 'Claim Extraction' else [], 'unresolved': [], 'optionalNextActions': []}
        if stage == 'Retrieval':
            result['exceptionRequest'] = {'type': 'scope_change', 'summary': '成本分析是可选扩展',
                                          'impact': '不影响架构说明', 'blocking': True}
        return json.dumps(result, ensure_ascii=False)

    def test_conversation_confirmation_research_and_report_are_separate(self):
        with (
            patch('research_swarm.engine.Engine', side_effect=AssertionError('legacy engine')),
            patch.object(self.ws.settings, 'public', return_value=PUBLIC),
            patch.object(self.ws.settings, 'role_status', return_value=REVIEW),
            patch.object(self.ws.settings, 'chat', side_effect=self.model),
            patch.object(RunTools, '_paper', return_value=copy.deepcopy(SOURCE)) as papers,
        ):
            self.ws.post(self.base + '/messages', {'text': '研究 Jev 的架构和原理，不做实验或计费研究。'})
            draft = self.wait(lambda detail: not detail['document']['polishing'])
            self.assertEqual(draft['brief']['status'], 'requirements_ready')
            self.assertTrue(draft['brief']['draft']['content']['plan']['stages'])
            self.assertIsNone(draft['research']['run'])
            papers.assert_not_called()
            with self.assertRaises(DomainError):
                self.ws.post(self.base + '/start', {'expectedRevision': 1, 'requestId': 'start'})
            self.ws.post(self.base + '/brief/confirm', {'expectedBriefVersion': 1})
            papers.assert_not_called()
            self.ws.post(self.base + '/start', {'expectedRevision': 1, 'requestId': 'start'})
            detail = self.finish()
        self.assertEqual(detail['research']['run']['status'], 'completed')
        self.assertTrue(papers.called)
        self.assertFalse(any(node['stage'] in ('Experiment', 'Replication') for node in detail['research']['nodes']))
        self.assertEqual({node['objectiveId'] for node in detail['research']['nodes']}, {'objective:1'})
        self.assertTrue(detail['exceptions'])
        self.assertTrue(all(not item['blocking'] for item in detail['exceptions']))
        report = detail['research']['report']
        self.assertIn('Jev 将准备与执行分离', report['summary'])
        self.assertEqual(report['claims'][0]['status'], 'supported')
        self.assertEqual(report['objectiveResults'][0]['question'], '解释 Jev 架构与原理')
        self.assertEqual(report['acceptance']['status'], 'met')
        self.assertFalse(report['acceptance']['scientificValidation'])

    def test_permission_alone_does_not_force_an_experiment(self):
        value = request_brief()
        value['executionPolicy']['allowLocalExperiment'] = True
        self.prepare(value)
        with patch.object(self.ws.settings, 'public', return_value=PUBLIC), \
             patch.object(self.ws.settings, 'role_status', return_value=REVIEW), \
             patch.object(self.ws.settings, 'chat', side_effect=self.model), \
             patch.object(RunTools, '_paper', return_value=copy.deepcopy(SOURCE)), \
             patch('research_swarm.local_tools.LocalResearchTools.call', side_effect=AssertionError('unnecessary experiment')):
            self.ws.post(self.base + '/research/start', {'expectedBriefVersion': 1, 'requestId': 'start'})
            detail = self.finish()
        self.assertEqual(detail['research']['run']['status'], 'completed')
        experiments = [node for node in detail['research']['nodes'] if node['stage'] == 'Experiment']
        self.assertEqual(experiments[0]['result']['analysisStatus'], 'skipped')
        stages = [node['stage'] for node in detail['research']['nodes']]
        self.assertGreater(max(index for index, stage in enumerate(stages) if stage == 'Epistemic Analysis'), stages.index('Experiment'))
        self.assertFalse(any(call.get('tool') in ('python_run', 'python_install') for call in self.app.store.tool_history()))

    def test_no_model_produces_an_unmet_record_not_a_success_claim(self):
        value = request_brief()
        value['executionPolicy'] = {}
        self.prepare(value)
        with patch.object(self.ws.settings, 'chat', side_effect=AssertionError('offline model call')):
            self.ws.post(self.base + '/research/start', {'expectedBriefVersion': 1, 'requestId': 'offline'})
            detail = self.finish()
        self.assertTrue(detail['research']['report']['ready'])
        self.assertEqual(detail['research']['report']['acceptance']['status'], 'unmet')
        self.assertIn('尚未完成该目标的分析', detail['research']['report']['summary'])
        self.assertEqual(detail['research']['report']['claims'], [])

    def test_real_provider_authentication_error_is_a_host_exception(self):
        value = request_brief()
        value['executionPolicy'] = {}
        self.prepare(value)
        original = copy.deepcopy(self.app.store.snapshot()['briefs'][0]['content'])
        self.ws.post('/api/settings', {'mode': 'llm', 'provider': {
            'baseUrl': 'https://models.invalid/v1', 'model': 'fixture-model', 'apiKey': 'fixture-key',
        }})
        def unauthorized(*args, **kwargs):
            raise urllib.error.HTTPError('https://models.invalid/v1/chat/completions', 401, 'Unauthorized',
                                         {}, io.BytesIO(b'{"error":{"message":"rejected fixture-key"}}'))
        with patch('urllib.request.urlopen', side_effect=unauthorized) as request:
            self.ws.post(self.base + '/research/start', {'expectedBriefVersion': 1, 'requestId': 'auth'})
            detail = self.finish()
        request.assert_called_once()
        self.assertNotIn('fixture-key', json.dumps(detail['exceptions']))
        self.assertEqual(detail['research']['run']['status'], 'blocked_exception')
        exception = detail['exceptions'][0]
        self.assertEqual(exception['cause']['resource'], 'model_connection')
        self.assertIn('retry', [option['id'] for option in exception['options']])
        self.assertEqual(detail['research']['run']['contract'], original)
        run = detail['research']['run']
        self.ws.post(self.base + '/exceptions/' + exception['id'] + '/resolve', {
            'runId': run['runId'], 'revision': exception['revision'], 'runRevision': run['revision'],
            'requestId': 'resolved-auth', 'optionId': 'retry',
        })
        after = self.ws.detail(self.task)
        self.assertEqual(after['research']['run']['status'], 'paused')
        self.assertEqual(after['research']['run']['contract'], original)
        self.wait(lambda _: not self.app.busy())
        # A paused/blocked task must not prevent the user from fixing its connection.
        self.ws.post('/api/settings', {'mode': 'evidence'})

    def test_provider_transport_retry_is_charged_before_second_request(self):
        value = request_brief()
        value['executionPolicy'] = {'budget': {'modelCalls': 1}}
        self.prepare(value)
        self.ws.post('/api/settings', {'mode': 'llm', 'provider': {
            'baseUrl': 'https://models.invalid/v1', 'model': 'fixture-model', 'apiKey': 'fixture-key',
        }})
        def unavailable(*args, **kwargs):
            raise urllib.error.HTTPError('https://models.invalid/v1/chat/completions', 503, 'Unavailable',
                                         {}, io.BytesIO(b'{"error":{"message":"temporarily unavailable"}}'))
        with patch('urllib.request.urlopen', side_effect=unavailable) as request:
            self.ws.post(self.base + '/research/start', {'expectedBriefVersion': 1, 'requestId': 'bounded-retry'})
            detail = self.finish()
        request.assert_called_once()
        self.assertEqual(detail['research']['run']['status'], 'blocked_exception')
        self.assertEqual(detail['research']['run']['usage']['modelCalls'], 1)
        self.assertEqual(detail['exceptions'][0]['cause']['code'], 'budget_exhausted')

    def test_unsupported_explicit_capability_returns_a_domain_error(self):
        value = request_brief()
        value['executionPolicy']['allowCodeInspection'] = True
        with self.assertRaises(DomainError) as caught:
            self.ws.post(self.base + '/messages', {'text': 'Read local code', 'brief': value})
        self.assertEqual(caught.exception.code, 'capability_unavailable')
        self.assertEqual(self.app.store.snapshot()['briefs'], [])

    def test_old_action_alias_cannot_expand_policy(self):
        self.prepare()
        with patch.object(self.app.scheduler, 'launch'):
            detail = self.ws.post(self.base + '/start', {'expectedRevision': 1, 'requestId': 'alias'})
        original = detail['research']['run']['contract']
        paused = self.ws.post(self.base + '/actions/pause', {})
        self.assertEqual(paused['research']['run']['status'], 'paused')
        with self.assertRaises(DomainError):
            self.ws.post(self.base + '/actions/resume', {'allowLocalExperiment': True})
        self.assertEqual(self.ws.detail(self.task)['research']['run']['contract'], original)


if __name__ == '__main__':
    unittest.main()
