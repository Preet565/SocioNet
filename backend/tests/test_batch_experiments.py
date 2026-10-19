from fastapi.testclient import TestClient
import pytest

from app.batch_experiments import aggregate_paired_results, derive_seed, descriptive_statistics, paired_effect_sizes
from app.experiments import run_side_by_side
from app.main import SimulationService, app
from app.models import BatchExperimentConfig, BatchObjective
from app.persistence import JSONRepository, SQLiteRepository
from app.simulation import Simulation


def test_batch_config_normalizes_objective_and_enforces_bounds() -> None:
    config = BatchExperimentConfig(
        experiment_id="  rumor-counterevidence ",
        population=4,
        ticks=2,
        repetitions=3,
        computational_budget=48,
        objective={"metric": " correction_reach ", "threshold": 0.1},
    )

    assert config.experiment_id == "rumor-counterevidence"
    assert config.objective.metric == "correction_reach"

    with pytest.raises(ValueError, match="computational_budget"):
        BatchExperimentConfig(
            experiment_id="rumor-counterevidence",
            population=4,
            ticks=2,
            repetitions=3,
            computational_budget=23,
        )
    with pytest.raises(ValueError, match="objective metric"):
        BatchExperimentConfig(experiment_id="rumor-counterevidence", objective={"metric": "not-a-metric"})


def test_seed_derivation_is_stable_and_repetition_specific() -> None:
    assert derive_seed(1234, 1) == derive_seed(1234, 1)
    assert derive_seed(1234, 1) != derive_seed(1234, 2)
    with pytest.raises(ValueError):
        derive_seed(1234, 0)


def test_descriptive_statistics_and_paired_effect_sizes_are_deterministic() -> None:
    summary = descriptive_statistics([1, 2, 3])
    effect = paired_effect_sizes([1, 2, 3])

    assert summary.count == 3
    assert summary.mean == 2
    assert summary.median == 2
    assert summary.variance == 1
    assert summary.standard_deviation == 1
    assert summary.confidence_interval is not None
    assert summary.confidence_interval.lower == 1
    assert summary.confidence_interval.upper == 3
    assert effect.paired_cohens_d == 2
    assert effect.cliffs_delta == 1
    assert effect.probability_treatment_better == 1


def test_aggregate_reports_paired_metrics_and_objective_success_rate() -> None:
    first = run_side_by_side(Simulation(seed=101).state, ticks=1)
    second = run_side_by_side(Simulation(seed=102).state, ticks=1)
    aggregate = aggregate_paired_results(
        [first, second],
        objective=BatchObjective(metric="total_contents", threshold=0),
    )

    assert aggregate.completed_pairs == 2
    assert aggregate.failed_runs == 0
    assert aggregate.objective.evaluated == 2
    assert aggregate.objective.success_rate == 1
    assert aggregate.metrics["total_contents"].control.count == 2
    assert aggregate.metrics["total_contents"].effect_size.cliffs_delta is not None


@pytest.mark.parametrize("repository_type", [SQLiteRepository, JSONRepository])
def test_batch_metadata_is_durable_in_both_repositories(tmp_path, repository_type) -> None:
    repository = repository_type(tmp_path / "batch-state.sqlite3")
    batch = {
        "batch_id": "batch-1",
        "experiment_id": "rumor-counterevidence",
        "universe_id": "prime",
        "status": "completed",
        "total_runs": 2,
        "completed_runs": 2,
        "failed_runs": 0,
        "progress": {"percent_complete": 100},
        "aggregate": {"objective_success_rate": 0.5},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:01+00:00",
    }
    repository.save_experiment_batch(batch)

    assert repository.get_experiment_batch("batch-1")["aggregate"]["objective_success_rate"] == 0.5
    assert repository.list_experiment_batches("prime")[0]["batch_id"] == "batch-1"


def test_batch_api_persists_children_progress_and_aggregate_after_restart(tmp_path) -> None:
    database_path = str(tmp_path / "batch-api.sqlite3")
    original = app.state.service
    app.state.service = SimulationService(seed=501, database_path=database_path)
    config = {
        "universe_id": "prime",
        "experiment_id": "rumor-counterevidence",
        "base_seed": 9001,
        "population": 8,
        "ticks": 2,
        "repetitions": 3,
        "computational_budget": 1000,
        "objective": {"metric": "correction_reach", "direction": "increase", "threshold": 0},
    }
    try:
        client = TestClient(app)
        created = client.post("/experiment-batches", json=config)
        assert created.status_code == 200
        batch = created.json()
        assert batch["status"] == "completed"
        assert batch["progress"]["percent_complete"] == 100
        assert batch["progress"]["current_repetition"] == 3
        assert batch["completed_runs"] == 3
        assert batch["failed_runs"] == 0
        assert batch["aggregate"]["completed_pairs"] == 3
        assert 0 <= batch["objective_success_rate"] <= 1
        assert set(batch["aggregate"]["metrics"]) >= {"total_contents", "correction_reach"}

        child_runs = client.get(f"/experiment-batches/{batch['batch_id']}/runs")
        assert child_runs.status_code == 200
        runs = sorted(child_runs.json()["runs"], key=lambda run: run["repetition"])
        assert [run["seed"] for run in runs] == [derive_seed(9001, repetition) for repetition in (1, 2, 3)]
        assert all(run["batch_id"] == batch["batch_id"] for run in runs)
        assert all(run["pair_seed"] == run["seed"] for run in runs)

        detail = client.get(f"/experiments/batches/{batch['batch_id']}")
        assert detail.status_code == 200
        assert detail.json()["batch_id"] == batch["batch_id"]
        progress = client.get(f"/experiment-batches/{batch['batch_id']}/progress")
        assert progress.status_code == 200
        assert progress.json()["execution_mode"] == "serial_synchronous"
        assert progress.json()["progress"]["completed_runs"] == 3
        listed = client.get("/experiment-batches", params={"universe_id": "prime"})
        assert listed.status_code == 200
        assert listed.json()["batches"][0]["batch_id"] == batch["batch_id"]

        repeat = client.post("/experiments/batches", json=config)
        assert repeat.status_code == 200
        assert repeat.json()["aggregate"] == batch["aggregate"]
    finally:
        app.state.service = original

    app.state.service = SimulationService(seed=999, database_path=database_path)
    try:
        client = TestClient(app)
        assert client.get(f"/experiment-batches/{batch['batch_id']}").json()["aggregate"] == batch["aggregate"]
        assert client.get("/experiment-runs", params={"universe_id": "prime"}).json()["runs"]
    finally:
        app.state.service = original


def test_batch_api_rejects_unknown_experiment_and_budget_overrun(tmp_path) -> None:
    original = app.state.service
    app.state.service = SimulationService(seed=502, database_path=str(tmp_path / "validation.sqlite3"))
    try:
        client = TestClient(app)
        unknown = client.post(
            "/experiment-batches",
            json={"experiment_id": "unknown-scenario", "population": 2, "ticks": 1, "repetitions": 1},
        )
        assert unknown.status_code == 422
        over_budget = client.post(
            "/experiment-batches",
            json={
                "experiment_id": "rumor-counterevidence",
                "population": 10,
                "ticks": 10,
                "repetitions": 2,
                "computational_budget": 100,
            },
        )
        assert over_budget.status_code == 422
    finally:
        app.state.service = original
