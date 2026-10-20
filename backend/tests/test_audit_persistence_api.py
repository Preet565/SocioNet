"""Audit regressions: every repository and API fixture uses a temporary store."""

from threading import Thread

import pytest
from fastapi.testclient import TestClient

from app import main, multi_experiments as multi
from app.main import SimulationService, app
from app.models import Intervention, ScheduledIntervention, SimulationConfig
from app.persistence import JSONRepository, SQLiteRepository
from app.simulation import Simulation


def small_config():
    return SimulationConfig(population=4)


def config_dict():
    return {
        "schema_version": 1, "name": "Audit", "hypothesis": "Matched arms agree",
        "seed": 12, "ticks": 1, "source": {"mode": "generated"},
        "simulation": {"population": 4}, "baseline_arm_id": "control",
        "arms": [{"id": "control", "name": "Control"}, {"id": "treatment", "name": "Treatment"}],
        "computational_budget": 100,
    }


@pytest.fixture(params=[SQLiteRepository, JSONRepository], ids=["sqlite", "json"])
def repository(request, tmp_path):
    return request.param(tmp_path / "audit.db")


@pytest.fixture
def api(tmp_path, monkeypatch):
    service = SimulationService(seed=12, database_path=str(tmp_path / "api.sqlite3"), config=small_config())
    monkeypatch.setattr(app.state, "service", service)
    return TestClient(app, raise_server_exceptions=False), service


def test_canonical_snapshot_ids_are_immutable_and_universe_scoped(repository):
    first = repository.save(Simulation(seed=12, config=small_config()).state)
    repository.save(Simulation(seed=13, config=small_config()).state)
    repository.save(Simulation(seed=14, config=small_config()).state, universe_id="echo")
    assert repository.load(first).seed == 12
    assert repository.load().seed == 13
    assert repository.load(universe_id="echo").seed == 14
    with pytest.raises(FileNotFoundError):
        repository.load(first, universe_id="echo")


def test_tagged_snapshot_explicit_universe_is_checked(repository):
    snapshot_id = repository.save(Simulation(seed=12, config=small_config()).state,
                                  provenance=multi.SOURCE_PROVENANCE, universe_id="echo")
    with pytest.raises(FileNotFoundError):
        repository.load(snapshot_id, provenance=multi.SOURCE_PROVENANCE, universe_id="prime")


def test_legacy_pair_saves_actual_config_hypothesis_and_hashes(api):
    client, service = api
    run = client.post("/experiment-runs", json={
        "experiment_id": "custom", "ticks": 1, "hypothesis": "Evidence changes outcomes",
        "treatment_interventions": [{"kind": "fact_check", "topic": "public art"}],
    }).json()
    assert run["status"] == "completed"
    assert run["hypothesis"] == "Evidence changes outcomes"
    assert run["config"]["treatment_interventions"][0]["kind"] == "fact_check"
    assert run["config_hash"] == multi.json_hash(run["config"])
    source = service.store.load(run["source_snapshot_id"], provenance="experiment_source")
    assert run["source_hash"] == multi.json_hash(source.model_dump(mode="json"))
    assert run["manifest"]["engine_version"]
    service.reset(99, config=small_config())
    repeated = client.post(f"/experiment-runs/{run['run_id']}/rerun")
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["result"] == run["result"]


def test_source_reset_seed_and_load_survive_restart(api):
    client, service = api
    first = client.post("/state/save").json()["snapshot_id"]
    response = client.post("/reset", json={"universe_id": "prime", "seed": 99})
    assert response.status_code == 200
    assert service.state.seed == 99
    assert client.post("/state/load", params={"snapshot_id": first}).status_code == 200
    restarted = SimulationService(database_path=str(service.store.repository.path), config=small_config())
    assert restarted.state.seed == 12


def test_failed_canonical_storage_does_not_advance_live_or_durable_state(api, monkeypatch):
    client, service = api
    before = service.state.model_dump(mode="json")
    def fail(*args, **kwargs):
        raise OSError("private-disk-path-and-secret")
    monkeypatch.setattr(service.store, "save_metric_history", fail)
    response = client.post("/simulate/tick", json={"ticks": 1})
    assert response.status_code == 500
    assert "private-disk" not in response.text
    assert service.state.model_dump(mode="json") == before
    assert service.store.load().model_dump(mode="json") == before


def test_pair_completion_write_failure_has_no_completed_artifacts(api, monkeypatch):
    client, service = api
    original = service.store.save_experiment_run
    def fail_completed(record):
        if record["status"] == "completed":
            raise OSError("private storage detail")
        return original(record)
    monkeypatch.setattr(service.store, "save_experiment_run", fail_completed)
    assert client.post("/experiment-runs", json={"experiment_id": "trust-shift", "ticks": 1}).status_code == 500
    [run] = service.store.list_experiment_runs()
    assert run["status"] == "failed"
    assert run["control_snapshot_id"] is None and run["treatment_snapshot_id"] is None
    assert "result" not in run and "private storage" not in str(run)


def test_legacy_running_runs_and_batches_recover(api):
    _, service = api
    service.store.save_experiment_run({
        "run_id": "interrupted", "experiment_id": "custom", "universe_id": "prime", "status": "running",
        "batch_id": "batch", "repetition": 1,
    })
    service.store.save_experiment_batch({
        "batch_id": "batch", "experiment_id": "custom", "universe_id": "prime", "status": "running",
        "total_runs": 2, "completed_runs": 0, "failed_runs": 0, "run_ids": [],
    })
    restarted = SimulationService(database_path=str(service.store.repository.path), config=small_config())
    run = restarted.store.get_experiment_run("interrupted")
    batch = restarted.store.get_experiment_batch("batch")
    assert run["status"] == batch["status"] == "failed"
    assert batch["run_ids"] == ["interrupted"]
    assert batch["completed_runs"] == 0
    assert batch["progress"]["percent_complete"] < 100


def test_list_filters_before_limit_and_can_request_summaries(api):
    client, service = api
    for index, status in enumerate(["failed", "completed", "completed"]):
        service.store.save_experiment_run({
            "run_id": str(index), "experiment_id": "custom", "universe_id": "prime", "status": status,
            "created_at": str(index), "updated_at": str(index), "result": {"huge": "x" * 10000},
        })
    response = client.get("/experiment-runs?status=failed&limit=1&include_results=false")
    assert response.status_code == 200
    [run] = response.json()["runs"]
    assert run["run_id"] == "0" and "result" not in run
    assert client.get("/experiment-runs?limit=201").status_code == 422


@pytest.mark.parametrize("mutation", ["queue", "memory", "trace", "claim", "causal"])
def test_import_source_integrity_and_hidden_work_are_checked(api, mutation):
    client, service = api
    config = config_dict()
    config.update(source={"mode": "universe"}, seed=None, simulation=None)
    service.tick(1)
    run = client.post("/multi-experiments", json=config).json()
    bundle = client.get(f"/multi-experiments/{run['id']}/export").json()
    source = bundle["source_snapshot"]
    if mutation == "queue":
        intervention = Intervention(kind="trust_shift", target_agent_id="citizen-001", delta=0.1)
        source["scheduled_interventions"] = [ScheduledIntervention(
            intervention_id=f"hidden-{index}", apply_at_tick=2, intervention=intervention,
        ).model_dump(mode="json") for index in range(1000)]
    elif mutation == "memory":
        source["agents"]["citizen-001"]["memories"] = [{
            "memory_id": "hidden-memory", "agent_id": "citizen-001", "content_id": "missing",
            "kind": "post", "summary": "hidden", "credibility": 0.5, "valence": 0.0,
            "created_tick": 1, "recall_strength": 1.0,
        }]
    elif mutation == "trace":
        source["traces"][0]["agent_id"] = "missing"
    elif mutation == "claim":
        source["contents"][0]["claim_id"] = "missing-claim"
    else:
        source["causal_links"][0]["parent_event_id"] = "content:missing"
    bundle["source_hash"] = multi.json_hash(source)
    bundle["results"]["source_hash"] = bundle["source_hash"]
    for arm in bundle["results"]["arms"]:
        arm["initial_snapshot_hash"] = bundle["source_hash"]
    response = client.post("/multi-experiments/import/validate", json=bundle)
    assert response.status_code == 422, response.text
    assert len(service.store.list_multi_experiments()) == 1


def test_execution_error_is_redacted_and_storage_failure_is_failed(api, monkeypatch):
    client, service = api
    def fail(*args, **kwargs):
        raise RuntimeError("private-token-and-path")
    with monkeypatch.context() as patch:
        patch.setattr(multi, "execute_arm", fail)
        response = client.post("/multi-experiments", json=config_dict())
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "private-token" not in response.text
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "save_metric_history", fail)
        response = client.post("/multi-experiments", json=config_dict())
    assert response.status_code == 500
    records = service.store.list_multi_experiments()
    assert records[0]["status"] == "running" and records[1]["status"] == "failed"


def test_legacy_intervention_lists_and_requests_are_bounded(api, monkeypatch):
    client, service = api
    intervention = {"kind": "fact_check", "topic": "public art"}
    payload = {"ticks": 1, "treatment_interventions": [intervention] * 17}
    assert client.post("/experiments/side-by-side", json=payload).status_code == 422
    assert client.post("/simulate/tick", json={"ticks": True}).status_code == 422
    response = client.post("/interventions", json={"kind": "seed_post", "topic": "x", "text": "sensitive" * 100})
    assert response.status_code == 422 and "sensitive" not in response.text


def test_state_response_is_detached_and_read_projection_holds_lock(api, monkeypatch):
    client, service = api
    state = main.state(request=type("Request", (), {"app": app})(), universe_id=None)
    # Direct endpoint calls must also return a frame, not a live world.
    service.tick(1)
    assert state.tick == 0
    original = main.frontend_state
    acquired = []
    def projection(*args, **kwargs):
        def other_thread():
            locked = service.lock.acquire(blocking=False)
            acquired.append(locked)
            if locked:
                service.lock.release()
        thread = Thread(target=other_thread)
        thread.start()
        thread.join(timeout=2)
        return original(*args, **kwargs)
    monkeypatch.setattr(main, "frontend_state", projection)
    assert client.get("/state?universe_id=prime").status_code == 200
    assert acquired == [True]
