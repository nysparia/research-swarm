# Paper research workspace

The approved direction is a local AI for Computer Science research partner. A user's question becomes candidate topics, an editable project brief, an evolving manuscript and reproducible experiments. Users influence the topic, tradeoffs and interpretation without reading every paper. This implementation follows the user's instruction to start; no additional design approval is required.

## Interaction

- Keep the task sidebar and a quiet single-input empty state. Preparation adds model-generated topic candidates with hypotheses, feasibility and falsification tests. Selecting a topic changes the brief, with an attributable decision record.
- During research, a shared workbench contains the real 3D swarm, editable paper, claim/evidence ledger, experiment protocol/results and tool terminal. A companion panel explains current work and shows consequential research choices. No fabricated terminals, progress, measurements or agent counts.
- Specialized workers cover literature, methods, baselines, experiments, statistics, falsification and writing, created as useful tasks within explicit resource limits. DSH remains optional tool infrastructure, not a general-purpose research controller.
- Model decisions pause execution; the user reviews affected nodes and chooses or supplies an alternative. Choices are passed to all subsequent workers. User contributions and their impact remain visible across rounds.
- Manuscript sections have independent revisions. Background proposals never replace user-authored sections. Users can review/apply an AI suggestion, edit directly and inspect evidence/source nodes. New rounds retain the paper.
- Outputs include Markdown, LaTeX source, BibTeX, claim/experiment ledgers, decisions, scripts, data and execution receipts. A generated paper is a draft; empty sections, unsupported claims and unexecuted experiments remain visible. User approval acknowledges a specific draft revision, not scientific validity.

## Engineering

Extend the existing Python workspace and scheduler rather than add another agent runtime. Persist paper state in conversation.json. Project real node outputs through validated paperSections / experimentDesign / researchDecision structures. Validate evidence IDs and limit sizes. Atomically pause in the scheduler on decision creation. Require current state revision for decisions; block resume while a decision is pending. Record user choices in the engine history and worker context.

UI uses existing Ant Design and BlurReveal. Components stay task-scoped, preserve local input on polling and support narrow widths. Heavy graph remains lazy-loaded. Existing tasks migrate lazily; no destructive change to research history.

## Validation

Tests cover manuscript edit races, non-overwrite, invalid citations, topic selection persistence, actual execution versus proposed experiments, decision pauses and stale submissions, export validity and restart. Build and browser-verify real controls with an isolated fixture workspace; make a bounded actual-provider request separately. Preserve current user runtime during development and restart only when idle. Review once after implementation, fix findings, run relevant suites and publish to the already-authorized organization repository.
