"""Single-task research runtime, retrieval budgets and commands."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .providers import Settings
from .runner import ResearchRunner
from .local_tools import LocalResearchTools


APP_ROOT = Path(__file__).resolve().parent.parent


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ResearchApplication:
    def __init__(self, source: Path, state_dir: Path, max_workers=3, static_dir=None, workflow='gated',
                 *, settings=None, mutation_lock=None, runner_wrapper=None):
        from .engine import Engine
        from .library import Library

        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.library = Library(source)
        local_settings = Settings(self.state_dir, Path(source))
        self.settings = local_settings if settings is None else settings
        self.runner = ResearchRunner(self.settings, self.state_dir, retrieve=self._node_retrieve,
                                     read_pdf=self._read_pdf, local_tools=LocalResearchTools(self.state_dir))
        runner = self.runner if runner_wrapper is None else runner_wrapper(self)
        self.engine = Engine(self.library.load(), self.state_dir / 'swarm.sqlite', runner=runner, max_workers=max_workers, workflow=workflow)
        self.static_dir = Path(static_dir or APP_ROOT / 'dist')
        self.operations = []
        self.operation_revision = 0
        self.operation_lock = threading.RLock()
        self.retrieval_lock = threading.Lock()
        self.mutation_lock = threading.RLock() if mutation_lock is None else mutation_lock
        self.budget_path = self.state_dir / 'search-budgets.json'
        self.search_budgets = json.loads(self.budget_path.read_text(encoding='utf-8')) if self.budget_path.is_file() else {}
        journal = self.state_dir / 'operations.jsonl'
        if journal.is_file():
            for line in journal.read_text(encoding='utf-8').splitlines()[-100:]:
                try:
                    item = json.loads(line)
                    self.operations = [o for o in self.operations if o['id'] != item['id']] + [item]
                except (ValueError, KeyError):
                    continue
            for item in self.operations:
                if item['status'] == 'running':
                    item.update(status='failed', error='上次检索因服务停止而中断，可重试。')

    def _read_pdf(self, paper_id, page_start=1, page_count=3):
        from .downloads import ensure_paper_pdf
        from .fulltext import read_pdf_evidence
        # Validate and read an existing file first; fetch only a known public paper.
        result = read_pdf_evidence(self.library, paper_id, page_start, page_count)
        if self.library.pdf_path(str(paper_id)) is None:
            fetched = ensure_paper_pdf(self.library, paper_id)
            if fetched['available']:
                result = read_pdf_evidence(self.library, paper_id, page_start, page_count)
            else:
                result['limitations'].append(fetched['message'])
        return result

    def snapshot(self):
        state = self.engine.snapshot()
        with self.operation_lock:
            state['operations'] = list(self.operations[-30:])
        return state

    def _operation(self, item):
        with self.operation_lock:
            self.operations = [o for o in self.operations if o['id'] != item['id']] + [dict(item)]
            self.operation_revision += 1
            with (self.state_dir / 'operations.jsonl').open('a', encoding='utf-8') as file:
                file.write(json.dumps(item, ensure_ascii=False) + '\n')

    def _retrieve(self, payload, node=None):
        query = str(payload.get('query', '')).strip()
        if not query or len(query) > 1000:
            raise ValueError('请输入 1 至 1000 字符的检索式')
        top_k = int(payload.get('topK', 10))
        if not 1 <= top_k <= 30:
            raise ValueError('每次补充检索数量为 1 至 30 篇')
        if not self.retrieval_lock.acquire(blocking=False):
            raise ValueError('已有补充检索正在执行，请等待当前检索完成')
        operation = {'id': uuid.uuid4().hex, 'type': 'retrieval', 'nodeId': node.get('id') if node else None, 'message': '正在多源检索并追踪引用：' + query, 'status': 'running', 'startedAt': utc_now()}
        self._operation(operation)
        try:
            with self.settings.lock:
                search = copy.deepcopy(self.settings.data['search'])
                keys = self.settings.search_credentials()
            result = self.library.retrieve(query, top_k, node_id=node.get('sourceNodeId') if node else None,
                                           search_settings=search, search_keys=keys)
            operation.update(status='completed' if result.get('ok', True) else 'failed', message=result.get('message', '补充检索完成，准备导入论文与证据。'), finishedAt=utc_now())
            operation['retrieval'] = result.get('retrieval', {})
            self._operation(operation)
            return result
        except Exception as exc:
            operation.update(status='failed', error=self.settings.safe_error(exc), finishedAt=utc_now())
            self._operation(operation)
            raise
        finally:
            self.retrieval_lock.release()

    def _node_retrieve(self, node, context, query, limit):
        budget_id = (node.get('input') or {}).get('searchBudgetId')
        if not budget_id or not (node.get('input') or {}).get('allowNewSearch'):
            raise ValueError('该节点没有有效的外部检索授权')
        with self.operation_lock:
            count = self.search_budgets.get(budget_id, 0)
            budget_limit = self.settings.public()['search']['maxCalls'] if context.get('workflow') == 'autonomous' else min(2, self.settings.public()['search']['maxCalls'])
            if count >= budget_limit:
                raise ValueError(f'本轮已用完 {budget_limit} 次外部检索预算；请汇总实际取得的材料，明确未核验事项。')
            self.search_budgets[budget_id] = count + 1
            temporary = self.budget_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.search_budgets), encoding='utf-8')
            temporary.replace(self.budget_path)
        # Library itself serializes its writes. Wait outside the engine lock so pause stays responsive.
        while self.retrieval_lock.locked():
            time.sleep(.2)
            current = self.engine.snapshot()
            live = next((n for n in current['nodes'] if n['id'] == node['id']), {})
            if current['paused'] or live.get('version') != node.get('version'):
                raise ValueError('节点已暂停或发生修改，取消尚未启动的检索')
        current = self.engine.snapshot()
        live = next((n for n in current['nodes'] if n['id'] == node['id']), {})
        if current['paused'] or live.get('version') != node.get('version') or live.get('status') != 'running':
            raise ValueError('节点已暂停或发生修改，取消尚未启动的检索')
        return self._retrieve({'query': query, 'topK': limit}, node)

    def post(self, path, payload):
        if path == '/api/provider/models/deepseek':
            return self.settings.deepseek_models(payload)
        if path.startswith('/api/actions/'):
            action = path.rsplit('/', 1)[-1]
            if action in ('refresh-library',):
                raise ValueError('此操作只能由服务器导入真实论文库')
            if 'library' in payload:
                raise ValueError('客户端不得提供伪造论文库')
            with self.mutation_lock:
                return self.engine.command(action, payload)
        if path == '/api/settings':
            with self.mutation_lock:
                state = self.engine.snapshot()
                if not state['paused'] or any(n['status'] == 'running' for n in state['nodes']):
                    raise ValueError('有节点正在执行，请先暂停并等待当前调用结束，再更改模型设置')
                old_mode = state['project']['mode']
                mode = payload.get('mode', old_mode)
                # Validate model fields before touching the engine.
                previous = copy.deepcopy(self.settings.data)
                result = self.settings.update(payload)
                try:
                    self.engine.command('mode', {'mode': mode})
                except Exception:
                    self.settings.restore(previous)
                    raise
                return result
        if path == '/api/provider/test':
            role = payload.get('role', 'main')
            self.settings.chat([{'role': 'user', 'content': 'Reply only: OK'}], max_tokens=32, role=role)
            return {'ok': True, 'role': role, 'message': '模型连接成功'}
        if path == '/api/retrieve':
            state = self.engine.snapshot()
            if any(n['status'] == 'running' for n in state['nodes']) or state['status'] == 'running':
                raise ValueError('请先暂停蜂群，再补充论文')
            result = self._retrieve(payload)
            state = self.engine.command('refresh-library', {'library': result['library']})
            return {'result': {k: v for k, v in result.items() if k != 'library'}, 'snapshot': state}
        if path == '/api/deepen':
            state = self.engine.snapshot()
            if payload.get('expectedRevision') != state['revision']:
                raise ValueError('状态已变化，请重新预览影响范围后确认')
            node_id = payload.get('nodeId')
            if payload.get('claimId'):
                from .claims import get_claim
                claim = get_claim(state, payload['claimId'])
                if node_id and node_id != claim.get('ownerNodeId'):
                    raise ValueError('主张与操作节点不匹配')
                node_id = claim.get('ownerNodeId')
            node = next((n for n in state['nodes'] if n['id'] == node_id), None)
            if not node:
                raise ValueError('节点不存在')
            if not str(payload.get('text', '')).strip():
                raise ValueError('请填写深入研究问题')
            command = {k: v for k, v in payload.items() if k not in ('query', 'topK', 'library')}
            command.update(kind='deepen', taskKind='research')
            if payload.get('allowNewSearch'):
                command['allowNewSearch'] = True
                command['searchBudgetId'] = uuid.uuid4().hex
            if payload.get('query', '').strip():
                result = self._retrieve(payload, node)
                command['library'] = result['library']
                new_ids = result.get('retrieval', {}).get('resultPaperIds') or result.get('retrieval', {}).get('newPaperIds', [])
                command['paperIds'] = new_ids
                command['paperScopeExplicit'] = True
                command['retrieval'] = result.get('retrieval', {})
            with self.mutation_lock:
                return self.engine.command('intervene', command)
        raise ValueError('未知 API 操作')
