import type { Snapshot } from './types';
import type { Workbench, Interaction } from './workbench/types';

export type TaskPhase = 'empty' | 'requirements' | 'retrieving' | 'researching' | 'completed' | 'failed';
export interface TaskSummary { id: string; title: string; phase: TaskPhase; updatedAt: string; round: number }
export interface PendingConcept { term: string; question: string; identityStatus?: 'confirmed' | 'ambiguous'; identityQuote?: string; qualifier?: string; core?: boolean }
export interface ConceptUnderstanding {
  status: 'checking' | 'searching' | 'drafting' | 'ready' | 'needs_clarification' | 'interrupted' | 'failed';
  revision: number;
  runId?: string;
  blockers: PendingConcept[];
  unresolved?: PendingConcept[];
  policyVersion?: string;
  resolved?: { term: string; status: string; definition?: string; source?: string; sourceIds?: string[]; quote?: string; identityStatus?: 'confirmed' | 'ambiguous'; identityQuote?: string }[];
}
export interface ResearchDocument { markdown: string; revision: number; polishing: boolean; polishedFrom?: number | null; source: 'model' | 'local'; questions: string[]; error: string | null; conceptUnderstanding?: ConceptUnderstanding }
export interface Message { id: string; role: 'user' | 'assistant' | 'system'; content: string; at: string; kind?: 'requirements' | 'progress' | 'result'; interactionId?: string; topicSelectionId?: string; status?: string; context?: { scope?: 'overview' | 'node'; nodeId?: string; nodeTitle?: string; artifactId: string; artifactRevision: number; artifactTitle?: string } }
export interface RunSummary { round: number; at: string; summary: string; mode: string }
export interface TaskDetail {
  task: TaskSummary;
  taskMode?: 'research' | 'reproduction';
  phase: TaskPhase;
  document: ResearchDocument;
  messages: Message[];
  state: Snapshot | null;
  error: string | null;
  artifacts: { name: string; kind: string; url: string }[];
  runs: RunSummary[];
  modelReady?: boolean;
  workbench?: Workbench;
  interactions?: Interaction[];
}
export const taskPhaseLabels: Record<TaskPhase, string> = { empty: '新任务', requirements: '准备研究', retrieving: '检索资料', researching: '研究中', completed: '本轮已结束', failed: '执行失败' };
