# Architecture

## Runtime split

The project is deliberately two local processes:

- `backend/app/` owns the only mutable simulation state and exposes FastAPI.
- `frontend/src/` owns view state and presentation; it never fabricates live results.
- SQLite stores canonical Prime snapshots, durable experiment run records, tagged source/control/treatment artifacts, and normalized per-tick metric samples. Canonical state loading filters on `provenance=canonical` and `universe_id=prime`, so a branch snapshot cannot replace Prime. The JSON fallback preserves the canonical single-file snapshot and uses sidecars for tagged experiment artifacts, runs, and metric samples.

## Simulation lifecycle

`Simulation.step()` increments one bounded tick, applies due interventions, and evaluates each citizen in stable ID order using a seed derived from `(world seed, phase, tick, agent id)`. Each agent runs:

1. **OBSERVE** — choose visible content from a bounded observation window.
2. **RETRIEVE MEMORY** — select up to five topic-matched memories.
3. **FORM INTENT** — choose among post, fact-check, reply, follow/unfollow, community change, or ignore from explicit probabilities and traits.
4. **EVALUATE OPTIONS** — record credibility, social trust, skepticism, and selected action.
5. **ACT** — mutate content, relationship, or community membership through legal action functions.
6. **UPDATE BELIEFS** — adjust stance/confidence using truth label, credibility, skepticism, and trust; remember the encounter.
7. **RECORD OUTCOME** — append an event and structured trace.

The trace contains execution facts and rule inputs. It does not claim to expose hidden chain-of-thought.

## Domain modules

- `agents.py`: deterministic citizen generation and heterogeneous traits.
- `memory.py`: bounded episodic memory, retrieval, decay, and durable limits.
- `beliefs.py`: evidence-aware belief updates.
- `relationships.py`: affinity/trust/following transitions.
- `communities.py`: seeded membership and join/leave operations.
- `simulation.py`: tick orchestration and lifecycle.
- `interventions.py`: typed validation, immediate/delayed application, intervention history.
- `experiments.py`: deep-copy fork from one snapshot and metric comparison.
- `analytics.py`: event-derived metrics, claim-specific metrics, and bounded history samples.
- `persistence.py`: additive SQLite schema migration, provenance-filtered snapshots, durable experiment runs, normalized metric/claim history, and JSON fallback.
- `presentation.py`: read-only browser projections derived from state.
- `main.py`: request validation, locking, API routes, and compatibility endpoints.

## Butterfly experiment

`run_side_by_side()` deep-copies the exact input snapshot twice before applying any branch intervention. It then executes both simulations with the same seed and deterministic per-agent RNG derivation. The catalog demo applies the same false fictional seed post to both branches; Echo also gets true, high-credibility counterevidence at tick +2. The output includes calculated metrics and the first differing recorded event.

Each API-created run first stores a tagged `experiment_source` snapshot, records `running` status, then stores `experiment_control` and `experiment_treatment` snapshots and updates the run to `completed`. Failed calculations remain as durable `failed` records. The catalog projection derives its status and last-run metadata from these records rather than process memory.

## Claims and history

Every claim-bearing origin content receives a stable `claim-{origin-content-id}` identifier. Replies, fact-checks, and counterevidence inherit the origin claim ID; branch forks therefore retain claim identity even when later content IDs diverge. A claim records its origin, source/intervention provenance, bounded content lineage, and lifecycle transitions (`active`, `disputed`, `confirmed`, or `corrected`). Old snapshots without these additive fields are accepted and hydrated when loaded.

`Simulation.step()` records one `MetricSample` after the complete tick in deterministic citizen-ID order. The initial frame is sample tick 0. Each state retains at most 200 recent samples. Durable experiment branches additionally write those samples to `metric_samples` and claim rows to `claim_metric_samples`, keyed by run, branch, and tick. This makes `/analytics/history` and `/experiment-runs/{run_id}/history` restart-safe without changing the legacy comparison response.

## Metrics

- **Exposure rate**: content-linked action events divided by population × max(1, tick).
- **Reshare/engagement rate**: non-ignore actions divided by population × max(1, tick).
- **Belief confidence**: mean confidence across all agent/topic belief records.
- **Correction reach**: distinct citizens whose recorded actions encountered a counterevidence content ID, divided by population.
- **Average relationship trust**: mean trust across observed directed relationship records.
- **Polarization**: mean absolute distance of each stance from the population mean stance. Stances range from -1 to +1.
- **Influence**: documented UI score from non-ignore actions, following count, and weighted trust ties, clamped to [0, 1].
- **Information entropy proxy**: `1 - belief_accuracy`, where belief accuracy is evidence-for / all evidence counts. This is a synthetic proxy, not Shannon entropy.
- **Time until claim stops spreading**: current tick minus the latest claim-bearing content tick (zero when no claim has been observed yet).

Claim-specific history uses these definitions:

- **Claim exposure**: one recorded agent action that observed a claim-bearing content item; unique exposure is the distinct actor count.
- **Claim reshare**: a claim-bearing reply or content explicitly marked as a repeat; it can overlap with the reply count by design.
- **Claim reply**: claim-bearing content with `kind=reply`.
- **Claim fact-check**: claim-bearing content with `kind=fact_check`.
- **Claim correction**: fact-check or counterevidence whose truth label contradicts the origin claim; this transition marks a false claim `corrected`.

The historical endpoints accept bounded tick ranges, branch/run filters, claim ID, status, and topic filters. The Butterfly Lab chart renders persisted control/treatment samples and lets the observer switch between content, exposure, reshare, correction, engagement, and polarization series. Every metric is recomputed from the current state and event log. Values describe a fictional model only.
