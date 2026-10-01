import test from 'node:test';
import assert from 'node:assert/strict';
import { boardExperiments, relatedClaim, reconcileBoardFeed, chartSeries } from '../src/workbench/boardState.ts';

const artifact = (id, kind, content = {}, extra = {}) => ({ id, kind, content, title: id, status: 'completed', revision: 1, sourceRevision: 1, nodeIds: [], claimRefs: [], evidenceIds: [], dependencies: [], sourceRefs: [], ...extra });
const plan = (id, nodeId, hypothesis = 'h1') => artifact(id, 'node_output', { structured: { experimentProtocol: { id: 'P1', hypothesis, method: '同一环境', dataset: '固定划分', baselines: ['FP32'], metrics: ['latency_ms'] } } }, { nodeIds: [nodeId] });
const node = (id, parentId, input = {}) => ({ id, parentId, input, active: true });

test('protocol cards only acquire execution data from their own branch, even when protocol IDs repeat', () => {
  const p1 = plan('plan:1', 'design:1'), p2 = plan('plan:2', 'design:2', 'h2');
  const job = artifact('job:1', 'experiment_job', { id: 'job:1', request: { nodeId: 'execute:1', protocolId: 'P1' }, result: { metrics: { latency_ms: 0 } }, status: 'completed' }, { nodeIds: ['execute:1'] });
  const sources = boardExperiments([p1, p2, job], [node('execute:1', 'design:1')]);
  assert.equal(sources.length, 2);
  assert.equal(sources[0].id, p1.id);
  assert.equal(sources[0].result.id, job.id);
  assert.equal(sources[0].measured, true);
  assert.equal(sources[1].result, undefined);
  assert.equal(sources[1].measured, false);
});

test('failed or stale executions never become measured chart evidence', () => {
  const failed = artifact('failed', 'experiment_job', { status: 'failed', result: { metrics: { score: 99 } } }, { status: 'failed' });
  const stale = artifact('stale', 'experiment_job', { status: 'completed', result: { metrics: { score: 99 } } }, { status: 'stale' });
  const sources = boardExperiments([failed, stale], []);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].measured, false);
});

test('related claim follows explicit versions or the protocol hypothesis, never an unrelated first claim', () => {
  const c1 = artifact('c1', 'claim', { id: 'c1', origin: { hypothesisId: 'h1' } }, { claimRefs: [{ claimId: 'c1', version: 1 }] });
  const c2 = artifact('c2', 'claim', { id: 'c2', origin: { hypothesisId: 'h2' } }, { claimRefs: [{ claimId: 'c2', version: 2 }] });
  const focus = { artifact: plan('p', 'n', 'h2'), protocol: { hypothesis: 'h2' } };
  assert.equal(relatedClaim(focus, [c1, c2], [])?.id, 'c2');
  assert.equal(relatedClaim({ artifact: artifact('x', 'experiment_job'), protocol: {} }, [c1, c2], []), undefined);
  assert.equal(relatedClaim({ artifact: artifact('x', 'experiment_job', {}, { claimRefs: [{ claimId: 'c2', version: 1 }] }), protocol: {} }, [c2], []), undefined);
});

test('new results append, polling order cannot reorder old cards, revisions update in place', () => {
  const a = artifact('a', 'node_output', { summary: 'A' }), b = artifact('b', 'claim'), c = artifact('c', 'expression');
  const first = reconcileBoardFeed(undefined, 'task1', [a, b]);
  assert.deepEqual(first.entries.map(e => e.id), ['a', 'b']);
  assert.deepEqual(first.freshIds, []);
  const next = reconcileBoardFeed(first, 'task1', [c, b, { ...a, revision: 2, content: { summary: 'A2' } }]);
  assert.deepEqual(next.entries.map(e => e.id), ['a', 'b', 'c']);
  assert.equal(next.entries[0].content.summary, 'A2');
  assert.deepEqual(next.freshIds, ['c']);
  const stale = reconcileBoardFeed(next, 'task1', [b, c]);
  assert.deepEqual(stale.entries.map(e => e.id), ['a', 'b', 'c']);
  assert.equal(stale.entries[0].status, 'stale');
  assert.deepEqual(reconcileBoardFeed(stale, 'task2', [b]).entries.map(e => e.id), ['b']);
});

test('initial feed shows recent results without treating history as newly generated', () => {
  const history = Array.from({ length: 8 }, (_, i) => artifact(String(i), 'node_output'));
  const first = reconcileBoardFeed(undefined, 't', history);
  assert.deepEqual(first.entries.map(e => e.id), ['4', '5', '6', '7']);
  const poll = reconcileBoardFeed(first, 't', history.toReversed());
  assert.deepEqual(poll.entries.map(e => e.id), ['4', '5', '6', '7']);
  assert.deepEqual(poll.freshIds, []);
});

test('chart series keep separate metrics and preserve zero and negative values', () => {
  const compared = chartSeries([{ method: 'A', latency_ms: 0, score: -2 }, { method: 'B', latency_ms: 8, score: 4 }]);
  assert.deepEqual(compared.map(s => s.key), ['latency_ms', 'score']);
  assert.deepEqual(compared[0].points.map(p => p.value), [0, 8]);
  assert.deepEqual(compared[1].points.map(p => p.value), [-2, 4]);
  const independent = chartSeries({ latency_ms: 12, accuracy: .8, invalid: Infinity });
  assert.equal(independent.length, 2);
  assert.ok(independent.every(s => s.points.length === 1));
  assert.deepEqual(chartSeries({ estimate: 'about 3' }), []);
});
