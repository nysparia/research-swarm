import type { ResearchNode, Snapshot } from './types';

export function failureReason(node: Partial<ResearchNode>): string {
  return (
    node.error?.message ||
    [...(node.logs || [])].reverse().find(log => log.level === 'error')?.message ||
    '执行失败，历史执行中保留了原始错误。'
  );
}

export function researchAssessment(state: Snapshot | null) {
  const unsupported = state?.report.claims.filter(claim => !claim.evidenceIds.length).length || 0;
  const unexecuted =
    state?.nodes.filter(
      node =>
        node.active &&
        node.kind === 'experiment' &&
        ['missing_input', 'needs_execution'].includes(String(node.output?.structured?.status)),
    ).length || 0;
  const quality = state?.report.structured?.quality as
    { status?: string; deferredTasks?: { tasks?: unknown[] }[] } | undefined;
  const deferred =
    quality?.deferredTasks?.reduce((count, item) => count + (item.tasks?.length || 0), 0) || 0;
  const unresolved = state?.report.unresolved?.length || 0;
  const gaps = [
    unexecuted && `${unexecuted} 项实验未实际执行`,
    unsupported && `${unsupported} 条判断缺少证据`,
    deferred && `${deferred} 项计划未执行`,
    unresolved && `${unresolved} 个未决问题`,
  ]
    .filter(Boolean)
    .join('；');
  return {
    unsupported,
    unexecuted,
    deferred,
    unresolved,
    description: gaps || '部分研究验收项仍未完成',
    incomplete: Boolean(
      unsupported || unexecuted || unresolved || quality?.status === 'incomplete',
    ),
  };
}
