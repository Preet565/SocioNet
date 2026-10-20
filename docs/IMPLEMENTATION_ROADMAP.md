# SocioNet implementation roadmap

This roadmap tracks the ten-phase evolution of NPC Internet — The Butterfly Effect. Each phase is implemented as a tested vertical slice; incomplete work remains explicitly marked.

1. **Reproducible experiment storage and provenance** — durable experiment records, tagged source/branch snapshots, run APIs, and Butterfly Lab history. **Implemented.**
2. **Time-series analytics and claim lifecycle** — per-tick samples, first-class claims, propagation histories, and comparison charts. **Implemented.**
3. **Multi-seed experiments and statistical robustness** — paired seed batches, descriptive summaries, effect sizes, objective success rates, durable child runs, and bounded progress. **Implemented.**
4. **Dynamic agents, relationships, and communities** — configurable archetype behavior, bounded evolving networks, homophily and cross-community interaction, community transitions, graph metrics, and dynamic histories. **Implemented.**
5. **Claim provenance and causal divergence explorer** — ancestry, event dependencies, branch comparison, and traceable impacts. **Planned.**
6. **Configurable multi-universe experiment builder** — versioned scenario configs, validation, multiple arms, import/export, and resource limits. **Planned.**
7. **Sensitivity, ablation, and intervention optimization** — reproducible parameter searches and explicit objectives. **Planned.**
8. **Real-time command center and replay** — event streaming, controls, rewind, replay, and synchronized timelines. **Planned.**
9. **Scenario gallery and resilience experiments** — reusable fictional scenarios with assumptions and evaluation criteria. **Planned.**
10. **Production hardening and reproducible release** — reports, CI, recovery tests, benchmarks, and final audit. **Planned.**

## Dependency order

Phase 2 establishes stable claim and metric-history contracts. Phase 3 builds on those contracts for bounded paired runs and cross-run summaries. Phase 4 extends the domain event model with dynamic topology and community history while retaining additive snapshot defaults. Phase 5 and later phases remain planned; the configurable builder and search tools must not be inferred from the Phase 4 implementation. Realtime features remain later because the current single-process SQLite ownership model is intentionally synchronous and deterministic.
