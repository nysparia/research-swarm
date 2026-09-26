import type { Claim } from './types';

export interface TopicCandidate { id: string; title: string; question: string; hypothesis: string; contribution: string; feasibility: string; firstExperiment: string }
export interface PaperSuggestion { id: string; markdown: string; evidenceIds: string[]; sourceNodeIds: string[]; sourceVersions: Record<string, number>; at: string }
export interface PaperSection { id: string; title: string; markdown: string; revision: number; author: 'AI' | 'user'; evidenceIds: string[]; sourceNodeIds: string[]; sourceVersions: Record<string, number>; suggestion: PaperSuggestion | null; stale: boolean; evidenceStatus: string; updatedAt?: string }
export interface ResearchDecision { id: string; nodeId: string; question: string; rationale?: string; options: { label: string; effect: string }[] }
export interface Contribution { id: string; kind: string; title: string; answer: string; at: string; effect: string; nodeId?: string; sectionId?: string }
export interface ExecutionReceipt { tool: string; status: string; nodeId: string; nodeVersion: number; round: number; returnCode: number | null; elapsedMs: number; script?: string; stdoutPath?: string; stderrPath?: string; receipt?: string; evidenceIds?: string[]; artifacts?: { name?: string; path: string }[] }
export interface ExperimentRecord { nodeId: string; title: string; status: string; executionStatus: 'executed' | 'failed' | 'unexecuted'; design: Record<string, unknown>; summary: string; unresolved: string[]; claimIds: string[]; receipts: ExecutionReceipt[] }
export interface PaperWorkspace {
  budgetTier?: 'compact' | 'team' | 'swarm';
  revision: number; topics: TopicCandidate[]; selectedTopicId: string | null; sections: PaperSection[];
  decisions: Contribution[]; pendingDecision?: ResearchDecision | null; claims: (Claim & { support: string; experimentEvidenceIds: string[] })[];
  experiments: ExperimentRecord[]; issues: string[]; approved: boolean;
  coverage: { written: number; total: number; linkedClaims: number; claims: number; executedExperiments: number; experiments: number };
}
