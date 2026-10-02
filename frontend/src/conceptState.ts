import type { ResearchDocument } from './taskTypes';

export const needsConceptClarification = (document: ResearchDocument) => Boolean(document.conceptUnderstanding?.blockers.length);
export function conceptProgress(document: ResearchDocument): string {
  const status = document.conceptUnderstanding?.status;
  if (document.polishing) return status === 'checking' ? '正在理解问题' : status === 'searching' ? '正在查询术语含义' : '正在整理需求';
  if (needsConceptClarification(document)) return '请先明确核心研究对象';
  return status === 'interrupted' ? '概念理解已中断，请在对话中重试' : status === 'failed' ? '概念理解未完成' : '需求已整理';
}
