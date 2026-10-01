import test from 'node:test';
import assert from 'node:assert/strict';
import { metricRows, moduleTabs, interactionPayload, canResumeJob, proposalIsCurrent, changedBlocks, interactionForArtifact, rebaseNotebook, notebookSubmissionUnchanged } from '../src/workbench/state.ts';

const artifact = (patch = {}) => ({ id: 'claim:c1', revision: 4, kind: 'claim', content: { statement: 'Original claim' }, nodeIds: ['n1'], status: 'unassessed', ...patch });
test('metrics only expose finite recorded values, never invent series or turn strings into numbers', () => {
  assert.deepEqual(metricRows(null), []);
  assert.deepEqual(metricRows({ accuracy: .8, note: '90', nested: { loss: .2 }, invalid: Infinity }), [{ name: 'accuracy', value: .8 }, { name: 'nested.loss', value: .2 }]);
  assert.deepEqual(metricRows([{ method: 'A', score: .7 }, { method: 'B', score: .6 }]), [{ name: 'A · score', value: .7 }, { name: 'B · score', value: .6 }]);
});
test('modules follow capabilities and available artifacts, with process secondary', () => {
  assert.deepEqual(moduleTabs(undefined, null).map(t => t.id), ['board', 'report', 'process']);
  const tabs = moduleTabs({ plan: { capabilities: ['experimentation'] }, artifacts: [] }, null).map(t => t.id);
  assert.ok(tabs.includes('experiments')); assert.ok(!tabs.includes('paper'));
  const reproduction = moduleTabs({ plan: { capabilities: ['reproduction'] }, artifacts: [artifact({ kind: 'expression', content: { kind: 'reproduction_report' } })] }, null).map(t => t.id);
  assert.ok(!reproduction.includes('paper'), 'a reproduction report is not a paper draft');
});
test('local ask pins the shown revision and only uses exact selected content', () => {
  const a = artifact();
  assert.deepEqual(interactionPayload(a, 'ask', 'why?', '', '', 'Original'), { kind: 'ask', text: 'why?', target: { artifactId: a.id, revision: 4, selection: { quote: 'Original' } } });
  assert.throws(() => interactionPayload(a, 'ask', 'why?', '', '', 'invented'), /选中/);
});
test('mutations require a current artifact, specific branch, and explicit revised statement', () => {
  assert.throws(() => interactionPayload(artifact({ status: 'stale' }), 'deepen', 'more'), /失效/);
  assert.throws(() => interactionPayload(artifact({ nodeIds: ['a', 'b'] }), 'challenge', 'why'), /节点/);
  assert.throws(() => interactionPayload(artifact(), 'revise', 'improve'), /完整/);
  assert.equal(interactionPayload(artifact(), 'revise', 'reason', 'Replacement').replacement, 'Replacement');
});
test('resume requires an actual checkpoint, an explicit resume protocol, and a stopped job', () => {
  const job = { status: 'failed', checkpoint: { path: 'runs/checkpoint', sha256: 'a'.repeat(64) }, request: { checkpoint: { resumeCode: 'resume()' } } };
  assert.ok(canResumeJob(job));
  assert.ok(!canResumeJob({ ...job, checkpoint: null }));
  assert.ok(!canResumeJob({ ...job, status: 'completed' }));
  assert.ok(!canResumeJob({ ...job, request: {} }));
});
test('impact confirmation becomes stale when document, engine or selected artifact changes', () => {
  const proposal = { status: 'pending', revision: 9, documentRevision: 3, target: { artifactId: 'claim:c1', revision: 4 } };
  const current = { document: { revision: 3 }, state: { revision: 9 }, workbench: { artifacts: [artifact()] } };
  assert.ok(proposalIsCurrent(proposal, current));
  assert.ok(!proposalIsCurrent(proposal, { ...current, document: { revision: 4 } }));
  assert.ok(!proposalIsCurrent(proposal, { ...current, workbench: { artifacts: [artifact({ revision: 5 })] } }));
});
test('notebook diffs preserve block identity and only submit changed text', () => {
  const blocks = [{ id: 'b1', title: 'scope', content: 'old', locked: true, source: 'user', kind: 'section' }];
  assert.deepEqual(changedBlocks(blocks, { b1: 'old' }), []);
  assert.deepEqual(changedBlocks(blocks, { b1: 'new' }), [{ op: 'update', id: 'b1', changes: { content: 'new' } }]);
  assert.throws(() => changedBlocks(blocks, { removed: 'unsaved' }), /已改变/);
});

test('a previous answer is never relabeled as an answer to the latest artifact version', () => {
  const reply = { id: 'i1', target: { artifactId: 'claim:c1', revision: 3 }, reply: 'old answer' };
  assert.equal(interactionForArtifact(reply, artifact()), null);
  assert.equal(interactionForArtifact(reply, artifact({ revision: 3 })), reply);
});
test('rebasing a notebook recovers text from deleted blocks and drops redundant removals', () => {
  const base = [{ id: 'removed', title: 'Boundary', content: 'old', kind: 'section', source: 'user', locked: true }];
  const result = rebaseNotebook(base, [], { removed: 'keep my new text' }, ['already-gone'], id => 'recovered:' + id);
  assert.deepEqual(result.edits, {});
  assert.equal(result.recovered[0].content, 'keep my new text');
  assert.equal(result.recovered[0].id, 'recovered:removed');
  assert.deepEqual(result.removed, []);
});
test('a confirmed preview may only clear the exact notebook edits it contained', () => {
  const submitted = { edits: { a: 'A' }, added: [], removed: [] };
  assert.ok(notebookSubmissionUnchanged(submitted, submitted));
  assert.ok(!notebookSubmissionUnchanged(submitted, { ...submitted, edits: { a: 'AB' } }));
  assert.ok(!notebookSubmissionUnchanged(submitted, { ...submitted, added: [{ content: 'new note' }] }));
});
