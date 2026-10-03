import type { ResearchNode, Snapshot, ModelWait } from '../types';

export const executionNodes = (state?: Snapshot | null) => (state?.nodes || []).filter(node => node.active && !node.input.superseded);
export const nodeExecutionLabel = (node: ResearchNode) => node.status === 'running' && node.modelWait ? '等待模型服务' : node.status === 'failed' ? '执行受阻' : '';
export function executionLabel(state?: Snapshot | null): string {
  if (!state) return '';
  const summary = state.executionSummary;
  if (state.paused) return summary?.status === 'waiting_user' ? '等待你确认' : '已暂停';
  if (summary?.status === 'blocked') return `研究受阻 · ${summary.failed} 个失败节点`;
  const nodes = executionNodes(state);
  const running = summary?.running ?? nodes.filter(n => n.status === 'running' && !n.modelWait).length;
  const waiting = summary?.waitingProvider ?? nodes.filter(n => n.status === 'running' && n.modelWait).length;
  const failed = summary?.failed ?? nodes.filter(n => n.status === 'failed').length;
  return [running ? `${running} 运行中` : '', waiting ? `${waiting} 等待服务` : '', failed ? `${failed} 分支受阻` : ''].filter(Boolean).join(' · ');
}
export function modelWaitLabel(wait: ModelWait, now: number): string {
  const due = wait.nextRetryAt ? Date.parse(wait.nextRetryAt) : NaN;
  const seconds = Number.isFinite(due) ? Math.max(0, Math.ceil((due - now) / 1000)) : 0;
  const attempt = wait.retryNumber > 0 ? `自动重试 ${wait.retryNumber}/${wait.maxRetries}` : '等待同一接口恢复';
  return `${attempt} · ${seconds > 0 ? `约 ${seconds} 秒后尝试` : '等待恢复探测'}`;
}
export const retryNodeRequest = (node: ResearchNode) => ({ nodeId: node.id, expectedNodeVersion: node.version });
