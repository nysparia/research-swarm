import type { TaskDetail } from '../taskTypes';
import type { CanonicalClaim, Evidence, ResearchNode } from '../types';
import type { Artifact } from './types';

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

export interface AgentCardData {
  node: ResearchNode;
  step: string;
  artifacts: Artifact[];
  discussionArtifact?: Artifact;
  outputArtifact?: Artifact;
  claimArtifact?: Artifact;
  jobArtifact?: Artifact;
  claim?: CanonicalClaim;
  evidence: Evidence[];
  unresolvedEvidenceCount: number;
  relationCounts: { for: number; against: number; mixed: number; qualify: number };
  protocol: Record<string, unknown>;
  problem: string;
}
export interface AgentEdge { source: string; target: string; type: string; reason: string }
export interface AgentStructureData { cards: AgentCardData[]; edges: AgentEdge[]; archivedCount: number }

/** Keep agents as the graph's identities. Claims and evidence are the UI inside their owners. */
export function buildAgentStructure(detail: TaskDetail): AgentStructureData {
  const state = detail.state;
  if (!state) return { cards: [], edges: [], archivedCount: 0 };
  const nodes = state.nodes.filter(node => node.active !== false && !node.input?.superseded);
  const ids = new Set(nodes.map(node => node.id));
  const currentArtifacts = (detail.workbench?.artifacts || []).filter(artifact => artifact.status !== 'stale');
  const claims = (state.claimGraph?.claims || []).filter(claim => !claim.archived);
  const allRelations = state.claimGraph?.relations || [];
  const cards = nodes.map(node => {
    const artifacts = currentArtifacts.filter(artifact => artifact.nodeIds.includes(node.id));
    const outputArtifact = artifacts.find(artifact => artifact.kind === 'node_output');
    const claim = claims.find(item => item.ownerNodeId === node.id);
    const claimArtifact = claim ? artifacts.find(artifact => artifact.kind === 'claim' && artifact.claimRefs.some(ref => ref.claimId === claim.id && ref.version === claim.version)) : undefined;
    const relations = claim ? allRelations.filter(relation => relation.claimId === claim.id && relation.claimVersion === claim.version) : [];
    const evidenceIds = new Set([
      ...strings(node.evidenceIds), ...strings(node.output?.evidenceIds),
      ...artifacts.flatMap(artifact => artifact.evidenceIds), ...relations.map(relation => relation.evidenceId),
    ]);
    // A job belongs only to its recorded owner, never to a neighbouring or similarly named protocol.
    const jobArtifact = artifacts.filter(artifact => artifact.kind === 'experiment_job').sort((a, b) => {
      const left = Date.parse(text(a.content.createdAt)) || 0, right = Date.parse(text(b.content.createdAt)) || 0;
      return left - right || a.revision - b.revision || a.id.localeCompare(b.id);
    }).at(-1);
    const structured = object(outputArtifact?.content.structured);
    const currentProtocol = object(structured.experimentProtocol);
    const protocol = Object.keys(currentProtocol).length ? currentProtocol : object(node.input?.experimentProtocol);
    const run = object(structured.experimentRun);
    const result = object(jobArtifact?.content.result);
    const problem = node.error?.message || text(object(run.problem).message)
      || (['failed', 'timed_out', 'interrupted'].includes(jobArtifact?.status || '') ? text(result.stderr) || text(jobArtifact?.content.error) : '');
    const locatedEvidence = state.evidence.filter(item => evidenceIds.has(item.id));
    return {
      node, step: text(node.input?.researchStep), artifacts, outputArtifact, claimArtifact, jobArtifact, claim,
      discussionArtifact: artifacts.find(artifact => artifact.kind === 'node_state') || claimArtifact || outputArtifact || jobArtifact,
      evidence: locatedEvidence, unresolvedEvidenceCount: evidenceIds.size - locatedEvidence.length, protocol, problem,
      relationCounts: {
        for: relations.filter(relation => relation.type === 'support' && relation.polarity === 'for').length,
        against: relations.filter(relation => relation.type === 'support' && relation.polarity === 'against').length,
        mixed: relations.filter(relation => relation.type === 'support' && ['mixed', 'unresolved'].includes(relation.polarity)).length,
        qualify: relations.filter(relation => relation.type === 'qualify').length,
      },
    };
  });
  const edges: AgentEdge[] = [], seen = new Set<string>();
  const add = (edge: AgentEdge) => {
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) return;
    const key = `${edge.source}|${edge.target}|${edge.type}`;
    if (!seen.has(key)) { seen.add(key); edges.push(edge); }
  };
  state.edges.forEach(add);
  nodes.forEach(node => { if (node.parentId) add({ source: node.parentId, target: node.id, type: 'decompose', reason: '父节点下派需求' }); });
  return { cards, edges, archivedCount: state.nodes.length - nodes.length };
}

export const AGENT_WIDTH = 188;
export const AGENT_HEIGHT = 206;
const COLUMN_GAP = 22, ROW_GAP = 62, MARGIN = 40;
export interface AgentPosition { id: string; parentId: string | null; x: number; y: number; width: number; height: number; depth: number }
export interface AgentLayout { nodes: AgentPosition[]; width: number; height: number }

/** An iterative forest layout tolerates orphan/cyclic legacy data without dropping any agents. */
export function layoutAgents(nodes: Pick<ResearchNode, 'id' | 'parentId'>[], previous?: AgentLayout): AgentLayout {
  if (!nodes.length) return { nodes: [], width: 0, height: 0 };
  const ids = new Set(nodes.map(node => node.id));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const parents = new Map(nodes.map(node => [node.id, node.parentId && ids.has(node.parentId) && node.parentId !== node.id ? node.parentId : null]));
  const visited = new Set<string>();
  for (const node of nodes) {
    const path: string[] = [], active = new Set<string>();
    let id: string | null = node.id;
    while (id && !visited.has(id)) {
      if (active.has(id)) { parents.set(id, null); break; }
      active.add(id); path.push(id); id = parents.get(id) || null;
    }
    path.forEach(key => visited.add(key));
  }
  const children = new Map<string, string[]>(), roots: string[] = [];
  for (const node of nodes) {
    const parent = parents.get(node.id);
    if (!parent) roots.push(node.id);
    else children.set(parent, [...(children.get(parent) || []), node.id]);
  }
  const stack = [...roots].reverse(), traversal: string[] = [], depth = new Map<string, number>();
  roots.forEach(id => depth.set(id, 0));
  while (stack.length) {
    const id = stack.pop()!; traversal.push(id);
    for (const child of [...(children.get(id) || [])].reverse()) { depth.set(child, (depth.get(id) || 0) + 1); stack.push(child); }
  }
  // Each depth is centred independently. A large experiment subtree must not
  // push the central agent's immediate children outside the readable viewport.
  const rows = new Map<number, string[]>();
  for (const id of traversal) { const level = depth.get(id) || 0; rows.set(level, [...(rows.get(level) || []), id]); }
  const rowWidth = (count: number) => count * AGENT_WIDTH + Math.max(0, count - 1) * COLUMN_GAP;
  const widest = Math.max(...[...rows.values()].map(row => rowWidth(row.length)));
  const initial = new Map<string, AgentPosition>();
  for (const [level, row] of rows) {
    const left = MARGIN + (widest - rowWidth(row.length)) / 2;
    row.forEach((id, column) => initial.set(id, { id, parentId: byId.get(id)!.parentId, x: left + column * (AGENT_WIDTH + COLUMN_GAP), y: MARGIN + level * (AGENT_HEIGHT + ROW_GAP), width: AGENT_WIDTH, height: AGENT_HEIGHT, depth: level }));
  }
  const prior = new Map((previous?.nodes || []).map(node => [node.id, node]));
  // An actual reparenting is a structural change. A mere new sibling is not a reason to move existing cards.
  const compatible = nodes.every(node => !prior.has(node.id) || (prior.get(node.id)!.parentId === node.parentId && prior.get(node.id)!.width === AGENT_WIDTH && prior.get(node.id)!.height === AGENT_HEIGHT));
  const placed = new Map<string, AgentPosition>();
  if (compatible && prior.size) {
    for (const node of nodes) { const before = prior.get(node.id); if (before) placed.set(node.id, before); }
    for (const id of traversal) {
      if (placed.has(id)) continue;
      const desired = initial.get(id)!, parent = placed.get(parents.get(id) || '');
      const origin = parent?.x ?? desired.x;
      const y = parent ? parent.y + AGENT_HEIGHT + ROW_GAP : desired.y;
      let x = origin, offset = 0;
      const blocked = (candidate: number) => candidate < MARGIN || [...placed.values()].some(item => Math.abs(item.y - y) < AGENT_HEIGHT + ROW_GAP / 2 && Math.abs(item.x - candidate) < AGENT_WIDTH + COLUMN_GAP);
      while (blocked(x)) { offset += 1; const step = Math.ceil(offset / 2) * (AGENT_WIDTH + COLUMN_GAP); x = origin + (offset % 2 ? step : -step); }
      placed.set(id, { ...desired, x, y });
    }
  } else for (const [id, position] of initial) placed.set(id, position);
  const result = nodes.map(node => placed.get(node.id)!);
  return { nodes: result, width: Math.max(...result.map(node => node.x + node.width)) + MARGIN, height: Math.max(...result.map(node => node.y + node.height)) + MARGIN };
}

export interface StructureView { x: number; y: number; scale: number }
export function initialStructureView(layout: AgentLayout, viewport: { width: number; height: number }): StructureView {
  const fit = Math.min(1, Math.max(1, viewport.width - 48) / Math.max(1, layout.width), Math.max(1, viewport.height - 48) / Math.max(1, layout.height));
  if (fit >= 1) return { x: (viewport.width - layout.width) / 2, y: (viewport.height - layout.height) / 2, scale: 1 };
  // Large swarms open at readable scale. The user can explicitly fit the full map at any time.
  const root = layout.nodes.find(node => node.id === 'central') || layout.nodes.find(node => !node.parentId) || layout.nodes[0];
  const scale = 1;
  return { x: viewport.width / 2 - ((root?.x || 0) + AGENT_WIDTH / 2) * scale, y: 62 - (root?.y || 0) * scale, scale };
}
