import json
import sqlite3

from fastapi.testclient import TestClient

from app.analytics import calculate_metrics
from app.interventions import apply_intervention
from app.main import SimulationService, app
from app.models import Intervention, SimulationState
from app.persistence import SQLiteRepository
from app.simulation import Simulation, append_content, run_seeded


def test_old_snapshot_payload_migrates_and_hydrates_claims_and_history(tmp_path) -> None:
    state = Simulation(seed=301).run(1)
    legacy = state.model_dump(mode="json")
    legacy.pop("claims", None)
    legacy.pop("metric_history", None)
    for content in legacy["contents"]:
        for field in ("claim_id", "is_reshare", "provenance", "intervention_id"):
            content.pop(field, None)
    for event in legacy["events"]:
        event.pop("observed_content_id", None)
        event.pop("claim_id", None)

    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, seed INTEGER NOT NULL, "
            "tick INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO snapshots (seed, tick, payload) VALUES (?, ?, ?)",
            (legacy["seed"], legacy["tick"], json.dumps(legacy)),
        )

    repository = SQLiteRepository(path)
    restored = SimulationState.model_validate(repository.load().model_dump(mode="json"))
    migrated = Simulation(state=restored).state
    assert migrated.seed == 301
    assert migrated.tick == 1
    assert migrated.claims
    assert all(content.claim_id for content in migrated.contents if content.claim_truth is not None)
    assert [sample.tick for sample in migrated.metric_history] == [1]


def test_metric_history_is_deterministic_and_monotonic() -> None:
    left = run_seeded(302, 4)
    right = run_seeded(302, 4)
    assert [sample.tick for sample in left.metric_history] == [0, 1, 2, 3, 4]
    assert left.metric_history == right.metric_history
    assert all(left.metric_history[index].tick < left.metric_history[index + 1].tick for index in range(4))


def test_claim_lifecycle_provenance_and_claim_specific_metrics() -> None:
    simulation = Simulation(state=Simulation(seed=303).state)
    origin = append_content(
        simulation.state,
        author_id="citizen-001",
        kind="post",
        topic="fictional transit",
        text="A false fictional transit claim.",
        claim_truth=False,
        provenance="intervention",
        intervention_id="intervention-test",
    )
    apply_intervention(
        simulation.state,
        Intervention(
            kind="counterevidence",
            topic="fictional transit",
            text="A credible fictional correction.",
            truth_label=True,
            strength=1.0,
        ),
    )
    claim = simulation.state.claims[origin.claim_id]
    metrics = calculate_metrics(simulation.state).claim_metrics[origin.claim_id]
    assert claim.claim_id == "claim-content-000001"
    assert claim.status.value == "corrected"
    assert claim.origin_intervention_id == "intervention-test"
    assert [event.status.value for event in claim.lifecycle] == ["active", "corrected"]
    assert metrics.fact_checks == 1
    assert metrics.corrections == 1
    assert metrics.reshares == 0


def test_historical_api_filters_and_claim_filters(tmp_path) -> None:
    original = app.state.service
    app.state.service = SimulationService(seed=304, database_path=str(tmp_path / "api.sqlite3"))
    try:
        client = TestClient(app)
        created = client.post("/experiment-runs", json={"experiment_id": "rumor-counterevidence", "ticks": 4}).json()
        run_id = created["run_id"]
        history = client.get(
            "/analytics/history",
            params={"universe_id": "prime", "run_id": run_id, "branch": "treatment", "from_tick": 2, "to_tick": 4, "limit": 2},
        )
        assert history.status_code == 200
        assert [sample["tick"] for sample in history.json()["samples"]] == [2, 3]
        claims = client.get(
            "/analytics/claims",
            params={"universe_id": "prime", "run_id": run_id, "branch": "treatment", "status": "corrected", "topic": "fictional civic event"},
        )
        assert claims.status_code == 200
        assert claims.json()["claims"]
        assert all(row["status"] == "corrected" for row in claims.json()["claims"])
    finally:
        app.state.service = original


def test_experiment_history_persists_across_service_restart(tmp_path) -> None:
    database_path = str(tmp_path / "history.sqlite3")
    first = SimulationService(seed=305, database_path=database_path)
    run = first.create_experiment_run(universe_id="prime", experiment_id="rumor-counterevidence", ticks=3)
    repository = SQLiteRepository(database_path)
    assert repository.list_metric_history(scope_id=run["run_id"], branch="control")
    restarted = SimulationService(seed=999, database_path=database_path)
    saved = restarted.store.list_metric_history(scope_id=run["run_id"], branch="treatment")
    assert [sample["tick"] for sample in saved] == [0, 1, 2, 3]
    assert restarted.store.get_experiment_run(run["run_id"])["status"] == "completed"
