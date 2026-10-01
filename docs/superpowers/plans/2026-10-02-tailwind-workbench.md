# Tailwind research workbench implementation

**Goal:** implement the approved large-board research workspace against the existing backend.
**Architecture:** typed projection helpers, task-scoped React workspace, reusable anchored interaction and notebook components, existing evidence/checkpoint detail primitives.
**Stack:** React, TypeScript, Tailwind CSS/Vite, existing Python workspace API.
**Spec:** ../specs/2026-10-02-tailwind-workbench.md
**Constraints:** no fabricated results; preserve local tasks and credentials; no GitHub push; user authorization already covers implementation and local verification.

1. Add tested workbench contracts and projection/interaction helpers. Test missing data, stale artifacts, exact targeting, and experiment resume eligibility.
2. Build task shell, entry/preparation, board, notebook and anchored discussion. Preserve drafts and reject cross-task responses.
3. Connect jobs, materials, source/version inspection, report/export, model configuration and secondary process views.
4. Run frontend tests/build, isolated browser acceptance and a fresh implementation review. Resolve findings, integrate locally, and show the result.

**Review focus:** data provenance; scientific status vs process status; stale revisions; drafts/IME and task switching; effect cleanup; resource/job action boundaries; responsive readability and anchored interaction; production assets and Linux-compatible dependency resolution.
