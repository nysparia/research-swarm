"""Durable single-worker local experiment queue and actual process receipts.

Resource requests constrain scheduling and thread-library hints, not OS CPU or
memory quotas. ProcessScope bounds descendant lifetime; this is not a security
sandbox. A resumed run always consumes a hashed, declared actual checkpoint.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import threading
import time
import uuid

from .local_tools import LocalResearchTools, ENVIRONMENT_CODE
from .process_scope import ProcessScope
from .research_materials import ResearchMaterials, safe_relative, reject_links, atomic_json


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


class ExperimentJobs:
    terminal = {'completed', 'failed', 'cancelled', 'timed_out', 'output_limit', 'interrupted'}

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closing = False
        self._closed = False
        self._scopes = {}
        self.materials = ResearchMaterials(self.root)
        self.tools = LocalResearchTools(self.root)
        self._ownership = (self.root / 'jobs.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self._ownership.seek(0)
                if not self._ownership.read(1):
                    self._ownership.write(b'0'); self._ownership.flush()
                self._ownership.seek(0)
                msvcrt.locking(self._ownership.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._ownership.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._ownership.close()
            raise ValueError('Experiment queue already has an owner') from exc
        self._db = sqlite3.connect(self.root / 'jobs.sqlite3', check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self._db.commit()
        self._gpu_count = self._detect_gpus()
        with self._lock:
            for job in self.list():
                if job['status'] in ('running', 'cancelling'):
                    job['status'] = 'interrupted'
                    job['error'] = 'Executor restarted; actual run interrupted. Retry restarts unless a verified checkpoint is resumed.'
                    if job['attempts']:
                        attempt = job['attempts'][-1]
                        run = self.root / 'runs' / job['id'] / ('attempt-' + str(attempt['number']))
                        actual_cwd = self._working_directory(job, run)
                        job['checkpoint'] = self._capture_checkpoint(job, run, actual_cwd)
                        recovered = {'tool': 'python_run', 'status': 'interrupted', 'returnCode': None,
                                     'jobId': job['id'], 'attempt': attempt['number'], 'error': job['error'],
                                     'artifacts': [], 'checkpoint': job['checkpoint'], 'recoveredAt': now(),
                                     'stdoutPath': attempt['stdoutPath'], 'stderrPath': attempt['stderrPath']}
                        recovered['workingDirectory'] = actual_cwd.relative_to(self.root).as_posix()
                        recovered.update({key: job['request'].get(key) for key in ('nodeId', 'nodeVersion', 'round', 'executionToken', 'protocolId', 'artifactId')})
                        recovery_receipt = run / 'recovery-receipt.json'
                        atomic_json(recovery_receipt, recovered)
                        attempt.update(status='interrupted', finishedAt=now(), receipt={'path': recovery_receipt.relative_to(self.root).as_posix(), 'sha256': digest(recovery_receipt)})
                        job['result'] = recovered
                    self._save(job)
        self._worker = threading.Thread(target=self._loop, daemon=True, name='research-experiment-queue')
        self._worker.start()

    def _detect_gpus(self):
        executable = shutil.which('nvidia-smi')
        if not executable:
            return 0
        directory = self.root / 'resource-probe'
        directory.mkdir(exist_ok=True)
        try:
            result = self.tools._process([executable, '--query-gpu=index', '--format=csv,noheader'], directory, directory, 5, lambda _: None, lambda: False)
            return len([line for line in result['stdout'].splitlines() if line.strip().isdigit()]) if result['status'] == 'completed' else 0
        except OSError:
            return 0

    def _save(self, job):
        job['revision'] += 1
        job['updatedAt'] = now()
        self._db.execute('INSERT OR REPLACE INTO jobs(id,data) VALUES (?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))
        self._db.commit()
        self._condition.notify_all()
        return copy.deepcopy(job)

    def get(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'job-[0-9a-f]{32}', job_id):
            raise ValueError('Unknown experiment job')
        with self._lock:
            row = self._db.execute('SELECT data FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise ValueError('Unknown experiment job')
            return json.loads(row[0])

    def list(self):
        with self._lock:
            return sorted((json.loads(row[0]) for row in self._db.execute('SELECT data FROM jobs')), key=lambda job: job['createdAt'])

    def submit(self, payload):
        allowed = {'code', 'timeoutSeconds', 'checkpoint', 'checkpointPath', 'resumeCode', 'resources', 'materialIds', 'protocolId', 'artifactId', 'nodeId', 'nodeVersion', 'round', 'executionToken'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('Unsupported job fields; only managed material IDs are accepted as inputs')
        code = payload.get('code')
        if not isinstance(code, str) or not code.strip() or len(code) > 60000:
            raise ValueError('Python code must contain 1–60000 characters')
        timeout = payload.get('timeoutSeconds', 90)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 86400:
            raise ValueError('timeoutSeconds must be between 1 and 86400')
        resources = payload.get('resources', {})
        if not isinstance(resources, dict) or set(resources) - {'cpuCores', 'gpuCount'}:
            raise ValueError('Unsupported resource request')
        cpu = resources.get('cpuCores', min(2, os.cpu_count() or 1))
        gpu = resources.get('gpuCount', 0)
        if isinstance(cpu, bool) or not isinstance(cpu, int) or not 1 <= cpu <= (os.cpu_count() or 1):
            raise ValueError('Requested CPU count exceeds available host CPUs')
        if isinstance(gpu, bool) or not isinstance(gpu, int) or not 0 <= gpu <= self._gpu_count:
            raise ValueError('Requested GPU count exceeds detected NVIDIA GPUs')
        checkpoint = payload.get('checkpoint')
        if checkpoint is None and ('checkpointPath' in payload or 'resumeCode' in payload):
            checkpoint = {'path': payload.get('checkpointPath'), 'resumeCode': payload.get('resumeCode')}
        if checkpoint is not None:
            if not isinstance(checkpoint, dict) or set(checkpoint) != {'path', 'resumeCode'}:
                raise ValueError('Checkpoint requires relative path and explicit resumeCode')
            safe_relative(checkpoint['path'])
            if not isinstance(checkpoint['resumeCode'], str) or not checkpoint['resumeCode'].strip() or len(checkpoint['resumeCode']) > 60000:
                raise ValueError('Checkpoint requires explicit bounded Python resumeCode')
        ids = payload.get('materialIds', [])
        if not isinstance(ids, list) or len(ids) > 30 or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
            raise ValueError('materialIds must contain up to 30 unique managed IDs')
        inputs = []
        names = set()
        for material_id in ids:
            material = self.materials.get(material_id)
            self.materials.path(material_id)
            if material['name'].casefold() in names:
                raise ValueError('Selected materials must have unique input filenames')
            names.add(material['name'].casefold())
            inputs.append({key: material[key] for key in ('id', 'name', 'sha256', 'bytes', 'source', 'revision')})
        for key in ('protocolId', 'artifactId', 'nodeId', 'executionToken'):
            if payload.get(key) is not None and (not isinstance(payload[key], str) or len(payload[key]) > 300):
                raise ValueError('Invalid job provenance link')
        for key in ('nodeVersion', 'round'):
            if key in payload and (type(payload[key]) is not int or payload[key] < 1):
                raise ValueError('Invalid experiment version')
        request = copy.deepcopy(payload)
        request.update(timeoutSeconds=timeout, resources={'cpuCores': cpu, 'gpuCount': gpu}, checkpoint=checkpoint, materialIds=ids)
        with self._condition:
            if self._closing:
                raise ValueError('Experiment queue is closed')
            job = {'id': 'job-' + uuid.uuid4().hex, 'revision': 0, 'status': 'queued', 'createdAt': now(),
                   'request': request, 'inputs': inputs, 'attempts': [], 'checkpoint': None, 'resumeMode': 'restart',
                   'resourceAvailability': {'logicalCpus': os.cpu_count() or 1, 'nvidiaGpus': self._gpu_count, 'maxConcurrentJobs': 1, 'enforcement': 'single-worker scheduling and thread hints; no OS resource quotas'}}
            return self._save(job)

    def _verified_checkpoint(self, job):
        checkpoint = job.get('checkpoint')
        protocol = job['request'].get('checkpoint')
        if not checkpoint or not protocol or not protocol.get('resumeCode'):
            raise ValueError('No actual checkpoint with explicit resume protocol exists; use retry for a restart')
        path = reject_links(self.root / safe_relative(checkpoint['path']))
        if not path.is_relative_to(self.root / 'runs' / job['id']) or not path.is_file() or path.stat().st_size > 50 * 1024 * 1024 or digest(path) != checkpoint['sha256']:
            raise ValueError('Checkpoint hash or task-local path is invalid')
        return path

    def action(self, job_id, action, expected_revision):
        with self._condition:
            if self._closing:
                raise ValueError('Experiment queue is closed')
            job = self.get(job_id)
            if type(expected_revision) is not int or expected_revision != job['revision']:
                raise ValueError('Job revision conflict')
            if action == 'cancel':
                if job['status'] == 'queued':
                    job['status'] = 'cancelled'
                elif job['status'] == 'running':
                    job['status'] = 'cancelling'
                elif job['status'] not in self.terminal | {'cancelling'}:
                    raise ValueError('Job cannot be cancelled')
            elif action in ('retry', 'resume'):
                if job['status'] not in self.terminal:
                    raise ValueError('Only a finished or interrupted job can restart')
                if action == 'resume':
                    self._verified_checkpoint(job)
                job.update(status='queued', resumeMode='resume' if action == 'resume' else 'restart')
                job.pop('error', None)
                job.pop('result', None)
            else:
                raise ValueError('Action must be cancel, retry or resume')
            return self._save(job)

    def logs(self, job_id, stream='stdout', offset=0, limit=65536):
        if stream not in ('stdout', 'stderr') or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 256 * 1024:
            raise ValueError('Invalid bounded log request')
        job = self.get(job_id)
        if not job['attempts']:
            return {'text': '', 'offset': offset, 'nextOffset': offset, 'bytes': 0, 'path': None}
        path = reject_links(self.root / safe_relative(job['attempts'][-1][stream + 'Path']))
        if not path.is_relative_to(self.root / 'runs' / job['id']):
            raise ValueError('Invalid task-local log path')
        if not path.is_file():
            return {'text': '', 'offset': offset, 'nextOffset': offset, 'bytes': 0, 'path': path.relative_to(self.root).as_posix()}
        with path.open('rb') as file:
            file.seek(offset)
            data = file.read(limit)
        return {'text': data.decode('utf-8', errors='replace'), 'offset': offset, 'nextOffset': offset + len(data),
                'bytes': path.stat().st_size, 'path': path.relative_to(self.root).as_posix()}

    def _loop(self):
        while True:
            with self._condition:
                queued = next((job for job in self.list() if job['status'] == 'queued'), None)
                if self._closing:
                    return
                if queued is None:
                    self._condition.wait(timeout=1)
                    continue
                attempt_number = len(queued['attempts']) + 1
                run = self.root / 'runs' / queued['id'] / ('attempt-' + str(attempt_number))
                run.mkdir(parents=True)
                cwd = self._working_directory(queued, run)
                attempt = {'number': attempt_number, 'mode': queued['resumeMode'], 'status': 'running', 'startedAt': now(),
                           'workingDirectory': cwd.relative_to(self.root).as_posix(),
                           'stdoutPath': (run / 'stdout.txt').relative_to(self.root).as_posix(),
                           'stderrPath': (run / 'stderr.txt').relative_to(self.root).as_posix()}
                queued['attempts'].append(attempt)
                queued['status'] = 'running'
                self._save(queued)
            try:
                with self.tools._process_lock:
                    result, checkpoint = self._execute(queued, run)
            except Exception as exc:
                result, checkpoint = {'tool': 'python_run', 'status': 'interrupted' if self._closing else 'failed', 'returnCode': None,
                                      'error': str(exc), 'artifacts': [], 'stdout': '', 'stderr': str(exc)}, None
            result.setdefault('protocolId', queued['request'].get('protocolId'))
            result.setdefault('artifactId', queued['request'].get('artifactId'))
            for key in ('nodeId', 'nodeVersion', 'round', 'executionToken'):
                result.setdefault(key, queued['request'].get(key))
            result.setdefault('stdoutPath', attempt['stdoutPath'])
            result.setdefault('stderrPath', attempt['stderrPath'])
            result.setdefault('elapsedMs', 0)
            result['jobId'] = queued['id']
            result['attempt'] = attempt_number
            with self._condition:
                current = self.get(queued['id'])
                # Cancellation/shutdown wins even if the process exited at the same instant.
                if self._closing:
                    current['status'] = 'interrupted'
                elif current['status'] == 'cancelling':
                    current['status'] = 'cancelled'
                else:
                    current['status'] = result['status']
                result['status'] = current['status']
                receipt_path = run / 'receipt.json'
                try:
                    atomic_json(receipt_path, result)
                    receipt_hash = digest(receipt_path)
                except Exception as exc:
                    # A process result without a durable receipt is not a
                    # verified experiment. Persist the failure and keep serving
                    # the queue instead of abandoning this job as 'running'.
                    reason = '实验执行记录保存失败：' + str(exc)
                    current['status'] = 'failed'
                    current['error'] = reason
                    result.update(status='failed', error=reason, artifacts=[], evidence=[], metrics=None)
                    result['stderr'] = (result.get('stderr', '') + '\n' + reason).strip()
                    result.pop('receipt', None)
                    current['attempts'][-1].update(status='failed', finishedAt=now(), error=reason)
                    current['result'] = result
                    current['checkpoint'] = checkpoint
                    self._save(current)
                    continue
                receipt = {'path': receipt_path.relative_to(self.root).as_posix(), 'sha256': receipt_hash}
                evidence = {'id': 'experiment:local:' + receipt_hash[:20], 'paperId': '', 'type': 'experiment', 'extractor': 'local_process',
                            'locator': receipt['path'], 'sha256': receipt_hash, 'tool': 'python_run', 'executionStatus': result['status'],
                            'quote': json.dumps({key: result.get(key) for key in ('tool', 'status', 'returnCode', 'elapsedMs', 'stdout', 'stderr', 'artifacts')}, ensure_ascii=False)[:45000], 'confidence': 1.0}
                result['evidence'] = [evidence]
                result['receipt'] = receipt['path']
                current['attempts'][-1].update(status=current['status'], finishedAt=now(), receipt=receipt)
                current['result'] = result
                current['checkpoint'] = checkpoint
                self._save(current)

    def _execute(self, job, run):
        request = job['request']
        cwd = reject_links(self._working_directory(job, run))
        cwd.mkdir(parents=True, exist_ok=True)
        manifests = []
        for material in job['inputs']:
            source = self.materials.path(material['id'])
            if digest(source) != material['sha256']:
                raise ValueError('Copied material hash changed')
            manifests.append({**material, 'path': str(source)})
        copied_inputs = self.tools._copy_input_materials(manifests, cwd, self.root)
        if job['resumeMode'] == 'resume':
            source = self._verified_checkpoint(job)
            target = cwd / safe_relative(request['checkpoint']['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if digest(target) != job['checkpoint']['sha256']:
                raise ValueError('Copied checkpoint hash changed')
        cancelled = lambda: self._closing or self.get(job['id'])['status'] == 'cancelling'
        python = self.tools._ensure_python(lambda _: None, cancelled)
        environment_script = ENVIRONMENT_CODE.replace('print(json.dumps(data,ensure_ascii=False))', 'data["packages"]={d.metadata["Name"]:d.version for d in importlib.metadata.distributions() if d.metadata.get("Name")}\nprint(json.dumps(data,ensure_ascii=False))')
        env_run = run / 'environment'
        environment_result = self.tools._process([str(python), '-I', '-X', 'utf8', '-c', environment_script], cwd, env_run, 20, lambda _: None, cancelled)
        if environment_result['status'] != 'completed':
            if cancelled():
                return {'tool': 'python_run', 'status': 'interrupted' if self._closing else 'cancelled', 'returnCode': environment_result['returnCode'], 'artifacts': [], 'stdout': '', 'stderr': ''}, None
            raise ValueError('Environment snapshot failed')
        environment = json.loads(environment_result['stdout'])
        environment['gpu'] = {'detectedNvidiaCount': self._gpu_count, 'requestedCount': request['resources']['gpuCount']}
        environment['dependencyIsolation'] = 'task-local venv'
        atomic_json(run / 'environment.json', environment)
        environment_hash = digest(run / 'environment.json')
        script = run / 'experiment.py'
        script.write_text(request['checkpoint']['resumeCode'] if job['resumeMode'] == 'resume' else request['code'], encoding='utf-8', newline='\n')
        script_hash = digest(script)
        argv = [str(python), '-I', '-X', 'utf8', '-u', str(script)]
        env = self.tools._env(cwd)
        cores = str(request['resources']['cpuCores'])
        env.update(OMP_NUM_THREADS=cores, OPENBLAS_NUM_THREADS=cores, MKL_NUM_THREADS=cores,
                   CUDA_VISIBLE_DEVICES=','.join(str(i) for i in range(request['resources']['gpuCount'])),
                   RESEARCH_INPUTS_DIR=str(cwd / 'inputs'))
        before = {path.relative_to(cwd).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns) for path in self._files(cwd)}
        started = time.monotonic()
        status = 'completed'
        out_path, err_path = run / 'stdout.txt', run / 'stderr.txt'
        with out_path.open('wb') as stdout, err_path.open('wb') as stderr:
            if cancelled():
                process = None
                status = 'interrupted' if self._closing else 'cancelled'
            else:
                scope = ProcessScope(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
                process = scope.process
                with self._lock:
                    self._scopes[job['id']] = scope
                try:
                    while process.poll() is None:
                        if cancelled():
                            status = 'interrupted' if self._closing else 'cancelled'; break
                        if time.monotonic() - started > request['timeoutSeconds']:
                            status = 'timed_out'; break
                        if out_path.stat().st_size + err_path.stat().st_size > 4 * 1024 * 1024:
                            status = 'output_limit'; break
                        time.sleep(.05)
                finally:
                    scope.close()
                    with self._lock:
                        self._scopes.pop(job['id'], None)
        if process is not None and process.returncode and status == 'completed':
            status = 'failed'
        artifacts = []
        for path in sorted(self._files(cwd)):
            if path.stat().st_size > 50 * 1024 * 1024 or len(artifacts) >= 30:
                continue
            relative = path.relative_to(cwd).as_posix()
            if relative.startswith('inputs/') or '__pycache__' in path.parts:
                continue
            if before.get(relative) == (path.stat().st_size, path.stat().st_mtime_ns):
                continue
            target = run / 'artifacts' / path.relative_to(cwd)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            artifacts.append({'name': path.name, 'path': target.relative_to(self.root).as_posix(), 'sha256': digest(target), 'bytes': target.stat().st_size})
        checkpoint = self._capture_checkpoint(job, run, cwd)
        metrics = None
        metrics_path = run / 'artifacts' / 'metrics.json'
        if metrics_path.is_file() and metrics_path.stat().st_size <= 256 * 1024:
            try:
                metrics = json.loads(metrics_path.read_text('utf-8'))
            except (ValueError, UnicodeError):
                pass
        def tail(path, size):
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - size))
                return stream.read(size).decode('utf-8', errors='replace')
        result = {'tool': 'python_run', 'status': status, 'returnCode': process.returncode if process else None,
                  'elapsedMs': round((time.monotonic() - started) * 1000), 'stdout': tail(out_path, 30000), 'stderr': tail(err_path, 12000),
                  'command': argv, 'script': script.relative_to(self.root).as_posix(), 'scriptSha256': script_hash,
                  'scriptChanged': not script.is_file() or digest(script) != script_hash, 'timeoutSeconds': request['timeoutSeconds'],
                  'workingDirectory': cwd.relative_to(self.root).as_posix(), 'artifacts': artifacts, 'metrics': metrics,
                  'stdoutPath': out_path.relative_to(self.root).as_posix(), 'stderrPath': err_path.relative_to(self.root).as_posix(),
                  'createdAt': job['attempts'][-1]['startedAt'], 'environment': environment,
                  'environmentPath': (run / 'environment.json').relative_to(self.root).as_posix(), 'inputs': copied_inputs,
                  'environmentSha256': environment_hash,
                  'checkpoint': checkpoint, 'resumeMode': job['resumeMode']}
        result.update({key: request.get(key) for key in ('nodeId', 'nodeVersion', 'round', 'executionToken', 'protocolId', 'artifactId')})
        return result, checkpoint

    def _working_directory(self, job, run):
        request = job['request']
        if run.name == 'attempt-1' and request.get('executionToken') and request.get('nodeId'):
            identity = {key: request.get(key, 1 if key != 'nodeId' else None)
                        for key in ('nodeId', 'nodeVersion', 'round', 'executionToken')}
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:32]
            return self.root / 'laboratory' / ('managed-' + key)
        return run / 'work'

    def _files(self, cwd):
        """Do not descend symlinks or Windows junctions when observing outputs."""
        for directory, subdirs, filenames in os.walk(cwd, followlinks=False):
            safe_dirs = []
            for name in subdirs:
                try:
                    reject_links(Path(directory) / name)
                    safe_dirs.append(name)
                except ValueError:
                    pass
            subdirs[:] = safe_dirs
            for name in filenames:
                try:
                    path = reject_links(Path(directory) / name)
                    if path.is_file() and path.is_relative_to(cwd):
                        yield path
                except (ValueError, OSError):
                    pass

    def _capture_checkpoint(self, job, run, cwd):
        protocol = job['request'].get('checkpoint')
        if not protocol:
            return None
        try:
            checkpoint_source = reject_links(cwd / safe_relative(protocol['path']))
            if checkpoint_source.is_file() and checkpoint_source.stat().st_size <= 50 * 1024 * 1024:
                checkpoint_copy = run / 'checkpoint' / safe_relative(protocol['path'])
                checkpoint_copy.parent.mkdir(parents=True, exist_ok=True)
                reject_links(checkpoint_copy)
                shutil.copyfile(checkpoint_source, checkpoint_copy)
                return {'path': checkpoint_copy.relative_to(self.root).as_posix(), 'sha256': digest(checkpoint_copy),
                        'bytes': checkpoint_copy.stat().st_size, 'protocol': 'explicit Python resumeCode', 'attempt': len(job['attempts'])}
        except (ValueError, OSError):
            pass
        return None

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closing = True
            self._condition.notify_all()
        self._worker.join(timeout=60)
        if self._worker.is_alive():
            raise RuntimeError('Experiment worker has not terminated; queue ownership retained')
        with self._lock:
            self._db.close()
            self._ownership.close()
            self._closed = True
