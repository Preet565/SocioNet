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
