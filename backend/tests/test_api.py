from fastapi.testclient import TestClient

from app.main import SimulationService, app


def test_api_exposes_state_tick_analytics_and_json_export(tmp_path) -> None:
    original = app.state.service
    app.state.service = SimulationService(seed=88, database_path=str(tmp_path / "api.sqlite3"))
    try:
        client = TestClient(app)
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["population"] == 100
        tick = client.post("/simulate/tick", json={"ticks": 1})
        assert tick.status_code == 200
        assert tick.json()["tick"] == 1
        metrics = client.get("/analytics")
        assert metrics.status_code == 200
        assert metrics.json()["population"] == 100
        exported = client.get("/export/json")
        assert exported.status_code == 200
        assert exported.json()["seed"] == 88
    finally:
        app.state.service = original


def test_api_returns_validation_error_for_invalid_intervention(tmp_path) -> None:
    original = app.state.service
    app.state.service = SimulationService(seed=89, database_path=str(tmp_path / "api.sqlite3"))
    try:
        client = TestClient(app)
        response = client.post(
            "/interventions",
            json={"kind": "seed_post", "topic": "public art"},
        )
        assert response.status_code == 422
    finally:
        app.state.service = original


def test_browser_projection_exposes_real_citizens_graph_and_experiment_result(tmp_path) -> None:
    original = app.state.service
    app.state.service = SimulationService(seed=90, database_path=str(tmp_path / "projection.sqlite3"))
    try:
        client = TestClient(app)
        state = client.get("/state?universe_id=prime")
        assert state.status_code == 200
        payload = state.json()
        assert payload["metrics"]["population"] == 100
        assert len(payload["citizens"]) == 100
        assert len(payload["nodes"]) == 100
        assert payload["edges"]

        detail = client.get("/citizens/citizen-001?universe_id=prime")
        assert detail.status_code == 200
        assert detail.json()["decisionTrace"] == []

        result = client.post(
            "/experiments/run",
            json={"universe_id": "prime", "experiment_id": "rumor-counterevidence"},
        )
        assert result.status_code == 200
        assert result.json()["earliest_divergence"] is not None
        assert "average_belief_confidence" in result.json()["metric_deltas"]
    finally:
        app.state.service = original


def test_experiment_runs_are_durable_provenanced_and_do_not_replace_prime(tmp_path) -> None:
    database_path = str(tmp_path / "runs.sqlite3")
    original = app.state.service
    app.state.service = SimulationService(seed=91, database_path=database_path)
    try:
        client = TestClient(app)
        before = client.get("/state?universe_id=prime").json()
        created = client.post(
            "/experiment-runs",
            json={"universe_id": "prime", "experiment_id": "rumor-counterevidence"},
        )
        assert created.status_code == 200
        run = created.json()
        assert run["status"] == "completed"
        assert run["source_snapshot"]["provenance"] == "experiment_source"
        assert run["control_snapshot"]["provenance"] == "experiment_control"
        assert run["treatment_snapshot"]["provenance"] == "experiment_treatment"

        after = client.get("/state?universe_id=prime").json()
        assert after["tick"] == before["tick"]
        assert client.post("/state/load", params={"snapshot_id": run["treatment_snapshot_id"]}).status_code == 404

        listed = client.get("/experiment-runs", params={"universe_id": "prime"})
        assert listed.status_code == 200
        assert listed.json()["runs"][0]["run_id"] == run["run_id"]
        assert client.get(f"/experiment-runs/{run['run_id']}").json()["status"] == "completed"
        exported = client.get(f"/experiment-runs/{run['run_id']}/export")
        assert exported.status_code == 200
        assert exported.json()["run_id"] == run["run_id"]

        catalog = client.get("/experiments", params={"universe_id": "prime"}).json()["experiments"]
        catalog_row = next(item for item in catalog if item["id"] == "rumor-counterevidence")
        assert catalog_row["status"] == "complete"
        assert catalog_row["last_run_id"] == run["run_id"]
    finally:
        app.state.service = original

    # A fresh service reads the durable status and still loads the canonical
    # Prime snapshot, proving the result is not an in-memory-only projection.
    app.state.service = SimulationService(seed=999, database_path=database_path)
    try:
        client = TestClient(app)
        catalog = client.get("/experiments", params={"universe_id": "prime"}).json()["experiments"]
        assert next(item for item in catalog if item["id"] == "rumor-counterevidence")["status"] == "complete"
        assert client.get("/health").json()["seed"] == 91
    finally:
        app.state.service = original
