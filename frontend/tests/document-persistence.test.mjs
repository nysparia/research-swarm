import assert from 'node:assert/strict';
import test from 'node:test';
import { createDraftPersistence } from '../src/documentPersistence.ts';
import { draftReducer, emptyDraft } from '../src/documentState.ts';

const document = (revision, markdown) => ({ revision, markdown, source: 'local', polishing: false, polishedFrom: null, questions: [], error: null });
const task = (id, revision, markdown) => ({ task: { id }, document: document(revision, markdown) });
function fixture() {
  let state = draftReducer(emptyDraft(), { type: 'reset', taskId: 'task-a', document: document(1, '# 原始需求') });
  let composing = false;
  const calls = [], accepted = [], reloads = [];
  const update = action => { state = draftReducer(state, action); };
  const persistence = createDraftPersistence({
    current: () => state, composing: () => composing, update,
    persist: (taskId, markdown, revision) => new Promise((resolve, reject) => calls.push({ taskId, markdown, revision, resolve, reject })),
    reload: async taskId => { reloads.push(taskId); return task(taskId, 8, '# 其他客户端的版本'); },
    accept: next => accepted.push(next), isConflict: error => error.status === 409, describeError: error => error.message,
  });
  return { ...persistence, calls, accepted, reloads, update, state: () => state, compose: value => { composing = value; } };
}

test('a save acknowledgement cannot authorize navigation after newer edits arrive', async () => {
  const f = fixture();
  f.update({ type: 'edit', text: '# 第一段' });
  const saving = f.save();
  await Promise.resolve();
  f.update({ type: 'edit', text: '# 第一段\n仍在输入' });
  f.calls[0].resolve(task('task-a', 2, '# 第一段'));
  assert.equal(await saving, false);
  assert.equal(f.isCurrentSaved('task-a'), false);
  assert.equal(f.state().text, '# 第一段\n仍在输入');
  assert.equal(f.state().dirty, true);
  const retry = f.save();
  await Promise.resolve();
  assert.equal(f.calls[1].markdown, '# 第一段\n仍在输入');
  assert.equal(f.calls[1].revision, 2);
  f.calls[1].resolve(task('task-a', 3, f.calls[1].markdown));
  assert.equal(await retry, true);
  assert.equal(f.isCurrentSaved('task-a'), true);
});

test('a response for a previous task cannot authorize a transition or overwrite the current task', async () => {
  const f = fixture();
  f.update({ type: 'edit', text: '# A 的修改' });
  const saving = f.save();
  await Promise.resolve();
  f.update({ type: 'reset', taskId: 'task-b', document: document(4, '# B 的需求') });
  f.calls[0].resolve(task('task-a', 2, '# A 的修改'));
  assert.equal(await saving, false);
  assert.equal(f.isCurrentSaved('task-a'), false);
  assert.equal(f.state().text, '# B 的需求');
  assert.equal(f.accepted.length, 0);
});

test('concurrent transition checks share the pending save and all reject newer unsaved input', async () => {
  const f = fixture();
  f.update({ type: 'edit', text: '# 保存快照' });
  const first = f.save();
  const second = f.save();
  await Promise.resolve();
  assert.equal(f.calls.length, 1);
  f.update({ type: 'edit', text: '# 保存快照\n新约束' });
  f.calls[0].resolve(task('task-a', 2, '# 保存快照'));
  assert.deepEqual(await Promise.all([first, second]), [false, false]);
  assert.equal(f.calls.length, 1);
  assert.equal(f.state().text, '# 保存快照\n新约束');
});

test('a revision conflict retains local input and blocks transitions until explicitly resolved and saved', async () => {
  const f = fixture();
  f.update({ type: 'edit', text: '# 我的研究边界' });
  const saving = f.save();
  await Promise.resolve();
  f.calls[0].reject(Object.assign(new Error('需求版本已变化'), { status: 409 }));
  assert.equal(await saving, false);
  assert.equal(f.state().text, '# 我的研究边界');
  assert.equal(f.state().conflict, true);
  assert.equal(f.isCurrentSaved('task-a'), false);
  assert.deepEqual(f.reloads, ['task-a']);
});

test('IME composition blocks saves and final transition checks without blocking subsequent normal autosave', async () => {
  const f = fixture();
  f.compose(true);
  f.update({ type: 'edit', text: '# 输入中' });
  assert.equal(await f.save(), false);
  assert.equal(f.calls.length, 0);
  assert.equal(f.isCurrentSaved('task-a'), false);
  f.compose(false);
  const saving = f.save();
  await Promise.resolve();
  f.calls[0].resolve(task('task-a', 2, '# 输入中'));
  assert.equal(await saving, true);
});

test('the final saved check catches edits made after the last save before start commits', async () => {
  const f = fixture();
  assert.equal(f.isCurrentSaved('task-a'), true);
  f.update({ type: 'edit', text: '# 获取最新版本期间写入的新边界' });
  assert.equal(f.isCurrentSaved('task-a'), false);
});
