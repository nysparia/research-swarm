import type { Snapshot, NodeStatus } from './types';

export interface VisualNode {
  id: string;
  title: string;
  status: NodeStatus;
  action: string;
  sourceKind: 'agent' | 'facet' | 'claim' | 'evidence';
  nodeId?: string;
  claimId?: string;
  evidenceId?: string;
  parentId?: string | null;
  facetId?: string;
  active: boolean;
  x?: number;
  y?: number;
}
export interface VisualLink { source: string | VisualNode; target: string | VisualNode; type: string; reason: string; id?: string }
export interface ResearchGraphData { nodes: VisualNode[]; links: VisualLink[] }

export function buildGraphData(state: Snapshot | null): ResearchGraphData {
  if (!state) return { nodes: [], links: [] };
  if (state.claimGraph?.claims.length) return buildClaimGraphData(state);
  const nodes: VisualNode[] = state.nodes.map(node => ({ id: node.id, title: node.title, status: node.status, action: node.logs.at(-1)?.message || node.output?.summary || ({ plan: '规划研究任务', execute: '执行研究任务', aggregate: '汇总子任务结果' } as Record<string, string>)[node.phase] || '等待调度', sourceKind: 'agent', nodeId: node.id, parentId: node.parentId, facetId: node.sourceNodeId || undefined, active: node.active }));
  const facetAgents = new Map(state.nodes.filter(node => node.sourceNodeId).map(node => [node.sourceNodeId!, node.id]));
  state.facetNodes.forEach(facet => {
    if (!facetAgents.has(facet.id)) {
      const id = `facet:${facet.id}`;
      nodes.push({ id, title: facet.title, status: 'pending', action: `${facet.facetName} · ${facet.paperIds.length} 篇关联论文`, sourceKind: 'facet', facetId: facet.id, active: false });
      facetAgents.set(facet.id, id);
    }
  });
  const nodeIds = new Set(nodes.map(node => node.id));
  const links: VisualLink[] = [];
  const seen = new Set<string>();
  const add = (source: string | undefined | null, target: string | undefined | null, type: string, reason: string) => {
    if (!source || !target || source === target || !nodeIds.has(source) || !nodeIds.has(target)) return;
    const key = `${source}|${target}|${type}`;
    if (seen.has(key)) return;
    seen.add(key); links.push({ source, target, type, reason });
  };
  state.edges.forEach(edge => add(edge.source, edge.target, edge.type, edge.reason));
  state.nodes.forEach(node => { if (node.parentId && !state.edges.some(edge => edge.source === node.parentId && edge.target === node.id)) add(node.parentId, node.id, 'decompose', '父子研究任务'); });
  state.facetNodes.forEach(facet => { if (facet.parentId) add(facetAgents.get(facet.parentId), facetAgents.get(facet.id), 'facet', `${facet.facetName}层级`); });
  return { nodes, links };
}

function buildClaimGraphData(state: Snapshot): ResearchGraphData {
  const graph = state.claimGraph!;
  const claimOwners = new Map<string, string[]>();
  for (const claim of graph.claims) {
    const owner = state.nodes.find(node => node.id === claim.ownerNodeId);
    if (!owner) continue;
    claimOwners.set(owner.id, [...(claimOwners.get(owner.id) || []), claim.id]);
  }
  // One-to-one verified owners may be represented by the claim card. Shared or legacy owners stay visible.
  const owners = new Map([...claimOwners].filter(([nodeId, ids]) => ids.length === 1 && nodeId !== 'central' && state.nodes.find(node => node.id === nodeId)?.input?.claimId === ids[0]).map(([nodeId, ids]) => [nodeId, `claim:${ids[0]}`]));
  const nodes: VisualNode[] = state.nodes.filter(node => !owners.has(node.id)).map(node => ({ id: node.id, title: node.title, status: node.status, action: node.logs.at(-1)?.message || node.output?.summary || '等待调度', sourceKind: 'agent', nodeId: node.id, parentId: owners.get(node.parentId || '') || node.parentId, active: node.active }));
  for (const claim of graph.claims) {
    const owner = state.nodes.find(node => node.id === claim.ownerNodeId);
    const replaced = owner ? owners.get(owner.id) === `claim:${claim.id}` : false;
    const parentId = claim.parentClaimIds?.length ? `claim:${claim.parentClaimIds[0]}` : owner && !replaced ? owner.id : owners.get(owner?.parentId || '') || owner?.parentId || (state.nodes.some(node => node.id === 'central') ? 'central' : undefined);
    nodes.push({ id: `claim:${claim.id}`, title: claim.statement, status: owner?.status || 'pending', action: owner?.logs.at(-1)?.message || claim.assessment?.reason || '等待证据检验', sourceKind: 'claim', nodeId: owner?.id, claimId: claim.id, parentId, active: !claim.archived && (!owner || owner.active) });
  }
  // Only current-version relations belong in the live graph. Older links remain in the claim history.
  const currentRelations = graph.relations.filter(relation => graph.claims.some(claim => claim.id === relation.claimId && claim.version === relation.claimVersion));
  const evidenceIds = new Set(currentRelations.map(relation => relation.evidenceId));
  for (const evidence of state.evidence.filter(item => evidenceIds.has(item.id))) {
    const firstRelation = currentRelations.find(relation => relation.evidenceId === evidence.id);
    nodes.push({ id: `evidence:${evidence.id}`, title: evidence.quote || `证据 ${evidence.id}`, status: 'completed', action: evidence.locator || '未定位', sourceKind: 'evidence', evidenceId: evidence.id, parentId: firstRelation ? `claim:${firstRelation.claimId}` : undefined, active: false });
  }
  const links: VisualLink[] = [];
  const nodeIds = new Set(nodes.map(node => node.id));
  const seen = new Set<string>();
  const add = (source: string | null | undefined, target: string | null | undefined, type: string, reason: string, id?: string) => {
    if (!source || !target || source === target || !nodeIds.has(source) || !nodeIds.has(target)) return;
    const key = id || `${source}|${target}|${type}`;
    if (seen.has(key)) return;
    seen.add(key); links.push({ source, target, type, reason, ...(id ? { id } : {}) });
  };
  for (const node of nodes) if (node.parentId && node.sourceKind !== 'evidence') add(node.parentId, node.id, 'decompose', node.sourceKind === 'claim' ? '主张与负责 agent' : '研究任务');
  for (const edge of state.edges) add(owners.get(edge.source) || edge.source, owners.get(edge.target) || edge.target, edge.type, edge.reason);
  for (const claim of graph.claims) for (const parentId of claim.parentClaimIds || []) add(`claim:${parentId}`, `claim:${claim.id}`, 'decompose', '主张依赖');
  for (const relation of currentRelations) add(`evidence:${relation.evidenceId}`, `claim:${relation.claimId}`, relation.type === 'qualify' ? 'evidence-qualify' : `evidence-${relation.polarity || 'unresolved'}`, relation.reason || relation.applicability || '证据关联', relation.id);
  return { nodes, links };
}
