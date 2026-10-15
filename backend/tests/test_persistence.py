from app.persistence import JSONRepository, SQLiteRepository, export_json
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
