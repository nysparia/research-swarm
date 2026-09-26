# Evidence-driven research cycle implementation plan

> Execute inline with executing-plans and test-driven-development. The user's explicit research specification authorizes implementation.

Goal: question -> background -> literature -> grounded topic -> hypotheses -> evidence demands -> sources -> experimental design/execution/repair -> hypothesis re-evaluation -> convergence.

Architecture: extend the existing durable Engine. A dedicated research_cycle controller owns typed research stages, per-hypothesis evidence contracts and experiment revision loops. ResearchRunner performs thinking, retrieval and real process execution; the controller validates stage transitions and evidence provenance. Existing tasks keep their original scheduling behavior; newly started model research uses this cycle. Resource exhaustion and unresolved hypotheses never become convergence.

Tech stack: Python, SQLite Store, existing DeepSeek provider, local research tools, React/Ant Design.

User specification: every stage has an agent; parent demand becomes more precise at each child; data has traceable sources; experimental issues go back to the designer; new data changes hypotheses and can create new ones.

- [x] Typed stage contracts, hypothesis/demand ledger and convergence checks. Reject nonexistent evidence and ungrounded verdicts.
- [x] Durable scheduler transitions: sequential background/literature/topic dependencies, hypothesis branches, evidence-first routing, design/execution/repair, upward return and new hypotheses. Preserve failed attempts and survive restart.
- [x] Runner/workspace integration and prompts: defer topics until literature research, expose stage/ledger/problems in current boards; independent execution and design roles.
- [x] Integration tests covering evidence sufficient, real experiment failure/repair, refutation, new hypothesis, missing evidence, resource exhaustion, restart and intervention. Full suites, bounded provider acceptance, browser check, integration and local restart.

Validation: 230 backend checks (229 passed, one existing platform skip), 37 frontend checks, production build, isolated browser checks, bounded real DeepSeek/local Python acceptance and post-integration workspace startup. Eight existing tasks and saved provider configuration remain intact. Public checkout contains no configured API secret.

Review risks: model output shape errors, process success mistaken for valid experimental data, obsolete evidence from a prior protocol, stale downstream conclusions after intervention, and budget exhaustion mistaken for scientific convergence.
