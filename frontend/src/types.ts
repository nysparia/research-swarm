export type NodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'waiting_user';
export type Feedback = 'interested' | 'not_interested' | 'read_later' | null;
export type Page = 'workbench' | 'requirements' | 'swarm' | 'papers' | 'recommendations' | 'report';
export type ActionKind = 'modify' | 'insert' | 'reject' | 'deepen';

export interface Activity {
  id: string;
  at: string;
  actor: 'AI' | 'user' | 'system';
  message: string;
  level?: string;
  nodeId?: string;
}
export interface Requirement {
  id: string;
  description: string;
  acceptance: string;
  constraints: string;
  version: number;
  sourceNodeIds?: string[];
}
export interface Claim {
  id: string;
  text: string;
  evidenceIds: string[];
  claimId?: string;
  claimVersion?: number;
  assessmentStatus?: CanonicalClaim['assessment']['status'];
  nodeId?: string;
  status: 'candidate' | 'confirmed' | 'rejected';
  limitations?: string;
}
export interface CanonicalClaimVersion { version: number; statement: string; scope: string; falsification: string; actor: string; reason: string; at?: string; createdAt?: string }
export interface CanonicalClaim {
  id: string; statement: string; scope: string; falsification: string; ownerNodeId: string | null;
  parentClaimIds: string[]; version: number; versions: CanonicalClaimVersion[];
  origin?: { kind?: string; nodeId?: string; evidenceIds?: string[] };
  assessment: { status: 'unassessed' | 'supported' | 'refuted' | 'mixed' | 'inconclusive'; reason?: string; evidenceIds?: string[]; limitations?: string; confirmedByUser?: boolean; review?: { independent?: boolean; status?: string; role?: string } };
  createdAt?: string; updatedAt?: string;
  archived?: boolean;
  decisions?: { actor: string; decision: string; version: number; reason?: string; at?: string; responsibilityName?: string; responsibilityAcknowledged?: boolean }[];
}
export interface ClaimRelation { id: string; claimId: string; claimVersion: number; evidenceId: string; type: 'support' | 'qualify'; polarity: 'for' | 'against' | 'mixed' | 'unresolved'; reason?: string; applicability?: string; quality?: 'usable' | 'limited' | 'unusable'; sourceGroup?: string; createdAt?: string; quote?: string; locator?: string; rule?: string; confidence?: number; semanticGate?: { passed: boolean; issues?: string[] } }
export interface ClaimExpression { id: string; kind: 'paper' | 'reproduction_report'; title: string; markdown: string; claimRefs: { claimId: string; version: number }[]; status: 'draft' | 'confirmed'; createdAt?: string }
export interface ClaimGraph { schemaVersion: number; claims: CanonicalClaim[]; relations: ClaimRelation[]; expressions: ClaimExpression[]; materials: Record<string, unknown>[] }
export interface ResearchNode {
  error?: { code?: string; message: string; at?: string; phase?: string; retryable?: boolean } | null;
  id: string;
  parentId: string | null;
  title: string;
  role: string;
  kind: string;
  phase: 'plan' | 'execute' | 'aggregate';
  status: NodeStatus;
  progress: number;
  input: Record<string, unknown>;
  output: { summary?: string; evidenceIds?: string[]; claims?: Claim[]; unresolved?: string[]; structured?: Record<string, unknown>; [key: string]: unknown } | null;
  logs: Activity[];
  sourceNodeId: string | null;
  requirementIds: string[];
  evidenceIds: string[];
  startedAt: string | null;
  finishedAt: string | null;
  elapsedMs: number;
  version: number;
  active: boolean;
}
export interface Paper {
  id: string;
  title: string;
  abstract: string;
  year: number | string;
  venue: string;
  doi: string;
  authors: string[] | string;
  codeUrl: string;
  pdfAvailable: boolean;
  pdfPath: string | null;
  score: number;
  scores: Record<string, number>;
  reason: string;
  reproducibility: string;
  workerStatus: NodeStatus;
  facetNodeIds: string[];
  evidenceIds: string[];
  facts: Record<string, unknown>[];
  scoreBasis: Record<string, unknown>[];
  feedback: Feedback;
}
export interface FacetNode {
  id: string;
  parentId: string | null;
  title: string;
  facetId: string;
  facetName: string;
  topology: string;
  paperIds: string[];
}
export interface Evidence {
  id: string;
  paperId: string;
  quote: string;
  locator: string;
  type: string;
  confidence: number;
  extractor: string;
}
export interface Checkpoint {
  id: string;
  type: 'requirements' | 'recommendations' | 'comparison' | 'final';
  status: 'pending' | 'confirmed' | 'superseded';
  title: string;
  summary: string;
  createdAt: string;
  resolvedAt?: string;
  revision: number;
  decision?: string;
  userNote?: string;
  responsibilityName?: string;
  responsibilityAcknowledged?: boolean;
}
export interface Snapshot {
  revision: number;
  project: { id: string; title: string; description: string; round: number; sourcePath: string; mode: 'evidence' | 'llm'; taskMode?: 'research' | 'reproduction'; researchDecision?: { id: string; question: string; options: { label: string; effect: string }[] } | null };
  stage: number;
  paused: boolean;
  status: 'idle' | 'running' | 'waiting_user' | 'failed' | 'completed';
  requirements: Requirement[];
  nodes: ResearchNode[];
  edges: { source: string; target: string; type: 'decompose' | 'return' | 'compare'; reason: string }[];
  papers: Paper[];
  facetNodes: FacetNode[];
  evidence: Evidence[];
  checkpoints: Checkpoint[];
  activeCheckpointId: string | null;
  activities: Activity[];
  report: { summary: string; claims: Claim[]; expressionId?: string; claimRefs?: { claimId: string; version: number }[]; unresolved: string[]; approved: boolean; ready?: boolean; structured?: Record<string, unknown> };
  claimGraph?: ClaimGraph;
  history: Record<string, unknown>[];
  operations?: { id: string; type: string; nodeId?: string; message: string; status: 'running' | 'completed' | 'failed'; startedAt: string; finishedAt?: string; error?: string; retrieval?: LiteratureRetrievalSummary }[];
}
export interface Impact { revision: number; affectedIds: string[]; downstreamCount: number }
export type ProviderRole = 'main' | 'judge' | 'redteam';
export interface ProviderSettings {
  type: string;
  baseUrl: string;
  model: string;
  hasKey: boolean;
  configured?: boolean;
  ready?: boolean;
  independentFromMain?: boolean;
  sharedWithMain?: boolean;
}
export interface SearchSettings {
  profile: 'standard' | 'deep';
  sources: ('openalex' | 'arxiv' | 'semantic_scholar')[];
  crossrefFallback: boolean;
  queryCount: number;
  perQuery: number;
  candidateLimit: number;
  maxRequests: number;
  maxSeconds: number;
  maxCalls: number;
  citationDepth: number;
  seedCount: number;
  neighborsPerSeed: number;
  yearFrom: number;
}
export type SearchKeySource = 'openalex' | 'semantic_scholar';
export interface LiteratureRetrievalSummary {
  version?: string;
  status?: 'complete' | 'partial' | 'failed';
  candidateCount?: number;
  duplicateCount?: number;
  requestCount?: number;
  cacheHits?: number;
  retainedCitationEdges?: number;
  sourceCounts?: Record<string, number>;
  stopReason?: string | null;
  queries?: { query: string; intent: string; yearFrom: number | null }[];
  errors?: { source: string; stage: string; reason: string }[];
}
export interface Settings {
  conceptSearch?: { provider: 'tavily'; ready: boolean };
  mode: 'evidence' | 'llm';
  provider: ProviderSettings;
  providers?: Record<ProviderRole, ProviderSettings>;
  providerConfigurations?: Record<ProviderRole, ProviderSettings>;
  providerRouting?: 'shared_main' | 'per_role';
  reviewPolicy?: 'independent' | 'shared';
  search?: SearchSettings;
  searchProfiles?: Record<'standard' | 'deep', Partial<SearchSettings>>;
  searchKeys?: Record<SearchKeySource, { hasKey: boolean }>;
  sourcePath: string;
  capabilities: Record<string, unknown>;
}
