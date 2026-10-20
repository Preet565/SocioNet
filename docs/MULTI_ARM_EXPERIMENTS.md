# Multi-universe experiment builder (schema v1)

Phase 6 adds configurable named experiment arms without changing the existing catalog, paired experiments, batches, or canonical universes. The format is **JSON**, with an authoritative schema at `GET /multi-experiments/schema`. No YAML library, external credentials, generative model, or additional service is needed.

## Run from the interface

1. Start the backend and frontend using the root README. Open **Butterfly Lab**.
2. In **One question. Multiple possible worlds.**, enter a name and hypothesis. Choose a generated world or a captured live universe.
3. For generated worlds, choose seed, population, topology, and exact archetype counts. Empty `agent_composition` uses the engine's trait-derived defaults. Advanced JSON exposes all validated simulation and archetype parameters.
4. Define shared fictional claims and their authors, credibility, visibility, and reshare probabilities.
5. Add 2–6 named arms and choose a baseline. Add immediate or scheduled interventions to each arm.
6. **Validate & preview plan** returns the resolved configuration, source hash, population, source tick, planned cost, arms, and community-target warnings. It does not save a run or advance a universe.
7. **Run validated experiment** creates a durable run. All arms start from exactly the same source hash. Results contain real final metrics, arm-minus-baseline deltas, semantic divergence, branch histories, and action logs.
8. Open a saved run, select an arm and metric, inspect its timeline/event window, and export its config or portable source bundle. **Duplicate** loads an editable config. **Rerun exact source** creates a new record using the original saved source, even if the live universe changed.

Import `docs/examples/multi-arm-rumor.json` for a small reproducible three-arm example. It uses 12 citizens × 4 ticks × 3 arms = 144 agent-ticks. No outcome is prescribed by the file.

## Configuration semantics

- `schema_version` is integer `1`; unknown config fields/versions are rejected.
- `source.mode=generated` requires `seed` and `simulation`. `source.universe_id` is the history namespace and does not mutate that live universe.
- `source.mode=universe` captures the selected live state under the service lock. Seed and simulation may be omitted; if supplied they must match the source. Composition overrides are rejected. The normalized config records the actual captured values.
- Shared claims are added **once** to the copied source before any fork. Config claim IDs are explicit identities, not topics. Two same-topic claims remain separate.
- `agent_composition` maps configured archetypes to exact nonnegative counts summing to population. Assignment uses deterministic shuffled citizen IDs before topology initialization.
- `visibility` is a keyed per-citizen/per-tick eligibility probability within the bounded observation window. `0` hides the claim content; `1` preserves legacy behavior. It is not passive audience reach.
- `reshare_probability` gates claim-bearing replies using a separate deterministic draw. Replies and fact-check descendants inherit parent policies. `0` prevents replies carrying that claim; the engine has no separate repost action.
- Each schedule entry has a source-relative `at_tick`; `0` is immediate, `1` is before the first tick's citizen decisions. Entries must fall within the requested run. `intervention.delay_ticks` must be zero: two competing clocks are rejected. Equal-offset entries preserve configuration order.
- Counterevidence requires a schedule `claim_id` matching a shared claim, with a matching topic. It links to that claim's origin rather than the latest same-topic post.
- Citizen targets use `agent_id`; directional relationship interventions use both `agent_id` and `target_agent_id`. Suppressing a relationship requires that it exists in the source. Suppressing a post requires a top-level source post.
- `target_community_id` is supported for trust shifts and seed posts. It resolves source membership once: trust shifts expand to captured member-observers; seed posts choose the specified captured member or the first sorted member as messenger. This is not an exclusive audience filter. Preview warnings disclose the expansion. Community targets on other intervention types are rejected.
- Targeted `fact_check` boosts the selected citizen/topic policy; without a citizen it retains the existing global fact-check-probability semantics.

## Reproducibility and safe storage

`multi_experiments` is an additive SQLite schema-v5 table. Source and final arm frames use `multi_experiment_source` / `multi_experiment_arm` snapshot tags, never `canonical`. Metrics reuse the existing metric tables under `(run ID, arm ID, tick)`.

Config and source SHA-256 hashes use UTF-8 sorted-key compact JSON. Every arm publishes the same pre-intervention `initial_snapshot_hash`. API creation optionally accepts `expected_source_hash`; the builder supplies the preview hash. If the source changes, including an intervention within the same tick, execution returns **409** before writing anything and requires revalidation.

Artifacts and metrics are persisted before completed references are published. A storage failure propagates, may leave an orphan artifact, and never creates a completed dangling reference. Interrupted `running` records become `failed` on restart; an explicit rerun is required. JSON fallback uses atomic file replacement and equivalent tagged artifacts. Corrupted JSON/SQLite and database locks are not hidden as empty histories.

Portable exports contain the normalized config, immutable source payload, hashes, engine/schema versions, and result metadata. Import validates schema, sizes, hashes, references, and engine compatibility before re-executing; imported results are never accepted as freshly computed outcomes. Generated bundles are additionally checked against source regeneration. A hash is an integrity checksum, **not** a signature or proof of trusted origin.

Keep the portable bundle's original JSON bytes when transferring it: JavaScript parse/stringify can normalize numeric tokens such as `1.0`, changing Python canonical hashes. The UI preserves raw bundle text for export, server preview, and import.

## APIs

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/multi-experiments/schema` | Versioned JSON Schema and hard limits |
| POST | `/multi-experiments/validate` | Read-only config/source validation and planned cost |
| POST | `/multi-experiments?expected_source_hash=<hash>` | Run a config, rejecting a stale preview hash |
| GET | `/multi-experiments?universe_id=prime&limit=50` | Bounded durable summaries, without full branch states |
| GET | `/multi-experiments/{id}` | Saved config, provenance, per-arm results/status/errors |
| GET | `/multi-experiments/{id}/arms/{arm_id}` | Final frame, histories, events, metrics and deltas |
| GET | `/multi-experiments/{id}/config` | Normalized config attachment |
| GET | `/multi-experiments/{id}/export` | Portable source/provenance bundle attachment |
| POST | `/multi-experiments/{id}/rerun` | New run from exact saved source |
| POST | `/multi-experiments/import/validate` | Read-only authoritative bundle validation/preview |
| POST | `/multi-experiments/import` | Validate bundle and execute as a new run |

For arm detail, `from_tick` and `to_tick` are absolute recorded ticks; `limit` caps samples (maximum 200), and `event_limit` caps events and trace collections (maximum 1,000). The `state` is explicitly the **final frame**, not a reconstruction at the selected tick. Its embedded histories follow the requested window/caps. Unknown IDs return 404, malformed or excessive configs return 422 with field messages, and a changed preview source returns 409.

Example API workflow, from the repository root:

```sh
curl -sS -X POST http://127.0.0.1:8000/multi-experiments/validate \
  -H 'content-type: application/json' \
  --data-binary @docs/examples/multi-arm-rumor.json

curl -sS -X POST http://127.0.0.1:8000/multi-experiments \
  -H 'content-type: application/json' \
  --data-binary @docs/examples/multi-arm-rumor.json

# Substitute the returned ID. Rerun does not use the current live universe.
curl -sS http://127.0.0.1:8000/multi-experiments/<id>/arms/delayed
curl -OJ http://127.0.0.1:8000/multi-experiments/<id>/export
curl -X POST http://127.0.0.1:8000/multi-experiments/<id>/rerun
```

## Resource limits and limitations

- 1–100 citizens, 1–20 ticks, 2–6 arms, at most 16 shared claims and 16 schedule entries per arm.
- Cost = population × ticks × arm count. Effective ceiling = `min(computational_budget, 2000)` agent-ticks. The configured budget may be up to 5,000 but cannot override the 2,000 hard cap.
- Config body ≤64 KiB; immutable source ≤4 MiB; portable bundle ≤32 MiB; list queries return at most 100 summaries.
- Execution is synchronous, serial, and local under one service lock. Use **one backend worker**. It does not yet support cancellation, background job queues, or concurrent interactive mutations during a run.
- Source capture rejects oversized advanced live worlds rather than truncating historical data or silently changing the population.
- Histories retain the engine's bounded window. This phase does not add historical rewind, multi-seed aggregation across arms, optimization, or a scenario gallery.
- Deltas and semantic divergence are observations of this deterministic model, not statistical causal effects or validated predictions about people.

## Tests

Run the complete offline suite after dependencies are installed:

```sh
make test
```

Run the optional live HTTP workflow, which starts a disposable Uvicorn process and invokes the real **frontend transport** against it:

```sh
make test-live
```

It validates a three-arm config, runs it, checks source isolation/hashes, retrieves histories, exports/imports/reruns it, restarts the backend, and reopens the same results. It uses a temporary database; it does not reset the user's simulation. Set `SOCIONET_TEST_PYTHON` if your Python executable differs from `python3`. This is an HTTP integration test, **not** a browser click/visual test.
