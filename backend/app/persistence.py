"""Small SQLite repository with a JSON fallback for local snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from app.models import SimulationState


class StateRepository(Protocol):
    backend_name: str

    def save(self, state: SimulationState) -> int: ...

    def load(self, snapshot_id: int | None = None) -> SimulationState: ...


class SQLiteRepository:
    backend_name = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seed INTEGER NOT NULL,
                    tick INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, state: SimulationState) -> int:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots (seed, tick, payload) VALUES (?, ?, ?)",
                (state.seed, state.tick, state.model_dump_json()),
            )
            return int(cursor.lastrowid)

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        with sqlite3.connect(self.path) as connection:
            if snapshot_id is None:
                row = connection.execute(
                    "SELECT payload FROM snapshots ORDER BY snapshot_id DESC LIMIT 1"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
                ).fetchone()
        if row is None:
            raise FileNotFoundError("no matching SQLite snapshot")
        return SimulationState.model_validate_json(row[0])


class JSONRepository:
    backend_name = "json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: SimulationState) -> int:
        self.path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        return 1

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        if not self.path.exists():
            raise FileNotFoundError("no JSON snapshot")
        return SimulationState.model_validate(json.loads(self.path.read_text(encoding="utf-8")))


class StateStore:
    """Prefer SQLite, but keep a usable JSON file if SQLite is unavailable."""

    def __init__(self, path: str | Path) -> None:
        sqlite_path = Path(path)
        try:
            self.repository: StateRepository = SQLiteRepository(sqlite_path)
        except (OSError, sqlite3.Error):
            self.repository = JSONRepository(sqlite_path.with_suffix(".json"))

    @property
    def backend_name(self) -> str:
        return self.repository.backend_name

    def save(self, state: SimulationState) -> int:
        return self.repository.save(state)

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        return self.repository.load(snapshot_id)


def export_json(state: SimulationState, destination: str | Path | None = None) -> str:
    payload = state.model_dump_json(indent=2)
    if destination is not None:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return payload
