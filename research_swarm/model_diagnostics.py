"""Local, redacted model attempts. Diagnostic records are never evidence."""
import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .output_protocol import validation_issues


class ModelDiagnostics:
    def __init__(self, root, node, context, settings):
        self.settings = settings
        self.callback = context.get('record_diagnostic')
        token = str(context.get('executionToken') or 'diagnostic:' + uuid.uuid4().hex)
        # Digest avoids collisions between tokens with different punctuation.
        self.relative = 'diagnostics/' + hashlib.sha256(token.encode()).hexdigest() + '.json'
        self.path = Path(root) / self.relative
        self.record = {'executionToken': token, 'nodeId': node.get('id'), 'nodeVersion': node.get('version'),
                       'phase': node.get('phase'), 'researchStep': node.get('input', {}).get('researchStep'),
                       'eligibleAsEvidence': False, 'attempts': [], 'startedAt': datetime.now(timezone.utc).isoformat()}
        self.secrets = set()
        data = getattr(settings, 'data', {})
        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() in ('apikey', 'api_key', 'authorization', 'token', 'password') and isinstance(item, str) and item:
                        self.secrets.add(item)
                    if key.lower() in ('apikeyenv', 'api_key_env') and isinstance(item, str) and os.getenv(item):
                        self.secrets.add(os.environ[item])
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(data)
        for method in ('search_credentials', 'concept_search_credential'):
            if hasattr(settings, method):
                values = getattr(settings, method)()
                self.secrets.update(v for v in (values.values() if isinstance(values, dict) else [values]) if isinstance(v, str) and v)
        for provider in data.get('providers', {}).values():
            if hasattr(settings, '_key'):
                key = settings._key(provider)
                if key:
                    self.secrets.add(key)

    def redact(self, value):
        if isinstance(value, dict):
            return {k: ('[REDACTED]' if k.lower() in ('apikey', 'api_key', 'authorization', 'headers', 'password', 'access_token') else self.redact(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            value = re.sub(r'(?i)(bearer\s+)[^\s"\',;]+', r'\1[REDACTED]', value)
            value = re.sub(r'(?i)((?:api[_-]?key|access_token|password)[\\"\s]*[:=][\\"\s]*)[^\\\s"\',;&}]+', r'\1[REDACTED]', value)
            value = re.sub(r'\b(?:sk-|tvly-)[A-Za-z0-9_-]+', '[REDACTED]', value)
            return value
        return value

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.redact(self.record), ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def chat(self, messages, **kwargs):
        role = kwargs.get('role', 'main')
        identity = {}
        if hasattr(self.settings, 'role_status'):
            identity = self.settings.role_status(role).get('identity') or {}
        attempt = {'number': len(self.record['attempts']) + 1, 'role': role, 'model': identity,
                   'messages': copy.deepcopy(messages), 'maxTokens': kwargs.get('max_tokens'), 'status': 'requested'}
        self.record['attempts'].append(attempt)
        self.save()
        try:
            raw = self.settings.chat(messages, **kwargs)
        except Exception as error:
            attempt.update(status='provider_failed', error=str(error))
            self.save()
            raise
        attempt.update(response=raw, status='returned')
        self.save()
        return raw

    def validate(self, issues=None):
        if self.record['attempts']:
            self.record['attempts'][-1].update(status='invalid' if issues else 'validated', validationErrors=issues or [])
            self.save()

    def finish(self, error=None):
        self.record.update(status='failed' if error else 'completed', finishedAt=datetime.now(timezone.utc).isoformat())
        if error:
            self.record['error'] = str(error)
        self.save()
        if self.callback:
            errors = [issue for a in self.record['attempts'] for issue in a.get('validationErrors', [])]
            self.callback(self.redact({'diagnosticRef': self.relative, 'attemptCount': len(self.record['attempts']),
                                      'validationErrors': errors, 'status': self.record['status']}))


class DiagnosticSettings:
    def __init__(self, settings, diagnostics):
        self.wrapped = settings
        self.diagnostics = diagnostics

    def __getattr__(self, key):
        return getattr(self.wrapped, key)

    def chat(self, messages, **kwargs):
        return self.diagnostics.chat(messages, **kwargs)


def record_validation(settings, error=None):
    diagnostics = getattr(settings, 'diagnostics', None)
    if diagnostics:
        diagnostics.validate(validation_issues(error) if error else None)
