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
