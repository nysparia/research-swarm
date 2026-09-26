import assert from 'node:assert/strict';
import test from 'node:test';
import { executionEntries, researchCounts, protocolValue } from '../src/paperState.ts';

test('professional workspace counts only actual participating agents', () => {
  assert.deepEqual(researchCounts({ nodes: [
    { id: 'a', active: true, status: 'running' }, { id: 'b', active: false, status: 'completed' },
    { id: 'c', active: true, status: 'completed' }, { id: 'd', active: true, status: 'failed' },
  ] }), { total: 3, running: 1, completed: 1, failed: 1 });
  assert.deepEqual(researchCounts(null), { total: 0, running: 0, completed: 0, failed: 0 });
});

test('terminal uses committed process observations and retains invalidated history honestly', () => {
  const state = { history: [ { id: 'status', type: 'intervention' },
    { id: 'old', type: 'tool-executed', valid: false, execution: { tool: 'python_run', status: 'cancelled' } },
    { id: 'new', type: 'tool-executed', valid: true, execution: { tool: 'python_run', status: 'completed' } },
  ] };
  const entries = executionEntries(state);
  assert.deepEqual(entries.map(e => [e.id, e.valid]), [['new', true], ['old', false]]);
  assert.equal(state.history[0].id, 'status');
});

test('missing experiment protocols are unknown, never invented', () => {
  assert.equal(protocolValue(undefined), '尚未明确');
  assert.equal(protocolValue(['accuracy', 'p95 latency']), 'accuracy；p95 latency');
  assert.equal(protocolValue({ seed: 42, split: '80/20' }), 'seed: 42；split: 80/20');
});
