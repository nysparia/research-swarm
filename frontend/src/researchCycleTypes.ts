export interface ResearchCycleState {
  status: 'running' | 'converged' | 'inconclusive' | 'budget_exhausted'; stage: string; iteration: number;
  topic: { title: string; question: string; rationale: string; evidenceIds: string[]; userChoice?: string } | null;
  hypotheses: { id: string; nodeId: string; statement: string; falsification: string; reason: string; status: string;
    dataRequestIds: string[]; verdict: { status: string; reason: string; limitations: string; evidenceIds: string[] } | null }[];
  dataRequests: { id: string; nodeId: string | null; hypothesisId: string; metric: string; definition: string;
    purpose: string; acceptance: string; scope: string; status: string; evidenceIds: string[]; source?: string }[];
  experiments: { nodeId: string; designNodeId: string; hypothesisId: string; demandId: string; protocolId: string;
    attempt: number; status: string; evidenceIds?: string[]; reviewStatus?: string; reviewReason?: string; problem?: { kind: string; message: string } }[];
  unresolved: string[];
}
