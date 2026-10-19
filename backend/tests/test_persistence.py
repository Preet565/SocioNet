import sqlite3

import pytest

from app.persistence import (
    CANONICAL_PROVENANCE,
    EXPERIMENT_SOURCE_PROVENANCE,
    JSONRepository,
    SQLiteRepository,
    export_json,
)
from app.simulation import Simulation


def test_sqlite_round_trip_preserves_seed_and_tick(tmp_path) -> None:
    state = Simulation(seed=73).run(1)
    repository = SQLiteRepository(tmp_path / "snapshots.sqlite3")
    snapshot_id = repository.save(state)
    restored = repository.load(snapshot_id)
    assert restored.model_dump(mode="json") == state.model_dump(mode="json")


def test_json_fallback_round_trip_and_export(tmp_path) -> None:
    state = Simulation(seed=74).run(1)
    repository = JSONRepository(tmp_path / "snapshot.json")
    repository.save(state)
    restored = repository.load()
    exported = export_json(restored, tmp_path / "export.json")
    assert '"seed": 74' in exported
    assert restored.tick == 1


def test_json_fallback_keeps_tagged_experiment_artifacts_out_of_canonical_file(tmp_path) -> None:
    repository = JSONRepository(tmp_path / "fallback.json")
    canonical = Simulation(seed=77).run(1)
    branch = Simulation(seed=77).run(2)
    repository.save(canonical)
    branch_id = repository.save(branch, provenance=EXPERIMENT_SOURCE_PROVENANCE, experiment_run_id="run-json")

    assert repository.load().tick == 1
    assert repository.load(branch_id, provenance=EXPERIMENT_SOURCE_PROVENANCE).tick == 2


def test_sqlite_tags_experiment_snapshots_and_keeps_canonical_load_filtered(tmp_path) -> None:
    state = Simulation(seed=75).run(1)
    repository = SQLiteRepository(tmp_path / "provenance.sqlite3")
    canonical_id = repository.save(state)
    experiment_id = repository.save(
        Simulation(seed=75).run(3),
        provenance=EXPERIMENT_SOURCE_PROVENANCE,
        universe_id="prime",
        experiment_run_id="run-1",
    )

    assert repository.load().tick == 1
    with pytest.raises(FileNotFoundError):
        repository.load(experiment_id)
    assert repository.load(experiment_id, provenance=EXPERIMENT_SOURCE_PROVENANCE).tick == 3
    assert repository.snapshot_metadata(canonical_id)["provenance"] == CANONICAL_PROVENANCE
    assert repository.snapshot_metadata(experiment_id, provenance=EXPERIMENT_SOURCE_PROVENANCE)["experiment_run_id"] == "run-1"

    repository.save_experiment_run(
        {
            "run_id": "run-1",
            "experiment_id": "rumor-counterevidence",
            "universe_id": "prime",
            "status": "completed",
            "seed": 75,
            "source_tick": 1,
            "ticks": 2,
            "source_snapshot_id": experiment_id,
            "control_snapshot_id": None,
            "treatment_snapshot_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:01+00:00",
        }
    )
    assert repository.get_experiment_run("run-1")["status"] == "completed"
    assert len(repository.list_experiment_runs("prime")) == 1


def test_existing_sqlite_schema_is_migrated_additively(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    state = Simulation(seed=76).state
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, seed INTEGER NOT NULL, "
            "tick INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO snapshots (seed, tick, payload) VALUES (?, ?, ?)",
            (state.seed, state.tick, state.model_dump_json()),
        )

    repository = SQLiteRepository(path)
    assert repository.load().seed == 76
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshots)")}
        assert {"provenance", "universe_id", "experiment_run_id"} <= columns
        assert connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "2"
