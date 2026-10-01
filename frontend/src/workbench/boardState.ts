import type { Artifact } from './types';
import type { ResearchNode } from '../types';

const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === 'string' ? value : '';
export const protocolStrings = (value: unknown): string[] => Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string' && !!v.trim()) : [];
export interface ChartSeries { key: string; points: { label: string; value: number }[] }

// A series compares the same recorded metric. Different units never share an axis.
export function chartSeries(metrics: unknown): ChartSeries[] {
  if (Array.isArray(metrics)) {
    const groups = metrics.map(object);
    const keys = [...new Set(groups.flatMap(row => Object.keys(row)))].filter(key => !['method', 'name', 'label'].includes(key));
    return keys.map(key => ({ key, points: groups.flatMap((row, i) => typeof row[key] === 'number' && Number.isFinite(row[key])
      ? [{ label: text(row.method) || text(row.name) || text(row.label) || `记录 ${i + 1}`, value: row[key] as number }] : []) })).filter(series => series.points.length > 0);
  }
  const walk = (value: unknown, prefix = ''): ChartSeries[] => Object.entries(object(value)).flatMap(([key, item]) => {
    const name = prefix + key;
    if (typeof item === 'number' && Number.isFinite(item)) return [{ key: name, points: [{ label: '本次实测', value: item }] }];
    return item && typeof item === 'object' && !Array.isArray(item) ? walk(item, name + '.') : [];
  });
  return walk(metrics);
}

export interface BoardExperiment {
  id: string; artifact: Artifact; plan?: Artifact; result?: Artifact;
  protocol: Record<string, unknown>; measured: boolean;
}
export function boardExperiments(artifacts: Artifact[], nodes: ResearchNode[]): BoardExperiment[] {
  const current = artifacts.filter(a => a.status !== 'stale');
  const plans = current.filter(a => a.kind === 'node_output' && Object.keys(object(object(a.content.structured).experimentProtocol)).length > 0);
  const jobs = current.filter(a => a.kind === 'experiment_job');
  const byNode = new Map(nodes.map(node => [node.id, node]));
  const matched = new Set<string>();
  const hasMeasured = (job?: Artifact) => Boolean(job && job.status === 'completed' && job.content.status === 'completed' && chartSeries(object(job.content.result).metrics).length);
  const sources = plans.map(plan => {
    const protocol = object(object(plan.content.structured).experimentProtocol);
    const result = jobs.filter(job => {
      const request = object(job.content.request);
      if (request.protocolId && request.protocolId !== protocol.id) return false;
      const ancestors = new Set<string>();
      let id = text(request.nodeId) || job.nodeIds[0];
      while (id && !ancestors.has(id)) { ancestors.add(id); id = byNode.get(id)?.parentId || ''; }
      return plan.nodeIds.some(id => ancestors.has(id));
    }).at(-1);
    if (result) matched.add(result.id);
    return { id: plan.id, artifact: result || plan, plan, result, protocol, measured: hasMeasured(result) };
  });
  // Keep independent jobs available without guessing which protocol they belong to.
  return [...sources, ...jobs.filter(job => !matched.has(job.id)).map(job => {
    const request = object(job.content.request);
    const owner = byNode.get(text(request.nodeId) || job.nodeIds[0]);
    return { id: job.id, artifact: job, result: job, protocol: object(owner?.input.experimentProtocol), measured: hasMeasured(job) };
  })];
}

export function relatedClaim(focus: Pick<BoardExperiment, 'artifact' | 'protocol'> | undefined, artifacts: Artifact[], nodes: ResearchNode[]) {
  if (!focus) return undefined;
  const claims = artifacts.filter(a => a.kind === 'claim' && a.status !== 'stale');
  const refs = focus.artifact.claimRefs;
  if (refs.length) return claims.find(a => refs.some(ref => a.claimRefs.some(c => c.claimId === ref.claimId && c.version === ref.version)));
  const owner = nodes.find(n => focus.artifact.nodeIds.includes(n.id));
  const hypothesis = text(focus.protocol.hypothesis) || text(owner?.input.hypothesisId);
  return hypothesis ? claims.find(a => object(a.content.origin).hypothesisId === hypothesis) : undefined;
}

export interface BoardFeed { taskId: string; entries: Artifact[]; knownIds: string[]; freshIds: string[] }
export function reconcileBoardFeed(previous: BoardFeed | undefined, taskId: string, artifacts: Artifact[]): BoardFeed {
  const candidates = artifacts.filter(a => ['node_output', 'claim', 'expression', 'experiment_job'].includes(a.kind));
  if (!previous || previous.taskId !== taskId) return { taskId, entries: candidates.filter(a => a.status !== 'stale').slice(-4), knownIds: candidates.map(a => a.id), freshIds: [] };
  const byId = new Map(candidates.map(a => [a.id, a]));
  const known = new Set(previous.knownIds);
  const fresh = candidates.filter(a => !known.has(a.id) && a.status !== 'stale');
  const entries = previous.entries.map(a => byId.get(a.id) || { ...a, status: 'stale', staleReason: '该产物已不在当前研究中，保留历史位置。' });
  return { taskId, entries: [...entries, ...fresh], knownIds: [...new Set([...previous.knownIds, ...candidates.map(a => a.id)])], freshIds: [...new Set([...previous.freshIds, ...fresh.map(a => a.id)])] };
}
