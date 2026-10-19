"""Durable state, provenance, and experiment repositories.

SQLite is the durable backend.  The JSON implementation deliberately keeps the
old single-file canonical snapshot contract while storing tagged experiment
artifacts in sidecar files, so an unavailable SQLite library or filesystem does
not make the existing API unusable.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.models import MetricSample, SimulationState


CANONICAL_PROVENANCE = "canonical"
EXPERIMENT_SOURCE_PROVENANCE = "experiment_source"
EXPERIMENT_CONTROL_PROVENANCE = "experiment_control"
EXPERIMENT_TREATMENT_PROVENANCE = "experiment_treatment"
SCHEMA_VERSION = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)


class StateRepository(Protocol):
    backend_name: str

    def save(
        self,
        state: SimulationState,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
        experiment_run_id: str | None = None,
    ) -> int: ...

    def load(
        self,
        snapshot_id: int | None = None,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
    ) -> SimulationState: ...

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]: ...

    def save_experiment_run(self, run: dict[str, Any]) -> None: ...

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_experiment_runs(self, universe_id: str | None = None) -> list[dict[str, Any]]: ...

    def save_metric_history(
        self,
        samples: list[MetricSample],
        *,
        scope_id: str,
        branch: str,
        universe_id: str,
        run_id: str | None = None,
    ) -> None: ...

    def list_metric_history(
        self,
        *,
        scope_id: str,
        branch: str,
        from_tick: int | None = None,
        to_tick: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]: ...


class SQLiteRepository:
    backend_name = "sqlite"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            self._initialize_schema(connection)

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        """Create the current schema and migrate older snapshot databases.

        Migrations are additive.  Existing snapshots predate provenance and
        were canonical Prime snapshots, so those rows receive that explicit
        tag rather than becoming invisible after the migration.
        """

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                seed INTEGER NOT NULL,
                tick INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                provenance TEXT NOT NULL DEFAULT 'canonical',
                universe_id TEXT NOT NULL DEFAULT 'prime',
                experiment_run_id TEXT
            )
            """
        )

        columns = {row[1] for row in connection.execute("PRAGMA table_info(snapshots)").fetchall()}
        migrations = {
            "provenance": "TEXT NOT NULL DEFAULT 'canonical'",
            "universe_id": "TEXT NOT NULL DEFAULT 'prime'",
            "experiment_run_id": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE snapshots ADD COLUMN {name} {definition}")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_runs (
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                universe_id TEXT NOT NULL,
                status TEXT NOT NULL,
                seed INTEGER,
                source_tick INTEGER,
                ticks INTEGER,
                source_snapshot_id INTEGER,
                control_snapshot_id INTEGER,
                treatment_snapshot_id INTEGER,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metric_samples (
                scope_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                universe_id TEXT NOT NULL,
                run_id TEXT,
                tick INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scope_id, branch, tick)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_metric_samples (
                scope_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                universe_id TEXT NOT NULL,
                run_id TEXT,
                tick INTEGER NOT NULL,
                claim_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scope_id, branch, tick, claim_id)
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_provenance ON snapshots (provenance, universe_id, snapshot_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots (experiment_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_experiment_runs_universe ON experiment_runs (universe_id, updated_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_metric_samples_run ON metric_samples (run_id, branch, tick)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_claim_metric_samples_claim ON claim_metric_samples (scope_id, branch, claim_id, tick)")
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "run_id": row["run_id"],
                "experiment_id": row["experiment_id"],
                "universe_id": row["universe_id"],
                "status": row["status"],
                "seed": row["seed"],
                "source_tick": row["source_tick"],
                "ticks": row["ticks"],
                "source_snapshot_id": row["source_snapshot_id"],
                "control_snapshot_id": row["control_snapshot_id"],
                "treatment_snapshot_id": row["treatment_snapshot_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return payload

    def save(
        self,
        state: SimulationState,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
        experiment_run_id: str | None = None,
    ) -> int:
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots (seed, tick, payload, created_at, provenance, universe_id, experiment_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (state.seed, state.tick, state.model_dump_json(), _now(), provenance, universe_id, experiment_run_id),
            )
            return int(cursor.lastrowid)

    def load(
        self,
        snapshot_id: int | None = None,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
    ) -> SimulationState:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            if snapshot_id is None:
                row = connection.execute(
                    "SELECT payload FROM snapshots WHERE provenance = ? AND universe_id = ? "
                    "ORDER BY snapshot_id DESC LIMIT 1",
                    (provenance, universe_id),
                ).fetchone()
            else:
                if provenance == CANONICAL_PROVENANCE:
                    row = connection.execute(
                        "SELECT payload FROM snapshots WHERE snapshot_id = ? AND provenance = ? AND universe_id = ?",
                        (snapshot_id, provenance, universe_id),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT payload FROM snapshots WHERE snapshot_id = ? AND provenance = ?",
                        (snapshot_id, provenance),
                    ).fetchone()
        if row is None:
            raise FileNotFoundError(f"no matching SQLite {provenance} snapshot")
        return SimulationState.model_validate_json(row["payload"])

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT snapshot_id, seed, tick, created_at, provenance, universe_id, experiment_run_id "
                "FROM snapshots WHERE snapshot_id = ? AND provenance = ?",
                (snapshot_id, provenance),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"no matching SQLite {provenance} snapshot")
        return dict(row)

    def save_experiment_run(self, run: dict[str, Any]) -> None:
        now = str(run.get("updated_at") or _now())
        created_at = str(run.get("created_at") or now)
        payload = json.dumps(run, separators=(",", ":"))
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO experiment_runs (
                    run_id, experiment_id, universe_id, status, seed, source_tick, ticks,
                    source_snapshot_id, control_snapshot_id, treatment_snapshot_id,
                    payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    experiment_id = excluded.experiment_id,
                    universe_id = excluded.universe_id,
                    status = excluded.status,
                    seed = excluded.seed,
                    source_tick = excluded.source_tick,
                    ticks = excluded.ticks,
                    source_snapshot_id = excluded.source_snapshot_id,
                    control_snapshot_id = excluded.control_snapshot_id,
                    treatment_snapshot_id = excluded.treatment_snapshot_id,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    str(run["run_id"]),
                    str(run["experiment_id"]),
                    str(run["universe_id"]),
                    str(run["status"]),
                    run.get("seed"),
                    run.get("source_tick"),
                    run.get("ticks"),
                    run.get("source_snapshot_id"),
                    run.get("control_snapshot_id"),
                    run.get("treatment_snapshot_id"),
                    payload,
                    created_at,
                    now,
                ),
            )

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM experiment_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row is not None else None

    def list_experiment_runs(self, universe_id: str | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            if universe_id is None:
                rows = connection.execute("SELECT * FROM experiment_runs ORDER BY updated_at DESC, run_id DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM experiment_runs WHERE universe_id = ? ORDER BY updated_at DESC, run_id DESC",
                    (universe_id,),
                ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def save_metric_history(
        self,
        samples: list[MetricSample],
        *,
        scope_id: str,
        branch: str,
        universe_id: str,
        run_id: str | None = None,
    ) -> None:
        bounded = sorted(samples, key=lambda sample: sample.tick)[-200:]
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM metric_samples WHERE scope_id = ? AND branch = ?", (scope_id, branch))
            connection.execute("DELETE FROM claim_metric_samples WHERE scope_id = ? AND branch = ?", (scope_id, branch))
            for sample in bounded:
                payload = sample.model_dump(mode="json")
                connection.execute(
                    "INSERT INTO metric_samples (scope_id, branch, universe_id, run_id, tick, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (scope_id, branch, universe_id, run_id, sample.tick, json.dumps(payload, separators=(",", ":")), _now()),
                )
                for claim_id, claim_payload in sorted(sample.metrics.claim_metrics.items()):
                    connection.execute(
                        "INSERT INTO claim_metric_samples (scope_id, branch, universe_id, run_id, tick, claim_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            scope_id,
                            branch,
                            universe_id,
                            run_id,
                            sample.tick,
                            claim_id,
                            json.dumps(claim_payload.model_dump(mode="json"), separators=(",", ":")),
                            _now(),
                        ),
                    )

    def list_metric_history(
        self,
        *,
        scope_id: str,
        branch: str,
        from_tick: int | None = None,
        to_tick: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["scope_id = ?", "branch = ?"]
        parameters: list[Any] = [scope_id, branch]
        if from_tick is not None:
            clauses.append("tick >= ?")
            parameters.append(from_tick)
        if to_tick is not None:
            clauses.append("tick <= ?")
            parameters.append(to_tick)
        parameters.append(min(200, max(1, limit)))
        query = f"SELECT payload FROM metric_samples WHERE {' AND '.join(clauses)} ORDER BY tick ASC LIMIT ?"
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                values.append(payload)
        return values


class JSONRepository:
    backend_name = "json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def _snapshot_index_path(self) -> Path:
        return self.path.with_suffix(".snapshots.json")

    @property
    def _experiment_runs_path(self) -> Path:
        return self.path.with_suffix(".experiments.json")

    @property
    def _metric_history_path(self) -> Path:
        return self.path.with_suffix(".metrics.json")

    def _read_list(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def _write_list(self, path: Path, values: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(values, indent=2), encoding="utf-8")

    def save(
        self,
        state: SimulationState,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
        experiment_run_id: str | None = None,
    ) -> int:
        if provenance == CANONICAL_PROVENANCE:
            self.path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            snapshot_id = 1
        else:
            index = self._read_list(self._snapshot_index_path)
            snapshot_id = max((int(item.get("snapshot_id", 0)) for item in index), default=1) + 1
            run_part = _safe_filename(experiment_run_id or "unattached")
            filename = f"{self.path.stem}.snapshot-{snapshot_id}-{run_part}-{_safe_filename(provenance)}{self.path.suffix}"
            artifact = self.path.with_name(filename)
            artifact.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        index = [item for item in self._read_list(self._snapshot_index_path) if item.get("snapshot_id") != snapshot_id]
        index.append(
            {
                "snapshot_id": snapshot_id,
                "seed": state.seed,
                "tick": state.tick,
                "created_at": _now(),
                "provenance": provenance,
                "universe_id": universe_id,
                "experiment_run_id": experiment_run_id,
                "path": str(self.path if provenance == CANONICAL_PROVENANCE else artifact),
            }
        )
        self._write_list(self._snapshot_index_path, index)
        return snapshot_id

    def load(
        self,
        snapshot_id: int | None = None,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
    ) -> SimulationState:
        if provenance == CANONICAL_PROVENANCE:
            path = self.path
            if snapshot_id is not None and snapshot_id != 1:
                raise FileNotFoundError("no matching JSON canonical snapshot")
        else:
            row = next(
                (
                    item
                    for item in self._read_list(self._snapshot_index_path)
                    if item.get("snapshot_id") == snapshot_id and item.get("provenance") == provenance
                ),
                None,
            )
            if row is None:
                raise FileNotFoundError(f"no matching JSON {provenance} snapshot")
            path = Path(str(row["path"]))
        if not path.exists():
            raise FileNotFoundError("no JSON snapshot")
        return SimulationState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]:
        row = next(
            (
                item
                for item in self._read_list(self._snapshot_index_path)
                if item.get("snapshot_id") == snapshot_id and item.get("provenance") == provenance
            ),
            None,
        )
        if row is None and provenance == CANONICAL_PROVENANCE and snapshot_id == 1 and self.path.exists():
            state = self.load()
            return {
                "snapshot_id": 1,
                "seed": state.seed,
                "tick": state.tick,
                "created_at": None,
                "provenance": CANONICAL_PROVENANCE,
                "universe_id": "prime",
                "experiment_run_id": None,
            }
        if row is None:
            raise FileNotFoundError(f"no matching JSON {provenance} snapshot")
        return row

    def save_experiment_run(self, run: dict[str, Any]) -> None:
        values = self._read_list(self._experiment_runs_path)
        values = [item for item in values if item.get("run_id") != run.get("run_id")]
        values.append(run)
        self._write_list(self._experiment_runs_path, values)

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read_list(self._experiment_runs_path) if item.get("run_id") == run_id), None)

    def list_experiment_runs(self, universe_id: str | None = None) -> list[dict[str, Any]]:
        values = self._read_list(self._experiment_runs_path)
        if universe_id is not None:
            values = [item for item in values if item.get("universe_id") == universe_id]
        return sorted(values, key=lambda item: str(item.get("updated_at", item.get("created_at", ""))), reverse=True)

    def save_metric_history(
        self,
        samples: list[MetricSample],
        *,
        scope_id: str,
        branch: str,
        universe_id: str,
        run_id: str | None = None,
    ) -> None:
        values = [
            item
            for item in self._read_list(self._metric_history_path)
            if not (item.get("scope_id") == scope_id and item.get("branch") == branch)
        ]
        values.extend(
            {
                "scope_id": scope_id,
                "branch": branch,
                "universe_id": universe_id,
                "run_id": run_id,
                "tick": sample.tick,
                "payload": sample.model_dump(mode="json"),
            }
            for sample in sorted(samples, key=lambda sample: sample.tick)[-200:]
        )
        self._write_list(self._metric_history_path, values)

    def list_metric_history(
        self,
        *,
        scope_id: str,
        branch: str,
        from_tick: int | None = None,
        to_tick: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rows = [
            item
            for item in self._read_list(self._metric_history_path)
            if item.get("scope_id") == scope_id and item.get("branch") == branch
        ]
        if from_tick is not None:
            rows = [item for item in rows if int(item.get("tick", 0)) >= from_tick]
        if to_tick is not None:
            rows = [item for item in rows if int(item.get("tick", 0)) <= to_tick]
        rows.sort(key=lambda item: int(item.get("tick", 0)))
        return [item["payload"] for item in rows[: min(200, max(1, limit))] if isinstance(item.get("payload"), dict)]


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

    def save(
        self,
        state: SimulationState,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
        experiment_run_id: str | None = None,
    ) -> int:
        return self.repository.save(
            state,
            provenance=provenance,
            universe_id=universe_id,
            experiment_run_id=experiment_run_id,
        )

    def load(
        self,
        snapshot_id: int | None = None,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
    ) -> SimulationState:
        return self.repository.load(snapshot_id, provenance=provenance, universe_id=universe_id)

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]:
        return self.repository.snapshot_metadata(snapshot_id, provenance=provenance)

    def save_experiment_run(self, run: dict[str, Any]) -> None:
        self.repository.save_experiment_run(run)

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_experiment_run(run_id)

    def list_experiment_runs(self, universe_id: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_experiment_runs(universe_id)

    def save_metric_history(
        self,
        samples: list[MetricSample],
        *,
        scope_id: str,
        branch: str,
        universe_id: str,
        run_id: str | None = None,
    ) -> None:
        self.repository.save_metric_history(
            samples,
            scope_id=scope_id,
            branch=branch,
            universe_id=universe_id,
            run_id=run_id,
        )

    def list_metric_history(
        self,
        *,
        scope_id: str,
        branch: str,
        from_tick: int | None = None,
        to_tick: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.repository.list_metric_history(
            scope_id=scope_id,
            branch=branch,
            from_tick=from_tick,
            to_tick=to_tick,
            limit=limit,
        )


def export_json(state: SimulationState, destination: str | Path | None = None) -> str:
    payload = state.model_dump_json(indent=2)
    if destination is not None:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return payload
