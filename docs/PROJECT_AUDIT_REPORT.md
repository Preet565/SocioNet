# SocioNet Phases 1–6 project audit

**Audit date:** 2026-10-20
**Repository:** `SocioNet`
**Scope:** original application plus Phases 1–6, persistence, APIs, frontend, deterministic execution, security/resource boundaries, and deployment checks.

## 1. Executive summary

The audit started from the actual working tree rather than previous completion claims. At the start, Phase 6 changes were present but uncommitted, the audit suite did not yet exist, and the default backend service could be initialized during test collection. The audit added isolated fixtures, 56+ audit/regression cases, persistence/API hardening, analytics/statistical corrections, frontend race/error/semantic fixes, and the Phase 6 documentation/reporting required for release review.

The final verified local result is:

- **188 backend tests passed** with one existing non-failing Starlette/httpx deprecation warning.
- **36 frontend tests passed** when the opt-in live test is enabled; the default frontend suite passes with that one live test skipped.
- Frontend TypeScript typecheck passed.
- Frontend production build passed.
- Real frontend transport → FastAPI → SQLite → backend restart workflow passed.
- A real multi-arm cross-phase HTTP workflow passed with Prime isolation, named arms, histories, export/import, rerun and restart recovery.
- `docker compose config --quiet` passed.
- Secret/absolute-path scans passed.
- Docker image build is **BLOCKED** because the local Docker daemon is not running.

## 2. Final release decision

## READY WITH LIMITATIONS

The core submission demonstration is working and reproducible. No unresolved critical defect remains in simulation determinism, canonical Prime isolation, experiment persistence, branch execution, metric history, causal comparison, multi-seed batches, or the Phase 6 builder workflow.

The project is not marked `READY FOR SUBMISSION` without limitation because:

1. Docker build/run could not be verified in this environment: Docker CLI is installed but the daemon is unavailable.
2. There is no committed browser-click/visual regression suite. React markup, native frontend tests, transport tests, and a live HTTP integration test are present; visual browser automation was not part of the available project toolchain.
3. The synchronous local execution model deliberately does not provide background cancellation or multi-worker execution.
4. Legacy numeric metric fields remain for API compatibility where their historical names are broader than the evidence supports. New nullable diagnostics expose the honest semantics.

These are documented limitations, not hidden failures.

## 3. Baseline and commands

### Baseline

Before the audit repairs, the existing application and Phase 6 implementation passed the then-current suite, but the audit found additional untested correctness boundaries. The audit added tests before declaring the final result.

### Final commands and outcomes

```text
make test
  PASS — 188 backend tests
  PASS — frontend typecheck
  PASS — 35 frontend tests plus 1 opt-in live test skipped by default
  PASS — frontend production build

SOCIONET_LIVE_TEST=1 npm test       # from frontend/
  PASS — 36 frontend tests, 0 skipped

make test-live
  PASS — real frontend transport → Uvicorn/FastAPI → SQLite → restart workflow

python3 -m compileall -q backend/app backend/tests
  PASS

docker compose config --quiet
  PASS

git diff --check
  PASS

git grep absolute-path/secret scan
  PASS — no machine-specific paths or known secret patterns

docker build -t socionet-audit .
  BLOCKED — Cannot connect to Docker daemon at
  unix:///Users/arshpreetdhillon/.docker/run/docker.sock
```

The backend warning is the existing:

```text
StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.
```

It does not fail the suite and was not hidden.

## 4. Phase validation matrix

| Area | Status | Evidence and result |
| --- | --- | --- |
| Original deterministic simulation | PASS | Existing agent, simulation, model, intervention, persistence and API tests remain green. Seeded replay and stable ordering are covered. |
| Phase 1 persistent experiments/provenance | PASS | SQLite/JSON round trips, additive migration, source/branch tags, restart, export, failure publication and Prime filtering are covered. |
| Phase 2 time-series/claims | PASS | Per-tick history, transitive legacy claim ancestry, explicit exposure merging, claim lifecycle and independent fixtures pass. |
| Phase 3 multi-seed/statistics | PASS | Deterministic seed derivation, paired seed/tick checks, sample statistics, exact median interval coverage, effect-size semantics, failures and missing metrics pass. |
| Phase 4 dynamic society | PASS | Archetypes affect actions, bounded relationships/memory, graph metrics, communities, topology history, deterministic evolution and performance bounds pass. |
| Phase 5 causal divergence | PASS | Ancestry links, semantic comparison, perturbations, impact summaries, dependency graph and restart artifacts pass. |
| Phase 6 multi-universe builder | PASS | Schema-v1 config, validation, 2–6 arms, common source hashes, schedules, claim policies, persistence, import/export, rerun, UI and live transport pass. |
| Frontend legacy screens | PASS with browser limitation | Render/normalization/transport tests cover Overview, Social Universe, Citizen Observatory, Information Ecosystem, Butterfly Lab, Intervention Console and Mind Explorer data paths. No automated visual browser suite is committed. |
| Docker deployment | BLOCKED | Compose syntax passes; daemon unavailable for image build/run. |

## 5. Defects found and repaired

### Analytics and claims

1. **Legacy claim origin depended on content list order.** A correction could be sorted before its origin and become the inferred truth label. The repair resolves ancestry to a root content item and derives lifecycle status from the complete claim group.
2. **Exposure counts mixed created content with observed content.** The repair merges explicit exposure records and legacy action observations, excludes content-creation result IDs, deduplicates mirrored records, and preserves state immutability during calculation.
3. **Transitive claim ancestry was incomplete for old snapshots.** Parent chains are now followed with cycle protection and deterministic origin IDs without mutating the snapshot.
4. **Correction reach omitted counterevidence-only records.** Counterevidence is now included where its truth label contradicts the origin claim.
5. **Polarization pooled unrelated topics.** It is now calculated as observation-weighted within-topic mean absolute deviation, with topic-level diagnostics.
6. **“Belief accuracy” was not identifiable from the stored model.** Topic beliefs have evidence counts but no proposition-level truth target. The compatibility numeric field remains, but `/analytics/diagnostics` exposes `belief_accuracy: null`, evaluable count, evidence count/fraction, and claim activity diagnostics. Documentation now qualifies the field.
7. **Claim stopping time was overstated.** The compatibility field now measures ticks since the latest recorded claim activity; diagnostics explicitly do not infer future cessation from a finite observation window.

### Statistical robustness

1. **Cliff’s delta was being inferred from paired differences alone.** That is not a marginal cross-arm Cliff’s delta. The repaired function returns `None` without both control and treatment distributions; aggregate batches calculate it from all cross-arm combinations.
2. **Small-sample confidence intervals reported nominal confidence as achieved confidence.** The order-statistic interval now reports achieved binomial coverage and explicitly marks unattainable requested levels.
3. **Standardized effect sizes emitted zero for undefined zero-variance cases.** They now use `null` for one-sample/zero-variation cases.
4. **Batch aggregation accepted duplicate seeds or mismatched arm ticks.** These are now rejected, and incomplete/failed pairs are excluded without imputation.
5. **Objective success rate and “treatment better” probability are separated.** Threshold ties can count as objective successes while strict win probability excludes ties and respects increase/decrease direction.

### Persistence, API and data integrity

1. **Canonical publication could expose an advanced live state before metric persistence succeeded.** Canonical snapshot and metric publication now occur before the live state is published as advanced; failed writes leave the durable/live state unchanged.
2. **Legacy paired records lacked complete reproducibility metadata.** Persisted config, hypothesis, config hash, source hash, engine/storage manifest and replay metadata were added. Records without sufficient old metadata fail safely instead of silently rerunning from a different source.
3. **Partial branch/storage failure could look completed.** Branch artifact and metric references are published transactionally; failures remain running/failed and do not expose completed dangling references.
4. **Interrupted legacy runs/batches were not all recovered.** Restart recovery now reconciles legacy experiment runs, batches and Phase 6 multi-experiment records.
5. **Imported source integrity checks were incomplete.** Validation now covers citizen, relationship, community, content, claim, memory, event, trace, exposure, belief-update, intervention, causal-link and queue references, plus resource bounds.
6. **Some API list/detail/read responses were unbounded or could serialize mutable state.** Limits, filters, bounded traces/intervention lists, copied state projections and lock-held reads were added.
7. **Reset seed handling was inconsistent for universe compatibility routes.** Supplied seeds are honored.
8. **Sensitive exception details could leak through affected paths.** Validation and execution errors are bounded/sanitized at the API boundary while durable records retain safe failure state.
9. **Pytest import-time service initialization could touch the default database.** `backend/tests/conftest.py` sets an isolated temporary database before collection; tests do not open the user’s canonical database.

### Frontend/API integration

1. **Detached control/treatment branches could be confused with live Prime/Echo.** UI labels and saved-result views now identify detached branches, source universe, run ID, seed, and baseline-relative deltas.
2. **Stale citizen/history/causal responses could overwrite a newer universe/run.** Requests are abortable and guarded by active universe/run identity.
3. **Metric labels implied unsupported real-world meanings.** Labels now say relationship affinity, relationship trust, evidence complement, topic activity, catalog readout, and simulated truth labels where appropriate.
4. **Timeouts and unavailable API were conflated.** Transport now distinguishes unavailable, timeout, HTTP validation, and malformed success payloads.
5. **Snake_case history/analytics payloads were not consistently normalized.** Frontend transport tests now cover backend normalization and missing metrics without inventing zeros.
6. **SVG chart points could render `NaN`; detached results lacked accessible table fallbacks.** Missing values are preserved, chart gaps are handled, and accessible tables are rendered.
7. **Phase 6 live-source preview could race with same-tick changes.** Builder sends the prepared source hash; backend returns 409 before persistence/execution when the source changed.

## 6. Cross-phase integration evidence

### Workflow A — full experiment lifecycle

**PASS.** The executed isolated workflow:

1. Created a temporary 8-citizen ring universe.
2. Advanced it two ticks.
3. Created a legacy durable experiment with treatment intervention.
4. Retrieved run, history, causal comparison, impact summary and causal graph.
5. Reran the saved legacy source.
6. Ran a single-variable citizen suppression perturbation.
7. Validated and ran a Phase 6 two-arm scenario from the checked-in example.
8. Retrieved list and arm detail.
9. Ran a two-repetition Phase 3 batch.
10. Exported the Phase 6 run.
11. Verified Prime live tick/state was not replaced by branch results.
12. Restarted `SimulationService` against the same temporary database and reopened multi-experiment and legacy records.

All endpoint checks returned 200 and the final corrected run completed successfully.

### Workflow B — agent/community evolution

**PASS.** Phase 4 tests and the existing simulation suite cover controlled archetype behavior, relationship formation/decay/following, memory bounds, community events, graph metrics, deterministic replay and a small performance limit. The phase 4 API history tests expose real network/community histories.

### Workflow C — counterfactual divergence

**PASS.** Phase 5 tests cover no-intervention identical histories, reproducible suppression perturbations, ID-independent semantic comparison, causal ancestry, impact summaries and dependency graph endpoints. Phase 3 paired batches repeat matched control/treatment runs over deterministic seeds.

### Workflow D — persistence/recovery

**PASS.** Temporary SQLite and JSON repositories cover migration, canonical filtering, tagged artifacts, restart, failed writes, interrupted records, corruption behavior, exact-source reruns and fallback atomic publication. The live Phase 6 workflow restarts the actual HTTP backend and reopens records.

### Workflow E — frontend/backend integration

**PASS with browser limitation.** The frontend’s real transport was executed against a disposable Uvicorn backend. It validated/ran a three-arm experiment, retrieved histories, imported/exported/reran it, restarted the backend and reopened results. Native/render tests cover all existing screen projections and the builder. No automated visual click suite was available.

## 7. Phase 6 audit details

Phase 6 is JSON, not YAML, which satisfies the versioned JSON/YAML requirement without adding a dependency. The complete contract is documented in [MULTI_ARM_EXPERIMENTS.md](MULTI_ARM_EXPERIMENTS.md), with a runnable example at [docs/examples/multi-arm-rumor.json](examples/multi-arm-rumor.json).

Verified behavior:

- Schema v1 and server JSON Schema endpoint.
- Generated source and captured live-universe source.
- 2–6 named arms with a common baseline.
- Shared claims injected once before forking.
- Exact archetype composition and Phase 4 simulation parameters.
- Immediate/source-relative schedule offsets.
- Claim-specific counterevidence rather than same-topic guessing.
- Citizen, community, post and directional relationship target validation.
- Visibility/reshare policies in actual observation/reply behavior.
- Per-arm metrics, histories, event logs and baseline deltas.
- Common initial source hash across arms.
- Persistent records, config export, portable source bundle, server-side bundle preview, import and exact-source rerun.
- Duplicate configuration workflow and stale source hash rejection.
- Bounded config/source/bundle sizes and effective 2,000 agent-tick cap.

## 8. Security, resource and data-integrity audit

- **Secrets/absolute paths:** no known secret patterns or machine-specific absolute paths in tracked source/docs/config.
- **Imports:** JSON parsing rejects duplicate keys, invalid constants, oversized bodies, wrong schema/engine/hash versions, malformed references and oversized sources/bundles. Unsafe YAML parsing is not used because the supported format is JSON.
- **SQL:** persistence uses parameterized SQL. Dynamic SQL fragments are limited to internal validated field names. Canonical and experiment provenance filters are explicit.
- **CORS:** remains environment-configured; no credentials are enabled by default.
- **Concurrency:** simulation mutations remain under the service lock; the application is intentionally single-process/local and synchronous.
- **Growth:** histories, traces, event responses, schedules, claims, relationships, memory, batch work and multi-arm source/results are bounded. Full final arm state is not returned from list endpoints.
- **Fallback:** JSON fallback uses atomic replacement and raises corruption rather than returning empty data. SQLite lock/corruption errors are not silently redirected to a new empty world.
- **User data:** audit fixtures use `tmp_path`/temporary directories. The optional live test uses a temporary database. No canonical user database was reset or deleted.

## 9. Known limitations and blocked checks

### BLOCKED

- **Docker image build/run:** blocked by unavailable Docker daemon. `docker compose config --quiet` passes, but `docker build -t socionet-audit .` could not connect to Docker Desktop’s socket. Re-run after starting Docker Desktop:

  ```sh
  docker build -t socionet-audit .
  docker run --rm -p 8000:8000 -v socionet-audit-data:/app/data socionet-audit
  ```

### PARTIAL / intentionally unsupported

- No background execution, cancellation or distributed worker support; multi-arm and batch work are synchronous under one service lock.
- Echo is an in-memory live comparison universe across restart; durable experiment source/arm artifacts survive restart. Prime is the canonical persisted universe.
- Browser visual/click automation is not part of the repository’s test dependencies. React markup, native tests, transport tests and a real HTTP integration test are the available evidence.
- The default `Metrics` contract retains legacy fields for compatibility. Use `/analytics/diagnostics` for nullable/qualified semantics.
- Docker base-image build reproducibility could not be runtime-verified here, even though Dockerfiles, lockfile, runtime pins, Compose syntax and documentation are present.

## 10. Repeatable commands

```sh
# Local application
python3 -m venv backend/.venv
. backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000

# Frontend in another terminal
cd frontend && npm ci && npm run dev -- --host 127.0.0.1

# Standard offline suite
make test

# Full cross-stack HTTP suite, disposable database
make test-live
# or: cd frontend && SOCIONET_LIVE_TEST=1 npm test

# Backend only, including all audit tests
cd backend && python3 -m pytest -o addopts=''

# Build frontend
cd frontend && npm run build
```

## 11. Buildathon demo script

1. Start backend and frontend; open the dashboard.
2. Show **Civilization Overview** with the live tick, population, relationship affinity and evidence-complement definitions.
3. Advance one tick, then open **Social Universe** and show real graph edges, degree and betweenness.
4. Open **Information Ecosystem** and show a fictional claim, simulated truth label, claim lifecycle and recorded exposures.
5. Open **Butterfly Lab** and run the catalog experiment. Show saved control/treatment provenance and the semantic divergence/causal explorer; explicitly say the branches are detached from live Prime/Echo.
6. Run a five-seed batch and show paired effect summaries and objective success rate.
7. In the Phase 6 builder, import `docs/examples/multi-arm-rumor.json`, validate its plan, run it, select the delayed arm, inspect the timeline/event table, export the bundle, and rerun the exact source.
8. Use **Intervention Console** for a bounded intervention, advance a tick, and inspect the resulting event/claim updates.
9. Restart the backend and reopen the saved Phase 6 result from Butterfly Lab to demonstrate durable provenance.
10. Mention the honest limitations: fictional rule-based agents, synthetic metrics, synchronous local execution, and no claim of real-world causal inference.
