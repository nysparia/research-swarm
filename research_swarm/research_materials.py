"""Explicit, immutable task-local input intake. No implicit host-file reads."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from .local_tools import LocalResearchTools
from .process_scope import ProcessScope

MAX_BYTES = 50 * 1024 * 1024


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Expected a task-local relative path')
    parts = value.split('/')
    if any(part in ('', '.', '..') or re.search(r'[<>"|?*\x00-\x1f]', part)
           or part.endswith((' ', '.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I) for part in parts):
        raise ValueError('Unsafe relative path')
    return Path(*parts)


def reject_links(path):
    """Check the spelling before resolve; reject junctions as well as symlinks."""
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if item.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Symlinks and reparse points are not managed inputs')
    return path


def atomic_json(path, value):
    path = Path(path).absolute()
    # The randomized sibling can cross MAX_PATH even when the final path does
    # not. Use Windows' extended spelling only for these local file operations.
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        spelling = str(path)
        path = Path('\\\\?\\UNC\\' + spelling[2:] if spelling.startswith('\\\\') else '\\\\?\\' + spelling)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.pending')
    try:
        with temporary.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class ResearchMaterials:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.directory = self.root / 'materials'
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def add(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'name', 'text', 'base64', 'sourcePath', 'repository'}:
            raise ValueError('Unsupported material fields; credentials are never stored')
        sources = [key for key in ('text', 'base64', 'sourcePath', 'repository') if key in payload]
        if len(sources) != 1:
            raise ValueError('Choose one explicit material source')
        source_type = sources[0]
        name = payload.get('name', 'repository.tar' if source_type == 'repository' else 'material.txt')
        relative = safe_relative(name)
        if len(relative.parts) != 1 or len(name) > 200:
            raise ValueError('Material name must be a filename')
        if name.casefold() == 'metadata.json':
            raise ValueError('metadata.json is reserved for the immutable material manifest; choose another input filename')
        if source_type == 'text':
            if not isinstance(payload['text'], str):
                raise ValueError('Material text must be a string')
            data = payload['text'].encode('utf-8')
            source = {'type': 'text', 'originalName': name}
        elif source_type == 'base64':
            encoded = payload['base64']
            if not isinstance(encoded, str) or len(encoded) > (MAX_BYTES + 2) // 3 * 4:
                raise ValueError('Encoded material exceeds size limit')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError('Invalid base64 material') from exc
            source = {'type': 'upload', 'originalName': name}
        elif source_type == 'sourcePath':
            spelling = payload['sourcePath']
            if not isinstance(spelling, str) or not Path(spelling).is_absolute() or '..' in Path(spelling).parts:
                raise ValueError('Explicit sourcePath must be an absolute file path without traversal')
            path = reject_links(Path(spelling))
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                raise ValueError('Source must be a regular file within size limit')
            data = path.read_bytes()
            source = {'type': 'sourcePath', 'sourcePath': str(path), 'originalName': path.name}
        else:
            data, source = self._repository(payload['repository'])
        if len(data) > MAX_BYTES:
            raise ValueError('Material exceeds 50 MiB limit')
        material_id = 'material-' + uuid.uuid4().hex
        with self._lock:
            directory = self.directory / material_id
            directory.mkdir()
            path = directory / name
            path.write_bytes(data)
            item = {'id': material_id, 'revision': 1, 'name': name, 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(), 'source': source,
                    'createdAt': datetime.now(timezone.utc).isoformat(),
                    'path': path.relative_to(self.root).as_posix()}
            atomic_json(directory / 'metadata.json', item)
            return item

    def get(self, material_id):
        if not isinstance(material_id, str) or not re.fullmatch(r'material-[0-9a-f]{32}', material_id):
            raise ValueError('Unknown managed material')
        with self._lock:
            path = self.directory / material_id / 'metadata.json'
            reject_links(path)
            if not path.is_file():
                raise ValueError('Unknown managed material')
            return json.loads(path.read_text('utf-8'))

    def list(self):
        with self._lock:
            return sorted((self.get(path.parent.name) for path in self.directory.glob('material-*/metadata.json')), key=lambda item: item['createdAt'])

    def path(self, material_id):
        item = self.get(material_id)
        path = reject_links(self.root / safe_relative(item['path']))
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise ValueError('Invalid managed material file')
        if path.stat().st_size != item['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Managed material hash changed')
        return path

    def _repository(self, repository):
        if not isinstance(repository, dict) or set(repository) != {'url', 'commit'}:
            raise ValueError('Repository requires only url and pinned commit')
        url, commit = repository['url'], repository['commit']
        if not isinstance(url, str) or len(url) > 2000 or not isinstance(commit, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
            raise ValueError('Repository must use a pinned full commit hash')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or re.search(r'[\s\x00-\x1f]', url):
            raise ValueError('Use a credential-free HTTPS repository URL')
        git = shutil.which('git')
        if not git:
            raise ValueError('Git is unavailable')
        with tempfile.TemporaryDirectory(dir=self.root, prefix='repository-intake-') as temporary:
            directory = Path(temporary)
            tools = LocalResearchTools(directory)
            env = tools._env(directory)
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='', SSH_ASKPASS='')
            archive = directory / 'repository.tar'
            commands = ([git, '-c', 'credential.helper=', '-c', 'core.hooksPath=' + os.devnull, 'init', '--bare', str(directory / 'repo')],
                        [git, '-c', 'credential.helper=', '-c', 'protocol.file.allow=never', '-C', str(directory / 'repo'), 'fetch', '--depth=1', '--no-tags', url, commit],
                        [git, '-C', str(directory / 'repo'), 'archive', '--format=tar', '--output=' + str(archive), commit])
            for argv in commands:
                with (directory / 'stdout').open('wb') as stdout, (directory / 'stderr').open('wb') as stderr:
                    scope = ProcessScope(argv, cwd=directory, env=env, stdout=stdout, stderr=stderr)
                    started = time.monotonic()
                    try:
                        while scope.process.poll() is None:
                            if time.monotonic() - started > 120 or sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()) > 100 * 1024 * 1024:
                                raise ValueError('Repository intake exceeded time or size limit')
                            time.sleep(.1)
                    finally:
                        scope.close()
                    if scope.process.returncode:
                        raise ValueError('Pinned repository intake failed')
            # Archive remains data: no hooks, extraction or repository commands in experiments.
            import tarfile
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                for member in members:
                    safe_relative(member.name.rstrip('/'))
                    if not (member.isfile() or member.isdir()):
                        raise ValueError('Repository contains symlinks or unsupported files')
                if sum(member.size for member in members) > MAX_BYTES:
                    raise ValueError('Repository exceeds material size limit')
            return archive.read_bytes(), {'type': 'repository', 'url': url, 'commit': commit.lower(), 'format': 'tar'}
