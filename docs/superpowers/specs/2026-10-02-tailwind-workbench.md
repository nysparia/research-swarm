# Tailwind research workbench

Approved on 2026-10-02 by the user's “开始写吧”, following the annotated Part 3 drawing. Implementation is authorized; no further design gate is needed.

The primary surface is a spacious research board, with task navigation, dynamic module tabs, actual agent activity represented by avatars, an editable notebook, and a bottom conversation composer. Clicking a result opens an anchored discussion. Process details and the existing 2D graph are secondary. Preparation retains the versioned Markdown editor and AI polishing. Completed work exposes reports, paper drafts and real downloads.

All visible research facts come from the existing task/workbench APIs. Missing experimental data has an honest empty state. Reading and asking do not mutate claims. Deepening, challenging, revising and notebook changes during research require a server-computed impact proposal and explicit confirmation. Stale proposals and edits preserve user input. Keep evidence locators, history, human checkpoints, task isolation, and the existing one-key DeepSeek route.

Use Tailwind compiled through Vite. Reuse the accessible overlay primitives and validated domain detail views where useful. Use localized blur entry with reduced-motion support; no full-page blur. Windows/Linux launch and existing local data must remain intact.

Acceptance: frontend state tests, TypeScript/production build, browser checks of preparation, board, anchored discussion, notebook conflict/impact review, live jobs and downloads. Isolated QA data must not become user research data. UI checks do not validate scientific claims or real-model research.
