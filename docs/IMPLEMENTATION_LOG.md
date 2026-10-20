# SocioNet implementation log

## Phase 1 — durable experiment storage and provenance

### Implemented

- Added additive SQLite schema initialization and migration metadata.
- Tagged snapshots as canonical, experiment source, control, or treatment artifacts.
- Added durable experiment run records with status, timestamps, source metadata, branch snapshot references, serialized results, and failure details.
- Added JSON sidecars for tagged snapshots and experiment records when SQLite is unavailable.
- Added experiment creation, listing, retrieval, and JSON export APIs, including `/experiments/runs` aliases.
- Kept the legacy `/experiments/run` and `/experiments/side-by-side` contracts available.
- Updated catalog status projections to recover from persisted run records after restart.
- Added Butterfly Lab durable run history and provenance links.

### Verification

Commands run:

```text
python3 -m pytest       # 21 passed
npm run typecheck       # passed
npm run build           # passed
make test               # passed
git diff --check        # passed
```

The backend emits the existing FastAPI/Starlette `httpx` deprecation warning; it does not fail the suite.

### Known limitations

- Runs execute synchronously in the API process; background queues and cancellation are not part of Phase 1.
- The active Echo universe remains in memory across backend restart; its experiment source and branch artifacts are durable and explicitly tagged.
- Experiment records currently use JSON payloads around the existing full-state result rather than normalized event tables.

## Phase 2 — metric history and claim lifecycle

### Implemented

- Added additive claim fields to state/content/events, with deterministic `claim-{origin-content-id}` IDs that remain shared across control/treatment forks.
- Added claim origin/intervention provenance, bounded lineage records, and lifecycle transitions for active, disputed, confirmed, and corrected claims.
- Added one metric sample at tick 0 and after each completed tick, retaining at most 200 samples per state or branch.
- Added SQLite `metric_samples` and `claim_metric_samples` tables with schema version 3, plus JSON sidecar support and old-snapshot hydration.
- Added claim-specific exposure, reshare, reply, fact-check, and correction metrics to current and historical analytics.
- Added filtered `/analytics/history`, `/analytics/claims`, `/analytics/claims/{claim_id}`, and durable experiment history endpoints while preserving the Phase 1 API responses.
- Added a real-data Butterfly Lab SVG comparison chart with selectable historical metrics and accessible per-tick points.
- Documented metric definitions, bounded retention, migration behavior, and the boundary with multi-seed and later causal-analysis phases.

### Verification

Commands run:

```text
python3 -m pytest tests/test_phase2.py tests/test_persistence.py tests/test_api.py  # 14 passed
make test                                                                        # 26 backend tests passed; frontend typecheck/build passed
cd frontend && npm run typecheck                                                  # passed
cd frontend && npm run build                                                      # passed
git diff --check                                                                  # passed
```

The backend emits the existing FastAPI/Starlette `httpx` deprecation warning; it does not fail the suite. The backend tests include additive migration/old snapshots, deterministic history, claim lifecycle/provenance, filtered history/claims APIs, and experiment history after service restart.

### Known limitations

- Only the most recent 200 samples are retained per state/branch; an old snapshot can be hydrated but cannot recover history that was never persisted.
- Exposure is an action-log observation count, not passive audience reach. Reshares intentionally overlap claim-bearing replies because the current domain has no separate reshare action.
- A single run remains one deterministic control/treatment pair; Phase 3 adds a separate bounded batch layer for repeated pairs and cross-run summaries. Causal ancestry remains a later phase.

## Phase 3 — multi-seed experiments and statistical robustness

### Implemented

- Added strict `BatchExperimentConfig` and objective models with bounded population, ticks, repetitions, intervention lists, confidence level, scalar objective metrics, and computational budget validation.
- Added stable SHA-256 seed derivation from a base seed and one-based repetition. Each repetition uses one pair seed for identical control/treatment source state initialization.
- Added bounded serial batch execution under the existing service lock. The batch and each child run are persisted before execution, after each repetition, and at final aggregation.
- Added additive SQLite columns/indexes and JSON sidecars for batch metadata, progress, aggregate output, batch IDs, repetition numbers, and pair/arm seed metadata on child runs.
- Added control, treatment, and paired descriptive statistics; exact distribution-free paired-median confidence intervals; paired Cohen's d; Cliff's delta; probability treatment is better; and explicit objective success rate over successful pairs.
- Added batch collection, detail, progress/status, and linked-child-run APIs with `/experiments/batches` compatibility aliases.
- Added real-data Butterfly Lab batch controls and durable batch history using the API's returned progress and aggregate result. Offline preview does not claim to have run a batch.
- Added targeted validation, statistics, persistence, API, restart, and deterministic-repeat tests.

### Verification

Commands run:

```text
python3 -m pytest tests/test_batch_experiments.py tests/test_persistence.py tests/test_api.py  # 17 passed
python3 -m pytest                                                                        # 34 passed
make test                                                                                # backend tests passed; frontend typecheck/build passed
cd frontend && npm run typecheck                                                        # passed
cd frontend && npm run build                                                            # passed
git diff --check                                                                        # passed
```

The backend emits the existing FastAPI/Starlette `httpx` deprecation warning; it does not fail the suite.

### Known limitations

- Batch execution is synchronous, serial, and in-process. There is no background queue, cancellation, live polling during the request, or distributed worker support.
- Batch confidence intervals describe the paired median with a conservative exact order-statistic method; they are not claims about real-world populations.
- Failed children are retained as durable `failed` runs and excluded from paired aggregate statistics; a batch can finish as `completed_with_failures`.

## Phase 4 — dynamic agents, relationships, and communities

### Implemented

- Added additive `SimulationConfig` validation for population, memory/history limits, relationship/following caps, formation/decay rates, homophily, cross-community interaction, archetype behavior, initial network structure, and community evolution bounds.
- Added explicit behavioral archetype parameters for posting, replying, fact-checking, following, leaving/joining, relationship formation/decay, homophily, and cross-community interaction. Presentation labels remain projections; the configured parameters are decision inputs.
- Added deterministic seeded initial `community`, `ring`, `random`, and `empty` network structures with stable ID ordering and bounded per-agent relationship/following density.
- Added homophily-aware observation and relationship formation, deterministic affinity/trust decay, and following adoption/unfollowing evolution.
- Added bounded memory and topology limits to relationship pruning, following lists, community memberships, and dynamic history samples.
- Added recorded community `join`, `leave`, `split`, and `merge` events plus per-tick network and membership history in serializable state snapshots.
- Added undirected-projection degree, average degree, density, and normalized Brandes betweenness metrics to analytics and frontend graph projections. The metric cost is documented as O(VE) per tick for the unweighted graph.
- Added `/network/history` and `/communities/history` APIs, reset-time `SimulationConfig` support, graph metric definitions, and minimal frontend display of degree, betweenness, and density.
- Preserved old snapshots and API responses with additive defaults. New histories and metrics are deterministically ordered by tick and stable IDs.

### Verification

Commands run:

```text
python3 -m pytest tests/test_phase4.py       # 7 passed
python3 -m pytest                            # 41 passed
cd frontend && npm run typecheck             # passed
cd frontend && npm run build                 # passed
git diff --check                             # passed
```

The backend emits the existing FastAPI/Starlette `httpx` deprecation warning; it does not fail the suite.

### Known limitations

- Betweenness is recalculated synchronously per tick with Brandes over an unweighted undirected projection, costing O(VE); this is bounded by the population and relationship caps but is not a cached or incremental graph algorithm.
- Community split/merge rules are deterministic threshold rules over ideology means and shared topics, not a general community-detection algorithm. At most one split and one merge are applied per tick.
- Dynamic histories are bounded by `SimulationConfig.history_limit` (5–200) and are stored in snapshots/API projections; they are not normalized into separate persistence tables.
- The initial graph is directed in agent state and browser edges, while degree/betweenness intentionally use its undirected social-topology projection. These are synthetic measures for the fictional simulator.

## Phase 5 — claim provenance and causal divergence explorer

### Implemented

- Added deterministic ancestry fields and registry links across claim provenance, content parents, content exposures, decision traces, belief updates, action events, and interventions. Old snapshots hydrate these additive records idempotently.
- Added semantic content and claim keys and branch comparison aligned by tick, citizen, and semantic occurrence. Generated event/content/claim/intervention/snapshot IDs are excluded from equality; silent stateful intervention differences are still surfaced when no action event diverges.
- Added citizen, claim, and community branch impact summaries with stable deltas and explicit evidence labels.
- Added typed causal dependency graph projections and aliases for canonical and durable control/treatment branches. Graph payloads distinguish simulator-declared **modeled dependency** links from **not statistically established effect** claims.
- Added immediate single-variable perturbation validation and deterministic effects for `suppress_citizen`, `suppress_post`, and directional `suppress_relationship`.
- Added additive causal payloads to the legacy experiment response, durable causal artifact endpoints, and the Butterfly Lab divergence explorer with impact counts, earliest semantic divergence, dependency-edge preview, and evidence note.
- Preserved legacy state loading and experiment fields while adding Phase 5 fields with Pydantic defaults.

### Verification

Commands run:

```text
python3 -m pytest tests/test_phase5.py       # 9 passed
make test                                    # 50 backend tests passed; frontend typecheck/build passed
cd frontend && npm run typecheck             # passed
cd frontend && npm run build                 # passed
git diff --check                             # passed
```

The backend emits the existing FastAPI/Starlette `httpx` deprecation warning; it does not fail the suite.

### Known limitations

- The ancestry graph records deterministic simulator dependencies; it does not estimate statistical causal effects or make claims about real populations.
- Citizen suppression leaves prior content and incoming ties in place; post suppression targets one existing top-level post; relationship suppression is directional.
- Impact summaries are calculated from the saved paired branch states and bounded event histories. They do not reconstruct passive reach or historical records that were never persisted.

## Phase 6 — configurable multi-universe experiment builder

### Implemented

- Added strict schema-v1 JSON configuration and JSON Schema endpoint; generated and copied live-universe sources; configurable population/topology/archetype parameters and exact composition.
- Shared claims are injected once before every arm forks; explicit claim IDs, credibility, visibility and reshare policies influence the engine. Added targeted fact-check policies without removing legacy global interventions.
- Added 2–6 named arms and a selected common baseline, immediate/source-relative schedules, validated citizen/post/claim/directional-relationship targets, and captured community observer/messenger semantics.
- Reused `Simulation`, interventions, metric histories and semantic comparisons; new execution remains independent of the HTTP adapter and browser.
- Added immutable config/source SHA-256 checksums, matching per-arm initial hashes, final tagged snapshots, baseline deltas, real metric/event history and durable failures. SQLite migration v5 adds `multi_experiments`; old snapshots are preserved.
- Added source-hash preconditions to reject stale preview/run sources (including same-tick mutations), read-only config/bundle validation, exact-source rerun, portable manifest import/export and config duplication.
- Added Butterfly Lab structured editor, advanced JSON, import/export, source/cost preview, history/reopen, per-arm results, timeline/event drill-down, accessible tables and loading/empty/error/offline states.
- Added atomic JSON fallback publication and explicit corruption/locking errors. Interrupted runs recover as failed; storage failures never publish completed dangling references.
- Added offline native frontend tests and an optional live HTTP test using the real frontend transport against disposable Uvicorn/SQLite instances, including backend restart.
- Isolated the backend's import-time default service database before pytest collection, so ordinary test collection no longer opens or recovers the user's live database.
- Documented the contract in `docs/MULTI_ARM_EXPERIMENTS.md` and added `docs/examples/multi-arm-rumor.json`.

### Verification

Runtime used here: Python **3.14.7**, Node **22.22.0** on macOS. The pinned container Python is still 3.12.8; no dependency/runtime upgrade was made.

```text
make test
  131 backend tests passed; one existing Starlette/httpx deprecation warning
  frontend TypeScript passed; 30 frontend tests passed
  one opt-in live test skipped in the offline command
  production frontend build passed (27 modules)
SOCIONET_LIVE_TEST=1 node --experimental-strip-types --test tests/liveWorkflow.test.mjs
  from frontend/: 1 live cross-stack workflow passed, no skips
docker compose config --quiet
  passed
git diff --check
  passed
```

The live workflow validated/ran three arms, checked equal initial hashes and delayed evidence timing, queried events/histories, verified canonical isolation, round-tripped the exported configuration, imported/reran results and reopened them after restarting the real HTTP backend. No user database was reset.

The documented example was also executed directly: **12 citizens × 4 ticks × 3 arms = 144 agent-ticks**, 48 final action events per arm, **0.130 seconds** for prepare + three-arm simulation on this machine (no HTTP or persistence in that timing; a concurrent test process was present). The source hash stayed unchanged. This is a local smoke measurement, not a Linux/container benchmark.

### Known limitations and next phase

- Effective cost cap is `min(configured budget, 2000)` agent-ticks, with 100 citizens/20 ticks/6 arms as individual maxima. Source/config/bundle sizes and response counts are bounded. Large live worlds are rejected, not truncated.
- Execution is synchronous and serialized under a single-process service lock; use one backend worker. No background queue or cancellation is claimed. Browser requests await completion.
- A crash may leave orphan artifacts; running records recover as failed on restart and can be explicitly rerun.
- Exact bundle replay requires the same engine version. Hashes detect integrity changes, not authenticity. The UI preserves raw bundle numeric tokens during transfer.
- Docker daemon was unavailable, so image build/run was **not** verified. No browser click/visual end-to-end automation was available; verification covers React markup, frontend logic/transport and live HTTP integration.
- Phase 7 sensitivity/ablation/optimization is next. Phases 7–10 remain unimplemented.

## Complete Phases 1–6 audit checkpoint

### Audit result

- Added `docs/PROJECT_AUDIT_REPORT.md` with a feature matrix, cross-phase workflows, defects/root causes/fixes, security/resource review, exact commands, limitations and release decision.
- Added audit regression fixtures for analytics/claim ancestry/exposure semantics, statistical robustness, persistence/API recovery, corruption/resource validation, and Phase 6 integration.
- Final backend result: **188 passed**, one existing non-failing Starlette/httpx deprecation warning.
- Final frontend result: **36 passed** with `SOCIONET_LIVE_TEST=1`, typecheck/build passed. Default `npm test` passes with the opt-in live test skipped.
- Final live HTTP workflow: frontend transport → disposable Uvicorn/FastAPI → temporary SQLite → restart/reopen passed.
- `docker compose config --quiet`, compileall and diff/secret/path checks passed.
- Docker image build is blocked only by the unavailable local Docker daemon.

### Release decision

**READY WITH LIMITATIONS.** Core simulation, persistence, experiments, batches, causal comparison, multi-arm builder, frontend transport and restart workflows are verified. Docker runtime and browser visual automation remain explicitly unverified, and synchronous single-process execution remains an intentional limitation.
