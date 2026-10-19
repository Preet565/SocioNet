# NPC Internet: The Butterfly Effect — backend

This directory contains the local Python backend for a deterministic social simulation. All citizens, communities, posts, claims, and interventions are fictional and sanitized. The default run creates exactly 100 citizens from seed `20261016`.

## Setup

The pinned runtime is Python 3.12.8 (`.python-version`). Python 3.12–3.14 is accepted by `pyproject.toml`.

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` is the offline test/install lockfile with exact versions. No external service or secret is required. Copy `.env.example` to `.env` only when environment configuration is useful; the application reads environment variables supplied by the process.

## Run

```bash
cd backend
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Useful endpoints:

- `GET /health` — seed, tick, population, and persistence backend.
- `GET /state` — complete current simulation state.
- `POST /simulate/tick` with `{"ticks": 1}` — advance one to 100 ticks.
- `POST /interventions` — apply or schedule a validated intervention.
- `POST /experiments/side-by-side` — fork the current snapshot into control and treatment branches.
- `POST /experiment-runs` (or `/experiments/runs`) — create a durable catalog or custom experiment run.
- `GET /experiment-runs?universe_id=prime|echo` — list durable run status and provenance.
- `GET /experiment-runs/{run_id}` — retrieve a complete run record, including its result and tagged snapshots.
- `GET /experiment-runs/{run_id}/export` — download the run record as JSON.
- `GET /analytics` — calculated engagement, belief, relationship, polarization, and topic metrics.
- `GET /traces?tick=1` — decision traces for every lifecycle phase.
- `GET /export/json` — downloadable state JSON.
- `POST /state/save` and `POST /state/load` — canonical Prime snapshot persistence; experiment artifacts are filtered out of this compatibility path.

The citizen decision lifecycle is:

`OBSERVE → RETRIEVE → FORM INTENT → EVALUATE → ACT → UPDATE → RECORD`

Actions include posts, replies, fact-checks, ignores, follows, unfollows, community joins, and community leaves. Every action is recorded with an event and decision trace.

## Test

Run the one offline project check from this directory:

```bash
cd backend
make test
```

The suite covers the 100-citizen seed, lifecycle transitions, beliefs, memories, relationships, invalid interventions, delayed counterevidence, reproducibility, analytics, experiments, persistence, additive SQLite migration, provenance filtering, durable run retrieval/export, and API behavior.

## Docker deploy

Build and run locally:

```bash
cd backend
docker build -t npc-internet-backend .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" npc-internet-backend
```

SQLite snapshots and experiment runs are written to `./data/socionet.sqlite3` by default. The repository initializes or additively migrates its schema on open. If SQLite cannot be opened, the repository falls back to the original canonical JSON snapshot plus tagged snapshot/run sidecars beside the configured database path.
