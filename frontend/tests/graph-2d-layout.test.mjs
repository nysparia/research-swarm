import assert from 'node:assert/strict';
import test from 'node:test';
import { applyManualNodePositions, layoutResearchGraph } from '../src/graph2dLayout.ts';

const node = (id, parentId, extra = {}) => ({
  id, parentId, title: id, status: 'pending', action: '等待调度', sourceKind: 'agent', active: false, ...extra,
});
const edge = (source, target, type = 'decompose') => ({ source, target, type, reason: '真实研究关系' });
const positions = result => new Map(result.nodes.map(item => [item.id, item]));

test('real task parents define downward layers even when another relationship suggests a different parent', () => {
  const input = [node('central', null), node('methods', 'central'), node('experiment', 'methods'), node('baseline', 'central')];
  const links = [edge('baseline', 'experiment'), edge('experiment', 'central', 'return'), edge('methods', 'baseline', 'compare')];
  const result = layoutResearchGraph(input, links);
  assert.equal(result.nodes.length, input.length);
  const byId = positions(result);
  assert.equal(byId.get('central').depth, 0);
  assert.equal(byId.get('methods').depth, 1);
  assert.equal(byId.get('baseline').depth, 1);
  assert.equal(byId.get('experiment').depth, 2);
  assert.ok(byId.get('methods').y >= byId.get('central').y + byId.get('central').height + 72);
  assert.ok(byId.get('experiment').y >= byId.get('methods').y + byId.get('methods').height + 72);
  assert.deepEqual(links.map(link => link.type), ['decompose', 'return', 'compare']);
});

test('only decomposition and facet edges infer missing parents, including graph object endpoints', () => {
  const central = node('central', null);
  const method = node('method');
  const facet = node('facet:paper', undefined, { sourceKind: 'facet' });
  const links = [edge(central, method), edge(method, facet, 'facet'), edge('facet:paper', 'central', 'return'), edge('central', 'comparison-only', 'compare')];
  const result = layoutResearchGraph([central, method, facet, node('comparison-only')], links);
  assert.equal(result.nodes.length, 4);
  const byId = positions(result);
  assert.equal(byId.get('method').depth, 1);
  assert.equal(byId.get('facet:paper').depth, 2);
  assert.equal(byId.get('comparison-only').depth, 0);
});

test('cards remain separated at every layer in uneven research branches', () => {
  const input = [node('central', null), node('a', 'central'), node('b', 'central'), node('c', 'central'), node('a1', 'a'), node('a2', 'a'), node('a3', 'a'), node('b1', 'b'), node('a11', 'a1'), node('b11', 'b1'), node('b12', 'b1'), node('isolated')];
  const result = layoutResearchGraph(input, []);
  assert.equal(result.nodes.length, input.length);
  const layers = new Map();
  for (const item of result.nodes) {
    assert.ok(item.width > 0 && item.height > 0);
    assert.equal(item.width, result.nodes[0].width);
    assert.equal(item.height, result.nodes[0].height);
    assert.ok(item.x >= 0 && item.y >= 0);
    assert.ok(item.x + item.width <= result.width);
    assert.ok(item.y + item.height <= result.height);
    layers.set(item.depth, [...(layers.get(item.depth) || []), item]);
  }
  for (const layer of layers.values()) {
    layer.sort((a, b) => a.x - b.x);
    for (let i = 1; i < layer.length; i++) assert.ok(layer[i].x >= layer[i - 1].x + layer[i - 1].width + 32);
  }
});

test('disconnected nodes, missing references and cycles all survive as finite readable cards', () => {
  const input = [node('central', 'cycle'), node('cycle', 'central'), node('self', 'self'), node('dangling', 'absent'), node('island'), node('child', 'cycle')];
  const result = layoutResearchGraph(input, [edge('missing', 'island'), edge('island', 'missing')]);
  assert.equal(result.nodes.length, input.length);
  assert.deepEqual(result.nodes.map(item => item.id), input.map(item => item.id));
  assert.equal(positions(result).get('central').depth, 0);
  assert.equal(positions(result).get('cycle').depth, 1);
  assert.equal(positions(result).get('child').depth, 2);
  for (const item of result.nodes) assert.ok(Number.isFinite(item.x) && Number.isFinite(item.y) && Number.isFinite(item.depth));
  assert.ok(Number.isFinite(result.width) && Number.isFinite(result.height));
});

test('polling and input ordering do not move cards or mutate graph data', () => {
  const input = [node('b', 'central'), node('central', null), node('a', 'central'), node('a1', 'a')];
  const links = [edge('central', 'b'), edge('central', 'a'), edge('a', 'a1')];
  const original = structuredClone({ input, links });
  const first = layoutResearchGraph(input, links);
  const refreshed = layoutResearchGraph(input.toReversed().map(item => ({ ...item, status: 'completed', action: '新结果已回传', active: true, x: -999, y: -999 })), links.toReversed().map(link => ({ ...link, reason: '证据已更新' })));
  assert.equal(first.nodes.length, input.length);
  assert.equal(refreshed.nodes.length, input.length);
  for (const item of first.nodes) {
    const next = positions(refreshed).get(item.id);
    assert.deepEqual([next.x, next.y, next.depth], [item.x, item.y, item.depth]);
  }
  assert.deepEqual([refreshed.width, refreshed.height], [first.width, first.height]);
  assert.deepEqual({ input, links }, original);
});

test('an empty task has no invented cards or canvas content', () => {
  assert.deepEqual(layoutResearchGraph([], [edge('absent', 'missing')]), { nodes: [], width: 0, height: 0 });
});

test('long task chains terminate without recursive stack limits', () => {
  const input = Array.from({ length: 12000 }, (_, index) => node(index === 0 ? 'central' : `task-${index}`, index === 0 ? null : index === 1 ? 'central' : `task-${index - 1}`));
  const result = layoutResearchGraph(input, []);
  assert.equal(result.nodes.length, 12000);
  assert.equal(result.nodes.at(-1).depth, 11999);
  assert.ok(Number.isFinite(result.height));
});

test('a manually placed card stays at its absolute coordinates when a new sibling changes the automatic layout', () => {
  const original = layoutResearchGraph([node('central', null), node('z', 'central')], []);
  const manual = { z: { x: 132, y: 280 } };
  const first = applyManualNodePositions(original.nodes, manual);
  const expanded = layoutResearchGraph([node('central', null), node('z', 'central'), node('a', 'central')], []);
  const automaticBefore = structuredClone(expanded.nodes);
  assert.notEqual(positions(original).get('z').x, positions(expanded).get('z').x);
  const updated = applyManualNodePositions(expanded.nodes, manual);
  const firstZ = first.find(item => item.id === 'z');
  const updatedZ = updated.find(item => item.id === 'z');
  assert.deepEqual([firstZ.x, firstZ.y], [132, 280]);
  assert.deepEqual([updatedZ.x, updatedZ.y], [132, 280]);
  assert.deepEqual(updated.find(item => item.id === 'a'), positions(expanded).get('a'));
  assert.deepEqual(expanded.nodes, automaticBefore);
});

test('manual positions retain extra node fields and ignore coordinates for absent cards', () => {
  const result = layoutResearchGraph([node('central', null)], []);
  const input = result.nodes.map(item => ({ ...item, evidenceCount: 3 }));
  const manual = { central: { x: -50, y: 150 }, absent: { x: 700, y: 800 } };
  const before = structuredClone({ input, manual });
  const positioned = applyManualNodePositions(input, manual);
  assert.equal(positioned.length, 1);
  assert.deepEqual([positioned[0].x, positioned[0].y], [-50, 150]);
  assert.equal(positioned[0].evidenceCount, 3);
  assert.deepEqual({ input, manual }, before);
});
