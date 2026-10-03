"""Adversarial tests for the v2 authority and execution-fencing boundary."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from research_swarm.v2_contracts import DomainError
from research_swarm.v2_store import ResearchStore


def contract():
    return {'question': 'Explain counter architecture', 'objects': ['counter'],
            'dimensions': ['architecture'], 'deliverables': ['report'],
            'scope': {'included': ['counter architecture'], 'excluded': ['cost'],
                      'objectives': [{'id': 'objective:1', 'description': 'Explain counter architecture'}]},
            'executionPolicy': {}, 'openQuestions': []}


def one_node(brief):
    return [{'id': 'n1', 'stage': 'Claim Extraction', 'objectiveId': 'objective:1', 'dependencies': []}]


class V2FoundationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'research.sqlite'
        self.task_id = 'a' * 32

    def prepared(self, value=None):
        store = ResearchStore(self.path, self.task_id)
        mid, generation, version = store.message('Explain counter architecture', 'draft', True)
        value = contract() if value is None else value
        value['sourceMessageIds'] = [mid]
        store.draft(value, generation, version)
        store.confirm(1)
        return store

    def start(self, store):
        return store.start({'expectedBriefVersion': 1, 'requestId': 'test-start'}, {}, one_node)[0]

    def test_future_schema_is_rejected_without_schema_writes(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE future_data(value TEXT)')
            db.execute('INSERT INTO future_data VALUES (?)', ('preserve',))
            db.execute('PRAGMA user_version=999')
        with self.assertRaises(DomainError):
            ResearchStore(self.path, self.task_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 999)
            self.assertEqual(db.execute('SELECT value FROM future_data').fetchall(), [('preserve',)])
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(names, {'future_data'})

    def test_existing_store_rejects_another_task_without_inserting_metadata(self):
        self.prepared()
        with closing(sqlite3.connect(self.path)) as db, db:
            before = db.execute('SELECT * FROM task').fetchall()
        with self.assertRaises(DomainError):
            ResearchStore(self.path, 'b' * 32)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT * FROM task').fetchall(), before)

    def expired_worker(self):
        store = self.prepared()
        run = self.start(store)
        self.assertTrue(store.owner('old-owner'))
        node, _ = store.claim(run['runId'], 'old-owner')
        with store.transaction() as db:
            db.execute('UPDATE ownership SET expires=0')
        return store, node

    def test_expired_owner_cannot_charge_for_an_external_call(self):
        store, node = self.expired_worker()
        with self.assertRaises(DomainError):
            store.charge(node, 'toolCalls', 'forbidden-call', {'sideEffect': True})
        self.assertEqual(store.tool_history(), [])

    def test_replacement_owner_fences_old_completion_before_recovery(self):
        store, node = self.expired_worker()
        self.assertTrue(store.owner('new-owner'))
        with self.assertRaises(DomainError):
            store.finish(node, {'summary': 'stale worker'})
        snapshot = store.snapshot()
        self.assertFalse(any(n.get('result', {}).get('summary') == 'stale worker'
                             for n in snapshot['nodes'] if isinstance(n.get('result'), dict)))

    def test_bool_revision_is_not_an_integer_revision(self):
        store = self.prepared()
        run = self.start(store)
        self.assertEqual(run['revision'], 1)
        with self.assertRaises(DomainError):
            store.action({'runId': run['runId'], 'revision': True, 'action': 'pause'})
        self.assertEqual(store.snapshot()['runs'][0]['status'], 'research_starting')

    def test_plan_cannot_exceed_frozen_node_budget(self):
        value = contract()
        value['executionPolicy'] = {'budget': {'nodes': 1}}
        store = self.prepared(value)
        def oversized(brief):
            return one_node(brief) + [{'id': 'n2', 'stage': 'Claim Extraction',
                                      'objectiveId': 'objective:1', 'dependencies': ['n1']}]
        with self.assertRaises(DomainError):
            store.start({'expectedBriefVersion': 1, 'requestId': 'too-many'}, {}, oversized)
        self.assertEqual(store.snapshot()['runs'], [])

    def test_unknown_stage_is_not_admitted(self):
        store = self.prepared()
        def unknown(brief):
            node = one_node(brief)[0]
            node['stage'] = 'Unregistered stage'
            return [node]
        with self.assertRaises(DomainError):
            store.start({'expectedBriefVersion': 1, 'requestId': 'bad-stage'}, {}, unknown)
        self.assertEqual(store.snapshot()['runs'], [])

    def test_cyclic_dependencies_do_not_create_a_stuck_run(self):
        store = self.prepared()
        def cyclic(brief):
            return [{'id': 'a', 'stage': 'Claim Extraction', 'objectiveId': 'objective:1', 'dependencies': ['b']},
                    {'id': 'b', 'stage': 'Claim Extraction', 'objectiveId': 'objective:1', 'dependencies': ['a']}]
        with self.assertRaises(DomainError):
            store.start({'expectedBriefVersion': 1, 'requestId': 'cycle'}, {}, cyclic)
        self.assertEqual(store.snapshot()['runs'], [])


if __name__ == '__main__':
    unittest.main()
