# Claim-first research core

> Historical design and acceptance record for main@621e725. The 2026-10-01 remediation adds separate blind judge/redteam routing, host evidence gates, responsibility signatures and conservative editorial equivalence. See [current remediation boundaries](audit-remediation.md) and the README; owner-only assessment and unrestricted confirmation described below are superseded.

## Ownership and storage

The existing SQLite envelope now persists `claimGraph` schema 1. The scheduler tree remains the execution/causal view. Each hypothesis first creates a canonical claim, then receives an owner node; every descendant carries its claim ID and version. Preparation nodes establish context before any scientific claim exists. Library facets organize material, not persistent research identity. The compatibility `researchCycle.hypotheses` records reference canonical claims rather than replacing them.

Claims store statement, scope, falsification criterion, immutable versions, owner/history, parent claim references, origin, assessments and user decisions. Reproduction versions additionally store target paper, evidence positions, expected metric, tolerance and conditions. Sources and observations retain their original IDs. A relation is `support` (for/against/mixed/unresolved) or `qualify`. Evidence quality and provenance are separate from direction. Repeated references to the same paper or execution share a source group; this is not a statistical independence claim.

## Research semantics

Thinking nodes specify what a measurement would establish. Workers read/search materials or execute the assigned protocol. Existing execution receipt, raw CSV recomputation and designer-review gates remain in force. A completed process does not imply a supported claim. Owner assessments examine all admissible current-version directional evidence, so omitting a known counterexample cannot yield an unqualified supported/converged result.

Research and reproduction modes are chosen in requirements. Reproduction must establish a located source target and must obtain actual approved experiment observations; a published result alone cannot satisfy a reproduction demand. Research can propose new falsifiable claims and deeper branches. Both paths preserve uncertainty and human research choices.

## Changes, history and expression

The existing optimistic `expectedRevision` guard and impact confirmation protect claim modifications. Claim changes append a version; old evidence links remain attached to their version. Changes to upstream context or reproduction targets also revise the scientific boundary. Invalidation resets assessments and marks dependent expressions stale, including when the statement version itself has not changed. User rejection is a recorded decision, not invented negative evidence.

Rollback preserves later papers, observations, relations, expressions and inactive execution nodes. If later same-version evidence is retained, the restored assessment requires reconsideration. Later expressions stay historical/stale even when their referenced statement version matches. Old snapshots migrate once; new expression text cannot silently create claims.

Expressions reference exact claim versions. `paper.md`/`reproduction-report.md` organize the current problem, claims, evidence directions, limits and unresolved issues as a draft. The export also contains graph/material JSON and the existing code/data/receipt archive. A draft or a user's confirmation is not an independent scientific validation.

## Verification scope (2026-10-01)

Windows backend: 263 tests, 247 passed and 16 platform-dependent skips. Frontend: 56 tests passed, TypeScript and production build passed. Tests cover pre-dispatch ownership, experiment repair, stale tokens, statement/scope/target changes, counterevidence omission, rollback, next-round owner history, source grouping and expression invalidation. Independent review findings were fixed and their reproductions rechecked. The final Windows run also exposed a pre-existing test synchronization race; its wait now requires two executing evidence workers, rather than counting a planning worker as an executing sibling.

Linux (WSL ColdX-Bench): the full 262-test suite passed before the final next-round regression test was added. The affected engine and claim-runtime modules subsequently passed all 52 tests. An actual isolated `start-research.sh` launch created a fresh venv, installed pypdf, served the page and task/health APIs, and exited cleanly with code 0 on SIGTERM. Local Python experiment execution was also checked. This is Linux runtime evidence, not certification of every Linux distribution or hardware configuration.

The seven existing local runtime databases were read without modification and migrated on copies in memory. Their source record IDs and evidence contents were preserved, migration was idempotent and no migrated owner reference was missing.

Browser acceptance uses a separate temporary workspace, a deterministic runner and actual small Python fixture experiments. Confirming a judgment, previewing affected tasks, revising a claim, inspecting old evidence/version history, resuming the affected branch and saving reproduction mode were exercised. No real user's pending research decision was answered by this acceptance run.

The deterministic scientific runner is a test double. These checks validate orchestration, storage, provenance and interaction; they do not establish a new scientific result, model output quality, or successful reproduction of a real paper. Production bundle still has the existing large main-chunk warning.
