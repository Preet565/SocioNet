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
