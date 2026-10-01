"""Transactional per-task artifact versions and replayable workbench events."""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .workbench_projection import project_workbench


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class WorkbenchStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT NOT NULL, revision INTEGER NOT NULL, value TEXT NOT NULL,
                PRIMARY KEY(id, revision));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                payload TEXT NOT NULL, at TEXT NOT NULL);
        ''')
        self._db.commit()

    def _check(self):
        if self._closed:
            raise ValueError('Workbench store is closed')

    def _snapshot(self):
        row = self._db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
        return json.loads(row[0]) if row else {'revision': 0, 'plan': {}, 'draft': {'revision': 0, 'blocks': []},
                                              'artifacts': [], 'report': {}}

    def _set_snapshot(self, snapshot):
        self._db.execute("INSERT INTO metadata(key,value) VALUES('snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_json(snapshot),))

    def _event(self, kind, payload):
        at = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        cursor = self._db.execute('INSERT INTO events(kind,payload,at) VALUES(?,?,?)', (kind, _json(payload), at))
        return cursor.lastrowid

    def sync(self, record, state=None, files=()):
        with self._lock:
            self._check()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                previous = self._snapshot()
                if previous.get('taskId') and previous['taskId'] != record.get('id'):
                    raise ValueError('Workbench store belongs to a different task')
                snapshot = project_workbench(record, state, files, previous)
                old = {a['id']: a for a in previous['artifacts']}
                changed = []
                for artifact in snapshot['artifacts']:
                    prior = old.get(artifact['id'])
                    artifact.pop('revision', None)
                    comparable = dict(prior or {})
                    comparable.pop('revision', None)
                    if prior is None or _json(artifact) != _json(comparable):
                        artifact['revision'] = prior['revision'] + 1 if prior else 1
                        self._db.execute('INSERT INTO artifacts(id,revision,value) VALUES(?,?,?)',
                                         (artifact['id'], artifact['revision'], _json(artifact)))
                        changed.append({'id': artifact['id'], 'revision': artifact['revision'], 'status': artifact['status']})
                    else:
                        artifact['revision'] = prior['revision']
                if _json(snapshot) != _json(previous):
                    event_id = self._event('workbench.sync', {})
                    snapshot['revision'] = event_id
                    self._db.execute('UPDATE events SET payload=? WHERE id=?',
                                     (_json({'snapshot': snapshot, 'artifacts': changed}), event_id))
                    self._set_snapshot(snapshot)
                self._db.commit()
                return copy.deepcopy(snapshot)
            except BaseException:
                self._db.rollback()
                raise

    def snapshot(self):
        with self._lock:
            self._check()
            return self._snapshot()

    def artifact(self, id, revision=None):
        if not isinstance(id, str) or not id:
            raise ValueError('Artifact ID is required')
        if revision is not None and (type(revision) is not int or revision < 1):
            raise ValueError('Artifact revision must be a positive integer')
        with self._lock:
            self._check()
            if revision is None:
                row = self._db.execute('SELECT value FROM artifacts WHERE id=? ORDER BY revision DESC LIMIT 1', (id,)).fetchone()
            else:
                row = self._db.execute('SELECT value FROM artifacts WHERE id=? AND revision=?', (id, revision)).fetchone()
            if not row:
                raise ValueError('Unknown artifact or revision')
            return json.loads(row[0])

    def artifact_versions(self, id):
        with self._lock:
            self._check()
            rows = self._db.execute('SELECT value FROM artifacts WHERE id=? ORDER BY revision', (id,)).fetchall()
            if not rows:
                raise ValueError('Unknown artifact')
            return [json.loads(row[0]) for row in rows]

    def events(self, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Invalid event cursor or limit')
        with self._lock:
            self._check()
            rows = self._db.execute('SELECT id,kind,payload,at FROM events WHERE id>? ORDER BY id LIMIT ?', (after, limit)).fetchall()
            return [{'id': row[0], 'revision': row[0], 'kind': row[1], 'payload': json.loads(row[2]), 'at': row[3]} for row in rows]

    def append_event(self, kind, payload):
        if not isinstance(kind, str) or not kind or len(kind) > 120 or not isinstance(payload, dict):
            raise ValueError('Invalid workbench event')
        # Serialize before beginning the write transaction to reject non-JSON values.
        _json(payload)
        with self._lock:
            self._check()
            self._db.execute('BEGIN IMMEDIATE')
            try:
                event_id = self._event(kind, payload)
                snapshot = self._snapshot()
                snapshot['revision'] = event_id
                self._set_snapshot(snapshot)
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
            return self.events(after=event_id - 1, limit=1)[0]

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True
