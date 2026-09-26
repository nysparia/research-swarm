import assert from 'node:assert/strict';
import test from 'node:test';
import { focusArtifacts, linkedSections, changedLines, chartGeometry, artifactCurrent } from '../src/deskState.ts';
import { executionEntries } from '../src/paperState.ts';

test('focusing one experiment links only its files and matching evidence-backed manuscript sections', () => {
  const artifacts = [{ kind: 'experiment', nodeId: 'a', path: 'a.py' }, { kind: 'experiment', nodeId: 'b', path: 'b.py' }];
  assert.equal(focusArtifacts(artifacts, 'a')[0].path, 'a.py');
  assert.equal(focusArtifacts(artifacts, 'a').length, 1);
  assert.equal(focusArtifacts(artifacts, null).length, 2);
  const sections = [{ id: 'method', sourceNodeIds: ['a'], evidenceIds: [] }, { id: 'results', sourceNodeIds: ['central'], evidenceIds: ['exp1'] }, { id: 'discussion', sourceNodeIds: [], evidenceIds: [] }];
  assert.deepEqual(linkedSections(sections, 'a', ['exp1']).map(s => s.id), ['method', 'results']);
});

test('actual code changes highlight the edited block without replaying unchanged prefixes and suffixes', () => {
  assert.deepEqual([...changedLines('a\nb\nc', 'a\nb2\nc')], [1]);
  assert.deepEqual([...changedLines('a\nc', 'a\nb\nc')], [1]);
  assert.deepEqual([...changedLines('same', 'same')], []);
});

test('chart axes preserve zero, negative values and the actual uneven numeric x positions', () => {
  const geometry = chartGeometry({ series: [{ points: [{ x: 1, y: -2 }, { x: 100, y: 3 }] }] });
  assert.equal(geometry.min, -2); assert.equal(geometry.max, 3);
  assert.equal(geometry.xMin, 1); assert.equal(geometry.xMax, 100); assert.equal(geometry.numericX, true);
});

test('live process start is superseded by its receipt and a stale process is marked interrupted', () => {
  const execution = { nodeId: 'a', nodeVersion: 1, round: 1, stdoutPath: 'runs/a/stdout.txt', status: 'running' };
  const state = { project: { round: 1 }, nodes: [{ id: 'a', version: 1, active: true, status: 'running' }], history: [
    { id: 'start', type: 'tool-started', valid: true, execution }, { id: 'end', type: 'tool-executed', valid: true, execution: { ...execution, status: 'completed' } },
  ] };
  assert.equal(executionEntries(state).length, 1); assert.equal(executionEntries(state)[0].id, 'end');
  state.history.pop(); state.nodes[0].version = 2;
  assert.equal(executionEntries(state)[0].execution.status, 'interrupted');
  assert.equal(artifactCurrent({ nodeId: 'a', nodeVersion: 1, round: 1, valid: true }, { state }), false);
});
