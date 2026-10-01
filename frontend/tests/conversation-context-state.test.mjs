import test from 'node:test';
import assert from 'node:assert/strict';
import { contextForNode, contextIsCurrent, overviewArtifact, isNearConversationEnd } from '../src/workbench/conversationState.ts';

const artifact = (id, kind, nodeIds, revision = 1, status = 'completed') => ({ id, kind, nodeIds, revision, status });
const detail = artifacts => ({ workbench: { artifacts }, state: { nodes: [{ id: 'root', parentId: null, active: true }, { id: 'worker', parentId: 'root', active: true }] } });
test('an unexecuted node has its own versioned discussion context, never another branches output', () => {
  const state = detail([artifact('output:root', 'node_output', ['root']), artifact('node_state:worker', 'node_state', ['worker'], 3)]);
  const context = contextForNode(state, 'worker');
  assert.equal(context.artifact.id, 'node_state:worker');
  assert.equal(context.artifact.revision, 3);
  assert.equal(contextForNode(state, 'missing'), null);
});
test('a selected version remains pinned and requires explicit refresh before mutation', () => {
  const selected = contextForNode(detail([artifact('n', 'node_state', ['worker'], 3)]), 'worker');
  assert.equal(contextIsCurrent(selected, detail([artifact('n', 'node_state', ['worker'], 4)])), false);
  assert.equal(selected.artifact.revision, 3);
  assert.equal(contextIsCurrent(selected, detail([artifact('n', 'node_state', ['worker'], 3, 'stale')])), false);
  assert.equal(contextIsCurrent(selected, detail([artifact('n', 'node_state', ['worker'], 3)])), true);
});
test('retired or historically projected content cannot initiate a new branch', () => {
  assert.equal(contextForNode(detail([artifact('old', 'node_output', ['worker'], 1, 'historical')]), 'worker'), null);
  const state = detail([artifact('n', 'node_state', ['worker'])]); const context = contextForNode(state, 'worker');
  state.state.nodes[1].active = false;
  assert.equal(contextIsCurrent(context, state), false);
});
test('the current report anchors overview discussions and history is not picked as fallback', () => {
  const state = detail([artifact('old', 'report', [], 1, 'stale'), artifact('root', 'node_state', ['root']), artifact('report:live', 'report', [])]);
  assert.equal(overviewArtifact(state).id, 'report:live');
  assert.equal(overviewArtifact(detail([artifact('old', 'report', [], 1, 'historical')])), undefined);
});
test('conversation follows at the end and lets the user keep reading earlier messages', () => {
  assert.equal(isNearConversationEnd(410, 1000, 600), true);
  assert.equal(isNearConversationEnd(100, 1000, 600), false);
});
