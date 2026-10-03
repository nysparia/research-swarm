import copy
import tempfile
import unittest
from pathlib import Path
from research_swarm.v2_contracts import DomainError,gate,validate_brief
from research_swarm.v2_store import ResearchStore


def brief():
    return {'question':'Compare bounded counters','objects':['counter'],'dimensions':['correctness'],'deliverables':['report'],'scope':{'included':['counter'],'excluded':['cost'],'objectives':[{'id':'objective:1','description':'Compare bounded counters'}]},'executionPolicy':{},'openQuestions':[]}


def plan(value):
    return [{'id':'n1','stage':'Claim Extraction','objectiveId':'objective:1','dependencies':[]}]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=ResearchStore(Path(self.temp.name)/'research.sqlite','a'*32)
        mid,g,v=self.store.message('explicit scope','draft',True)
        value=brief();value['sourceMessageIds']=[mid]
        self.store.draft(value,g,v)

    def start(self):
        self.store.confirm(1)
        return self.store.start({'expectedBriefVersion':1,'requestId':'x'},{},plan)[0]

    def test_confirmation_and_start_gate(self):
        with self.assertRaises(DomainError):self.store.start({'expectedBriefVersion':1,'requestId':'x'},{},plan)
        run=self.start()
        duplicate,fresh=self.store.start({'expectedBriefVersion':1,'requestId':'x'},{},plan)
        self.assertFalse(fresh);self.assertEqual(run['runId'],duplicate['runId'])
        with self.assertRaises(DomainError):self.store.start({'expectedBriefVersion':2,'requestId':'x'},{},plan)

    def test_pending_message_invalidates_confirmation(self):
        self.store.confirm(1);self.store.message('changed','revise_scope',True)
        with self.assertRaises(DomainError):self.store.start({'expectedBriefVersion':1,'requestId':'x'},{},plan)

    def test_sources_and_stale_cas(self):
        value=brief();value['sourceMessageIds']=['fake']
        with self.assertRaises(DomainError):self.store.draft(value,1,1)
        with self.assertRaises(DomainError):self.store.confirm(0)

    def test_no_experiment_or_objective_escape(self):
        contract=self.store.snapshot()['briefs'][0]['content']
        for stage,obj,tool in [('Experiment','objective:1',None),('Replication','objective:1',None),('Claim Extraction','objective:9',None),('Claim Extraction','objective:1','python_run')]:
            with self.assertRaises(DomainError):gate(contract,stage,obj,tool)

    def test_cancel_stale_completion(self):
        run=self.start();self.store.owner('one');node,run=self.store.claim(run['runId'],'one')
        current=self.store.snapshot()['runs'][0]
        self.store.action({'action':'cancel','runId':run['runId'],'revision':current['revision']})
        with self.assertRaises(DomainError):self.store.finish(node,{'summary':'stale'})

    def test_transaction_event_rollback(self):
        before=self.store.snapshot()['cursor']
        with self.assertRaises(RuntimeError):
            with self.store.transaction() as db:
                self.store.event(db,'fake',{});raise RuntimeError('rollback')
        self.assertEqual(self.store.snapshot()['cursor'],before)

    def test_restart_interrupt_and_owner(self):
        run=self.start();self.store.owner('one');self.assertFalse(self.store.owner('two'))
        self.store.claim(run['runId'],'one');self.store.owner('one',True)
        self.store.recover('two')
        self.assertEqual(self.store.snapshot()['runs'][0]['status'],'interrupted')

    def test_unknown_side_effect_requires_explicit_retry(self):
        run=self.start();self.store.owner('one');node,_=self.store.claim(run['runId'],'one')
        self.store.charge(node,'toolCalls','call',{'sideEffect':True})
        current=self.store.snapshot()['runs'][0]
        self.store.action({'action':'pause','runId':run['runId'],'revision':current['revision']})
        current=self.store.snapshot()['runs'][0]
        with self.assertRaises(DomainError):self.store.action({'action':'resume','runId':run['runId'],'revision':current['revision']})
        resumed=self.store.action({'action':'retry','acknowledgeUnknownSideEffects':True,'runId':run['runId'],'revision':current['revision']})
        self.assertEqual(resumed['status'],'researching')
