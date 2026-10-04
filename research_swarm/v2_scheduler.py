"""Explicit-start scheduler with renewable ownership and fenced result commits."""
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

from .v2_contracts import DomainError
from .v2_store import uid


class Scheduler:
    def __init__(self, store, pipeline):
        self.store, self.pipeline = store, pipeline
        self.owner = uid()
        self.lock = threading.RLock()
        self.thread = None
        self.pending = None
        self.closed = False
        self.stop = threading.Event()
        self.owns = self.store.recover(self.owner)
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True, name='v2-lease')
        self.heartbeat_thread.start()

    def _heartbeat(self):
        while not self.stop.wait(5):
            if self.owns:
                try:
                    self.owns = self.store.heartbeat(self.owner)
                except (sqlite3.Error, OSError):
                    # An uncertain lease cannot authorize more calls or commits.
                    self.owns = False

    def launch(self, run_id):
        with self.lock:
            if self.closed:
                raise DomainError('closed', '执行器已关闭', 409)
            if not self.owns:
                raise DomainError('ownership_lost', '另一实例拥有执行权；本实例只读', 409)
            if self.thread and self.thread.is_alive():
                self.pending = run_id
                return
            self.thread = threading.Thread(target=self._drive, args=(run_id,), daemon=True, name='v2-research')
            self.thread.start()

    def _drive(self, run_id):
        try:
            self._run(run_id)
        finally:
            with self.lock:
                self.thread = None
                pending, self.pending = self.pending, None
                if pending and not self.closed and self.owns:
                    self.launch(pending)

    def _run(self, run_id):
        snapshot = self.store.snapshot()
        current_run = next((item for item in snapshot['runs'] if item['runId'] == run_id), None)
        parallelism = ((current_run or {}).get('contract', {}).get('executionPolicy', {})
                       .get('budget', {}).get('parallelism', 1))
        stop = threading.Event()

        def worker():
            while not self.closed and self.owns and not stop.is_set():
                try:
                    claimed = self.store.claim(run_id, self.owner)
                except DomainError:
                    return
                if not claimed:
                    return
                node, run = claimed
                started = time.perf_counter()
                try:
                    settings = self.pipeline.settings
                    task_id = self.store.snapshot()['task']['id']
                    context = settings.usage_context(task_id, node['id']) if hasattr(settings, 'usage_context') else nullcontext()
                    with context:
                        result = self.pipeline.execute(node, run)
                    self.store.finish(node, result)
                    elapsed = time.perf_counter() - started
                    self.store.metric('node_timing', {'runId': run_id, 'nodeId': node['id'],
                                                      'stage': node.get('stage'), 'seconds': round(elapsed, 3)})
                except Exception as exc:
                    if not self._failed(node, run, exc):
                        stop.set()
                        return

        with ThreadPoolExecutor(max_workers=max(1, min(parallelism, 8)), thread_name_prefix='v2-node') as pool:
            futures = [pool.submit(worker) for _ in range(max(1, min(parallelism, 8)))]
            for future in futures:
                future.result()

    def _failed(self, node, run, error):
        from .v2_exceptions import accept, host_type
        try:
            message = self.pipeline.settings.safe_error(error)
            kind = host_type(error)
            if kind:
                accepted = accept(self.store, node, {'type': kind, 'message': message}, error)
                if accepted['blocking']:
                    return False
                # An unavailable optional branch must not stop the contract's main line.
                self.store.finish(node, {
                    'state': self.pipeline.state(node), 'summary': '可选验证未完成：' + message,
                    'analysisStatus': 'incomplete', 'unresolved': [message],
                    'optionalNextActions': [accepted['message']], 'executions': [],
                })
                return True
            self.store.finish(node, error=message)
        except DomainError:
            pass
        return False

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        # Fence workers before waiting for slow model or tool calls.
        for run in self.store.snapshot()['runs']:
            if run['status'] in ('researching', 'research_starting') and self.owns:
                try:
                    self.store.action({'runId': run['runId'], 'revision': run['revision'], 'action': 'pause'})
                except DomainError:
                    pass
        self.stop.set()
        self.heartbeat_thread.join(timeout=1)
        if self.thread:
            self.thread.join(timeout=5)
        self.store.owner(self.owner, True)
