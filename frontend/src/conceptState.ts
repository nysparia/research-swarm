import type { ResearchDocument } from './taskTypes';

export const conceptBlockers = (document: ResearchDocument) => (document.conceptUnderstanding?.blockers ?? []).filter(item => item.identityStatus !== 'confirmed' && item.core !== false);
export const needsConceptClarification = (document: ResearchDocument) => Boolean(conceptBlockers(document).length);
export const hasPendingConceptFacts = (document: ResearchDocument) => Boolean(document.conceptUnderstanding?.unresolved?.some(item => item.identityStatus === 'confirmed'));
export const conceptSourceLabel = (kind?: string) => kind === 'official' ? '官方来源' : kind === 'third_party' ? '第三方来源' : '来源类型未标注';
export function conceptProgress(document: ResearchDocument): string {
  const status = document.conceptUnderstanding?.status;
  if (document.polishing) return status === 'checking' ? '正在理解问题' : status === 'searching' ? '正在查询术语含义' : '正在整理需求';
  if (status === 'interrupted') return '概念理解已中断，请在对话中重试';
  if (status === 'failed') return '概念理解未完成';
  if (needsConceptClarification(document)) return '请先明确核心研究对象';
  if (hasPendingConceptFacts(document)) return '官方资料待核实，需求已整理';
  return '需求已整理';
}
