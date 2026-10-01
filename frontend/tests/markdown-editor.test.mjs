import assert from 'node:assert/strict';
import test from 'node:test';
import { EditorSelection, EditorState, Transaction } from '@codemirror/state';
import { history, undo, redo } from '@codemirror/commands';
import { documentChange, markdownTransaction } from '../src/workbench/markdownCommands.ts';

const create = (doc, from, to = from) => EditorState.create({ doc, selection: EditorSelection.single(from, to), extensions: [history()] });
const format = (state, action) => state.update(markdownTransaction(state, action)).state;

test('formatting wraps a selection and toggles without dropping surrounding Chinese text', () => {
  const state = create('保留 模型能力 的边界', 3, 7);
  const bold = format(state, 'bold');
  assert.equal(bold.doc.toString(), '保留 **模型能力** 的边界');
  assert.equal(bold.sliceDoc(bold.selection.main.from, bold.selection.main.to), '模型能力');
  assert.equal(format(bold, 'bold').doc.toString(), state.doc.toString());
});

test('multi-line conversion does not include a line starting at selection end', () => {
  const original = '目标一\n目标二\n边界不改';
  const changed = format(create(original, 0, 8), 'checklist');
  assert.equal(changed.doc.toString(), '- [ ] 目标一\n- [ ] 目标二\n边界不改');
  assert.equal(format(changed, 'checklist').doc.toString(), original);
});

test('ordered lists convert instead of nesting markers', () => {
  const original = '1. 数据集\n2. 实验指标';
  assert.equal(format(create(original, 0, original.length), 'checklist').doc.toString(), '- [ ] 数据集\n- [ ] 实验指标');
});

test('toolbar edit has one undo and redo transaction', () => {
  let state = format(create('准确率', 0, 3), 'bold');
  const view = { get state() { return state; }, dispatch(transaction) { state = transaction.state; } };
  assert.equal(undo(view), true);
  assert.equal(state.doc.toString(), '准确率');
  assert.equal(redo(view), true);
  assert.equal(state.doc.toString(), '**准确率**');
});

test('code fences safely contain existing triple backticks', () => {
  const selected = '```python\nprint(1)\n```';
  const state = format(create(selected, 0, selected.length), 'code');
  assert.equal(state.doc.toString(), '````\n' + selected + '\n````');
  assert.equal(state.sliceDoc(state.selection.main.from, state.selection.main.to), selected);
});

test('incoming changes preserve an unchanged selection and ignore identical polls', () => {
  const initial = '# 需求\n\nCPU 实验\n';
  const next = '# 需求\n\nCPU 实验\n\n## 验收\n证据可追溯';
  const state = create(initial, 6, 9);
  const update = state.update({ changes: documentChange(initial, next), annotations: Transaction.addToHistory.of(false) });
  assert.equal(update.state.doc.toString(), next);
  assert.deepEqual(update.state.selection.toJSON(), state.selection.toJSON());
  assert.equal(documentChange(next, next), null);
});

test('minimal external changes never split emoji surrogate pairs', () => {
  for (const [before, after] of [['实验🧪甲', '实验🧫甲'], ['结果甲😀', '结果乙😀'], ['😀甲', '😀乙']]) {
    const delta = documentChange(before, after);
    assert.equal(before.slice(0, delta.from) + delta.insert + before.slice(delta.to), after);
    assert.equal(/[\uDC00-\uDFFF]/.test(before[delta.from] || ''), false);
    assert.equal(/[\uDC00-\uDFFF]/.test(before[delta.to] || ''), false);
  }
});
