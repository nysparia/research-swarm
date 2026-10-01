"""Pure, bounded notebook editing with stable section identities."""
from __future__ import annotations

import copy
import re
import uuid

MAX_BLOCKS = 200
MAX_TEXT = 200_000
FIELDS = {'id', 'kind', 'title', 'content', 'locked', 'source'}


def _validate(blocks):
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        raise ValueError('Draft must contain at most 200 blocks')
    ids, total = set(), 0
    for block in blocks:
        if not isinstance(block, dict) or set(block) - FIELDS:
            raise ValueError('Unknown draft block fields')
        for key in ('id', 'kind', 'title', 'content', 'source'):
            if not isinstance(block.get(key), str):
                raise ValueError('Draft block fields must be strings')
        if not block['id'] or len(block['id']) > 200 or block['id'] in ids:
            raise ValueError('Draft block IDs must be unique')
        if not isinstance(block.get('locked'), bool):
            raise ValueError('Block locked must be boolean')
        if len(block['title']) > 500 or len(block['kind']) > 80 or len(block['source']) > 80:
            raise ValueError('Draft block metadata exceeds limits')
        total += len(block['content'])
        ids.add(block['id'])
    if total > MAX_TEXT:
        raise ValueError('Draft exceeds text limit')


def _sections(markdown):
    chunks, lines, title, fence = [], [], '', None
    for line in markdown.replace('\r\n', '\n').splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        heading = None if fence else re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$', line)
        if heading and lines and '\n'.join(lines).strip():
            chunks.append((title, '\n'.join(lines).strip('\n')))
            lines = []
        if heading:
            title = heading.group(1)
        lines.append(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line.strip().strip(token[0]).strip():
                fence = None
    if '\n'.join(lines).strip():
        chunks.append((title, '\n'.join(lines).strip('\n')))
    return chunks


def reconcile_blocks(markdown, previous=None, actor='AI'):
    """Reconcile whole Markdown; an AI cannot replace or drop locked clauses."""
    if not isinstance(markdown, str) or len(markdown) > MAX_TEXT:
        raise ValueError('Draft Markdown must be bounded text')
    previous = copy.deepcopy(previous or [])
    _validate(previous)
    ai = str(actor).lower() == 'ai'
    used, result = set(), []
    sections = _sections(markdown)
    # Reserve exact matches before title matching so duplicate headings move safely.
    exact = {}
    for index, (_, content) in enumerate(sections):
        match = next((b for b in previous if b['id'] not in used and b['content'] == content), None)
        if match:
            exact[index] = match
            used.add(match['id'])
    for index, (title, content) in enumerate(sections):
        match = exact.get(index)
        if match is None:
            match = next((b for b in previous if b['id'] not in used and b['title'] == title), None)
        if match:
            used.add(match['id'])
            block = copy.deepcopy(match)
            if not (ai and block['locked']):
                block.update(title=title, content=content)
                if content != match['content']:
                    block['source'] = str(actor).lower()
                    if not ai:
                        block['locked'] = True
        else:
            block = {'id': 'block:' + uuid.uuid4().hex, 'kind': 'section' if title else 'text',
                     'title': title, 'content': content, 'locked': not ai, 'source': str(actor).lower()}
        result.append(block)
    for index, block in enumerate(previous):
        if ai and block['locked'] and block['id'] not in used:
            result.insert(min(index, len(result)), copy.deepcopy(block))
    _validate(result)
    return result


def patch_blocks(blocks, operations):
    """Apply an atomic user edit. Updated clauses become protected by default."""
    _validate(blocks)
    if not isinstance(operations, list) or len(operations) > MAX_BLOCKS:
        raise ValueError('Draft operations must be a bounded list')
    result = copy.deepcopy(blocks)
    for operation in operations:
        if not isinstance(operation, dict):
            raise ValueError('Invalid draft operation')
        op = operation.get('op')
        if op == 'add':
            if set(operation) - {'op', 'block', 'afterId'} or not isinstance(operation.get('block'), dict):
                raise ValueError('Unknown add fields')
            block = dict(operation['block'])
            block.setdefault('id', 'block:' + uuid.uuid4().hex)
            for key, default in (('kind', 'section'), ('title', ''), ('content', ''), ('locked', True), ('source', 'user')):
                block.setdefault(key, default)
            if 'afterId' in operation and operation['afterId'] is not None:
                index = next((i for i, b in enumerate(result) if b['id'] == operation['afterId']), None)
                if index is None:
                    raise ValueError('Unknown insertion anchor')
                result.insert(index + 1, block)
            elif 'afterId' in operation:
                result.insert(0, block)
            else:
                result.append(block)
        elif op in ('update', 'remove'):
            allowed = {'op', 'id', 'changes'} | (FIELDS - {'id'}) if op == 'update' else {'op', 'id'}
            if set(operation) - allowed:
                raise ValueError('Unknown operation fields')
            index = next((i for i, b in enumerate(result) if b['id'] == operation.get('id')), None)
            if index is None:
                raise ValueError('Unknown draft block')
            if op == 'remove':
                result.pop(index)
            else:
                changes = operation.get('changes', {k: v for k, v in operation.items() if k not in ('op', 'id')})
                if not isinstance(changes, dict) or set(changes) - (FIELDS - {'id'}):
                    raise ValueError('Unknown update fields')
                if 'changes' in operation and set(operation) - {'op', 'id', 'changes'}:
                    raise ValueError('Use changes or direct fields, not both')
                changes = dict(changes)
                changes.setdefault('source', 'user')
                changes.setdefault('locked', True)
                result[index].update(changes)
        else:
            raise ValueError('Unknown draft operation')
        _validate(result)
    return result


def render_blocks(blocks):
    _validate(blocks)
    return '\n\n'.join(b['content'].strip('\n') for b in blocks if b['content'].strip())
