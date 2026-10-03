import test from 'node:test';
import assert from 'node:assert/strict';
import { executionLabel, modelWaitLabel, nodeExecutionLabel, retryNodeRequest } from '../src/workbench/executionState.ts';

const node = (id, status, extra = {}) => ({ id, status, active: true, input: {}, version: 3, ...extra });
const wait = { retryNumber: 2, maxRetries: 3, reason: 'busy', nextRetryAt: '2026-10-03T16:10:30Z', kind: 'cooldown' };
test('waiting provider nodes are never counted as actively running', () => {
  const state = { paused: false, nodes: [node('a', 'running'), node('b', 'running', { modelWait: wait }), node('c', 'failed')] };
  assert.equal(executionLabel(state), '1 运行中 · 1 等待服务 · 1 分支受阻');
  assert.equal(nodeExecutionLabel(state.nodes[1]), '等待模型服务');
  assert.equal(nodeExecutionLabel(state.nodes[2]), '执行受阻');
});
test('authoritative aggregate state controls blocked and paused displays', () => {
  const state = { paused: false, nodes: [], executionSummary: { status: 'blocked', failed: 2 } };
  assert.equal(executionLabel(state), '研究受阻 · 2 个失败节点');
  state.paused = true;
  assert.equal(executionLabel(state), '已暂停');
  state.executionSummary.status = 'waiting_user';
  assert.equal(executionLabel(state), '等待你确认');
});
test('countdown is derived locally, stops at zero, and distinguishes shared admission', () => {
  assert.equal(modelWaitLabel(wait, Date.parse('2026-10-03T16:10:10Z')), '自动重试 2/3 · 约 20 秒后尝试');
  assert.equal(modelWaitLabel(wait, Date.parse('2026-10-03T16:11:00Z')), '自动重试 2/3 · 等待恢复探测');
  assert.equal(modelWaitLabel({ ...wait, retryNumber: 0, nextRetryAt: null }, 0), '等待同一接口恢复 · 等待恢复探测');
});
test('retry binds the failed node version instead of an unrelated changing task revision', () => {
  assert.deepEqual(retryNodeRequest(node('failed-a', 'failed')), { nodeId: 'failed-a', expectedNodeVersion: 3 });
});
