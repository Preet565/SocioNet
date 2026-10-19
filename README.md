# NPC Internet — The Butterfly Effect

NPC Internet is a local, deterministic civilization simulator. One hundred fictional citizens observe signals, retrieve memories, form intents, evaluate bounded options, act, update beliefs and relationships, and record the result. The browser is an observation and experiment console: the user does not act as a citizen and no event can post to a real network.

## What is implemented

- Seeded 100-citizen world with distinct interests, ideology, skepticism, curiosity, trust baselines, communities, beliefs, memories, relationships, and action history.
- Full agent lifecycle with structured decision traces for every phase.
- Rule-based post, reply, fact-check, ignore, follow/unfollow, and community membership actions.
- Belief updates that use evidence truth labels, credibility, skepticism, and source trust; popularity is not treated as proof.
- Sparse seeded social graph that changes through interactions and relationship updates.
- Bounded discrete ticks, delayed interventions, reproducible branch execution, and SQLite snapshot persistence.
- Durable experiment runs with explicit source/control/treatment snapshot provenance, additive SQLite schema migration, and canonical Prime filtering.
- Per-tick metric history with bounded durable branch samples and deterministic tick ordering.
- Bounded serial multi-seed batches with deterministic paired seeds, durable child runs, progress metadata, descriptive statistics, distribution-free median intervals, paired effect sizes, and explicit objective success rates.
- First-class stable claim IDs, claim lifecycle/provenance, and claim-specific exposure, reshare, reply, fact-check, and correction metrics.
- FastAPI projections for overview, graph, citizens, feed, event stream, interventions, minds, metrics, export, and experiments.
- Butterfly Lab catalog with the **One rumor, two universes** scenario: a false fictional civic-event claim is run against a delayed credible counterevidence branch from the same snapshot.
- Dark, responsive React command center with actual API-driven SVG topology, profiles, feed, trace explorer, metrics, and comparison readouts.
- Clearly labelled offline preview when the API cannot be reached. Preview data is not presented as a simulation result.

## Prerequisites

- Python 3.12.8 (`backend/.python-version`); compatible Python 3.12–3.14 releases are supported
- Node.js 22.22.0 (`frontend/.nvmrc`)
- npm; Docker is optional

No paid key, external model, external database, or external generative AI service is required.

## Setup

```sh
git clone <repository-url>
cd SocioNet

python3 -m venv backend/.venv
. backend/.venv/bin/activate
python -m pip install -r backend/requirements.txt

cd frontend
npm ci
cd ..
```

SQLite is created in `backend/data/` on first backend start. That path is ignored by git. Copy `backend/.env.example` to `backend/.env` only if changing the database or CORS origins. Copy `frontend/.env.example` only if the API is not at `http://localhost:8000`.

## Run locally

Use two terminals from the repository root:

```sh
# terminal 1
. backend/.venv/bin/activate
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```sh
# terminal 2
cd frontend
npm run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5173>. The API docs are at <http://127.0.0.1:8000/docs>.

## Single test command

The root command runs the backend suite and frontend typecheck/build checks offline:

```sh
make test
```

Equivalent focused checks are:

```sh
cd backend && make test
cd frontend && npm test
cd frontend && npm run build
```

## Judge demo: one rumor, two universes

1. Start both services and open the dashboard.
2. Select **Civilization Overview** and click **Reset both / run demo flow** or the prominent **Run the demo flow** button.
3. The UI resets Prime and Echo, advances the synchronized starting frame, and requests the backend's `rumor-counterevidence` experiment.
4. Open **Butterfly Lab**. The result card shows the actual seed, branch tick count, calculated metric deltas, earliest recorded divergence, and an interactive chart backed by persisted control/treatment samples. Before the run, no result is shown.
5. Use **One rumor, two universes → Run experiment** to rerun it. The scenario forks an identical serialized snapshot; control gets only the unverified fictional claim while treatment receives credible counterevidence after two ticks. The durable run history shows the source and branch snapshot tags and provides an export link.
6. Open **Information Ecosystem** to inspect actual posts, claim status, credibility, exposure counts, and the event stream. Open **Social Universe** to see graph nodes and relationship edges derived from the state.
7. Select a citizen in **Citizen Observatory** for beliefs, memories, relationships, interests, recent decisions, and the executed trace. **Agent Mind Explorer** exposes structured decision evidence, not fabricated hidden chain-of-thought.
8. In **Intervention Console**, pause naturally at a tick boundary, select a supported intervention, enter a rationale, apply it to Prime or Echo, then advance a tick and inspect its event and belief effects.

Canonical API experiment request:

```sh
curl -X POST http://127.0.0.1:8000/experiments/run \
  -H 'content-type: application/json' \
  -d '{"universe_id":"prime","experiment_id":"rumor-counterevidence"}'
```

Durable experiment-run endpoints:

```sh
# Create a catalog run (the legacy /experiments/run endpoint remains supported)
curl -X POST http://127.0.0.1:8000/experiment-runs \
  -H 'content-type: application/json' \
  -d '{"universe_id":"prime","experiment_id":"rumor-counterevidence"}'

# List, retrieve, or export saved runs
curl 'http://127.0.0.1:8000/experiment-runs?universe_id=prime'
curl http://127.0.0.1:8000/experiment-runs/<run-id>
curl -OJ http://127.0.0.1:8000/experiment-runs/<run-id>/export
```

Run a bounded multi-seed batch. The API executes repetitions serially and returns the durable batch record after each paired control/treatment run has been persisted:

```sh
curl -X POST http://127.0.0.1:8000/experiment-batches \
  -H 'content-type: application/json' \
  -d '{"universe_id":"prime","experiment_id":"rumor-counterevidence","base_seed":9001,"population":50,"ticks":6,"repetitions":5,"computational_budget":250000,"confidence_level":0.95,"objective":{"metric":"correction_reach","direction":"increase","threshold":0.05}}'

curl 'http://127.0.0.1:8000/experiment-batches?universe_id=prime'
curl http://127.0.0.1:8000/experiment-batches/<batch-id>
curl http://127.0.0.1:8000/experiment-batches/<batch-id>/progress
curl http://127.0.0.1:8000/experiment-batches/<batch-id>/runs
```

`/experiments/batches` and its detail/progress/runs routes are compatibility aliases. Batch requests reject unknown catalog scenarios unless explicit interventions are supplied, enforce population/tick/repetition/budget bounds, and validate objective metrics against the scalar analytics contract.

To replay from the initial seed, click the reset controls or call:

```sh
curl -X POST http://127.0.0.1:8000/reset \
  -H 'content-type: application/json' \
  -d '{"universe_id":"prime"}'
```

## API surface

- `GET /health`
- `GET /state` for the canonical Pydantic state; `GET /state?universe_id=prime|echo` for the browser projection
- `POST /simulate/tick` for the canonical world; `POST /tick` for a universe projection
- `POST /reset`
- `GET /analytics`
- `GET /analytics/history` with universe, run/branch, tick-range, and bounded-limit filters
- `GET /analytics/claims` and `GET /analytics/claims/{claim_id}` for lifecycle, provenance, and claim-specific metric filters
- `GET /citizens`, `GET /citizens/{citizen_id}`
- `GET /experiments`, `POST /experiments/run`, `POST /experiments/side-by-side`
- `POST /experiment-runs` (also `/experiments/runs`) to create a durable run; `GET` on the same collection and `/{run_id}` to list/retrieve; `/{run_id}/export` to download its JSON provenance/result record
- `GET /experiment-runs/{run_id}/history` (also `/experiments/runs/{run_id}/history`) for persisted control/treatment time series
- `POST /experiment-batches` (also `/experiments/batches`) for validated paired multi-seed execution; `GET` collection, `/{batch_id}`, `/{batch_id}/progress` (also `/status`), and `/{batch_id}/runs` return durable metadata, aggregate results, progress, and linked child runs
- `POST /interventions`
- `GET /traces`, `GET /export/json`
- `POST /state/save`, `POST /state/load`

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for state ownership, lifecycle rules, metrics, persistence, and UI data flow. The short judge script is in [docs/DEMO.md](docs/DEMO.md).

## Docker

The repository contains a single-container `Dockerfile` as well as separate production images for the API and web console. The single-container image builds the React console, serves it from FastAPI, and persists snapshots in a mounted volume:

```sh
docker build -t socionet .
docker run --rm -p 8000:8000 -v socionet-data:/app/data socionet
```

Open <http://127.0.0.1:8000>. The same image exposes the API at `/health` and `/docs`.

For the two-service layout, run the included compose file:

```sh
docker compose up --build
```

Open <http://127.0.0.1:8080>. If Docker Desktop is not running, use the local commands above. Docker verification was not possible in environments without a running daemon.

`frontend/package-lock.json` is the committed npm dependency lockfile. Backend dependencies are explicitly version-pinned in `backend/requirements.txt`; no external services or credentials are required.

## Limitations, honestly disclosed

- The citizens are deterministic rule-based agents, not general language models. Their text is intentionally bounded and fictional.
- The graph layout is deterministic seeded geometry for a stable SVG view, not a physics solver; topology and edge weights are real state.
- Experiment branch execution remains an in-process deterministic calculation, while its result, status, and tagged source/control/treatment snapshots are now durable in SQLite. The JSON fallback keeps the canonical file contract and writes tagged experiment artifacts and run records to sidecars.
- Canonical state loading is filtered to `provenance=canonical` and `universe_id=prime`; experiment snapshots cannot replace Prime through the existing state-load path.
- Metric and claim history is bounded to the most recent 200 samples per state/branch; old snapshots can be hydrated with stable claim IDs but cannot reconstruct samples that were never recorded.
- Claim exposures count recorded observations represented by action events, not passive reach; replies are also counted as claim reshares when they carry a claim ID. These are synthetic event-log measures, not measurements of real-world audiences.
- Phase 3 batches are bounded, serial, synchronous, and in-process. Progress is durably updated after each completed pair; background queues, cancellation, and distributed execution are later concerns.
- Batch confidence intervals are exact distribution-free intervals for the paired median, and effect sizes are computed from successful paired differences. They are robustness summaries for this fictional simulator, not inferential claims about real populations.
- Metrics are synthetic measurements of a fictional population and must not be interpreted as evidence about real people or communities.
