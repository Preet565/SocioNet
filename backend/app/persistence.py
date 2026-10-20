"""Durable state, provenance, and experiment repositories.

SQLite is the durable backend.  The JSON implementation deliberately keeps the
old single-file canonical snapshot contract while storing tagged experiment
artifacts in sidecar files, so an unavailable SQLite library or filesystem does
not make the existing API unusable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from app.models import MetricSample, SimulationState

try:
    import sqlite3
except ImportError:  # A Python build without the optional SQLite extension.
    sqlite3 = None


CANONICAL_PROVENANCE = "canonical"
EXPERIMENT_SOURCE_PROVENANCE = "experiment_source"
EXPERIMENT_CONTROL_PROVENANCE = "experiment_control"
EXPERIMENT_TREATMENT_PROVENANCE = "experiment_treatment"
SCHEMA_VERSION = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)


def _atomic_text(path: Path, payload: str) -> None:
    """Publish JSON artifacts atomically; a failed write preserves the old file."""
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


class StateRepository(Protocol):
    backend_name: str

    def transaction(self) -> Any: ...

    def save_multi_experiment(self, record: dict[str, Any]) -> None: ...

    def get_multi_experiment(self, run_id: str) -> dict[str, Any] | None: ...

    def list_multi_experiments(
        self, universe_id: str | None = None, *, limit: int | None = 50, status: str | None = None,
    ) -> list[dict[str, Any]]: ...

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
        universe_id: str | None = None,
    ) -> SimulationState: ...

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]: ...

    def save_experiment_run(self, run: dict[str, Any]) -> None: ...

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_experiment_runs(self, universe_id: str | None = None, **filters: Any) -> list[dict[str, Any]]: ...

    def save_experiment_batch(self, batch: dict[str, Any]) -> None: ...

    def get_experiment_batch(self, batch_id: str) -> dict[str, Any] | None: ...

    def list_experiment_batches(self, universe_id: str | None = None, **filters: Any) -> list[dict[str, Any]]: ...

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
        self._lock = RLock()
        self._active_connection = None
        with self._connection() as connection:
            self._initialize_schema(connection)

    @contextmanager
    def _connection(self):
        with self._lock:
            if self._active_connection is not None:
                yield self._active_connection
                return
            connection = sqlite3.connect(self.path)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

    @contextmanager
    def transaction(self):
        """All nested repository operations share one commit/rollback boundary."""
        with self._lock:
            if self._active_connection is not None:
                yield
                return
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._active_connection = connection
                try:
                    yield
                finally:
                    self._active_connection = None

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
        existing_version = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if existing_version is not None and int(existing_version[0]) > SCHEMA_VERSION:
            raise RuntimeError("database schema is newer than this backend")
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
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(experiment_runs)").fetchall()}
        run_migrations = {
            "batch_id": "TEXT",
            "repetition": "INTEGER",
            "arm_seed": "INTEGER",
        }
        for name, definition in run_migrations.items():
            if name not in run_columns:
                connection.execute(f"ALTER TABLE experiment_runs ADD COLUMN {name} {definition}")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS experiment_batches (
                batch_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                universe_id TEXT NOT NULL,
                status TEXT NOT NULL,
                total_runs INTEGER NOT NULL,
                completed_runs INTEGER NOT NULL,
                failed_runs INTEGER NOT NULL,
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
        connection.execute(
            """CREATE TABLE IF NOT EXISTS multi_experiments (
                id TEXT PRIMARY KEY,
                universe_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_multi_experiments_universe ON multi_experiments (universe_id, created_at DESC, id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_provenance ON snapshots (provenance, universe_id, snapshot_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots (experiment_run_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_experiment_runs_universe ON experiment_runs (universe_id, updated_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_experiment_runs_batch ON experiment_runs (batch_id, repetition)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_experiment_batches_universe ON experiment_batches (universe_id, updated_at DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_metric_samples_run ON metric_samples (run_id, branch, tick)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_claim_metric_samples_claim ON claim_metric_samples (scope_id, branch, claim_id, tick)")
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise ValueError("corrupt experiment run payload")
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
                "batch_id": row["batch_id"],
                "repetition": row["repetition"],
                "arm_seed": row["arm_seed"],
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
        with self._connection() as connection:
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
        universe_id: str | None = None,
    ) -> SimulationState:
        # Omitted universe retains legacy tagged-ID lookup; an explicit universe
        # always scopes the lookup. Canonical compatibility defaults to Prime.
        if provenance == CANONICAL_PROVENANCE and universe_id is None:
            universe_id = "prime"
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            if snapshot_id is None:
                row = connection.execute(
                    "SELECT payload FROM snapshots WHERE provenance = ? AND (? IS NULL OR universe_id = ?) "
                    "ORDER BY snapshot_id DESC LIMIT 1",
                    (provenance, universe_id, universe_id),
                ).fetchone()
            else:
                if universe_id is not None:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experiment_runs (
                    run_id, experiment_id, universe_id, status, seed, source_tick, ticks,
                    source_snapshot_id, control_snapshot_id, treatment_snapshot_id,
                    batch_id, repetition, arm_seed, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    batch_id = excluded.batch_id,
                    repetition = excluded.repetition,
                    arm_seed = excluded.arm_seed,
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
                    run.get("batch_id"),
                    run.get("repetition"),
                    run.get("arm_seed", run.get("seed")),
                    payload,
                    created_at,
                    now,
                ),
            )

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM experiment_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._row_to_run(row) if row is not None else None

    def list_experiment_runs(self, universe_id: str | None = None, *, status: str | None = None,
                             batch_id: str | None = None, limit: int | None = 50,
                             include_results: bool = True) -> list[dict[str, Any]]:
        clauses, parameters = [], []
        for field, value in (("universe_id", universe_id), ("status", status), ("batch_id", batch_id)):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        query = "SELECT * FROM experiment_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, run_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(min(200, max(1, limit)))
        # Avoid fetching/deserializing branch frames for lightweight listings.
        if not include_results:
            query = query.replace("SELECT *", "SELECT run_id, experiment_id, universe_id, status, seed, source_tick, ticks, "
                                  "source_snapshot_id, control_snapshot_id, treatment_snapshot_id, created_at, updated_at, "
                                  "batch_id, repetition, arm_seed, '{}' AS payload")
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_run(row) for row in rows]

    @staticmethod
    def _row_to_batch(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise ValueError("corrupt experiment batch payload")
        payload.update(
            {
                "batch_id": row["batch_id"],
                "experiment_id": row["experiment_id"],
                "universe_id": row["universe_id"],
                "status": row["status"],
                "total_runs": row["total_runs"],
                "completed_runs": row["completed_runs"],
                "failed_runs": row["failed_runs"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )
        return payload

    def save_experiment_batch(self, batch: dict[str, Any]) -> None:
        now = str(batch.get("updated_at") or _now())
        created_at = str(batch.get("created_at") or now)
        payload = json.dumps(batch, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO experiment_batches (
                    batch_id, experiment_id, universe_id, status, total_runs,
                    completed_runs, failed_runs, payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    experiment_id = excluded.experiment_id,
                    universe_id = excluded.universe_id,
                    status = excluded.status,
                    total_runs = excluded.total_runs,
                    completed_runs = excluded.completed_runs,
                    failed_runs = excluded.failed_runs,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    str(batch["batch_id"]),
                    str(batch["experiment_id"]),
                    str(batch["universe_id"]),
                    str(batch["status"]),
                    int(batch.get("total_runs", 0)),
                    int(batch.get("completed_runs", 0)),
                    int(batch.get("failed_runs", 0)),
                    payload,
                    created_at,
                    now,
                ),
            )

    def get_experiment_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM experiment_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return self._row_to_batch(row) if row is not None else None

    def list_experiment_batches(self, universe_id: str | None = None, *, status: str | None = None,
                                limit: int | None = 50) -> list[dict[str, Any]]:
        clauses, parameters = [], []
        for field, value in (("universe_id", universe_id), ("status", status)):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        query = "SELECT * FROM experiment_batches"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC, batch_id DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(min(200, max(1, limit)))
        with self._connection() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_batch(row) for row in rows]

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
        with self._connection() as connection:
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
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        values = [json.loads(row[0]) for row in rows]
        if any(not isinstance(value, dict) for value in values):
            raise ValueError("corrupt metric sample payload")
        return values

    def save_multi_experiment(self, record: dict[str, Any]) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO multi_experiments (id, universe_id, status, created_at, payload) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, payload = excluded.payload",
                (record["id"], record["universe_id"], record["status"], record["created_at"],
                 json.dumps(record, separators=(",", ":"), allow_nan=False)),
            )

    def get_multi_experiment(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM multi_experiments WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        if not isinstance(payload, dict):
            raise ValueError("corrupt multi-experiment payload")
        return payload

    def list_multi_experiments(
        self, universe_id: str | None = None, *, limit: int | None = 50, status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for field, value in (("universe_id", universe_id), ("status", status)):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"SELECT payload FROM multi_experiments{where} ORDER BY created_at DESC, id DESC"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(min(100, max(1, limit)))
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        values = [json.loads(row[0]) for row in rows]
        if any(not isinstance(value, dict) for value in values):
            raise ValueError("corrupt multi-experiment payload")
        return values


_JSON_LOCKS: dict[str, Any] = {}


def _serialized_repository(cls):
    """Serialize JSON read/modify/publish operations, including peer instances."""
    def locked(method):
        @wraps(method)
        def call(self, *args, **kwargs):
            with self._lock:
                return method(self, *args, **kwargs)
        return call
    for name, method in vars(cls).copy().items():
        if not name.startswith("_") and name != "transaction" and callable(method):
            setattr(cls, name, locked(method))
    return cls


@_serialized_repository
class JSONRepository:
    backend_name = "json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _JSON_LOCKS.setdefault(str(self.path.resolve()), RLock())
        self._staged: dict[str, Any] | None = None

    @property
    def _catalog_path(self) -> Path:
        return self.path.with_suffix(".repository.json")

    def _catalog(self) -> dict[str, Any]:
        if not self._catalog_path.exists():
            return {}
        value = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("corrupt JSON repository catalog")
        return value

    @contextmanager
    def transaction(self):
        """One atomic metadata manifest publishes immutable frames and histories.

        Old sidecars remain readable. Frames are written first; a failed commit
        may leave an unreferenced frame but cannot publish a dangling reference.
        This fallback supports one backend process, like the synchronous API.
        """
        with self._lock:
            if self._staged is not None:
                yield
                return
            self._staged = self._catalog()
            try:
                yield
                _atomic_text(self._catalog_path, json.dumps(self._staged, separators=(",", ":"), allow_nan=False))
            finally:
                self._staged = None

    @property
    def _snapshot_index_path(self) -> Path:
        return self.path.with_suffix(".snapshots.json")

    @property
    def _experiment_runs_path(self) -> Path:
        return self.path.with_suffix(".experiments.json")

    @property
    def _experiment_batches_path(self) -> Path:
        return self.path.with_suffix(".batches.json")

    @property
    def _metric_history_path(self) -> Path:
        return self.path.with_suffix(".metrics.json")

    @property
    def _multi_experiments_path(self) -> Path:
        return self.path.with_suffix(".multi-experiments.json")

    def _read_list(self, path: Path) -> list[dict[str, Any]]:
        catalog = self._staged if self._staged is not None else self._catalog()
        if path.name in catalog:
            # Never return mutable objects owned by an in-progress transaction.
            payload = json.loads(json.dumps(catalog[path.name]))
        elif path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            return []
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise ValueError(f"corrupt JSON repository list: {path.name}")
        return payload

    def _write_list(self, path: Path, values: list[dict[str, Any]]) -> None:
        if self._staged is None:
            with self.transaction():
                self._write_list(path, values)
        else:
            self._staged[path.name] = json.loads(json.dumps(values, allow_nan=False))

    def save(
        self,
        state: SimulationState,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str = "prime",
        experiment_run_id: str | None = None,
    ) -> int:
        with self.transaction():
            index = self._read_list(self._snapshot_index_path)
            # Import the legacy single canonical frame without changing its ID.
            if not index and self.path.exists():
                legacy = SimulationState.model_validate_json(self.path.read_text(encoding="utf-8"))
                index.append({"snapshot_id": 1, "seed": legacy.seed, "tick": legacy.tick,
                              "created_at": None, "provenance": CANONICAL_PROVENANCE,
                              "universe_id": "prime", "experiment_run_id": None, "path": self.path.name})
            snapshot_id = max((int(item.get("snapshot_id", 0)) for item in index), default=0) + 1
            run_part = _safe_filename(experiment_run_id or "unattached")
            filename = f"{self.path.stem}.snapshot-{snapshot_id}-{run_part}-{uuid4().hex}{self.path.suffix}"
            artifact = self.path.with_name(filename)
            _atomic_text(artifact, state.model_dump_json())
            index.append({
                "snapshot_id": snapshot_id,
                "seed": state.seed,
                "tick": state.tick,
                "created_at": _now(),
                "provenance": provenance,
                "universe_id": universe_id,
                "experiment_run_id": experiment_run_id,
                "path": artifact.name,
            })
            self._write_list(self._snapshot_index_path, index)
        return snapshot_id

    def load(
        self,
        snapshot_id: int | None = None,
        *,
        provenance: str = CANONICAL_PROVENANCE,
        universe_id: str | None = None,
    ) -> SimulationState:
        if universe_id is None and provenance == CANONICAL_PROVENANCE:
            universe_id = "prime"
        rows = [item for item in self._read_list(self._snapshot_index_path)
                if (snapshot_id is None or item.get("snapshot_id") == snapshot_id)
                and item.get("provenance") == provenance
                and (universe_id is None or item.get("universe_id", "prime") == universe_id)]
        if rows:
            row = max(rows, key=lambda item: int(item["snapshot_id"]))
            stored = Path(str(row["path"]))
            # Older indexes stored absolute/working-directory-relative paths.
            # Resolve only a sibling file, never an arbitrary imported path.
            path = self.path.parent / stored.name
            if path.resolve().parent != self.path.parent.resolve():
                raise ValueError("snapshot artifact must be a repository sibling")
        elif (not self._read_list(self._snapshot_index_path) and provenance == CANONICAL_PROVENANCE
              and universe_id == "prime" and snapshot_id in (None, 1)):
            path = self.path
        else:
            raise FileNotFoundError(f"no matching JSON {provenance} snapshot")
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

    def list_experiment_runs(self, universe_id: str | None = None, *, status: str | None = None,
                             batch_id: str | None = None, limit: int | None = 50,
                             include_results: bool = True) -> list[dict[str, Any]]:
        values = self._read_list(self._experiment_runs_path)
        values = [item for item in values if (universe_id is None or item.get("universe_id") == universe_id)
                  and (status is None or item.get("status") == status)
                  and (batch_id is None or item.get("batch_id") == batch_id)]
        values.sort(key=lambda item: (str(item.get("updated_at", item.get("created_at", ""))), str(item["run_id"])), reverse=True)
        if limit is not None:
            values = values[:min(200, max(1, limit))]
        return values if include_results else [{key: value for key, value in item.items() if key != "result"} for item in values]

    def save_experiment_batch(self, batch: dict[str, Any]) -> None:
        values = self._read_list(self._experiment_batches_path)
        values = [item for item in values if item.get("batch_id") != batch.get("batch_id")]
        values.append(batch)
        self._write_list(self._experiment_batches_path, values)

    def get_experiment_batch(self, batch_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read_list(self._experiment_batches_path) if item.get("batch_id") == batch_id), None)

    def list_experiment_batches(self, universe_id: str | None = None, *, status: str | None = None,
                                limit: int | None = 50) -> list[dict[str, Any]]:
        values = self._read_list(self._experiment_batches_path)
        if universe_id is not None:
            values = [item for item in values if item.get("universe_id") == universe_id]
        if status is not None:
            values = [item for item in values if item.get("status") == status]
        values.sort(key=lambda item: (str(item.get("updated_at", item.get("created_at", ""))), str(item["batch_id"])), reverse=True)
        return values if limit is None else values[:min(200, max(1, limit))]

    def save_multi_experiment(self, record: dict[str, Any]) -> None:
        values = [item for item in self._read_list(self._multi_experiments_path) if item["id"] != record["id"]]
        values.append(record)
        self._write_list(self._multi_experiments_path, values)

    def get_multi_experiment(self, run_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read_list(self._multi_experiments_path) if item["id"] == run_id), None)

    def list_multi_experiments(
        self, universe_id: str | None = None, *, limit: int | None = 50, status: str | None = None,
    ) -> list[dict[str, Any]]:
        values = [item for item in self._read_list(self._multi_experiments_path)
                  if (universe_id is None or item["universe_id"] == universe_id)
                  and (status is None or item["status"] == status)]
        values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return values if limit is None else values[:min(100, max(1, limit))]

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
        if sqlite3 is None:
            self.repository = JSONRepository(sqlite_path.with_suffix(".json"))
            return
        try:
            self.repository: StateRepository = SQLiteRepository(sqlite_path)
        except OSError:
            self.repository = JSONRepository(sqlite_path.with_suffix(".json"))
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", None)
            # Locking, SQL/schema errors and corruption must not silently switch
            # an existing database to an unrelated empty JSON world.
            if code is not None and code & 0xFF not in {sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}:
                raise
            self.repository = JSONRepository(sqlite_path.with_suffix(".json"))

    @property
    def backend_name(self) -> str:
        return self.repository.backend_name

    def transaction(self):
        return self.repository.transaction()

    def save_multi_experiment(self, record: dict[str, Any]) -> None:
        self.repository.save_multi_experiment(record)

    def get_multi_experiment(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_multi_experiment(run_id)

    def list_multi_experiments(
        self, universe_id: str | None = None, *, limit: int | None = 50, status: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list_multi_experiments(universe_id, limit=limit, status=status)

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
        universe_id: str | None = None,
    ) -> SimulationState:
        return self.repository.load(snapshot_id, provenance=provenance, universe_id=universe_id)

    def snapshot_metadata(self, snapshot_id: int, *, provenance: str = CANONICAL_PROVENANCE) -> dict[str, Any]:
        return self.repository.snapshot_metadata(snapshot_id, provenance=provenance)

    def save_experiment_run(self, run: dict[str, Any]) -> None:
        self.repository.save_experiment_run(run)

    def get_experiment_run(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_experiment_run(run_id)

    def list_experiment_runs(self, universe_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        return self.repository.list_experiment_runs(universe_id, **filters)

    def save_experiment_batch(self, batch: dict[str, Any]) -> None:
        self.repository.save_experiment_batch(batch)

    def get_experiment_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self.repository.get_experiment_batch(batch_id)

    def list_experiment_batches(self, universe_id: str | None = None, **filters: Any) -> list[dict[str, Any]]:
        return self.repository.list_experiment_batches(universe_id, **filters)

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
