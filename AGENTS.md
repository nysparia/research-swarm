# Agent workspace conventions

## Private agent files

Keep agent-owned memory, notes, and scratch output under `.agents/`. This
directory is local to the checkout and is ignored by Git.

Use these subdirectories:

- `.agents/memory/` — durable project or agent memory that helps future runs.
- `.agents/notes/` — notes for the current task or investigation.
- `.agents/tmp/` — disposable plans, intermediate data, and debug output.
- `.agents/<agent-id>/` — files that must remain isolated to one agent.

Use a descriptive filename and include the task or date when the file is tied
to one run, for example `.agents/notes/task-123.md`.

## Shared instructions

Put instructions that every contributor or agent must follow in tracked
documentation such as this file or another clearly named file under `docs/`.
Keep private memory in `.agents/` so generated context does not become part of
the project history.

## Research artifacts

Task state and experiment artifacts belong under `.research-state/tasks/<task-id>/`
and temporary repository outputs belong under `work/` or `benchmark-results/`.
Follow the existing `.gitignore` rules for those paths.
