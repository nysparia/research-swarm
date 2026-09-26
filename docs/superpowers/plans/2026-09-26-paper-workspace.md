# Paper workspace implementation ledger

Execution: inline, using the existing application, isolated worktree and test state. The user approved the product direction and requested implementation.

- [x] 1. Persistent paper model: topics, versioned sections/suggestions, evidence/experiment projections, Markdown/LaTeX/BibTeX export. Test citation validation, races and provenance.
- [x] 2. Workspace/scheduler integration: topic selection and manuscript APIs; real decision gates; specialist prompts, experimental protocols and persistent user context. Test pause/choice/restart and prevent premature resume.
- [x] 3. Ant Design workbench: topic cards; graph, manuscript, claims, experiments and real terminal panels; decision/impact controls and contribution history. Preserve original task flows and motion.
- [ ] 4. Full validation, browser acceptance, focused fresh review, repair, integration, restart and GitHub push.

Baseline: Python 176 tests (one skip), frontend 30 tests passing. Native worktree tool was unavailable for this projectless task (not a git repository); git fallback created work/paper-workspace at 1983b18. Existing runtime remains on port 4381.

Latest user steering: many simultaneous boards, many terminals, many specialist agents. Implemented 17 board types and an adjustable parallel terminal wall, with actual 12 / 24 / more node sessions. Default paper budget is 240 tasks, 8 iterations, 8 worker concurrency; CPU processes remain serialized per task for comparable measurements.
