import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from research_swarm.v2_store import ResearchStore
from research_swarm.v2_tools import RunTools
from research_swarm.v2_contracts import DomainError
from research_swarm.scientific_validation import validate_measurements
from test_v2_store import brief

PROTOCOL={'id':'tiny-protocol','hypothesis':'bounded deterministic values','method':'stdlib CSV arithmetic','dataset':'three fixed observations','baselines':['predeclared mean'],'metrics':['mean'],'replicates':3,'validityChecks':['count'],'acceptance':'mean equals 2','outputSchema':{'mean':'number'},'rawData':{'file':'raw.csv','metrics':{'mean':{'column':'value','statistic':'mean'}}}}
CODE='''import csv,json
with open('raw.csv','w',newline='') as f:
 w=csv.writer(f);w.writerow(['value']);w.writerows([[1],[2],[3]])
with open('metrics.json','w') as f:
 json.dump({'protocolId':'tiny-protocol','replicates':3,'validation':{'passed':True,'checks':[{'name':'count','passed':True}]},'mean':2},f)
print('done')
'''


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=ResearchStore(self.root/'research.sqlite','c'*32)
        mid,g,v=self.store.message('Explicitly execute tiny local standard library experiment','draft',True)
        value=brief();value['executionPolicy']={'allowLocalExperiment':True};value['sourceMessageIds']=[mid]
        self.store.draft(value,g,v);self.store.confirm(1)
        self.run=self.store.start({'expectedBriefVersion':1,'requestId':'experiment'},{},lambda c:[{'id':'experiment','stage':'Experiment','objectiveId':'objective:1','dependencies':[]}])[0]
        self.store.owner('test');self.node,_=self.store.claim(self.run['runId'],'test')
        self.tools=RunTools(self.store,self.root/'unused-source',self.root/'artifacts',None)
        self.addCleanup(self.tools.close)

    def test_real_authorized_standard_library_receipt_and_hash_gate(self):
        with patch('research_swarm.experiment_jobs.ExperimentJobs._detect_gpus',return_value=0):
            result=self.tools.call(self.run,self.node,'python_run',{'code':CODE,'timeoutSeconds':10,'protocol':PROTOCOL})
        self.assertEqual(result['status'],'completed',result)
        self.assertIsNotNone(result['measurements'],result.get('measurementErrors'))
        self.assertEqual(result['measurements']['mean'],2)
        self.assertEqual(result['scientificStatus'],'unreviewed')
        history=self.store.tool_history();self.assertEqual(len(history),1);self.assertEqual(history[0]['status'],'completed')
        with patch('research_swarm.experiment_jobs.ExperimentJobs',side_effect=AssertionError('duplicate experiment')):
            reused=self.tools.call(self.run,self.node,'python_run',{'code':CODE,'timeoutSeconds':10,'protocol':PROTOCOL})
        self.assertEqual(reused['scriptSha256'],result['scriptSha256']);self.assertEqual(len(self.store.tool_history()),1)
        artifact_root=self.tools.root/result['artifactRoot'];metrics=next(a for a in result['artifacts'] if Path(a['path']).name=='metrics.json')
        (artifact_root/metrics['path']).write_text('{}',encoding='utf-8')
        errors=[];data,ids=validate_measurements(artifact_root,[result],PROTOCOL,errors)
        self.assertIsNone(data);self.assertTrue(errors)

    def test_tools_cannot_install_or_cross_objective(self):
        with self.assertRaises(DomainError):self.tools.call(self.run,self.node,'python_install',{'packages':['numpy']})
        with self.assertRaises(DomainError):self.tools.call(self.run,dict(self.node,objectiveId='objective:foreign'),'python_run',{'code':CODE,'protocol':PROTOCOL})
        self.assertEqual(self.store.tool_history(),[])
