import copy
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from research_swarm.workspace import WorkspaceApplication
from research_swarm.v2_contracts import DomainError,validate_output
from research_swarm.v2_exceptions import accept,resolve
from research_swarm.v2_tools import RunTools
from test_v2_store import brief
import test_v2_store as store_tests


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=WorkspaceApplication(Path(__file__).resolve().parents[1]/'vendor'/'ai-access',Path(self.temp.name),import_existing=False)
        self.addCleanup(self.ws.close)
        self.detail=self.ws.post('/api/tasks',{'workflowVersion':'conversation_only_v2'})
        self.tid=self.detail['task']['id'];self.base='/api/tasks/'+self.tid;self.app=self.ws._v2_apps[self.tid]

    def prepare(self,value=None):
        result=self.ws.post(self.base+'/messages',{'text':'Compare bounded counters, no costs or new mechanisms.','brief':value or brief()})
        self.assertEqual(result['brief']['status'],'requirements_ready')
        self.ws.post(self.base+'/brief/confirm',{'expectedBriefVersion':1})

    def wait(self):
        deadline=time.time()+20
        while time.time()<deadline:
            d=self.ws.detail(self.tid);run=d['research']['run']
            if run and run['status'] not in ('researching','research_starting'):return d
            time.sleep(.02)
        self.fail('pipeline did not finish')

    def fake_model(self,messages,**kwargs):
        if kwargs.get('role')!='main':raise AssertionError('Unexpected role')
        user=json.loads(messages[-1]['content'])
        if 'stage' not in user:return 'Fixed run explanation; no authorization.'
        return json.dumps({'summary':user['stage']+' analysis','claims':[{'statement':'The counter is bounded','evidenceIds':['e1']}] if user['stage']=='Claim Extraction' else [],'unresolved':['Not independently verified'],'optionalNextActions':['Cost study is outside scope']})

    def test_fake_model_end_to_end_export_without_engine(self):
        value=brief();value['executionPolicy']={'allowPaperSearch':True}
        self.prepare(value)
        evidence={'id':'e1','paperId':'p1','type':'full_text','locator':'page 1','quote':'The counter is bounded.'}
        result={'library':{'papers':[{'id':'p1','title':'Counter study','abstract':'','evidenceIds':['e1']}],'evidence':[evidence]},'retrieval':{}}
        with patch('research_swarm.engine.Engine',side_effect=AssertionError('legacy engine constructed')),patch.object(self.ws.settings,'public',return_value={'mode':'llm','capabilities':{'modelReady':True},'search':{}}),patch.object(self.ws.settings,'chat',side_effect=self.fake_model),patch.object(self.ws.settings,'role_status',return_value={'ready':False}),patch.object(RunTools,'_paper',return_value=result):
            self.ws.post(self.base+'/research/start',{'expectedBriefVersion':1,'requestId':'start'})
            detail=self.wait()
        self.assertEqual(detail['research']['run']['status'],'completed',detail['research']['nodes'])
        state=detail['state'];self.assertEqual(len(state['claimGraph']['claims']),1)
        self.assertEqual(state['claimGraph']['claims'][0]['assessment']['status'],'inconclusive')
        serialized=json.dumps(detail['research'])
        self.assertNotIn('researchCycle',serialized);self.assertNotIn('researchDecision',serialized)
        response=self.ws.read_api(self.base+'/export');self.assertEqual(response[0],200)
        with zipfile.ZipFile(io.BytesIO(response[1])) as archive:
            data=json.loads(archive.read('research-data.json'));self.assertEqual(data['brief']['version'],1)
            self.assertEqual(data['run']['runId'],detail['research']['run']['runId'])
            self.assertNotIn('settings',data['run'])
        events=self.ws.event_page(self.tid,0,2)
        self.assertTrue(events['more']);self.assertEqual(events['nextCursor'],events['events'][-1]['id'])
        self.assertTrue(self.ws.event_page(self.tid,events['nextCursor'])['events'])

    def test_ask_does_not_change_run_and_explicit_scope_creates_new_draft(self):
        self.prepare();self.ws.post(self.base+'/start',{'expectedRevision':1,'requestId':'x'});detail=self.wait()
        run=copy.deepcopy(detail['research']['run'])
        self.ws.post(self.base+'/messages',{'text':'What are the results?'})
        self.assertEqual(self.ws.detail(self.tid)['brief']['draft']['version'],1)
        changed=brief();changed['question']='Different bounded counter'
        self.ws.post(self.base+'/messages',{'text':'Change question explicitly','intent':'revise_scope','brief':changed})
        detail=self.ws.detail(self.tid)
        self.assertEqual(detail['brief']['draft']['version'],2)
        self.assertEqual(detail['research']['run']['contract'],run['contract'])
        with self.assertRaises(DomainError):self.ws.post(self.base+'/research/start',{'expectedBriefVersion':1,'requestId':'new'})

    def test_unknown_old_control_and_fake_evidence_rejected(self):
        for output in ({'summary':'x','researchDecision':{}},{'summary':'x','claims':[{'statement':'x','evidenceIds':['invented']}]},{'summary':'x','blocking':True}):
            with self.assertRaises(DomainError):validate_output(output,set())

    def test_budget_blocks_before_external_request(self):
        value=brief();value['executionPolicy']={'allowPaperSearch':True,'budget':{'searchCalls':0}}
        self.prepare(value)
        with patch.object(RunTools,'_paper',side_effect=AssertionError('external call')) as external:
            self.ws.post(self.base+'/research/start',{'expectedBriefVersion':1,'requestId':'x'})
            detail=self.wait()
        self.assertEqual(detail['research']['run']['status'], 'blocked_exception')
        self.assertEqual(detail['exceptions'][0]['type'], 'cost_or_resource_required')
        self.assertEqual(detail['exceptions'][0]['cause']['code'], 'budget_exhausted')
        external.assert_not_called()

    def test_legacy_import_is_read_only_unconfirmed_and_idempotent(self):
        old=self.ws.post('/api/tasks',{});oldid=old['task']['id']
        path=Path(self.temp.name)/'tasks'/oldid/'conversation.json';before=path.read_bytes()
        with patch('research_swarm.engine.Engine',side_effect=AssertionError('legacy engine')):
            history=self.ws.read_api('/api/tasks/'+oldid+'/history')[1]
            imported=self.ws.post('/api/tasks/'+oldid+'/import-v2',{})
            again=self.ws.post('/api/tasks/'+oldid+'/import-v2',{})
        self.assertEqual(path.read_bytes(),before)
        self.assertTrue(history['readOnly']);self.assertIsNone(imported['brief']['confirmedVersion'])
        self.assertEqual(imported['task']['id'],again['task']['id'])
        self.assertEqual(len(again['messages']),1)

    def test_cancel_inflight_result_cannot_publish(self):
        value=brief();value['executionPolicy']={'allowPaperSearch':True};self.prepare(value)
        entered=threading.Event();release=threading.Event()
        def blocked(*args):
            entered.set();release.wait(5);return {'library':{'papers':[],'evidence':[]}}
        with patch.object(RunTools,'_paper',side_effect=blocked):
            self.ws.post(self.base+'/research/start',{'expectedBriefVersion':1,'requestId':'x'})
            self.assertTrue(entered.wait(2));run=self.ws.detail(self.tid)['research']['run']
            self.ws.post(self.base+'/research/actions',{'runId':run['runId'],'revision':run['revision'],'action':'cancel'})
            release.set();self.app.scheduler.thread.join(2)
        detail=self.ws.detail(self.tid);self.assertEqual(detail['research']['run']['status'],'cancelled')
        self.assertFalse(any(n.get('result') for n in detail['research']['nodes']))
        self.assertEqual(self.app.store.tool_history()[0]['status'],'completed')


class ExceptionTests(unittest.TestCase):
    setUp=store_tests.StoreTests.setUp
    start=store_tests.StoreTests.start
    def test_optional_blocking_request_cannot_pause(self):
        run=self.start();self.store.owner('owner');node,_=self.store.claim(run['runId'],'owner')
        exception=accept(self.store,node,{'type':'scope_change','message':'Cost analysis','blocking':True})
        self.assertFalse(exception['blocking']);self.assertEqual(self.store.snapshot()['runs'][0]['status'],'researching')
        current=self.store.snapshot()['runs'][0]
        payload={'runId':run['runId'],'revision':1,'runRevision':current['revision'],'requestId':'resolve','optionId':'dismiss'}
        first=resolve(self.store,exception['id'],payload);second=resolve(self.store,exception['id'],payload)
        self.assertEqual(first,second)
        with self.assertRaises(DomainError):resolve(self.store,exception['id'],dict(payload,optionId='revise_scope'))

    def test_host_exception_and_no_permission_escalation(self):
        run=self.start();self.store.owner('owner');node,_=self.store.claim(run['runId'],'owner')
        exception=accept(self.store,node,{'type':'missing_core_input','message':'required material absent'},DomainError('missing_core_input','absent'))
        self.assertTrue(exception['blocking']);current=self.store.snapshot()['runs'][0]
        self.assertEqual(current['status'],'blocked_exception')
        payload={'runId':run['runId'],'revision':1,'runRevision':current['revision'],'requestId':'resolve','optionId':'revise_scope'}
        with self.assertRaises(DomainError):resolve(self.store,exception['id'],dict(payload,executionPolicy={'allowLocalExperiment':True}))
        resolve(self.store,exception['id'],payload)
        after=self.store.snapshot()['runs'][0]
        self.assertEqual(after['status'],'paused');self.assertEqual(after['contract'],run['contract'])
