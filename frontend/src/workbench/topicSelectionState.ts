import type { ResearchCycle } from '../types';

export type TopicSelectionRequest = { expectedRevision: number } & (
  { mode: 'candidate'; candidateId: string } |
  { mode: 'custom'; customText: string } |
  { mode: 'edited'; customText: string; baseCandidateId: string }
);

export const pendingTopic = (cycle?: ResearchCycle | null) => cycle?.topicSelection?.status === 'pending';
export function topicSelectionRequest(revision: number, mode: 'candidate' | 'custom' | 'edited', value: string, baseCandidateId?: string): TopicSelectionRequest {
  if (mode === 'candidate') return { expectedRevision: revision, mode, candidateId: value };
  if (!value.trim() || value.trim().length > 8000) throw new Error('请填写 1 至 8000 字的课题');
  if (mode === 'edited') {
    if (!baseCandidateId) throw new Error('请重新选择要编辑的候选课题');
    return { expectedRevision: revision, mode, customText: value, baseCandidateId };
  }
  return { expectedRevision: revision, mode, customText: value };
}
