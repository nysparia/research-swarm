import test from 'node:test';
import assert from 'node:assert/strict';
import { buildAgentStructure, initialStructureView, layoutAgents, AGENT_WIDTH, AGENT_HEIGHT } from '../src/workbench/agentStructureState.ts';

const node = (id, parentId = null, extra = {}) => ({ id, parentId, title: id, role: '研究 agent', kind: 'research', input: {}, active: true, output: null, evidenceIds: [], logs: [], ...extra });
const artifact = (id, kind, owner, extra = {}) => ({ id, kind, nodeIds: [owner], claimRefs: [], evidenceIds: [], content: {}, revision: 1, status: 'completed', ...extra });
const detail = (nodes, artifacts = [], extra = {}) => ({ task: { id: 'task' }, state: { nodes, edges: [], evidence: [], ...extra }, workbench: { artifacts } });

test('every current agent remains a node; claim ownership does not replace or duplicate the agent', () => {
  const nodes = [node('central'), node('owner', 'central'), node('old', 'central', { active: false }), node('superseded', 'owner', { input: { superseded: true } })];
  const claim = { id: 'claim1', ownerNodeId: 'owner', statement: 'Claim', version: 2 };
  const data = buildAgentStructure(detail(nodes, [], { claimGraph: { claims: [claim], relations: [] } }));
  assert.deepEqual(data.cards.map(item => item.node.id), ['central', 'owner']);
  assert.equal(data.cards[1].claim.id, 'claim1');
  assert.equal(data.archivedCount, 2);
  assert.deepEqual(data.edges, [{ source: 'central', target: 'owner', type: 'decompose', reason: '父节点下派需求' }]);
});

test('current claim relations retain opposing evidence and filter old versions', () => {
  const claim = { id: 'c', ownerNodeId: 'n', version: 2, statement: 'Claim' };
  const relations = [
    { claimId: 'c', claimVersion: 2, type: 'support', polarity: 'for', evidenceId: 'a' },
    { claimId: 'c', claimVersion: 2, type: 'support', polarity: 'against', evidenceId: 'b' },
    { claimId: 'c', claimVersion: 2, type: 'qualify', polarity: 'mixed', evidenceId: 'q' },
    { claimId: 'c', claimVersion: 1, type: 'support', polarity: 'for', evidenceId: 'old' },
  ];
  const data = buildAgentStructure(detail([node('n')], [], { evidence: ['a', 'b', 'q', 'old'].map(id => ({ id })), claimGraph: { claims: [claim], relations } }));
  assert.deepEqual(data.cards[0].relationCounts, { for: 1, against: 1, mixed: 0, qualify: 1 });
  assert.deepEqual(data.cards[0].evidence.map(item => item.id), ['a', 'b', 'q']);
});

test('discussion includes nodes before output, and jobs never leak across siblings or stale runs', () => {
  const artifacts = [
    artifact('state:a', 'node_state', 'a'),
    artifact('job:a', 'experiment_job', 'a', { content: { createdAt: '2026-10-02T00:00:00Z', status: 'completed', result: { metrics: { latency: 12 } } } }),
    artifact('job:a-old', 'experiment_job', 'a', { status: 'stale', revision: 9 }),
    artifact('job:b', 'experiment_job', 'b', { content: { status: 'running' } }),
  ];
  const data = buildAgentStructure(detail([node('a'), node('b')], artifacts));
  assert.equal(data.cards[0].discussionArtifact.id, 'state:a');
  assert.equal(data.cards[0].jobArtifact.id, 'job:a');
  assert.equal(data.cards[1].jobArtifact.id, 'job:b');
  assert.equal(data.cards[0].outputArtifact, undefined);
});

test('a recorded execution problem is visible even when the research node completed its reporting task', () => {
  const output = artifact('out', 'node_output', 'n', { content: { structured: { experimentRun: { status: 'problem', verified: false, problem: { message: 'No dataset' } } } } });
  const card = buildAgentStructure(detail([node('n', null, { status: 'completed' })], [output])).cards[0];
  assert.equal(card.problem, 'No dataset');
  assert.equal(card.node.status, 'completed');
});

test('unresolved evidence references are not counted as located evidence', () => {
  const card = buildAgentStructure(detail([node('n', null, { evidenceIds: ['present', 'missing', 'present'] })], [], { evidence: [{ id: 'present' }] })).cards[0];
  assert.equal(card.evidence.length, 1);
  assert.equal(card.unresolvedEvidenceCount, 1);
});

test('hierarchy layout includes orphans and cycles exactly once without overlapping cards', () => {
  const nodes = [node('central'), node('a', 'central'), node('b', 'central'), node('c', 'a'), node('orphan', 'missing'), node('x', 'y'), node('y', 'x')];
  const layout = layoutAgents(nodes);
  assert.equal(layout.nodes.length, nodes.length);
  assert.equal(new Set(layout.nodes.map(item => item.id)).size, nodes.length);
  assert.ok(layout.nodes.every(item => Number.isFinite(item.x) && Number.isFinite(item.y)));
  for (let i = 0; i < layout.nodes.length; i++) for (let j = i + 1; j < layout.nodes.length; j++) {
    const a = layout.nodes[i], b = layout.nodes[j];
    assert.ok(Math.abs(a.x - b.x) >= AGENT_WIDTH || Math.abs(a.y - b.y) >= AGENT_HEIGHT, `${a.id} overlaps ${b.id}`);
  }
  assert.ok(layout.nodes.find(item => item.id === 'c').y > layout.nodes.find(item => item.id === 'a').y);
});

test('new nodes append without moving existing cards; a polling reorder cannot scramble positions', () => {
  const first = [node('central'), node('a', 'central'), node('b', 'central'), node('c', 'a')];
  const before = layoutAgents(first);
  const after = layoutAgents([node('new', 'a'), node('new-child', 'new'), ...first.toReversed()], before);
  for (const position of before.nodes) assert.deepEqual(after.nodes.find(item => item.id === position.id), position);
  const addition = after.nodes.find(item => item.id === 'new');
  assert.ok(addition.y > after.nodes.find(item => item.id === 'a').y);
  assert.ok(after.nodes.find(item => item.id === 'new-child').y > addition.y);
  for (const other of after.nodes.filter(item => item.id !== 'new')) assert.ok(Math.abs(other.x - addition.x) >= AGENT_WIDTH || Math.abs(other.y - addition.y) >= AGENT_HEIGHT);
});

test('large research trees open at readable scale instead of shrinking every node to a speck', () => {
  const layout = layoutAgents([node('central'), ...Array.from({ length: 60 }, (_, i) => node(`n${i}`, 'central'))]);
  const view = initialStructureView(layout, { width: 620, height: 780 });
  assert.equal(view.scale, 1);
  const root = layout.nodes.find(item => item.id === 'central');
  const centre = (root.x + root.width / 2) * view.scale + view.x;
  assert.equal(centre, 310);
  assert.ok(root.y * view.scale + view.y >= 0);
});

test('three direct children remain centred and visible despite a wide deeper experiment tier', () => {
  const upper = [node('central'), node('a', 'central'), node('b', 'central'), node('c', 'central')];
  const deep = Array.from({ length: 43 }, (_, i) => node(`experiment:${i}`, ['a', 'b', 'c'][i % 3]));
  const layout = layoutAgents([...upper, ...deep]);
  const view = initialStructureView(layout, { width: 649, height: 662 });
  assert.equal(view.scale, 1);
  const root = layout.nodes.find(item => item.id === 'central');
  const children = layout.nodes.filter(item => item.parentId === 'central');
  assert.equal((children[0].x + children.at(-1).x + AGENT_WIDTH) / 2, root.x + AGENT_WIDTH / 2);
  for (const child of children) {
    const x = child.x * view.scale + view.x, y = child.y * view.scale + view.y;
    assert.ok(x >= 0 && x + child.width <= 649, `${child.id} is clipped horizontally`);
    assert.ok(y >= 0 && y + child.height <= 662, `${child.id} is clipped vertically`);
  }
  const shallower = layoutAgents(upper), shallowRoot = shallower.nodes.find(item => item.id === 'central');
  assert.deepEqual(children.map(item => item.x - root.x), shallower.nodes.filter(item => item.parentId === 'central').map(item => item.x - shallowRoot.x));
});
