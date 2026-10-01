"""Local provider settings and bounded structured model requests."""
from __future__ import annotations

import copy
import contextvars
from contextlib import contextmanager
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .search_settings import KEY_ENVS, PROFILES, normalize_search, update_search


class ModelOutputError(ValueError):
    """A recoverable malformed or incomplete provider response."""


class ModelOutputTruncated(ModelOutputError):
    """A completion reached its configured output budget."""


class ModelConnectionError(RuntimeError):
    """A temporary transport failure, never an authentication failure."""


def parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, count=1).rsplit('```', 1)[0].strip()
    try:
        def reject_constant(value):
            raise ValueError('JSON 中不能包含非有限数值')
        result = json.loads(text, parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise ModelOutputError('模型未返回有效 JSON；本次结果未写入，可查看日志后重试') from exc
    if not isinstance(result, dict):
        raise ModelOutputError('模型输出必须是 JSON 对象')
    return result


class Settings:
    ROLES = ('main', 'judge', 'redteam')
    ROLE_NAMES = {'main': '主研究', 'judge': '证据裁判', 'redteam': '对抗复核'}
    EMPTY_PROVIDER = {'type': 'openai', 'baseUrl': '', 'model': '', 'apiKey': '', 'apiKeyEnv': ''}

    def __init__(self, state_dir: Path, source: Path | None):
        self.path = Path(state_dir) / 'config.local.json'
        self.source = Path(source) if source else None
        self.lock = threading.RLock()
        self.usage_path = Path(state_dir) / 'provider-usage.sqlite'
        self._usage_context = contextvars.ContextVar('provider_usage_context', default=(None, None))
        self.data = {'mode': 'evidence', 'provider': {'type': 'openai', 'baseUrl': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'apiKey': '', 'apiKeyEnv': 'DEEPSEEK_API_KEY'}}
        if self.source and (self.source / 'config.json').is_file():
            cfg = json.loads((self.source / 'config.json').read_text(encoding='utf-8-sig'))
            llm = cfg.get('llm', {})
            p = llm.get('providers', {}).get(llm.get('active_provider'), {})
            if p:
                self.data['provider'] = {'type': p.get('type', 'openai'), 'baseUrl': p.get('base_url', ''), 'model': llm.get('active_model') or next(iter(p.get('models', [])), ''), 'apiKey': p.get('api_key', ''), 'apiKeyEnv': p.get('api_key_env', '')}
        if self.path.is_file():
            self.data.update(json.loads(self.path.read_text(encoding='utf-8')))
        self.data = self._normalize(self.data)

    @classmethod
    def _normalize(cls, data: dict) -> dict:
        """Migrate the legacy main slot without copying its credentials to reviewers."""
        data = copy.deepcopy(data)
        configured = data.get('providers', {})
        if not isinstance(configured, dict) or any(role not in cls.ROLES for role in configured):
            raise ValueError('模型角色必须为 main、judge 或 redteam')
        providers = {}
        for role in cls.ROLES:
            fields = configured.get(role, data.get('provider', {}) if role == 'main' else {})
            if fields is None:
                fields = {}
            if not isinstance(fields, dict):
                raise ValueError('模型角色配置必须为对象')
            providers[role] = dict(cls.EMPTY_PROVIDER, **fields)
        data['providers'] = providers
        data.setdefault('reviewPolicy', 'independent')
        if data['reviewPolicy'] not in ('independent', 'shared'):
            raise ValueError('reviewPolicy 必须为 independent 或 shared')
        # Retain a shared main-slot alias for existing callers and on-disk readers.
        data['provider'] = providers['main']
        data['search'] = normalize_search(data.get('search'))
        keys = data.setdefault('searchKeys', {})
        if not isinstance(keys, dict) or set(keys) - set(KEY_ENVS) or any(k is not None and not isinstance(k, str) for k in keys.values()):
            raise ValueError('论文源密钥配置无效')
        return data

    @classmethod
    def _validate_role(cls, role: str) -> str:
        if not isinstance(role, str) or role not in cls.ROLES:
            raise ValueError('模型角色必须为 main、judge 或 redteam')
        return role

    @staticmethod
    def _identity(provider: dict) -> dict:
        parsed = urllib.parse.urlsplit(provider.get('baseUrl', ''))
        # Hostname/scheme case and a trailing slash do not create independence.
        host = (parsed.hostname or '').lower()
        if host in ('127.0.0.1', 'localhost', '::1'):
            host = 'localhost'
        if ':' in host:
            host = '[' + host + ']'
        port = parsed.port
        if port and (parsed.scheme.lower(), port) not in (('https', 443), ('http', 80)):
            host += ':' + str(port)
        endpoint = urllib.parse.urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip('/'), '', ''))
        return {'type': provider.get('type', ''), 'baseUrl': endpoint, 'model': provider.get('model', '')}

    @classmethod
    def _different_identity(cls, first: dict, second: dict) -> bool:
        a, b = cls._identity(first), cls._identity(second)
        return (a['baseUrl'], a['model']) != (b['baseUrl'], b['model'])

    def _key(self, provider: dict | None = None) -> str:
        p = provider if provider is not None else self.data['provider']
        return p.get('apiKey', '') or os.getenv(p.get('apiKeyEnv', ''), '')

    def _effective_provider(self, role):
        provider = self.data['providers'][role]
        if (role != 'main' and self.data['reviewPolicy'] == 'shared'
                and not provider.get('baseUrl') and not provider.get('model')):
            return self.data['providers']['main']
        return provider

    def role_status(self, role: str = 'main') -> dict:
        """Describe configured routing; readiness is not a live connectivity check."""
        self._validate_role(role)
        with self.lock:
            p = self._effective_provider(role)
            main = self.data['providers']['main']
            local = urllib.parse.urlparse(p.get('baseUrl', '')).hostname in ('127.0.0.1', 'localhost', '::1')
            configured = bool(p.get('model') and p.get('baseUrl'))
            return {k: p.get(k, '') for k in ('type', 'baseUrl', 'model')} | {
                'hasKey': bool(self._key(p)), 'configured': configured,
                'ready': bool(configured and (local or self._key(p))),
                'identity': self._identity(p), 'local': local,
                'reviewPolicy': self.data['reviewPolicy'],
                'sharedWithMain': role != 'main' and p is main,
                'independentFromMain': bool(role != 'main' and configured and main.get('model')
                                             and main.get('baseUrl') and self._different_identity(p, main)),
            }

    def public(self) -> dict:
        with self.lock:
            providers = {role: self.role_status(role) for role in self.ROLES}
            review_ready = all(providers[role]['ready'] for role in self.ROLES)
            distinct = all(providers[role]['independentFromMain'] for role in ('judge', 'redteam'))
            distinct = distinct and self._different_identity(self.data['providers']['judge'], self.data['providers']['redteam'])
            return {'mode': self.data['mode'], 'reviewPolicy': self.data['reviewPolicy'], 'provider': copy.deepcopy(providers['main']), 'providers': providers,
                    'search': copy.deepcopy(self.data['search']),
                    'searchProfiles': copy.deepcopy(PROFILES),
                    'searchKeys': {source: {'hasKey': bool(key)} for source, key in self.search_credentials().items()},
                    'sourcePath': str(self.source or ''),
                    'presets': [{'id': 'ollama', 'label': 'Ollama 本机接口', 'type': 'openai', 'baseUrl': 'http://127.0.0.1:11434/v1'},
                                {'id': 'lm-studio', 'label': 'LM Studio 本机接口', 'type': 'openai', 'baseUrl': 'http://127.0.0.1:1234/v1'}],
                    'capabilities': {'modelReady': providers['main']['ready'], 'evidenceReady': True,
                                     'roleReady': {role: providers[role]['ready'] for role in self.ROLES},
                                     'independentReviewReady': bool(review_ready and distinct),
                                     'reviewReady': bool(review_ready and (distinct or self.data['reviewPolicy'] == 'shared')),
                                     'independenceBasis': 'configured_endpoint_and_model',
                                     'independenceNotice': '仅比较已配置的端点和模型名称，不保证不同厂商、模型族或统计独立性。',
                                     'reproductionScope': 'preflight_and_small_experiments',
                                     'executionLimits': {'defaultTimeoutSeconds': 90, 'maxTimeoutSeconds': 180,
                                                         'installTimeoutSeconds': 300, 'localParallel': 1,
                                                         'resume': 'restart_node', 'gpuConfigured': False,
                                                         'costEstimateAvailable': False},
                                     'tools': ['paper_search', 'paper_read', 'paper_retrieve', 'evidence_lookup', 'facet_read', 'experiment_statistics', 'local_environment', 'python_install', 'python_run', 'artifact_read'],
                                     'experimentalExecution': '本课题独立 Python 环境：环境探测、科研依赖安装、实际脚本执行、超时与暂停、原始日志和产物凭据',
                                     'sourceRetrieval': bool(self.source), 'dsh': False}}

    @classmethod
    def _validate_provider(cls, provider: dict, role: str):
        p = provider
        if p['type'] not in ('openai', 'anthropic'):
            raise ValueError('不支持的模型接口类型')
        # A secondary slot can be explicitly unconfigured; it never inherits main.
        if role != 'main' and not p['baseUrl'] and not p['model']:
            return
        parsed = urllib.parse.urlparse(p['baseUrl'])
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('模型地址不得含凭据、查询参数或片段')
        if not parsed.hostname or not (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('远程模型地址须使用 HTTPS；本机服务允许 HTTP')
        try:
            parsed.port
        except ValueError:
            raise ValueError('模型地址端口无效') from None
        p['baseUrl'] = p['baseUrl'].rstrip('/')
        if not p.get('model'):
            raise ValueError('请填写模型名称')

    def update(self, payload: dict) -> dict:
        with self.lock:
            if not isinstance(payload, dict):
                raise ValueError('运行设置必须为对象')
            data = copy.deepcopy(self.data)
            if 'reviewPolicy' in payload:
                if payload['reviewPolicy'] not in ('independent', 'shared'):
                    raise ValueError('reviewPolicy 必须为 independent 或 shared')
                data['reviewPolicy'] = payload['reviewPolicy']
            if 'search' in payload:
                data['search'] = update_search(data.get('search'), payload['search'])
            if 'searchKeys' in payload:
                keys = payload['searchKeys']
                if not isinstance(keys, dict) or set(keys) - set(KEY_ENVS):
                    raise ValueError('论文源密钥配置无效')
                for source, value in keys.items():
                    if value is None:
                        data['searchKeys'][source] = None  # Explicitly disable environment fallback.
                    elif not isinstance(value, str):
                        raise ValueError('论文源密钥须为文本')
                    elif value.strip():
                        data['searchKeys'][source] = value.strip()
            if 'mode' in payload:
                if payload['mode'] not in ('evidence', 'llm'):
                    raise ValueError('运行模式必须为 evidence 或 llm')
                data['mode'] = payload['mode']
            changes = payload.get('providers', {})
            if not isinstance(changes, dict):
                raise ValueError('模型角色配置必须为对象')
            changes = dict(changes)
            if 'provider' in payload:
                legacy = payload['provider']
                if not isinstance(legacy, dict):
                    raise ValueError('模型配置必须为对象')
                main = changes.get('main', {})
                if not isinstance(main, dict):
                    raise ValueError('主研究模型配置必须为对象')
                changes['main'] = dict(legacy, **main)
            for role, fields in changes.items():
                self._validate_role(role)
                if fields is None and role != 'main':
                    data['providers'][role] = dict(self.EMPTY_PROVIDER)
                    continue
                if not isinstance(fields, dict):
                    raise ValueError('模型角色配置必须为对象')
                p = data['providers'][role]
                for key in ('type', 'baseUrl', 'model', 'apiKey', 'apiKeyEnv'):
                    if key in fields:
                        if not isinstance(fields[key], str):
                            raise ValueError('模型配置字段必须为文本')
                        # An empty password preserves only this role's existing secret.
                        if key != 'apiKey' or fields[key].strip():
                            p[key] = fields[key].strip()
                if fields.get('clearKey'):
                    p['apiKey'] = ''
                    p['apiKeyEnv'] = ''
                self._validate_provider(p, role)
            data['provider'] = data['providers']['main']
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.data = data
            return self.public()

    def configure_deepseek(self, payload: dict) -> dict:
        """Explicit opt-in to one-key role separation, preserving the main secret."""
        if not isinstance(payload, dict) or set(payload) - {'apiKey', 'model'}:
            raise ValueError('DeepSeek 设置只接受 apiKey 和 model；使用官方地址')
        with self.lock:
            provider = {'type': 'openai', 'baseUrl': 'https://api.deepseek.com'}
            for field in ('apiKey', 'model'):
                if field in payload:
                    if not isinstance(payload[field], str):
                        raise ValueError('DeepSeek 设置字段必须为文本')
                    if payload[field].strip():
                        provider[field] = payload[field].strip()
            provider.setdefault('model', self.data['providers']['main'].get('model') or 'deepseek-chat')
            return self.update({'mode': 'llm', 'reviewPolicy': 'shared', 'provider': provider})

    @contextmanager
    def usage_context(self, task_id, node_id=None):
        for value in (task_id, node_id):
            if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
                raise ValueError('用量上下文 ID 无效')
        token = self._usage_context.set((task_id, node_id))
        try:
            yield
        finally:
            self._usage_context.reset(token)

    def _usage_database(self):
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.usage_path, timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, task_id TEXT, node_id TEXT, role TEXT, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, usage_reported INTEGER, error INTEGER)')
        return db

    def _record_usage(self, role, usage, error):
        usage = usage if isinstance(usage, dict) else {}
        def count(*names):
            for name in names:
                value = usage.get(name)
                if type(value) is int and value >= 0:
                    return value
            return None
        incoming, outgoing = count('prompt_tokens', 'input_tokens'), count('completion_tokens', 'output_tokens')
        total = count('total_tokens')
        if total is None and incoming is not None and outgoing is not None:
            total = incoming + outgoing
        task_id, node_id = self._usage_context.get()
        with self.lock:
            db = self._usage_database()
            try:
                with db:
                    db.execute('INSERT INTO requests (task_id,node_id,role,input_tokens,output_tokens,total_tokens,usage_reported,error) VALUES (?,?,?,?,?,?,?,?)',
                               (task_id, node_id, role, incoming, outgoing, total, int(total is not None), int(error)))
            finally:
                db.close()

    def usage(self, task_id=None) -> dict:
        with self.lock:
            db = self._usage_database()
            try:
                where, args = (' WHERE task_id = ?', (task_id,)) if task_id is not None else ('', ())
                row = db.execute('SELECT COUNT(*),COALESCE(SUM(error),0),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(total_tokens),0),COALESCE(SUM(usage_reported),0) FROM requests' + where, args).fetchone()
                roles = {role: {'calls': calls, 'errors': errors, 'totalTokens': tokens} for role, calls, errors, tokens in
                         db.execute('SELECT role,COUNT(*),SUM(error),COALESCE(SUM(total_tokens),0) FROM requests' + where + ' GROUP BY role', args)}
            finally:
                db.close()
        return {'taskId': task_id, 'calls': row[0], 'errors': row[1], 'inputTokens': row[2], 'outputTokens': row[3],
                'totalTokens': row[4], 'usageReportedCalls': row[5], 'usageMissingCalls': row[0] - row[5],
                'roles': roles, 'billedCurrency': None, 'billedAmount': None, 'costEstimateAvailable': False}

    def safe_error(self, error: Exception) -> str:
        text = str(error)
        with self.lock:
            secrets = {key for provider in self.data['providers'].values()
                       for key in (self._key(provider), provider.get('apiKey', ''), os.getenv(provider.get('apiKeyEnv', ''), '')) if key}
            secrets.update(key for key in self.search_credentials().values() if key)
            for key in sorted(secrets, key=len, reverse=True):
                text = text.replace(key, '[已隐藏]')
        return re.sub(r'(?i)(bearer\s+|api[_-]?key[=: ]+)[^\s,;]+', r'\1[已隐藏]', text)[:600]

    def search_credentials(self):
        with self.lock:
            configured = self.data.get('searchKeys', {})
            return {source: (configured[source] or '') if source in configured else os.getenv(env, '')
                    for source, env in KEY_ENVS.items()}

    def restore(self, previous: dict):
        """Restore an already validated local configuration after a rejected transaction."""
        with self.lock:
            temporary = self.path.with_suffix('.tmp')
            previous = self._normalize(previous)
            temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
            self.data = copy.deepcopy(previous)

    def chat(self, messages: list[dict], max_tokens: int = 5500, *, json_mode=False, on_retry=None, role='main') -> str:
        self._validate_role(role)
        messages = copy.deepcopy(messages)
        repaired_output, retried_connection = False, False
        # A transport retry must not spend the one JSON-format repair (or vice versa).
        # Each failure class gets one retry, for at most three requests in total.
        for attempt in range(3 if json_mode else 1):
            try:
                text = self._chat_once(messages, max_tokens, json_mode=json_mode, role=role)
                if json_mode:
                    parse_json_object(text)
                return text
            except ModelOutputError as exc:
                if not json_mode or repaired_output:
                    raise
                repaired_output = True
                if on_retry:
                    on_retry('模型响应格式不完整，正在自动重试 1/1；尚未写入研究结果。')
                if isinstance(exc, ModelOutputTruncated):
                    max_tokens = min(16000, max_tokens * 2)
                messages.append({'role': 'user', 'content': '上一响应没有形成可解析的 JSON 对象。请按原定结构重新输出，压缩长文本，最多 8 条 claims；只返回一个完整 JSON 对象，不含代码围栏、说明前缀或其他文本。不要为了格式捏造证据。'})
            except ModelConnectionError:
                if not json_mode or retried_connection:
                    raise
                retried_connection = True
                if on_retry:
                    on_retry('模型连接暂时失败，正在重试 1/1；已完成节点保持不变。')

    def _chat_once(self, messages: list[dict], max_tokens: int, *, json_mode=False, role='main') -> str:
        telemetry = {}
        try:
            value = self._request_once(messages, max_tokens, json_mode=json_mode, role=role, telemetry=telemetry)
        except Exception:
            if telemetry.get('attempted'):
                self._record_usage(role, telemetry.get('usage'), True)
            raise
        self._record_usage(role, telemetry.get('usage'), False)
        return value

    def _request_once(self, messages, max_tokens, *, json_mode=False, role='main', telemetry):
        self._validate_role(role)
        with self.lock:
            p = copy.deepcopy(self._effective_provider(role))
            key = self._key(p)
        if not p.get('model') or not p.get('baseUrl'):
            raise ValueError(f'未配置{self.ROLE_NAMES[role]}模型（{role}）；请在运行设置中单独配置，不能自动使用主研究模型代替')
        local = urllib.parse.urlparse(p['baseUrl']).hostname in ('127.0.0.1', 'localhost', '::1')
        if not key and not local:
            raise ValueError(f'未配置{self.ROLE_NAMES[role]}模型密钥，请在运行设置中填写；或切换为已有数据核验')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if p['type'] == 'anthropic':
            body = {'model': p['model'], 'max_tokens': max_tokens, 'temperature': 0.2, 'system': '\n\n'.join(m['content'] for m in messages if m['role'] == 'system'), 'messages': [m for m in messages if m['role'] != 'system']}
            headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
            endpoint = p['baseUrl'] + '/messages'
        else:
            body = {'model': p['model'], 'max_tokens': max_tokens, 'temperature': 0.2, 'messages': messages}
            if json_mode:
                body['response_format'] = {'type': 'json_object'}
            if urllib.parse.urlparse(p['baseUrl']).hostname == 'api.deepseek.com':
                body['thinking'] = {'type': 'disabled'}
            headers['Authorization'] = 'Bearer ' + (key or 'local')
            endpoint = p['baseUrl'] + '/chat/completions'
        request = urllib.request.Request(endpoint, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
        telemetry['attempted'] = True
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError('模型响应超出 4 MB 限制')
                result = json.loads(raw.decode('utf-8'))
                telemetry['usage'] = result.get('usage')
        except urllib.error.HTTPError as exc:
            if exc.code in (408, 429, 500, 502, 503, 504):
                raise ModelConnectionError(f'模型服务暂时不可用（HTTP {exc.code}），可稍后继续') from None
            raise RuntimeError(f'模型接口返回 HTTP {exc.code}；请检查地址、模型和额度') from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ModelConnectionError('模型连接失败或超时：' + self.safe_error(exc)) from None
        if p['type'] == 'anthropic':
            text = ''.join(part.get('text', '') for part in result.get('content', []) if part.get('type') == 'text')
        else:
            choices = result.get('choices', [])
            if choices and choices[0].get('finish_reason') == 'length':
                raise ModelOutputTruncated('模型输出被长度限制截断，请重试或缩小单节点范围')
            text = (choices[0].get('message', {}).get('content') if choices else '') or ''
        if not isinstance(text, str) or not text.strip():
            raise ModelOutputError('模型返回空内容')
        if json_mode:
            parse_json_object(text)
        return text
