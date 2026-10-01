export interface DraftBlock { id: string; kind: string; title: string; content: string; source: string; locked: boolean }
export interface Artifact {
  id: string; kind: string; title: string; status: string; content: Record<string, unknown>;
  revision: number; sourceRevision: string | number; nodeIds: string[];
  claimRefs: { claimId: string; version: number }[]; evidenceIds: string[]; dependencies: string[];
  sourceRefs: Record<string, unknown>[]; staleReason?: string;
}
export interface ExperimentJob {
  id: string; revision: number; status: string; createdAt: string; updatedAt?: string;
  request: { code?: string; timeoutSeconds?: number; materialIds?: string[]; resources?: { cpuCores: number; gpuCount: number }; checkpoint?: { path: string; resumeCode: string } | null };
  checkpoint?: { path: string; sha256: string; bytes?: number } | null;
  result?: { metrics?: unknown; stdout?: string; stderr?: string; elapsedMs?: number; returnCode?: number; artifacts?: Record<string, unknown>[]; environment?: unknown; receipt?: string; script?: string };
  attempts: { status: string; startedAt?: string; finishedAt?: string; receipt?: { path: string; sha256: string } }[];
  resourceAvailability?: { logicalCpus: number; nvidiaGpus: number; maxConcurrentJobs: number; enforcement: string };
}
export interface Workbench {
  taskId: string; revision: number;
  plan: { intent: string; capabilities: string[]; modules?: { id: string; capability: string; title: string; enabled: boolean }[]; rationale?: string; source: string };
  draft: { revision: number; blocks: DraftBlock[] };
  artifacts: Artifact[];
  report: { markdown?: string; ready?: boolean; approved?: boolean; artifactIds?: string[] };
  executionSettings: { revision: number; maxTimeoutSeconds: number; materialIds: string[]; resources?: { cpuCores: number; gpuCount: number } };
}
export type InteractionKind = 'ask' | 'challenge' | 'revise' | 'deepen';
export interface InteractionRequest {
  kind: InteractionKind; text: string; replacement?: string; nodeId?: string;
  showInConversation?: boolean; scope?: 'overview' | 'node';
  target: { artifactId: string; revision: number; selection?: { quote: string } };
}
export interface Proposal {
  id: string; type: 'intervention' | 'draft'; status: string; revision: number; documentRevision: number;
  target?: InteractionRequest['target']; text?: string; replacement?: string;
  affectedNodeIds: string[]; affectedArtifactIds: string[]; blocks?: DraftBlock[];
  command?: { nodeId: string; kind: string; text: string };
}
export interface Interaction { id: string; status: string; reply?: string | null; error?: string; stale?: boolean; source?: string; proposal?: Proposal; text: string; target: InteractionRequest['target']; showInConversation?: boolean; kind?: InteractionKind; nodeId?: string; scope?: 'overview' | 'node'; context?: { scope: 'overview' | 'node'; nodeId?: string; artifactId: string; artifactRevision: number } }
export interface ProposalReview { proposal: Proposal; request?: InteractionRequest; onApplied?: () => void }
export interface Material { id: string; kind: string; filename?: string; name?: string; sha256?: string; bytes?: number; status?: string; [key: string]: unknown }
