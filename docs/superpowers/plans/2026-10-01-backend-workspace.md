# Research workspace backend implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for the independent backend modules. The root implements shared API integration and performs cross-module acceptance. The user explicitly requested backend implementation now; no frontend redesign or remote push is included.

**Goal:** Complete the backend needed by the output-first research UX while preserving existing task data and the old frontend.

**Architecture:** Keep the existing claim/evidence engine authoritative. Add a per-task SQLite workbench projection with versioned artifacts and replayable events, structured requirement blocks, targeted interaction proposals, and managed experiment jobs. Model roles may share one explicitly configured DeepSeek credential without claiming independent validation.

**Tech Stack:** Python 3.10+, standard library SQLite/HTTP/threading, existing research engine. No new mandatory runtime dependencies.

**Spec:** User-provided ui_ux design: minimal entry, progressive conversation and notebook, dynamically composed research modules, output-first board, anchored local questions that can alter research, persistent notebook, incremental reports, claim/evidence provenance. Previous backend gap analysis is the accepted implementation scope.

## Global constraints

- Do not change frontend layout or run/answer any existing user's research task.
- Preserve original claims, evidence, old versions, failed experiments and task isolation.
- Questions explain; only explicit confirmed mutation proposals may change research.
- Never describe same-model role separation as independent scientific validation.
- Existing API clients remain compatible. New APIs use revision guards and stable IDs.
- State and all secrets remain outside git. No real paid research experiment is required for acceptance.
- A killed process resumes only from a declared actual checkpoint, otherwise it is an explicit restart.
- Experiment provenance and raw-data gates remain mandatory.

## Task 1: Workbench data, progressive notebook, intent and artifacts

Owner: data implementer. Files: new research_swarm/workbench_store.py, workbench_projection.py, research_plan.py, draft_blocks.py; tests/test_workbench_data.py.

Interfaces:
- WorkbenchStore(path): sync(record, state=None, files=()) -> snapshot; snapshot(); artifact(id, revision=None); events(after=0, limit=100); append_event(kind, payload); close(). Methods thread-safe, SQLite transactions, stable IDs/revisions, no duplicate events on unchanged sync.
- infer_plan(text, task_mode='research') -> {intent, capabilities, modules, rationale}; combines review, investigation, reproduction, experimentation and paper preparation; deterministic fallback does not claim AI inference.
- reconcile_blocks(markdown, previous=None, actor='AI') -> list of {id, kind, title, content, locked, source}; preserve IDs across unchanged and moved sections and user-locked content.
- patch_blocks(blocks, operations) -> blocks; render_blocks(blocks) -> markdown. Operations add/update/remove; update/remove by id; validate limits/unknown fields; locked user clauses cannot be overwritten by AI reconciliation.
- Snapshot fields: revision (event cursor), plan, draft, artifacts, report. Artifact includes id, kind, title, revision, status, content, nodeIds, claimRefs, evidenceIds, dependencies. Node outputs, claims, actual file outputs and a live research report are projected with provenance. Stale artifacts remain inspectable and versioned. Do not infer support from citations.

- [x] Write behavior tests for stable block identity, protected user changes, concurrent saves, event replay and stale artifact propagation.
- [x] Run red tests, implement pure domain modules and SQLite storage, run green tests.
- [x] Report interfaces and targeted verification to root; root owns API integration.

## Task 2: One-key role policy and usage accounting

Owner: provider implementer. Files: providers.py, semantic_review.py, runner.py, claim_runtime.py, research_cycle.py, associated tests. Coordinate any API changes; root owns workspace/server.

Interfaces:
- Settings.configure_deepseek(payload) -> public settings: optional apiKey/model, use existing main key if omitted, official endpoint only, mode llm, explicit reviewPolicy='shared'. Existing explicit independent settings stay compatible until this operation.
- Settings.usage(task_id=None) -> real token totals/calls/errors; actual billed currency remains unknown unless supplied, do not invent costs. Optional Settings.usage_context(task_id, node_id=None) context manager.
- Shared policy routes isolated main/judge/redteam prompts using main credentials when reviewer is not separately configured; same-source reviews are completed but independent=false, reviewLevel='same_model'. Independent policy preserves current strict rejection. Host gates and raw evidence still required. Science verdicts expose provenance; never counterfeit independent=true.

- [x] Tests first: one-key configure, secrets absent from public views, legacy strict routing, completed shared review retains independence=false, host gates still reject forged observations, usage persists.
- [x] Implement and verify affected research/evidence suites.

## Task 3: Managed experiment jobs and material intake

Owner: execution implementer. Files: new experiment_jobs.py, research_materials.py; local_tools.py only when compatible; tests/test_experiment_jobs.py. Root owns HTTP routes.

Interfaces:
- ExperimentJobs(root): submit(payload)->job; list(); get(id); action(id, action, expected_revision)->job; close(). Actions cancel/retry/resume; bounded resources, CPU/GPU intent and environment snapshots, durable logs/receipts/metrics, process-tree termination, no secret inheritance. Store checkpoints with hash and permit resume only when a verified file plus explicit resume protocol exists. Restart recovery marks interrupted jobs, never silently says running/completed.
- Jobs accept Python code, timeoutSeconds up to 86400, declared checkpoint/resumeCode optional, resource request, attached managed material IDs; artifact/protocol links expose provenance. Export/read logs by safe task-local path. Existing short engine tools continue working; allow explicit execution settings and managed-job wiring for long engine execution without bypassing experiment receipt gates. Coordinate helper signatures with root.
- ResearchMaterials(root): add(payload)->material; list(); get(id); path(id)->Path. Text/base64 upload or explicit source path copy, sha256, original source metadata, immutable revisions; optional pinned git repository intake using subprocess argument arrays, no shell, no credential persistence. Only user-requested sources are read; reject symlinks/traversal and enforce size limits. Materials copied to isolated jobs for input, never arbitrary host file paths from model prompts.

- [x] Red/green tests with actual short Python processes for queue, cancel, failure, restart, resume checkpoint and durable artifacts; material traversal and intake hashes.
- [x] Long timeout accepted without waiting an hour; do not claim a full real-paper reproduction.

## Task 4: Workspace integration and interaction transactions

Owner: root. Files: workspace.py, new workspace_backend.py, server.py, tests/test_workspace_backend.py, docs/backend-workspace-api.md.

APIs under /api/tasks/:id:
- GET workbench, plan, draft, artifacts/index, artifacts/:id/versions, events?after=N (JSON) and events/stream (SSE Last-Event-ID).
- POST plan for explicit capability updates, draft for guarded block edits and draft/proposals plus draft/apply for running-research scope changes.
- POST interactions {target:{artifactId,revision,selection?},text,kind:'ask'|'challenge'|'revise'|'deepen'}; explanations never pause/rewrite requirements. Mutations produce a durable proposal with current engine revision, artifact revision, affected node/artifact IDs and operation kind.
- POST proposals/:id/apply {expectedRevision,confirmed:true}; reject stale proposals, idempotent apply, dispatch existing engine intervention, mark dependent outputs stale, refresh report. No unrelated sibling reruns.
- GET/POST jobs, materials; POST jobs/:id/actions; GET jobs/:id/logs. GET usage; POST /api/settings/deepseek.
- Include workbench projections in existing task detail additively. Publish event updates from real engine state via supervisor. Generic active-task messages use read-only context answers; explicit modification continues existing behavior.

- [x] Red tests through real WorkspaceApplication and HTTP for compatible old API, live artifacts before completion, anchored question preservation, proposal conflicts/idempotence, draft impact, replay after reconnect, secret isolation and job routes.
- [x] Implement adapter and integrate; run targeted tests.

## Task 5: Verification and delivery

- [x] Independent code review, resolve correctness findings with regressions.
- [x] Full Python suite, existing frontend tests/build only if frontend contracts require it, actual HTTP acceptance against isolated state and actual local experiment.
- [x] Update README/API docs with exact capabilities and boundaries; no claim of scientific success.
- Integration: fast-forward the clean local checkout after checks; preserve user runtime data and active service. No GitHub push unless separately requested. Git history records the integration result.

## Review focus

1. Stale async replies must not overwrite new drafts, targets or research decisions.
2. Task-local IDs, files, events and secrets must not cross tasks or escape paths.
3. Confirmed mutations must apply once, reject changed context, and preserve unrelated branches.
4. Actual failure/cancellation/restart must not become positive scientific evidence.
5. Same-model role reuse must never acquire the independent flag through normalization or summaries.
