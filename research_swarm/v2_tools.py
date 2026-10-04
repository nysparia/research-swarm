"""Frozen-policy tools with run-local environments and write-ahead receipts."""
import copy
import hashlib
import json
import os
import random
import sqlite3
import threading
import time
from pathlib import Path

from .experiment_jobs import ExperimentJobs
from .research_materials import reject_links, safe_relative
from .scientific_validation import validate_measurements, validate_protocol
from .v2_contracts import DomainError, gate, validate_target
from .v2_store import uid


class _RunExperimentJobs(ExperimentJobs):
    """Recover old queued jobs without letting a new owner execute them."""

    def __init__(self, root):
        self._recovered = threading.Event()
        self._recovery_error = None
        try:
            super().__init__(root)
            self._recovered.wait()
            if self._recovery_error is not None:
                raise self._recovery_error
        except Exception:
            worker = getattr(self, '_worker', None)
            if worker is not None and worker.ident is not None:
                self.close()
            else:
                # The base constructor can fail after opening the owner/DB handles.
                for name in ('_db', '_ownership'):
                    handle = getattr(self, name, None)
                    if handle is not None:
                        handle.close()
            raise

    def _loop(self):
        try:
            with self._condition:
                for job in self.list():
                    if job['status'] == 'queued':
                        job.update(
                            status='interrupted',
                            error='Run executor restarted; an explicit node retry is required.',
                        )
                        self._save(job)
        except Exception as exc:
            self._recovery_error = exc
        finally:
            self._recovered.set()
        if self._recovery_error is None:
            super()._loop()


class RunTools:
    def __init__(self, store, source, root, settings):
        self.store = store
        self.source = Path(source)
        self.root = Path(root).resolve()
        self.settings = settings
        self._jobs = {}
        self._lock = threading.RLock()
        self._closing = threading.Event()

    def close(self):
        """Cancel active calls, then release every queue's worker and file handles."""
        self._closing.set()
        with self._lock:
            errors = []
            for run_id, jobs in list(self._jobs.items()):
                try:
                    jobs.close()
                except Exception as exc:
                    errors.append(exc)
                else:
                    del self._jobs[run_id]
            if errors:
                # Retain failed managers so a later close can finish releasing them.
                raise DomainError('resource_unavailable', '实验队列未能关闭：' + str(errors[0])) from errors[0]

    def cancelled(self, node):
        if self._closing.is_set():
            return True
        try:
            with self.store.connect() as db:
                self.store.guard(db, node)
            return False
        except DomainError:
            return True

    def call(self, run, node, name, arguments):
        # Serialize deduplication and installation against execution in this adapter.
        # close sets the cancellation event before waiting for an active call.
        with self._lock:
            if self._closing.is_set():
                raise DomainError('resource_unavailable', '运行工具已关闭')
            return self._call(run, node, name, arguments)

    def _call(self, run, node, name, arguments):
        with self.store.connect() as db:
            run, _ = self.store.guard(db, node)
        gate(run['contract'], node['stage'], node['objectiveId'], name)
        allowed = {
            'paper_search': {'query', 'limit'},
            'paper_read': {'paperId', 'pageStart', 'pageCount'},
            'paper_download': {'paperId'},
            'python_run': {'code', 'timeoutSeconds', 'protocol', 'claimId'},
            'python_install': {'packages', 'source'},
        }
        if name not in allowed:
            raise DomainError('policy_denied', '工具未授权', 403)
        if not isinstance(arguments, dict) or set(arguments) - allowed[name]:
            raise DomainError('invalid_tool', '工具参数必须是对象且不含未知字段')
        arguments = copy.deepcopy(arguments)
        binding = {}
        if name == 'python_run':
            try:
                validate_protocol(arguments.get('protocol'))
            except (ValueError, TypeError) as exc:
                raise DomainError('invalid_tool', str(exc)) from exc
            binding = self._claim_binding(run, node, arguments)
        try:
            digest = hashlib.sha256(json.dumps(
                {'tool': name, 'arguments': arguments}, sort_keys=True, allow_nan=False,
            ).encode()).hexdigest()
        except (ValueError, TypeError) as exc:
            raise DomainError('invalid_tool', '工具参数必须是有效 JSON') from exc
        attempt = node.get('attempt', 1)
        prior = [
            call for call in self.store.tool_history()
            if call['runId'] == run['runId'] and call['nodeId'] == node['id']
            and call.get('attempt', 1) == attempt and call.get('digest') == digest
        ]
        if prior and prior[-1]['status'] == 'completed':
            result = copy.deepcopy(prior[-1]['result'])
            if name in ('python_run', 'python_install'):
                self._verify_receipt(run, result)
                if any(result.get(key) != value for key, value in binding.items()):
                    raise DomainError('invalid_receipt', '缓存凭据与当前主张版本或复现目标不一致', 409)
            self._check_result(node, result)
            return result
        if prior and prior[-1]['status'] == 'started':
            raise DomainError('unknown_side_effect', '尚无完成回执，不自动重复工具调用', 409)

        call_id = uid()
        self.store.charge(node, 'toolCalls', call_id, {
            'tool': name, 'arguments': arguments, 'digest': digest, 'attempt': attempt,
            'sideEffect': name in ('python_run', 'python_install'),
        })
        if name in ('paper_search', 'paper_download'):
            self.store.charge(node, 'searchCalls')
        if self.cancelled(node):
            raise DomainError('stale_result', '工具外发前运行已过期', 409)
        if name.startswith('paper_'):
            result = self._paper(run, node, name, arguments)
        else:
            result = self._local(run, node, name, arguments)
            result.update(binding)
        self.store.receipt(call_id, result)
        self._check_result(node, result)
        return result

    def _local(self, run, node, name, arguments):
        run_id = run['runId']
        artifact_root = self.root / run_id
        try:
            reject_links(artifact_root)
            if run_id not in self._jobs:
                self._jobs[run_id] = _RunExperimentJobs(artifact_root)
            jobs = self._jobs[run_id]
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            raise DomainError('resource_unavailable', '实验环境不可用：' + str(exc)) from exc
        context = {
            'round': run['briefVersion'],
            'cancelled': lambda: self.cancelled(node),
            'executionToken': str(node['attempt']) + ':' + node['token'],
            'experimentJobs': jobs,
            'executionSettings': {
                'maxTimeoutSeconds': min(90, run['contract']['executionPolicy']['budget']['seconds']),
            },
        }
        # Demo-only execution path, enabled explicitly through an environment flag.
        if os.environ.get('RESEARCH_SWARM_MOCK_EXECUTION', '').lower() in ('1', 'true', 'yes'):
            delay = random.uniform(0.35, 1.15)
            time.sleep(delay)
            if name == 'python_install':
                return {'tool': name, 'status': 'completed',
                        'packages': arguments.get('packages', []),
                        'stdout': 'install completed', 'stderr': '', 'returnCode': 0,
                        'durationSeconds': round(delay, 3)}
            return {'tool': name, 'status': 'completed',
                    'stdout': 'execution completed',
                    'stderr': '', 'returnCode': 0, 'evidence': [],
                    'durationSeconds': round(delay, 3)}
        local_arguments = {key: value for key, value in arguments.items() if key not in ('protocol', 'claimId')}
        try:
            # Using the queue's own tools shares its venv AND installation/process locks.
            result = jobs.tools.call(
                name, local_arguments, dict(node, version=node['briefVersion']), context, lambda _: None,
            )
        except DomainError:
            raise
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            # No fabricated completion: the started ledger entry remains unknown.
            if self.cancelled(node):
                raise DomainError('stale_result', '本机执行已过期或关闭', 409) from exc
            raise DomainError('resource_unavailable', '实验环境不可用：' + str(exc)) from exc
        except (ValueError, TypeError) as exc:
            unavailable = (
                'Experiment queue is closed',
                'Requested CPU count exceeds available host CPUs',
                'Requested GPU count exceeds detected NVIDIA GPUs',
            )
            code = 'resource_unavailable' if str(exc) in unavailable else 'invalid_tool'
            raise DomainError(code, str(exc)) from exc
        result.update(artifactRoot=artifact_root.relative_to(self.root).as_posix(), nodeAttempt=node['attempt'])
        if name == 'python_run':
            result['evidenceIds'] = [item['id'] for item in result.get('evidence', []) if item.get('locator')]
            errors = []
            data, _ = validate_measurements(artifact_root, [result], arguments['protocol'], errors)
            result.update(
                protocol=arguments['protocol'], measurements=data, measurementErrors=errors,
                scientificStatus='unreviewed' if data else 'invalid',
            )
        infrastructure_failure = (
            result.get('status') == 'failed' and result.get('returnCode') is None and result.get('error')
        )
        installation_failure = name == 'python_install' and result.get('status') in ('failed', 'timed_out', 'output_limit')
        if infrastructure_failure or installation_failure:
            result['errorCode'] = 'resource_unavailable'
        return result

    def _check_result(self, node, result):
        if self.cancelled(node):
            raise DomainError('stale_result', '外部回执仅保留历史，不发布过期结果', 409)
        if result.get('errorCode') == 'resource_unavailable':
            reason = result.get('error') or result.get('stderr') or result.get('status')
            raise DomainError('resource_unavailable', '实验环境或依赖不可用：' + str(reason)[-1800:])

    def _claim_binding(self, run, node, arguments):
        claim_id = arguments.get('claimId')
        replication = node['stage'] == 'Replication'
        if claim_id is None:
            if replication:
                raise DomainError('invalid_tool', '复现执行必须选择上下文中已登记的 claimId')
            return {}
        if not isinstance(claim_id, str) or not claim_id.strip() or len(claim_id) > 300:
            raise DomainError('invalid_tool', 'claimId 必须是有界非空字符串')
        claim = None
        for prior in reversed(self.store.snapshot()['nodes']):
            if (prior['runId'] != run['runId'] or prior['objectiveId'] != node['objectiveId']
                    or prior['status'] != 'completed'):
                continue
            state = (prior.get('result') or {}).get('state')
            if not state:
                continue
            claims = state.get('claimGraph', {}).get('claims', [])
            claim = next((item for item in claims if item.get('id') == claim_id), None)
            # Completed states are cumulative. Do not revive an older/removed claim.
            break
        if not claim or claim.get('archived') or type(claim.get('version')) is not int or claim['version'] < 1:
            raise DomainError('invalid_tool', '请选择本 Run 与当前目标中已登记的主张版本')
        target = claim.get('reproductionTarget')
        protocol = arguments['protocol']
        if replication:
            from .claim_runtime import validate_reproduction

            try:
                evidence = state.get('evidence', [])
                validate_target(target, {item['id'] for item in evidence})
                validate_reproduction([{'reproductionTarget': target}], evidence)
                cited = [item for item in evidence if item['id'] in target['evidenceIds']]
                if any(item.get('type') != 'full_text' for item in cited):
                    raise ValueError('复现目标必须来自原论文可定位正文')
                versions = claim.get('versions')
                if versions is not None and not any(
                    item.get('version') == claim['version'] and item.get('reproductionTarget') == target
                    for item in versions
                ):
                    raise ValueError('复现目标与已登记主张版本不一致')
            except (ValueError, TypeError, KeyError) as exc:
                raise DomainError('invalid_tool', str(exc)) from exc
            metric = target['metric']
            conditions = protocol.get('conditions')
            if (metric not in protocol['metrics'] or protocol['outputSchema'].get(metric) not in ('number', 'integer')
                    or ('metric' in protocol and protocol['metric'] != metric)
                    or not isinstance(conditions, str) or not conditions.strip() or conditions != target['conditions']):
                raise DomainError('invalid_tool', '复现协议的指标、输出结构与非空 conditions 必须绑定已登记目标')
        return {
            'claimId': claim_id, 'claimVersion': claim['version'],
            'reproductionTarget': copy.deepcopy(target), 'conditions': copy.deepcopy(protocol.get('conditions')),
        }

    def _verify_receipt(self, run, result):
        """Only reuse run-contained receipts whose referenced file hashes still match."""
        try:
            root = reject_links(self.root / safe_relative(result.get('artifactRoot'))).resolve()
            if not root.is_relative_to(self.root / run['runId']):
                raise ValueError('凭据产物根目录不属于本 Run')
            hashes = []
            for path_key, hash_key in (('script', 'scriptSha256'), ('environmentPath', 'environmentSha256')):
                if result.get(hash_key) is not None:
                    hashes.append((result.get(path_key), result[hash_key]))
            for item in result.get('artifacts', []) + result.get('inputs', []):
                if item.get('sha256') is not None:
                    hashes.append((item.get('path'), item['sha256']))
            for item in result.get('evidence', []):
                if item.get('sha256') is not None:
                    hashes.append((item.get('locator'), item['sha256']))
            checkpoint = result.get('checkpoint') or {}
            if checkpoint.get('sha256') is not None:
                hashes.append((checkpoint.get('path'), checkpoint['sha256']))
            provenance = (result.get('measurements') or {}).get('_provenance', {})
            hashes.extend(provenance.get('artifactHashes', {}).items())
            for relative, expected in hashes:
                path = reject_links(root / safe_relative(relative))
                if not path.resolve().is_relative_to(root) or not path.is_file():
                    raise ValueError('凭据文件缺失或路径越界')
                hasher = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        hasher.update(chunk)
                if hasher.hexdigest() != expected:
                    raise ValueError('凭据文件哈希不匹配：' + relative)
        except (ValueError, TypeError, OSError) as exc:
            raise DomainError('invalid_receipt', '拒绝复用未通过校验的工具回执：' + str(exc), 409) from exc

    def _paper(self, run, node, name, args):
        from .sources import prepare_source
        from .library import Library
        from .tools import ResearchTools
        from .fulltext import read_pdf_evidence

        # Every Run owns its source manifest. No old Library state silently enters a new contract.
        target = self.root / run['runId'] / 'source'
        source = prepare_source(
            self.source, target, run['runId'], run['contract']['question'][:80],
            run['contract']['question'], import_existing=False,
        )
        library = Library(source)
        if name == 'paper_search':
            query = args.get('query') or next(
                objective['description'] for objective in run['contract']['scope']['objectives']
                if objective['id'] == node['objectiveId']
            )
            if not isinstance(query, str) or not 1 <= len(query) <= 1000:
                raise DomainError('invalid_query', '检索式无效')
            limit = args.get('limit', 5)
            if type(limit) is not int or not 1 <= limit <= 10:
                raise DomainError('invalid_query', '检索数量无效')
            result = library.retrieve(
                query, limit, search_settings=run['settings'].get('search'),
                search_keys=self.settings.search_credentials() if run['contract']['executionPolicy']['allowCredentials'] else {},
            )
            return {'library': result.get('library', library.load()), 'retrieval': result.get('retrieval', {})}
        if name == 'paper_download':
            from .downloads import ensure_paper_pdf
            result = ensure_paper_pdf(library, str(args.get('paperId')))
            return {'download': result, 'fullText': read_pdf_evidence(library, str(args.get('paperId')), 1, 3)}
        return ResearchTools(
            library.load(), read_pdf=lambda pid, start, count: read_pdf_evidence(library, pid, start, count),
        ).call(name, args)
