"""Task-local transactional authority. No model or network call holds a transaction."""
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .v2_contracts import ACTIVE, VERSION, DomainError, bounded, validate_brief, ready, gate


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


MAX_STORED_BYTES = 8 * 1024 * 1024


def encode(value):
    return json.dumps(bounded(value, MAX_STORED_BYTES), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class ResearchStore:
    def __init__(self, path, task_id=None, title='新科研任务'):
        self.path = Path(path)
        self._validate_existing(task_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS task(id TEXT PRIMARY KEY, workflow TEXT NOT NULL, title TEXT NOT NULL, draft INTEGER NOT NULL DEFAULT 0, confirmed INTEGER, generation INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS singleton_task ON task((1));
            CREATE TABLE IF NOT EXISTS briefs(version INTEGER PRIMARY KEY, content TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS confirmations(version INTEGER PRIMARY KEY REFERENCES briefs(version), at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS historical_import(source_task TEXT PRIMARY KEY, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, role TEXT NOT NULL, content TEXT NOT NULL, intent TEXT, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, brief INTEGER NOT NULL REFERENCES briefs(version), status TEXT NOT NULL, revision INTEGER NOT NULL, epoch INTEGER NOT NULL, content TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON runs((1)) WHERE status NOT IN ('completed','cancelled');
            CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), status TEXT NOT NULL, token TEXT, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS exceptions(id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), revision INTEGER NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, payload TEXT NOT NULL, at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox(event INTEGER PRIMARY KEY REFERENCES events(id), projected INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS requests(operation TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(operation,key));
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), node TEXT NOT NULL REFERENCES nodes(id), status TEXT NOT NULL, content TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ownership(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires REAL NOT NULL, generation INTEGER NOT NULL);
            PRAGMA user_version=2;
            ''')
            if task_id:
                db.execute('INSERT OR IGNORE INTO task(id,workflow,title,created) VALUES(?,?,?,?)',(task_id,VERSION,title[:80],now()))
            row = db.execute('SELECT id, workflow FROM task').fetchone()
            if not row or row['workflow'] != VERSION:
                raise DomainError('unsupported_workflow', '未知流程版本', 409)
            if task_id is not None and row['id'] != task_id:
                raise DomainError('task_mismatch', '任务库已经属于另一任务', 409)

    def _validate_existing(self, task_id):
        import re
        if task_id is not None and re.fullmatch(r'[a-f0-9]{32}', self.path.parent.name) and self.path.parent.name != task_id:
            raise DomainError('task_mismatch','任务 ID 与目录不一致',409)
        if not self.path.exists():
            if task_id is None:
                raise DomainError('task_not_found','任务库不存在',404)
            return
        with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True, factory=ClosingConnection) as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version != 2:
                raise DomainError('unsupported_schema','未知数据库 schema；未执行迁移或写入',409)
            if 'task' not in tables:
                raise DomainError('invalid_schema','缺少任务身份',409)
            rows=db.execute('SELECT id,workflow FROM task').fetchall()
            if len(rows)!=1 or rows[0][1]!=VERSION or (task_id is not None and rows[0][0]!=task_id):
                raise DomainError('task_mismatch','任务身份或流程版本不匹配',409)
            if re.fullmatch(r'[a-f0-9]{32}', self.path.parent.name) and rows[0][0]!=self.path.parent.name:
                raise DomainError('task_mismatch','任务身份与目录不一致',409)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def transaction(self):
        with self.lock:
            db = self.connect()
            try:
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback(); raise
            finally:
                db.close()

    def event(self, db, kind, payload):
        event = db.execute('INSERT INTO events(kind,payload,at) VALUES(?,?,?)',(kind,encode(payload),now())).lastrowid
        db.execute('INSERT INTO outbox(event) VALUES(?)',(event,))
        return event

    def metric(self, kind, payload):
        with self.transaction() as db:
            self.event(db, 'metric.' + kind, bounded(payload, 20000))

    def snapshot(self):
        with self.connect() as db:
            db.execute('BEGIN')
            task = dict(db.execute('SELECT * FROM task').fetchone())
            briefs = [{'version': row['version'], 'content': json.loads(row['content']),
                       'createdAt': row['created'], 'confirmedAt': row['confirmed_at'],
                       'status': 'confirmed' if row['confirmed_at'] else 'draft'}
                      for row in db.execute('SELECT b.*, c.at AS confirmed_at FROM briefs b '
                                            'LEFT JOIN confirmations c ON c.version=b.version ORDER BY b.version')]
            runs = [self._run(r) for r in db.execute('SELECT * FROM runs ORDER BY rowid')]
            nodes = [dict(json.loads(r['content']),id=r['id'],runId=r['run'],status=r['status'],token=r['token']) for r in db.execute('SELECT * FROM nodes ORDER BY rowid')]
            exceptions = [dict(json.loads(r['content']),id=r['id'],runId=r['run'],revision=r['revision'],status=r['status']) for r in db.execute('SELECT * FROM exceptions')]
            return {'task':task,'briefs':briefs,'runs':runs,'nodes':nodes,'exceptions':exceptions,'messages':[dict(r) for r in db.execute('SELECT * FROM messages ORDER BY rowid')], 'cursor':db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]}

    @staticmethod
    def _run(row):
        if not row:
            raise DomainError('run_not_found','运行不存在',404)
        return dict(json.loads(row['content']),runId=row['id'],briefVersion=row['brief'],status=row['status'],revision=row['revision'],epoch=row['epoch'])

    def message(self, content, intent, draft=False):
        with self.transaction() as db:
            mid = uid()
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?)',(mid,'user',content,intent,now()))
            if draft:
                db.execute('UPDATE task SET generation=generation+1')
            task = dict(db.execute('SELECT * FROM task').fetchone())
            self.event(db,'message.created',{'messageId':mid,'intent':intent})
            return mid,task['generation'],task['draft']

    def assistant(self, content, *, generation=None):
        with self.transaction() as db:
            if generation is not None and db.execute('SELECT generation FROM task').fetchone()[0] != generation:
                return False
            self._assistant(db, content)
            return True

    def _assistant(self, db, content):
        mid = uid()
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (mid, 'assistant', content, 'answer', now()))
        self.event(db, 'message.created', {'messageId': mid})

    def draft(self, value, generation, expected_version, summary=None):
        value = validate_brief(value)
        if any(type(number) is not int or number < 0 for number in (generation, expected_version)):
            raise DomainError('stale_intent', '需求代际和版本必须是非负整数', 409)
        with self.transaction() as db:
            task = db.execute('SELECT * FROM task').fetchone()
            if task['generation'] != generation or task['draft'] != expected_version:
                raise DomainError('stale_intent', '旧理解结果已过期', 409)
            sources = {row[0] for row in db.execute("SELECT id FROM messages WHERE role='user'")}
            if not set(value['sourceMessageIds']).issubset(sources):
                raise DomainError('source_mismatch', 'Brief 来源不是本任务用户消息')
            version = expected_version + 1
            db.execute('INSERT INTO briefs VALUES(?,?,?)', (version, encode(value), now()))
            db.execute('UPDATE task SET draft=?, title=?', (version, value['question'][:80]))
            self.event(db, 'brief.created', {'version': version, 'sourceMessageIds': value['sourceMessageIds']})
            if summary is not None:
                self._assistant(db, summary)
            return version

    def confirm(self, version):
        with self.transaction() as db:
            task=db.execute('SELECT * FROM task').fetchone()
            if type(version) is not int or task['draft'] != version:
                raise DomainError('stale_brief','草稿版本已变化',409)
            brief=db.execute('SELECT content FROM briefs WHERE version=?',(version,)).fetchone()
            if not brief or not ready(json.loads(brief[0])):
                raise DomainError('brief_not_ready','核心需求尚未澄清',409)
            # A newer message not yet understood must invalidate confirmation/start.
            last = db.execute("SELECT id FROM messages WHERE role='user' AND intent IN ('draft','revise_scope','next_round') ORDER BY rowid DESC LIMIT 1").fetchone()
            if last and last[0] not in json.loads(brief[0])['sourceMessageIds']:
                raise DomainError('intent_pending','新需求尚未完成理解',409)
            if task['confirmed'] == version:
                return
            db.execute('INSERT OR IGNORE INTO confirmations VALUES(?,?)', (version, now()))
            db.execute('UPDATE task SET confirmed=?', (version,))
            self.event(db, 'brief.confirmed', {'version': version, 'actor': 'user'})

    def replay(self, db, operation, key, payload):
        if not isinstance(key,str) or not 1<=len(key)<=128:
            raise DomainError('request_id_required','需要 requestId')
        digest=hashlib.sha256(encode(payload).encode()).hexdigest()
        row=db.execute('SELECT * FROM requests WHERE operation=? AND key=?',(operation,key)).fetchone()
        if row and row['digest'] != digest:
            raise DomainError('idempotency_conflict','同一 requestId 不能用于不同载荷',409)
        return (json.loads(row['result']) if row else None),digest

    def start(self, payload, settings, plan):
        with self.transaction() as db:
            prior,digest=self.replay(db,'start',payload.get('requestId'),payload)
            if prior:
                return self._run(db.execute('SELECT * FROM runs WHERE id=?',(prior['runId'],)).fetchone()),False
            task=db.execute('SELECT * FROM task').fetchone(); version=payload.get('expectedBriefVersion')
            if type(version) is not int or not version or task['draft'] != version or task['confirmed'] != version:
                raise DomainError('brief_not_confirmed','只能启动当前已确认契约',409)
            brief=json.loads(db.execute('SELECT content FROM briefs WHERE version=?',(version,)).fetchone()[0])
            last=db.execute("SELECT id FROM messages WHERE role='user' AND intent IN ('draft','revise_scope','next_round') ORDER BY rowid DESC LIMIT 1").fetchone()
            if last and last[0] not in brief['sourceMessageIds']:
                raise DomainError('intent_pending','新需求尚未完成理解',409)
            if db.execute("SELECT 1 FROM runs WHERE status NOT IN ('completed','cancelled')").fetchone():
                raise DomainError('active_run','请先显式取消已有运行',409)
            rid=uid(); nodes=plan(brief)
            self.validate_plan(brief,nodes)
            content={'contract':brief,'settings':bounded(settings),'createdAt':now(),'startedAt':time.time(),'usage':{},'report':None}
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,version,'research_starting',1,1,encode(content)))
            for node in nodes:
                gate(brief,node['stage'],node['objectiveId'])
                db.execute('INSERT INTO nodes VALUES(?,?,?,?,?)',(rid+':'+node['id'],rid,'pending',None,encode(dict(node,briefVersion=version))))
            self.event(db,'run.created',{'runId':rid,'briefVersion':version})
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',('start',payload['requestId'],digest,encode({'runId':rid})))
            return self._run(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone()),True

    @staticmethod
    def validate_plan(brief,nodes):
        import re
        if not isinstance(nodes,list) or not 1<=len(nodes)<=brief['executionPolicy']['budget']['nodes']:
            raise DomainError('invalid_plan','节点数量超出冻结预算')
        ids=set()
        for node in nodes:
            if not isinstance(node,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(node.get('id',''))) or node['id'] in ids:
                raise DomainError('invalid_plan','节点 ID 无效或重复')
            ids.add(node['id'])
            gate(brief,node.get('stage'),node.get('objectiveId'))
            deps=node.get('dependencies')
            if not isinstance(deps,list) or any(not isinstance(x,str) for x in deps) or len(set(deps))!=len(deps):
                raise DomainError('invalid_plan','依赖必须是唯一节点 ID 列表')
        remaining={n['id']:set(n['dependencies']) for n in nodes}
        if any(deps-ids for deps in remaining.values()):
            raise DomainError('invalid_plan','存在悬空依赖')
        while remaining:
            ready_ids={key for key,deps in remaining.items() if not deps}
            if not ready_ids:
                raise DomainError('invalid_plan','节点依赖成环')
            remaining={key:deps-ready_ids for key,deps in remaining.items() if key not in ready_ids}

    def owner(self, owner, release=False):
        with self.transaction() as db:
            if release:
                db.execute('UPDATE ownership SET expires=0 WHERE owner=?',(owner,)); return True
            row=db.execute('SELECT * FROM ownership').fetchone()
            if row and row['owner'] != owner and row['expires']>time.time():
                return False
            generation = row['generation'] if row and row['owner']==owner and row['expires']>time.time() else (row['generation']+1 if row else 1)
            db.execute('INSERT OR REPLACE INTO ownership VALUES(1,?,?,?)',(owner,time.time()+30,generation))
            return True

    def heartbeat(self, owner):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM ownership WHERE owner=?',(owner,)).fetchone()
            if not row or row['expires']<=time.time():
                return False
            expires=time.time()+30
            db.execute('UPDATE ownership SET expires=? WHERE owner=?',(expires,owner))
            for node in db.execute("SELECT id,content FROM nodes WHERE status='running'").fetchall():
                value=json.loads(node['content'])
                if value.get('owner')==owner and value.get('ownerGeneration')==row['generation']:
                    value['leaseUntil']=expires
                    db.execute('UPDATE nodes SET content=? WHERE id=?',(encode(value),node['id']))
            return True

    def recover(self, owner):
        if not self.owner(owner):
            return False
        with self.transaction() as db:
            rows=db.execute("SELECT id FROM runs WHERE status IN ('research_starting','researching')").fetchall()
            for row in rows:
                db.execute("UPDATE runs SET status='interrupted',epoch=epoch+1,revision=revision+1 WHERE id=?",(row[0],))
                db.execute("UPDATE nodes SET status='interrupted',token=NULL WHERE run=? AND status='running'",(row[0],))
                self.event(db,'run.interrupted',{'runId':row[0]})
        return True

    def action(self, payload):
        with self.transaction() as db:
            rid=payload.get('runId'); run=self._run(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())
            if type(payload.get('revision')) is not int or payload['revision']<0 or payload['revision'] != run['revision']:
                raise DomainError('stale_run','运行版本已变化',409)
            action=payload.get('action'); status=run['status']
            if action in ('resume','retry'):
                if status not in ('paused','interrupted','failed','blocked_exception'):
                    raise DomainError('invalid_transition','运行不能恢复',409)
                if db.execute("SELECT 1 FROM exceptions WHERE run=? AND status='open'",(rid,)).fetchone():
                    raise DomainError('exception_open','请先解决全部阻塞异常',409)
                unknown=db.execute("SELECT 1 FROM calls WHERE run=? AND status='started' AND json_extract(content,'$.sideEffect')=1",(rid,)).fetchone()
                if unknown and (action != 'retry' or payload.get('acknowledgeUnknownSideEffects') is not True):
                    raise DomainError('unknown_side_effect','副作用结果不明；显式 retry 并确认重复执行风险',409)
                db.execute("UPDATE nodes SET status='pending',token=NULL WHERE run=? AND status IN ('running','interrupted','failed')",(rid,))
                target='researching'
            elif action in ('pause','cancel') and status not in ('completed','cancelled'):
                target='paused' if action=='pause' else 'cancelled'
                db.execute("UPDATE nodes SET status='interrupted',token=NULL WHERE run=? AND status='running'",(rid,))
            else:
                raise DomainError('invalid_transition','状态转换无效',409)
            db.execute('UPDATE runs SET status=?,epoch=epoch+1,revision=revision+1 WHERE id=?',(target,rid))
            self.event(db,'run.'+target,{'runId':rid})
            return self._run(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def claim(self, rid, owner):
        with self.transaction() as db:
            lease=db.execute('SELECT * FROM ownership').fetchone()
            if not lease or lease['owner'] != owner or lease['expires'] <= time.time():
                raise DomainError('ownership_lost','执行所有权已失效',409)
            run=self._run(db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())
            if run['status'] not in ('research_starting','researching'):
                return None
            rows=db.execute('SELECT * FROM nodes WHERE run=? ORDER BY rowid',(rid,)).fetchall()
            completed={json.loads(r['content'])['id'] for r in rows if r['status']=='completed'}
            for row in rows:
                node=json.loads(row['content'])
                if row['status']!='pending' or not set(node['dependencies']).issubset(completed): continue
                gate(run['contract'],node['stage'],node['objectiveId'])
                token=uid(); node.update(attempt=node.get('attempt',0)+1,leaseUntil=min(time.time()+30,lease['expires']),owner=owner,ownerGeneration=lease['generation'])
                db.execute("UPDATE nodes SET status='running',token=?,content=? WHERE id=?",(token,encode(node),row['id']))
                db.execute("UPDATE runs SET status='researching',revision=revision+1 WHERE id=?",(rid,))
                self.event(db,'node.claimed',{'runId':rid,'nodeId':row['id'],'stage':node['stage']})
                return dict(node,id=row['id'],token=token,epoch=run['epoch'],runId=rid),run
            return None

    def guard(self, db, node):
        run=self._run(db.execute('SELECT * FROM runs WHERE id=?',(node['runId'],)).fetchone())
        row=db.execute('SELECT * FROM nodes WHERE id=? AND run=?',(node['id'],node['runId'])).fetchone()
        lease=db.execute('SELECT * FROM ownership').fetchone()
        saved=json.loads(row['content']) if row else {}
        if any(saved.get(key)!=node.get(key) for key in ('stage','objectiveId','briefVersion','owner','ownerGeneration','attempt')):
            raise DomainError('stale_result','节点绑定与已领取记录不一致',409)
        if not lease or lease['owner']!=node.get('owner') or lease['generation']!=node.get('ownerGeneration') or lease['expires']<=time.time() or saved.get('leaseUntil',0)<=time.time():
            raise DomainError('ownership_lost','节点执行所有权或租约已失效',409)
        if run['epoch']!=node['epoch'] or run['status']!='researching' or not row or row['token']!=node['token'] or row['status']!='running' or run['briefVersion']!=node['briefVersion']:
            raise DomainError('stale_result','运行或节点结果已过期',409)
        return run,row

    def charge(self, node, kind, call_id=None, call=None):
        with self.transaction() as db:
            run,_=self.guard(db,node); usage=run['usage']; budget=run['contract']['executionPolicy']['budget']
            if time.time()-run['startedAt']>budget['seconds'] or usage.get(kind,0)>=budget[kind]:
                raise DomainError('budget_exhausted','冻结预算已耗尽',409)
            usage[kind]=usage.get(kind,0)+1
            db.execute('UPDATE runs SET content=?,revision=revision+1 WHERE id=?',(encode({k:v for k,v in run.items() if k not in ('runId','briefVersion','status','revision','epoch')}),node['runId']))
            if call_id:
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?)',(call_id,node['runId'],node['id'],'started',encode(call)))
            self.event(db,'budget.charged',{'runId':node['runId'],'kind':kind,'callId':call_id})

    def receipt(self, call_id, result):
        result = bounded(result, MAX_STORED_BYTES)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM calls WHERE id=?', (call_id,)).fetchone()
            if row is None:
                raise DomainError('call_not_found', '工具调用不属于本任务', 404)
            content = json.loads(row['content'])
            if row['status'] == 'completed':
                if encode(content.get('result')) != encode(result):
                    raise DomainError('receipt_conflict', '已完成回执不可覆盖', 409)
                return
            content['result'] = result
            db.execute("UPDATE calls SET status='completed', content=? WHERE id=?", (encode(content), call_id))
            self.event(db, 'tool.receipt', {'callId': call_id, 'runId': row['run'], 'nodeId': row['node']})

    def finish(self, node, result=None, error=None):
        with self.transaction() as db:
            run,row=self.guard(db,node); content=json.loads(row['content']); content['result']=bounded(result, MAX_STORED_BYTES); content['error']=error
            db.execute('UPDATE nodes SET status=?,content=?,token=NULL WHERE id=?',('failed' if error else 'completed',encode(content),node['id']))
            if error:
                db.execute("UPDATE runs SET status='failed',revision=revision+1 WHERE id=?",(node['runId'],))
            elif node['stage']=='Report':
                content_run={k:v for k,v in run.items() if k not in ('runId','briefVersion','status','revision','epoch')}; content_run.update(report=result,finishedAt=now())
                db.execute("UPDATE runs SET status='completed',revision=revision+1,content=? WHERE id=?",(encode(content_run),node['runId']))
            else:
                db.execute('UPDATE runs SET revision=revision+1 WHERE id=?',(node['runId'],))
            self.event(db,'node.failed' if error else 'node.completed',{'runId':node['runId'],'nodeId':node['id']})

    def events(self, after=0, limit=100):
        try: after=int(after); limit=max(1,min(int(limit),500))
        except (ValueError,TypeError): raise DomainError('invalid_cursor','事件游标无效')
        if after<0: raise DomainError('invalid_cursor','事件游标无效')
        with self.connect() as db:
            rows=db.execute('SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?',(after,limit)).fetchall()
            events=[dict(id=r['id'],type=r['kind'],payload=json.loads(r['payload']),at=r['at']) for r in rows]
            cursor=events[-1]['id'] if events else after
            more=bool(db.execute('SELECT 1 FROM events WHERE id>? LIMIT 1',(cursor,)).fetchone())
            return {'events':events,'nextCursor':cursor,'more':more,'cursor':cursor,'cursorKind':'domain_event'}

    def tool_history(self):
        with self.connect() as db:
            return [dict(id=r['id'],runId=r['run'],nodeId=r['node'],status=r['status'],**json.loads(r['content'])) for r in db.execute('SELECT * FROM calls')]
