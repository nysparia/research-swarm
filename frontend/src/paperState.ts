import type { Snapshot } from './types';
import type { ExecutionReceipt } from './paperTypes';

export function executionEntries(state: Snapshot | null) {
  const seen = new Set<string>();
  return [...(state?.history || [])].reverse().filter(entry => {
    if (!['tool-executed', 'tool-started'].includes(String(entry.type)) || !entry.execution) return false;
    const execution = entry.execution as ExecutionReceipt;
    const key = execution.stdoutPath || String(entry.id);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).map(entry => {
    const execution = { ...entry.execution as ExecutionReceipt };
    const node = state?.nodes?.find(node => node.id === execution.nodeId);
    const current = Boolean(node?.active && node.version === execution.nodeVersion && state?.project?.round === execution.round);
    if (entry.type === 'tool-started' && (!current || node?.status !== 'running')) execution.status = 'interrupted';
    return { id: String(entry.id), at: String(entry.at || ''), valid: entry.valid === true, current, execution };
  });
}

export function researchCounts(state: Snapshot | null) {
  const nodes = (state?.nodes || []).filter(node => node.active);
  return { total: nodes.length, running: nodes.filter(node => node.status === 'running').length,
    completed: nodes.filter(node => node.status === 'completed').length, failed: nodes.filter(node => node.status === 'failed').length };
}

export const protocolLabels: Record<string, string> = { hypothesis: '可证伪假设', dataset: '数据与划分', baselines: '对照基线', metrics: '评价指标', ablations: '消融 / 反例', successCriterion: '判定标准', reproducibility: '复现协议' };
export function protocolValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(protocolValue).join('；');
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${key}: ${protocolValue(item)}`).join('；');
  return value === undefined || value === null ? '尚未明确' : String(value);
}
