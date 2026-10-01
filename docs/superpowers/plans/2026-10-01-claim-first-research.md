# Claim-first research implementation

The user approved implementing the claim/evidence/expression architecture on 2026-10-01. Preserve the existing conversation UI, 2D graph, human decisions, local execution receipts and Windows/Linux support. DSH/local workers execute tools; the product owns orchestration.

## Binding design and shared contract

`state.claimGraph = {schemaVersion: 1, claims: [], relations: [], expressions: [], materials: []}` is the canonical persisted research graph, stored in the existing atomic SQLite envelope. `project.taskMode` is `research` or `reproduction`. Existing papers remain source materials, `state.evidence` remains precise located observations. Papers, execution receipts and datasets can all be materials. Claims and evidence are many-to-many; source group counts never claim statistical independence.

A claim has `id, statement, scope, falsification, origin, ownerNodeId, parentClaimIds, version, versions, assessment, createdAt, updatedAt`. Origin is an object including kind (hypothesis/reproduction/legacy/user), nodeId and source evidence IDs when known. Each immutable version stores statement, scope, falsification, actor, reason and timestamp. Assessment has `status` (unassessed/supported/refuted/mixed/inconclusive), `reason, evidenceIds, limitations, confirmedByUser`. Model judgments stay provisional. Owner agent plans and reasons; descendant workers receive `claimId` and `claimVersion` and never become paper-owned persistent researchers.

Relations have `id, claimId, claimVersion, evidenceId, type` (`support` or `qualify`), `polarity` (for/against/mixed/unresolved for support; unresolved for qualify), `reason, applicability, quality` (usable/limited/unusable), `sourceGroup, createdAt`. Invalid input is rejected, missing direction is unresolved, contradictory/negative evidence is retained. Changing a claim appends a version; prior-version relations remain visible but cannot support the revised statement. No disqualified class or vote-based truth scoring.

Expressions have `id, kind` (paper/reproduction_report), `title, markdown, claimRefs` (claimId/version), `status` (draft/confirmed), and timestamp. References must resolve to the specified claim version. Unsupported statements are visibly flagged; references alone never make a statement verified. User confirmation and retraction are separately recorded, and editing invalidates dependent work.

Domain module API (mutates passed state, called under engine lock):

* `ensure_graph(state)` idempotently migrates legacy states and refreshes material metadata; returns graph. Legacy citations use unresolved support unless a recorded scientific verdict gives a direction.
* `create_claim(state, statement, scope='', falsification='', origin=None, owner_node_id=None, parent_claim_ids=None, claim_id=None, actor='AI') -> claim`.
* `get_claim(state, claim_id) -> claim`, `revise_claim(state, claim_id, statement, scope=None, falsification=None, actor='user', reason='') -> claim`.
* `add_relation(state, claim_id, evidence_id, relation_type='support', polarity='unresolved', reason='', applicability='', quality='limited', claim_version=None) -> relation`.
* `assess_claim(state, claim_id, status, reason, evidence_ids=None, limitations='', confirmed_by_user=False) -> claim` requires located usable/limited evidence and current-version relations for decisive assessments; no evidence cannot prove a claim.
* `create_expression(state, kind, title, markdown, claim_refs, confirmed=False) -> expression` validates all references.
* `preserve_history(restored_state, previous_state)` merges immutable claims/versions/relations/materials/expressions for rollback without presenting future claim versions as current.

## Task 1 — canonical domain and migration

Implement `research_swarm/claims.py` and focused tests. Cover stable migration, unknown refs, positive/negative/qualifying evidence, source grouping, append-only revisions, stale evidence, expressions and rollback preservation. Do not edit engine/UI files.

## Task 2 — runtime, task modes and export

Integrate canonical graph at initialization/readback, worker contexts, claim creation before dispatch, inherited claim identity, typed evidence relations, verdicts, user interventions, rollback and next rounds. Preserve experiment protocol/receipt checks. Reproduction mode identifies claims from requested source papers and validates replication conditions, research mode generates hypotheses. Modes belong to requirements and cannot be changed silently. Build expression references from claims and export graph, materials, code/data and traceable reports. Test realistic deterministic workflow and migration against copied local state.

## Task 3 — claim-centered 2D UI

Extend frontend types and graph projection to show claim ownership rather than paper ownership. Keep root, preparation tasks, dependent execution tasks and expandable evidence. Claim drawer displays proposition/scope/test, evidence direction and exact location, versions, owner logs and interventions. Show references in result view and task mode in editable requirements. Preserve pan/zoom, drag, Ant Design and existing motion. Add graph tests, build and inspect in isolated browser.

## Task 4 — Linux completion and integration review

Retain pending Linux launcher/platform/service work. Validate actual launch, process cleanup and local experiment in WSL; run backend and frontend suites, inspect full diff and do independent code review. Document precise limits of fixture/model validation. Back up local state before integration and restart with existing waiting decisions intact. No force push, no new UI redesign.

## Acceptance

One claim exists before its workers run; all descendants carry identity/version. New hypotheses become claims, workers return located evidence, negative findings persist, revisions cannot inherit stale proof, and expressions reference exact claim versions. Existing tasks migrate without losing IDs, papers, evidence or audit history. Reproduction/research are selectable and affect prompts. Both OS runtimes can execute local experiments. Final verification distinguishes tests, browser validation and actual scientific results.
