# NPC Internet — The Butterfly Effect

NPC Internet is a local, deterministic civilization simulator. One hundred fictional citizens observe signals, retrieve memories, form intents, evaluate bounded options, act, update beliefs and relationships, and record the result. The browser is an observation and experiment console: the user does not act as a citizen and no event can post to a real network.

## What is implemented

- Seeded 100-citizen world with distinct interests, ideology, skepticism, curiosity, trust baselines, communities, beliefs, memories, relationships, and action history.
- Full agent lifecycle with structured decision traces for every phase.
- Rule-based post, reply, fact-check, ignore, follow/unfollow, and community membership actions.
- Belief updates that use evidence truth labels, credibility, skepticism, and source trust; popularity is not treated as proof.
- Sparse seeded social graph that changes through interactions and relationship updates.
- Bounded discrete ticks, delayed interventions, reproducible branch execution, and SQLite snapshot persistence.
- FastAPI projections for overview, graph, citizens, feed, event stream, interventions, minds, metrics, export, and experiments.
- Butterfly Lab catalog with the **One rumor, two universes** scenario: a false fictional civic-event claim is run against a delayed credible counterevidence branch from the same snapshot.
- Dark, responsive React command center with actual API-driven SVG topology, profiles, feed, trace explorer, metrics, and comparison readouts.
- Clearly labelled offline preview when the API cannot be reached. Preview data is not presented as a simulation result.

## Prerequisites

- Python 3.12.8 (`backend/.python-version`)
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
4. Open **Butterfly Lab**. The result card shows the actual seed, branch tick count, calculated metric deltas, and earliest recorded divergence. Before the run, no result is shown.
5. Use **One rumor, two universes → Run experiment** to rerun it. The scenario forks an identical serialized snapshot; control gets only the unverified fictional claim while treatment receives credible counterevidence after two ticks.
6. Open **Information Ecosystem** to inspect actual posts, claim status, credibility, exposure counts, and the event stream. Open **Social Universe** to see graph nodes and relationship edges derived from the state.
7. Select a citizen in **Citizen Observatory** for beliefs, memories, relationships, interests, recent decisions, and the executed trace. **Agent Mind Explorer** exposes structured decision evidence, not fabricated hidden chain-of-thought.
8. In **Intervention Console**, pause naturally at a tick boundary, select a supported intervention, enter a rationale, apply it to Prime or Echo, then advance a tick and inspect its event and belief effects.

Canonical API experiment request:

```sh
curl -X POST http://127.0.0.1:8000/experiments/run \
  -H 'content-type: application/json' \
  -d '{"universe_id":"prime","experiment_id":"rumor-counterevidence"}'
```

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
- `GET /citizens`, `GET /citizens/{citizen_id}`
- `GET /experiments`, `POST /experiments/run`, `POST /experiments/side-by-side`
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
- The current experiment API retains branch results in process memory and persists the source snapshots, not a full experiment-results table across server restarts.
- SQLite persistence currently promises the canonical Prime snapshot; the Echo branch is an in-memory counterfactual until it is saved through the canonical snapshot endpoint.
- Metrics are synthetic measurements of a fictional population and must not be interpreted as evidence about real people or communities.
