import type { TaskDetail } from '../taskTypes';
import type { Artifact } from './types';

export interface ResearchContext { nodeId?: string; artifact: Artifact }
export function contextForNode(detail: TaskDetail, nodeId: string): ResearchContext | null {
  const artifacts = (detail.workbench?.artifacts || []).filter(a => a.nodeIds.includes(nodeId) && !['stale', 'historical'].includes(a.status));
  const artifact = artifacts.find(a => a.kind === 'node_state') || artifacts.find(a => a.kind === 'claim') || artifacts.find(a => a.kind === 'node_output') || artifacts[0];
  return artifact ? { nodeId, artifact } : null;
}
export function overviewArtifact(detail: TaskDetail): Artifact | undefined {
  const artifacts = (detail.workbench?.artifacts || []).filter(a => !['stale', 'historical'].includes(a.status));
  return artifacts.find(a => a.id === 'report:live') || artifacts.find(a => a.kind === 'node_state' && a.nodeIds.includes(detail.state?.nodes.find(n => n.active && !n.parentId)?.id || '')) || artifacts[0];
}
export function contextIsCurrent(context: ResearchContext, detail: TaskDetail): boolean {
  return Boolean(detail.workbench?.artifacts.some(a => a.id === context.artifact.id && a.revision === context.artifact.revision && !['stale', 'historical'].includes(a.status)) && (!context.nodeId || detail.state?.nodes.some(n => n.id === context.nodeId && n.active)));
}
export function isNearConversationEnd(scrollTop: number, scrollHeight: number, clientHeight: number): boolean { return scrollHeight - scrollTop - clientHeight < 80; }
