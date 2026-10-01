import assert from 'node:assert/strict';
import test from 'node:test';
import { buildGraphData, mergeGraphNodes, mergeGraphLinkReasons } from '../src/graphData.ts';

test('polling refreshes relationship evidence without replacing simulated endpoints', () => {
  const source = { id: 'central', x: 23, fx: 23 }, target = { id: 'child', x: 46 };
  const link = { source, target, type: 'decompose', reason: '旧判断' };
  mergeGraphLinkReasons([link], [{ source: 'central', target: 'child', type: 'decompose', reason: '新证据要求追加对照' }]);
  assert.equal(link.reason, '新证据要求追加对照');
  assert.equal(link.source, source);
  assert.equal(link.target, target);
  assert.equal(source.fx, 23);
});

test('polling keeps distinct reasons for parallel evidence relations', () => {
  const current = [{ id: 'r1', source: 'e1', target: 'c1', type: 'evidence-qualify', reason: '旧限制 A' }, { id: 'r2', source: 'e1', target: 'c1', type: 'evidence-qualify', reason: '旧限制 B' }];
  mergeGraphLinkReasons(current, [{ ...current[0], reason: '新限制 A' }, { ...current[1], reason: '新限制 B' }]);
  assert.deepEqual(current.map(link => link.reason), ['新限制 A', '新限制 B']);
});

test('3D graph contains actual agents, missing facet nodes and their hierarchy without invented nodes', () => {
  const graph = buildGraphData({
    nodes: [{ id: 'central', title: '总 agent', status: 'running', phase: 'plan', logs: [{ message: '检索中' }], sourceNodeId: null, active: true, parentId: null }, { id: 'agent-1', title: '方法分析', status: 'pending', phase: 'execute', logs: [], sourceNodeId: '1', active: false, parentId: 'central' }],
    edges: [{ source: 'central', target: 'agent-1', type: 'decompose', reason: '研究分工' }],
    facetNodes: [{ id: '1', title: '方法', parentId: null, facetName: '方法树', paperIds: ['p1'] }, { id: '2', title: '子方法', parentId: '1', facetName: '方法树', paperIds: ['p2'] }],
  });
  assert.deepEqual(graph.nodes.map(node => node.id), ['central', 'agent-1', 'facet:2']);
  assert.equal(graph.nodes[0].action, '检索中');
  assert.ok(graph.links.some(link => link.source === 'agent-1' && link.target === 'facet:2' && link.type === 'facet'));
  assert.equal(graph.links.filter(link => link.source === 'central' && link.target === 'agent-1').length, 1);
});

test('an empty task never renders decorative or demo graph nodes', () => {
  assert.deepEqual(buildGraphData(null), { nodes: [], links: [] });
});

test('canonical claims own their workers and evidence while preparation stays visible', () => {
  const graph = buildGraphData({
    nodes: [
      { id: 'central', title: '总 agent', status: 'running', phase: 'plan', logs: [], input: {}, sourceNodeId: null, active: true, parentId: null },
      { id: 'prep', title: '需求准备', status: 'completed', phase: 'plan', logs: [], input: {}, sourceNodeId: null, active: true, parentId: 'central' },
      { id: 'owner', title: '旧论文切面', status: 'running', phase: 'plan', logs: [], input: { claimId: 'c1' }, sourceNodeId: 'f1', active: true, parentId: 'central' },
      { id: 'worker', title: '反例检验', status: 'completed', phase: 'execute', logs: [], input: { claimId: 'c1', claimVersion: 2 }, sourceNodeId: null, active: true, parentId: 'owner' },
    ], edges: [], facetNodes: [], evidence: [{ id: 'e1', quote: 'negative result', locator: 'p.4', paperId: 'p1' }],
    claimGraph: { schemaVersion: 1, claims: [{ id: 'c1', statement: '方法提高准确率', ownerNodeId: 'owner', parentClaimIds: [], version: 2, assessment: { status: 'mixed', confirmedByUser: false } }], relations: [{ id: 'r1', claimId: 'c1', claimVersion: 2, evidenceId: 'e1', type: 'support', polarity: 'against' }], materials: [], expressions: [] },
  });
  assert.deepEqual(graph.nodes.filter(node => node.sourceKind === 'claim').map(node => [node.id, node.nodeId, node.claimId]), [['claim:c1', 'owner', 'c1']]);
  assert.ok(graph.nodes.some(node => node.id === 'prep'));
  assert.ok(!graph.nodes.some(node => node.id === 'owner'));
  assert.ok(graph.links.some(link => link.source === 'claim:c1' && link.target === 'worker' && link.type === 'decompose'));
  assert.ok(graph.links.some(link => link.source === 'evidence:e1' && link.target === 'claim:c1' && link.type === 'evidence-against'));
});

test('a revised claim keeps old evidence out of the live graph', () => {
  const graph = buildGraphData({
    nodes: [{ id: 'central', title: '总 agent', status: 'completed', logs: [], input: {}, active: true, parentId: null }],
    edges: [], facetNodes: [], evidence: [{ id: 'old', quote: '旧版本观察', locator: 'p.1' }, { id: 'current', quote: '当前观察', locator: 'p.2' }],
    claimGraph: { schemaVersion: 1, claims: [{ id: 'c1', statement: '修订主张', ownerNodeId: null, parentClaimIds: [], version: 2, assessment: { status: 'inconclusive' } }], relations: [
      { id: 'r1', claimId: 'c1', claimVersion: 1, evidenceId: 'old', type: 'support', polarity: 'for' },
      { id: 'r2', claimId: 'c1', claimVersion: 2, evidenceId: 'current', type: 'support', polarity: 'against' },
    ], materials: [], expressions: [] },
  });
  assert.ok(!graph.nodes.some(node => node.id === 'evidence:old'));
  assert.ok(graph.nodes.some(node => node.id === 'evidence:current' && node.parentId === 'claim:c1'));
  assert.ok(graph.links.some(link => link.source === 'evidence:current' && link.target === 'claim:c1' && link.type === 'evidence-against'));
});

test('claim ownership preserves each dependent task layer and historical owners', () => {
  const graph = buildGraphData({
    nodes: [
      { id: 'central', title: '总 agent', status: 'running', logs: [], input: {}, active: true, parentId: null },
      { id: 'owner', title: '主张负责者', status: 'running', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'central' },
      { id: 'demand', title: '数据需求', status: 'completed', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'owner' },
      { id: 'source', title: '数据来源', status: 'completed', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'demand' },
      { id: 'design', title: '实验设计', status: 'completed', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'source' },
      { id: 'execute', title: '实验执行', status: 'pending', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'design' },
      { id: 'legacy', title: '历史切面', status: 'completed', logs: [], input: {}, active: false, parentId: 'central' },
    ], edges: [], facetNodes: [], evidence: [],
    claimGraph: { schemaVersion: 1, claims: [{ id: 'c1', statement: '待论证', ownerNodeId: 'owner', parentClaimIds: [], version: 1, assessment: { status: 'unassessed' } }, { id: 'legacy-c', statement: '旧主张', ownerNodeId: 'legacy', parentClaimIds: [], version: 1, assessment: { status: 'inconclusive' } }], relations: [], materials: [], expressions: [] },
  });
  assert.deepEqual(['demand', 'source', 'design', 'execute'].map(id => graph.nodes.find(node => node.id === id).parentId), ['claim:c1', 'demand', 'source', 'design']);
  assert.ok(graph.nodes.some(node => node.id === 'legacy'));
  assert.equal(graph.nodes.find(node => node.id === 'claim:legacy-c').active, false);
});

test('shared owner and central remain visible; qualifying and unresolved evidence keep separate links', () => {
  const graph = buildGraphData({
    nodes: [{ id: 'central', title: '总 agent', status: 'completed', logs: [], input: {}, active: true, parentId: null }, { id: 'shared', title: '审计节点', status: 'completed', logs: [], input: { claimId: 'c1' }, active: true, parentId: 'central' }],
    edges: [], facetNodes: [], evidence: [{ id: 'e1', quote: '一条观察', locator: 'p.1' }],
    claimGraph: { schemaVersion: 1, claims: [
      { id: 'c1', statement: '主张甲', ownerNodeId: 'shared', parentClaimIds: [], version: 1, assessment: { status: 'inconclusive' } },
      { id: 'c2', statement: '主张乙', ownerNodeId: 'shared', parentClaimIds: [], version: 1, assessment: { status: 'inconclusive' } },
      { id: 'c3', statement: '审计主张', ownerNodeId: 'central', parentClaimIds: [], version: 1, assessment: { status: 'inconclusive' } },
    ], relations: [
      { id: 'r1', claimId: 'c1', claimVersion: 1, evidenceId: 'e1', type: 'support', polarity: 'unresolved', reason: '方向尚不明确' },
      { id: 'r2', claimId: 'c1', claimVersion: 1, evidenceId: 'e1', type: 'qualify', polarity: 'unresolved', reason: '只适用某种实验条件' },
      { id: 'r3', claimId: 'c1', claimVersion: 1, evidenceId: 'e1', type: 'qualify', polarity: 'unresolved', reason: '样本量有限' },
    ], materials: [], expressions: [] },
  });
  assert.ok(graph.nodes.some(node => node.id === 'central'));
  assert.ok(graph.nodes.some(node => node.id === 'shared'));
  assert.deepEqual(graph.links.filter(link => link.source === 'evidence:e1' && link.target === 'claim:c1').map(link => [link.id, link.type, link.reason]), [
    ['r1', 'evidence-unresolved', '方向尚不明确'],
    ['r2', 'evidence-qualify', '只适用某种实验条件'],
    ['r3', 'evidence-qualify', '样本量有限'],
  ]);
});

test('polling changes status while keeping simulated and manually dragged node positions', () => {
  const oldNode = { id: 'agent-1', title: '方法分析', status: 'pending', x: 65, y: -12, z: 37, fx: 65, fy: -12, fz: 37 };
  const incoming = [{ id: 'agent-1', title: '方法分析', status: 'completed', action: '已有可追溯输出' }, { id: 'agent-2', title: '新分支', status: 'pending' }];
  const merged = mergeGraphNodes(incoming, new Map([[oldNode.id, oldNode]]));
  assert.equal(merged[0], oldNode);
  assert.equal(merged[0].status, 'completed');
  assert.deepEqual([merged[0].x, merged[0].y, merged[0].z, merged[0].fx, merged[0].fy, merged[0].fz], [65, -12, 37, 65, -12, 37]);
  assert.equal(merged[1].id, 'agent-2');
});
