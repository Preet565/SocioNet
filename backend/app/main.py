"""FastAPI application for the local NPC Internet simulation."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.analytics import analytics_diagnostics, calculate_metrics, record_metric_sample
from app.batch_experiments import aggregate_paired_results, derive_seed
from app.causality import branch_causal_payload, causal_graph, compare_branches, ensure_causal_registry, impact_summary
from app.experiments import run_side_by_side
from app.interventions import InterventionValidationError, apply_intervention
from app.models import (
    ExperimentRequest as DomainExperimentRequest,
    ExperimentResult,
    BatchExperimentConfig,
    BatchProgress,
    Intervention,
    Metrics,
    PerturbationRequest,
    SimulationConfig,
    SimulationState,
    TickRequest as DomainTickRequest,
)
from app.multi_api import router as multi_experiments_router
from app.multi_experiments import (
    ENGINE_VERSION, MAX_AGENT_TICKS, MultiExperimentRunner,
    canonical_json, json_hash, validate_execution_limits,
)
from app.persistence import (
    EXPERIMENT_CONTROL_PROVENANCE,
    EXPERIMENT_SOURCE_PROVENANCE,
    EXPERIMENT_TREATMENT_PROVENANCE,
    StateStore,
    SCHEMA_VERSION,
    export_json,
)
from app.presentation import EXPERIMENT_CATALOG, citizen_view, frontend_state
from app.simulation import Simulation, create_initial_state, ensure_claim_registry


class ResetRequest(BaseModel):
    seed: int = Field(default=20261016, ge=0, le=2**63 - 1, strict=True)
    universe_id: Literal["prime", "echo"] | None = None
    config: SimulationConfig | None = None


class UniverseRequest(BaseModel):
    universe_id: Literal["prime", "echo"] = "prime"


class UniverseTickRequest(UniverseRequest):
    steps: int = Field(default=1, ge=1, le=100, strict=True)


class TickRequest(DomainTickRequest):
    ticks: int = Field(default=1, ge=1, le=100, strict=True)


class ExperimentRequest(DomainExperimentRequest):
    ticks: int = Field(default=3, ge=1, le=50, strict=True)
    control_interventions: list[Intervention] = Field(default_factory=list, max_length=16)
    treatment_interventions: list[Intervention] = Field(default_factory=list, max_length=16)
    hypothesis: str | None = Field(default=None, max_length=2000)


def _bounded_interventions(interventions: list[Intervention]) -> None:
    if len(interventions) > 16:
        raise ValueError("at most 16 interventions are allowed per branch")
    for intervention in interventions:
        for field in ("agent_id", "target_agent_id", "content_id", "claim_id"):
            value = getattr(intervention, field)
            if value is not None and (not value.strip() or len(value) > 100):
                raise ValueError("intervention references must be 1-100 characters")
        canonical_json(intervention.model_dump(mode="json"))


class ConsoleInterventionRequest(UniverseRequest):
    action: Literal["context_card", "bridge_prompt", "slow_signal", "highlight_care"]
    target: str = Field(min_length=1, max_length=100)
    intensity: float = Field(default=0.35, ge=0, le=1)
    rationale: str = Field(min_length=5, max_length=280)


class ExperimentRunRequest(UniverseRequest):
    experiment_id: str = Field(min_length=1, max_length=80)


class ExperimentRunCreateRequest(UniverseRequest):
    experiment_id: str = Field(min_length=1, max_length=80)
    ticks: int = Field(default=6, ge=1, le=50, strict=True)
    hypothesis: str | None = Field(default=None, max_length=2000)
    control_interventions: list[Intervention] = Field(default_factory=list, max_length=16)
    treatment_interventions: list[Intervention] = Field(default_factory=list, max_length=16)


class SimulationService:
    def __init__(
        self,
        seed: int = 20261016,
        database_path: str | None = None,
        config: SimulationConfig | None = None,
    ) -> None:
        path = database_path or os.getenv("SOCIONET_DB_PATH", "./data/socionet.sqlite3")
        self.store = StateStore(path)
        try:
            persisted = self.store.load()
        except FileNotFoundError:
            persisted = None
        self.universes: dict[str, Simulation] = {
            "prime": Simulation(state=persisted) if persisted is not None else Simulation(seed=seed, config=config),
            "echo": Simulation(
                seed=persisted.seed if persisted is not None else seed,
                config=config,
            ),
        }
        # Keep the original single-world API stable for scripts and tests.
        self.simulation = self.universes["prime"]
        self.experiment_runs: dict[tuple[str, str], Any] = {}
        self.lock = RLock()
        self.multi = MultiExperimentRunner(self.store)
        self.multi.recover_interrupted()
        self.recover_interrupted()
        if persisted is None:
            self._save_canonical(self.universes["prime"].state, "prime")
        else:
            self.store.save_metric_history(
                self.universes["prime"].state.metric_history,
                scope_id="canonical:prime",
                branch="canonical",
                universe_id="prime",
            )

    @property
    def state(self) -> SimulationState:
        return self.simulation.state

    def universe(self, universe_id: str) -> Simulation:
        try:
            return self.universes[universe_id]
        except KeyError as error:
            raise HTTPException(status_code=404, detail=f"unknown universe_id: {universe_id}") from error

    def tick(self, ticks: int) -> SimulationState:
        return self.tick_universe("prime", ticks)

    def tick_universe(self, universe_id: str, ticks: int) -> SimulationState:
        with self.lock:
            current = self.universe(universe_id).state
            if type(ticks) is not int or not 1 <= ticks <= 100 or len(current.agents) * ticks > MAX_AGENT_TICKS:
                raise ValueError("tick request exceeds the bounded agent-tick limit")
            validate_execution_limits(current, ticks=ticks, arm_count=1)
            simulation = Simulation(state=current.model_copy(deep=True))
            simulation.run(ticks)
            return self._publish_universe(universe_id, simulation)

    def reset(self, seed: int, config: SimulationConfig | None = None) -> SimulationState:
        with self.lock:
            return self._publish_universe("prime", Simulation(seed=seed, config=config))

    def reset_universe(
        self,
        universe_id: str,
        seed: int | None = None,
        config: SimulationConfig | None = None,
    ) -> SimulationState:
        with self.lock:
            current = self.universe(universe_id)
            next_simulation = Simulation(
                seed=current.state.seed if seed is None else seed,
                config=config or current.state.config,
            )
            return self._publish_universe(universe_id, next_simulation)

    def _publish_universe(self, universe_id: str, simulation: Simulation) -> SimulationState:
        if universe_id == "prime":
            self._save_canonical(simulation.state, universe_id)
        self.universes[universe_id] = simulation
        if universe_id == "prime":
            self.simulation = simulation
        return simulation.state.model_copy(deep=True)

    def apply(self, intervention: Intervention) -> SimulationState:
        return self.apply_universe("prime", intervention)

    def apply_universe(self, universe_id: str, intervention: Intervention) -> SimulationState:
        with self.lock:
            current = self.universe(universe_id).state
            _bounded_interventions([intervention])
            validate_execution_limits(current, ticks=1, arm_count=1, additional=[intervention])
            simulation = Simulation(state=current.model_copy(deep=True))
            apply_intervention(simulation.state, intervention)
            record_metric_sample(simulation.state)
            return self._publish_universe(universe_id, simulation)

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        with self.lock:
            simulation = Simulation(state=self.store.load(snapshot_id))
            # Loading an old frame is itself a durable canonical transition.
            return self._publish_universe("prime", simulation)

    def _save_canonical(self, state: SimulationState, universe_id: str) -> int:
        with self.lock, self.store.transaction():
            frame = state.model_copy(deep=True)
            snapshot_id = self.store.save(frame, universe_id=universe_id)
            self.store.save_metric_history(
                frame.metric_history,
                scope_id=f"canonical:{universe_id}",
                branch="canonical",
                universe_id=universe_id,
            )
        return snapshot_id

    def recover_interrupted(self) -> None:
        """A restart terminates synchronous work; reconcile durable batch children."""
        with self.lock, self.store.transaction():
            for summary in self.store.list_experiment_runs(limit=None, include_results=False):
                if summary["status"] not in {"created", "running"}:
                    continue
                record = self.store.get_experiment_run(summary["run_id"])
                record.update(status="failed", error="Execution interrupted; no automatic resume is supported.",
                              updated_at=datetime.now(timezone.utc).isoformat())
                self.store.save_experiment_run(record)
            for record in self.store.list_experiment_batches(limit=None):
                if record["status"] not in {"created", "running"}:
                    continue
                self._fail_batch(record, "Execution interrupted; rerun the saved batch configuration.")

    def _fail_batch(self, record: dict[str, Any], message: str) -> None:
        children = self.store.list_experiment_runs(batch_id=record["batch_id"], limit=None, include_results=False)
        children.sort(key=lambda child: (child.get("repetition") or 0, child["run_id"]))
        record["run_ids"] = [child["run_id"] for child in children]
        record["runs"] = [{key: child.get(key) for key in ("run_id", "repetition", "seed", "status")} for child in children]
        record["completed_runs"] = sum(child["status"] == "completed" for child in children)
        record["failed_runs"] = sum(child["status"] == "failed" for child in children)
        total = max(1, record.get("total_runs", 1))
        record["progress"] = BatchProgress(
            total_runs=total, completed_runs=record["completed_runs"], failed_runs=record["failed_runs"],
            percent_complete=100 * record["completed_runs"] / total,
            current_repetition=max((child.get("repetition") or 0 for child in children), default=0),
        ).model_dump(mode="json")
        record.pop("aggregate", None)
        record.pop("objective_success_rate", None)
        record.update(status="failed", error=message, updated_at=datetime.now(timezone.utc).isoformat())
        self.store.save_experiment_batch(record)

    def catalog_statuses(self, universe_id: str) -> dict[str, dict[str, Any]]:
        """Return latest durable run status for each catalog experiment."""

        statuses: dict[str, dict[str, Any]] = {}
        for run in self.store.list_experiment_runs(universe_id, limit=None, include_results=False):
            experiment_id = str(run.get("experiment_id", ""))
            if experiment_id not in statuses:
                raw_status = str(run.get("status", ""))
                catalog_status = {
                    "completed": "complete",
                    "running": "running",
                    "created": "running",
                    "failed": "paused",
                }.get(raw_status, "ready")
                statuses[experiment_id] = {
                    "status": catalog_status,
                    "last_run": run.get("updated_at") or run.get("created_at"),
                    "run_id": run.get("run_id"),
                    "run_status": raw_status,
                }
        return statuses

    def create_experiment_run(
        self,
        *,
        universe_id: str,
        experiment_id: str,
        ticks: int,
        control_interventions: list[Intervention] | None = None,
        treatment_interventions: list[Intervention] | None = None,
        hypothesis: str | None = None,
    ) -> dict[str, Any]:
        """Run and persist one reproducible experiment with tagged snapshots."""

        with self.lock:
            source = self.universe(universe_id).state.model_copy(deep=True)
            return self._create_experiment_run_from_source(
                source=source,
                universe_id=universe_id,
                experiment_id=experiment_id,
                ticks=ticks,
                control_interventions=control_interventions,
                treatment_interventions=treatment_interventions,
                hypothesis=hypothesis,
            )

    def _create_experiment_run_from_source(
        self,
        *,
        source: SimulationState,
        universe_id: str,
        experiment_id: str,
        ticks: int,
        control_interventions: list[Intervention] | None = None,
        treatment_interventions: list[Intervention] | None = None,
        batch_id: str | None = None,
        repetition: int | None = None,
        raise_on_error: bool = True,
        hypothesis: str | None = None,
    ) -> dict[str, Any]:
        """Persist one pair, optionally as a child of a multi-seed batch."""

        run_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        if control_interventions is None and treatment_interventions is None:
            control, treatment = catalog_interventions(source, experiment_id)
        else:
            control, treatment = control_interventions or [], treatment_interventions or []
        _bounded_interventions(control)
        _bounded_interventions(treatment)
        if type(ticks) is not int or not 1 <= ticks <= 50:
            raise ValueError("experiment ticks must be within 1-50")
        validate_execution_limits(source, ticks=ticks, arm_count=2, additional=control + treatment)
        saved_config = {
            "schema_version": 1, "experiment_id": experiment_id, "universe_id": universe_id,
            "seed": source.seed, "source_tick": source.tick, "ticks": ticks,
            "simulation": source.config.model_dump(mode="json"), "hypothesis": hypothesis,
            "control_interventions": [item.model_dump(mode="json") for item in control],
            "treatment_interventions": [item.model_dump(mode="json") for item in treatment],
        }
        record: dict[str, Any] = {
            "run_id": run_id,
            "experiment_id": experiment_id,
            "universe_id": universe_id,
            "status": "running",
            "seed": source.seed,
            "pair_seed": source.seed,
            "control_seed": source.seed,
            "treatment_seed": source.seed,
            "arm_seed": source.seed,
            "source_tick": source.tick,
            "ticks": ticks,
            "batch_id": batch_id,
            "repetition": repetition,
            "source_snapshot_id": None,
            "control_snapshot_id": None,
            "treatment_snapshot_id": None,
            "created_at": created_at,
            "updated_at": created_at,
            "hypothesis": hypothesis,
            "hypothesis_status": "provided" if hypothesis else "unspecified",
            "config": saved_config,
            "config_hash": json_hash(saved_config),
            "source_hash": json_hash(source.model_dump(mode="json")),
            "manifest": {"engine_version": ENGINE_VERSION, "config_version": 1, "storage_schema_version": SCHEMA_VERSION,
                         "hash_algorithm": "sha256", "canonical_json": "utf8-sort_keys-compact-ensure_ascii_false",
                         "execution_mode": "serial_synchronous", "cancellation_supported": False},
        }
        with self.store.transaction():
            source_snapshot_id = self.store.save(source, provenance=EXPERIMENT_SOURCE_PROVENANCE,
                                                 universe_id=universe_id, experiment_run_id=run_id)
            record["source_snapshot_id"] = source_snapshot_id
            record["source_snapshot"] = self._snapshot_reference(source_snapshot_id, EXPERIMENT_SOURCE_PROVENANCE)
            self.store.save_experiment_run(record)
        initial = deepcopy(record)
        try:
            result = run_side_by_side(
                source,
                ticks=ticks,
                control_interventions=control,
                treatment_interventions=treatment,
            )
        except Exception:
            self._fail_run(initial, "Experiment execution failed.")
            if raise_on_error:
                raise
            return initial
        try:
            with self.store.transaction():
                return self._complete_pair(record, result, experiment_id, universe_id)
        except Exception:
            self._fail_run(initial, "Experiment storage or result publication failed.")
            raise

    def _fail_run(self, record: dict[str, Any], message: str) -> None:
        record.update(status="failed", error=message, updated_at=datetime.now(timezone.utc).isoformat())
        self.store.save_experiment_run(record)

    def _complete_pair(self, record: dict[str, Any], result: ExperimentResult,
                       experiment_id: str, universe_id: str) -> dict[str, Any]:
            run_id = record["run_id"]
            control_snapshot_id = self.store.save(
                result.control,
                provenance=EXPERIMENT_CONTROL_PROVENANCE,
                universe_id=universe_id,
                experiment_run_id=run_id,
            )
            treatment_snapshot_id = self.store.save(
                result.treatment,
                provenance=EXPERIMENT_TREATMENT_PROVENANCE,
                universe_id=universe_id,
                experiment_run_id=run_id,
            )
            self.store.save_metric_history(
                result.control.metric_history,
                scope_id=run_id,
                branch="control",
                universe_id=universe_id,
                run_id=run_id,
            )
            self.store.save_metric_history(
                result.treatment.metric_history,
                scope_id=run_id,
                branch="treatment",
                universe_id=universe_id,
                run_id=run_id,
            )
            legacy_result = experiment_result_payload(result, experiment_id, universe_id)
            updated_at = datetime.now(timezone.utc).isoformat()
            record.update(
                {
                    "status": "completed",
                    "control_snapshot_id": control_snapshot_id,
                    "treatment_snapshot_id": treatment_snapshot_id,
                    "control_snapshot": self._snapshot_reference(control_snapshot_id, EXPERIMENT_CONTROL_PROVENANCE),
                    "treatment_snapshot": self._snapshot_reference(treatment_snapshot_id, EXPERIMENT_TREATMENT_PROVENANCE),
                    "result": legacy_result,
                    "control_hash": json_hash(result.control.model_dump(mode="json")),
                    "treatment_hash": json_hash(result.treatment.model_dump(mode="json")),
                    "updated_at": updated_at,
                }
            )
            self.store.save_experiment_run(record)
            self.experiment_runs[(universe_id, experiment_id)] = result
            return record

    def rerun_experiment(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.store.get_experiment_run(run_id)
            if record is None:
                raise HTTPException(404, detail="unknown experiment run")
            if not all(key in record for key in ("config", "manifest", "config_hash", "source_hash")):
                raise HTTPException(409, detail="Legacy run lacks exact-source replay metadata.")
            config = record["config"]
            source = self.store.load(record["source_snapshot_id"], provenance=EXPERIMENT_SOURCE_PROVENANCE,
                                     universe_id=record["universe_id"])
            metadata = self.store.snapshot_metadata(record["source_snapshot_id"], provenance=EXPERIMENT_SOURCE_PROVENANCE)
            if (record["manifest"]["engine_version"] != ENGINE_VERSION
                or json_hash(config) != record["config_hash"]
                or json_hash(source.model_dump(mode="json")) != record["source_hash"]
                or metadata.get("experiment_run_id") != run_id):
                raise HTTPException(409, detail="Saved experiment provenance is incompatible or invalid.")
            return self._create_experiment_run_from_source(
                source=source, universe_id=record["universe_id"], experiment_id=config["experiment_id"],
                ticks=config["ticks"], hypothesis=config.get("hypothesis"),
                control_interventions=[Intervention.model_validate(item) for item in config["control_interventions"]],
                treatment_interventions=[Intervention.model_validate(item) for item in config["treatment_interventions"]],
            )

    def create_experiment_batch(self, config: BatchExperimentConfig) -> dict[str, Any]:
        """Execute a bounded batch serially and persist progress after every pair."""

        with self.lock:
            if any(type(value) is not int for value in (config.population, config.ticks, config.repetitions)):
                raise ValueError("batch population, ticks, and repetitions must be integers")
            _bounded_interventions(config.control_interventions)
            _bounded_interventions(config.treatment_interventions)
            universe = self.universe(config.universe_id)
            base_seed = universe.state.seed if config.base_seed is None else config.base_seed
            effective_config = config.model_copy(update={"base_seed": base_seed})
            catalog_ids = {experiment["id"] for experiment in EXPERIMENT_CATALOG}
            if effective_config.experiment_id not in catalog_ids and not (
                effective_config.control_interventions or effective_config.treatment_interventions
            ):
                raise ValueError(f"unknown experiment_id: {effective_config.experiment_id}")

            batch_id = uuid4().hex
            created_at = datetime.now(timezone.utc).isoformat()
            record: dict[str, Any] = {
                "batch_id": batch_id,
                "experiment_id": effective_config.experiment_id,
                "universe_id": effective_config.universe_id,
                "status": "running",
                "config": effective_config.model_dump(mode="json"),
                "total_runs": effective_config.repetitions,
                "completed_runs": 0,
                "failed_runs": 0,
                "run_ids": [],
                "runs": [],
                "progress": BatchProgress(
                    total_runs=effective_config.repetitions,
                    completed_runs=0,
                    failed_runs=0,
                    percent_complete=0.0,
                    current_repetition=0,
                ).model_dump(mode="json"),
                "created_at": created_at,
                "updated_at": created_at,
            }
            self.store.save_experiment_batch(record)
            successful_results: list[ExperimentResult] = []
            failed_pairs = 0

            for repetition in range(1, effective_config.repetitions + 1):
                seed = derive_seed(base_seed, repetition)
                source = create_initial_state(seed, count=effective_config.population)
                child = self._create_experiment_run_from_source(
                    source=source,
                    universe_id=effective_config.universe_id,
                    experiment_id=effective_config.experiment_id,
                    ticks=effective_config.ticks,
                    control_interventions=(
                        None
                        if not effective_config.control_interventions and not effective_config.treatment_interventions
                        else effective_config.control_interventions
                    ),
                    treatment_interventions=(
                        None
                        if not effective_config.control_interventions and not effective_config.treatment_interventions
                        else effective_config.treatment_interventions
                    ),
                    batch_id=batch_id,
                    repetition=repetition,
                    raise_on_error=False,
                )
                record["run_ids"].append(child["run_id"])
                record["runs"].append(
                    {
                        "run_id": child["run_id"],
                        "repetition": repetition,
                        "seed": seed,
                        "status": child["status"],
                    }
                )
                if child["status"] == "completed":
                    successful_results.append(ExperimentResult.model_validate(child["result"]))
                    record["completed_runs"] += 1
                else:
                    failed_pairs += 1
                    record["failed_runs"] += 1
                record["progress"] = BatchProgress(
                    total_runs=effective_config.repetitions,
                    completed_runs=record["completed_runs"],
                    failed_runs=record["failed_runs"],
                    percent_complete=((repetition / effective_config.repetitions) * 100),
                    current_repetition=repetition,
                ).model_dump(mode="json")
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.store.save_experiment_batch(record)

            aggregate = aggregate_paired_results(
                successful_results,
                objective=effective_config.objective,
                confidence_level=effective_config.confidence_level,
                failed_runs=failed_pairs,
            )
            record["aggregate"] = aggregate.model_dump(mode="json")
            record["objective_success_rate"] = aggregate.objective_success_rate
            record["progress"] = BatchProgress(
                total_runs=effective_config.repetitions,
                completed_runs=record["completed_runs"],
                failed_runs=record["failed_runs"],
                percent_complete=100.0,
                current_repetition=effective_config.repetitions,
            ).model_dump(mode="json")
            record["status"] = (
                "completed"
                if record["failed_runs"] == 0
                else "completed_with_failures"
                if record["completed_runs"] > 0
                else "failed"
            )
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.store.save_experiment_batch(record)
            return record

    def create_perturbation(self, payload: PerturbationRequest) -> dict[str, Any]:
        """Run one explicit suppression variable against an unchanged control."""

        with self.lock:
            if type(payload.ticks) is not int or not 1 <= payload.ticks <= 50:
                raise ValueError("perturbation ticks must be within 1-50")
            _bounded_interventions([payload.perturbation])
            source = self.universe(payload.universe_id).state.model_copy(deep=True)
            return self._create_experiment_run_from_source(
                source=source,
                universe_id=payload.universe_id,
                experiment_id=f"perturbation:{payload.perturbation.kind.value}",
                ticks=payload.ticks,
                control_interventions=[],
                treatment_interventions=[payload.perturbation],
            )

    def _snapshot_reference(self, snapshot_id: int, provenance: str) -> dict[str, Any]:
        metadata = self.store.snapshot_metadata(snapshot_id, provenance=provenance)
        return {
            "snapshot_id": metadata["snapshot_id"],
            "seed": metadata["seed"],
            "tick": metadata["tick"],
            "created_at": metadata.get("created_at"),
            "provenance": metadata["provenance"],
            "universe_id": metadata["universe_id"],
            "experiment_run_id": metadata.get("experiment_run_id"),
        }


app = FastAPI(
    title="NPC Internet: The Butterfly Effect",
    version="0.1.0",
    description="A local, deterministic simulation of fictional citizens and information flow.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("SOCIONET_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.service = SimulationService()


def get_service(request: Request) -> SimulationService:
    return request.app.state.service


def projected_state(service: SimulationService, universe_id: str) -> dict[str, Any]:
    with service.lock:
        simulation_state = service.universe(universe_id).state.model_copy(deep=True)
    return frontend_state(
        simulation_state,
        universe_id,
        experiment_statuses=service.catalog_statuses(universe_id),
    )


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    service = get_service(request)
    with service.lock:
        return {
            "status": "ok",
            "seed": service.state.seed,
            "tick": service.state.tick,
            "population": len(service.state.agents),
            "persistence": service.store.backend_name,
        }


@app.get("/state")
def state(request: Request, universe_id: str | None = Query(default=None)) -> dict[str, Any] | SimulationState:
    service = get_service(request)
    if universe_id:
        return projected_state(service, universe_id)
    with service.lock:
        return service.state.model_copy(deep=True)


@app.post("/simulate/tick", response_model=SimulationState)
def simulate_tick(payload: TickRequest, request: Request) -> SimulationState:
    return get_service(request).tick(payload.ticks)


@app.post("/tick")
def compatibility_tick(payload: UniverseTickRequest, request: Request) -> dict[str, Any]:
    service = get_service(request)
    simulation_state = service.tick_universe(payload.universe_id, payload.steps)
    return frontend_state(
        simulation_state,
        payload.universe_id,
        experiment_statuses=service.catalog_statuses(payload.universe_id),
    )


@app.post("/reset", response_model=None)
def compatibility_reset(payload: ResetRequest, request: Request) -> dict[str, Any] | SimulationState:
    service = get_service(request)
    if payload.universe_id is None:
        return service.reset(payload.seed, config=payload.config)
    simulation_state = service.reset_universe(payload.universe_id, seed=payload.seed, config=payload.config)
    return frontend_state(
        simulation_state,
        payload.universe_id,
        experiment_statuses=service.catalog_statuses(payload.universe_id),
    )


def _console_intervention(payload: ConsoleInterventionRequest) -> Intervention:
    topic = "fictional civic event"
    if payload.action == "context_card":
        return Intervention(
            kind="counterevidence",
            topic=topic,
            text=f"Credible context card: {payload.rationale}",
            truth_label=True,
            strength=max(0.05, payload.intensity),
        )
    if payload.action == "bridge_prompt":
        return Intervention(
            kind="seed_post",
            topic=topic,
            text=f"Fictional bridge prompt: {payload.rationale}",
            strength=max(0.05, payload.intensity),
        )
    if payload.action == "slow_signal":
        return Intervention(kind="fact_check", topic=topic, strength=max(0.05, payload.intensity))
    return Intervention(
        kind="trust_shift",
        target_agent_id=payload.target,
        delta=max(0.02, payload.intensity * 0.25),
    )


@app.post("/interventions")
def intervention(payload: dict[str, object], request: Request) -> dict[str, Any] | SimulationState:
    service = get_service(request)
    try:
        # The canonical API accepts the typed Intervention shape. The command
        # center also has a deliberately smaller operator-facing shape.
        if "kind" in payload:
            typed = Intervention.model_validate(payload)
            return service.apply(typed)
        console = ConsoleInterventionRequest.model_validate(payload)
        state = service.apply_universe(console.universe_id, _console_intervention(console))
        return frontend_state(
            state,
            console.universe_id,
            experiment_statuses=service.catalog_statuses(console.universe_id),
        )
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid intervention request") from error


@app.get("/analytics", response_model=Metrics)
def analytics(request: Request) -> Metrics:
    service = get_service(request)
    with service.lock:
        state = service.state.model_copy(deep=True)
    return calculate_metrics(state)


@app.get("/analytics/diagnostics")
def analytics_diagnostics_endpoint(request: Request) -> dict[str, Any]:
    """Expose nullable diagnostics for metrics whose semantics need context."""

    service = get_service(request)
    with service.lock:
        state = service.state.model_copy(deep=True)
    return analytics_diagnostics(state)


def _bounded_limit(value: int) -> int:
    return min(200, max(1, value))


def _history_samples(
    service: SimulationService,
    *,
    universe_id: str,
    run_id: str | None,
    branch: str,
    from_tick: int | None,
    to_tick: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    scope_id = run_id or f"canonical:{universe_id}"
    return service.store.list_metric_history(
        scope_id=scope_id,
        branch=branch,
        from_tick=from_tick,
        to_tick=to_tick,
        limit=_bounded_limit(limit),
    )


@app.get("/analytics/history")
def analytics_history(
    request: Request,
    universe_id: Literal["prime", "echo"] = "prime",
    run_id: str | None = Query(default=None, min_length=1, max_length=100),
    branch: Literal["canonical", "control", "treatment"] = "canonical",
    from_tick: int | None = Query(default=None, ge=0),
    to_tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    service = get_service(request)
    if run_id and branch == "canonical":
        branch = "control"
    if run_id:
        run = service.store.get_experiment_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
        if run.get("universe_id") != universe_id:
            raise HTTPException(status_code=404, detail=f"experiment run is not in universe_id: {universe_id}")
    samples = _history_samples(
        service,
        universe_id=universe_id,
        run_id=run_id,
        branch=branch,
        from_tick=from_tick,
        to_tick=to_tick,
        limit=limit,
    )
    if not run_id and not samples:
        with service.lock:
            state = service.universe(universe_id).state.model_copy(deep=True)
        samples = [sample.model_dump(mode="json") for sample in state.metric_history]
        samples = [
            sample
            for sample in samples
            if (from_tick is None or sample["tick"] >= from_tick)
            and (to_tick is None or sample["tick"] <= to_tick)
        ][:_bounded_limit(limit)]
    return {
        "run_id": run_id,
        "universe_id": universe_id,
        "branch": branch,
        "samples": samples,
        "sample_count": len(samples),
        "metric_definitions": metric_definitions(),
    }


def _bounded_dynamic_history(
    rows: list[dict[str, Any]],
    *,
    from_tick: int | None,
    to_tick: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows
        if (from_tick is None or int(row.get("tick", 0)) >= from_tick)
        and (to_tick is None or int(row.get("tick", 0)) <= to_tick)
    ]
    selected.sort(key=lambda row: int(row.get("tick", 0)))
    return selected[:_bounded_limit(limit)]


@app.get("/network/history")
def network_history(
    request: Request,
    universe_id: Literal["prime", "echo"] = "prime",
    from_tick: int | None = Query(default=None, ge=0),
    to_tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    service = get_service(request)
    with service.lock:
        state = service.universe(universe_id).state.model_copy(deep=True)
    samples = _bounded_dynamic_history(
        [sample.model_dump(mode="json") for sample in state.network_history],
        from_tick=from_tick,
        to_tick=to_tick,
        limit=limit,
    )
    return {
        "universe_id": universe_id,
        "samples": samples,
        "sample_count": len(samples),
        "metric_cost": "Brandes betweenness is recalculated per tick in O(VE) for the unweighted undirected projection.",
    }


@app.get("/communities/history")
def communities_history(
    request: Request,
    universe_id: Literal["prime", "echo"] = "prime",
    from_tick: int | None = Query(default=None, ge=0),
    to_tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    service = get_service(request)
    with service.lock:
        state = service.universe(universe_id).state.model_copy(deep=True)
    samples = _bounded_dynamic_history(
        [sample.model_dump(mode="json") for sample in state.community_history],
        from_tick=from_tick,
        to_tick=to_tick,
        limit=limit,
    )
    events = _bounded_dynamic_history(
        [event.model_dump(mode="json") for event in state.community_events],
        from_tick=from_tick,
        to_tick=to_tick,
        limit=limit,
    )
    return {
        "universe_id": universe_id,
        "samples": samples,
        "events": events,
        "sample_count": len(samples),
        "event_count": len(events),
    }


def _run_branch_state(run: dict[str, Any], branch: str) -> SimulationState | None:
    result = run.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(branch), dict):
        return None
    try:
        state = SimulationState.model_validate(result[branch])
        ensure_claim_registry(state)
        ensure_causal_registry(state)
        return state
    except ValidationError:
        return None


def _run_branch_pair(run: dict[str, Any]) -> tuple[SimulationState, SimulationState]:
    control = _run_branch_state(run, "control")
    treatment = _run_branch_state(run, "treatment")
    if control is None or treatment is None:
        raise HTTPException(status_code=404, detail="experiment branch states unavailable")
    return control, treatment


def _claim_rows(state: SimulationState, claim_id: str | None = None, status: str | None = None, topic: str | None = None) -> list[dict[str, Any]]:
    metrics = calculate_metrics(state).claim_metrics
    rows: list[dict[str, Any]] = []
    for current_id in sorted(state.claims):
        claim = state.claims[current_id]
        if claim_id and current_id != claim_id:
            continue
        if status and claim.status.value != status:
            continue
        if topic and claim.topic != topic:
            continue
        rows.append({**claim.model_dump(mode="json"), "metrics": metrics.get(current_id).model_dump(mode="json") if current_id in metrics else None})
    return rows


@app.get("/analytics/claims")
def analytics_claims(
    request: Request,
    universe_id: Literal["prime", "echo"] = "prime",
    run_id: str | None = Query(default=None, min_length=1, max_length=100),
    branch: Literal["control", "treatment"] = "control",
    claim_id: str | None = Query(default=None, min_length=1, max_length=100),
    status: str | None = Query(default=None, min_length=1, max_length=30),
    topic: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    service = get_service(request)
    if run_id:
        run = service.store.get_experiment_run(run_id)
        if run is None or run.get("universe_id") != universe_id:
            raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
        state = _run_branch_state(run, branch)
        if state is None:
            raise HTTPException(status_code=404, detail=f"branch history unavailable: {branch}")
    else:
        with service.lock:
            state = service.universe(universe_id).state.model_copy(deep=True)
    rows = _claim_rows(state, claim_id=claim_id, status=status, topic=topic)[:_bounded_limit(limit)]
    return {"run_id": run_id, "universe_id": universe_id, "branch": branch if run_id else "canonical", "claims": rows}


@app.get("/analytics/claims/{claim_id}")
def analytics_claim(
    claim_id: str,
    request: Request,
    universe_id: Literal["prime", "echo"] = "prime",
    run_id: str | None = Query(default=None, min_length=1, max_length=100),
    branch: Literal["control", "treatment"] = "control",
) -> dict[str, Any]:
    service = get_service(request)
    if run_id:
        run = service.store.get_experiment_run(run_id)
        if run is None or run.get("universe_id") != universe_id:
            raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
        state = _run_branch_state(run, branch)
        if state is None:
            raise HTTPException(status_code=404, detail=f"branch history unavailable: {branch}")
    else:
        with service.lock:
            state = service.universe(universe_id).state.model_copy(deep=True)
    rows = _claim_rows(state, claim_id=claim_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown claim_id: {claim_id}")
    effective_branch = branch if run_id else "canonical"
    samples = _history_samples(
        service,
        universe_id=universe_id,
        run_id=run_id,
        branch=effective_branch,
        from_tick=None,
        to_tick=None,
        limit=200,
    )
    claim_history = [
        {"tick": sample.get("tick"), "metrics": sample.get("metrics", {}).get("claim_metrics", {}).get(claim_id)}
        for sample in samples
        if isinstance(sample.get("metrics"), dict)
        and claim_id in sample["metrics"].get("claim_metrics", {})
    ]
    return {**rows[0], "run_id": run_id, "universe_id": universe_id, "branch": effective_branch, "history": claim_history}


def metric_definitions() -> dict[str, str]:
    return {
        "exposures": "Recorded agent actions that observed content carrying the claim.",
        "reshares": "Claim-bearing replies or explicitly marked claim-bearing repeats.",
        "replies": "Claim-bearing content whose kind is reply.",
        "fact_checks": "Claim-bearing content whose kind is fact_check.",
        "corrections": "Fact-check or counterevidence content whose truth label contradicts the origin claim.",
        "history_limit": "At most 200 recent per-tick samples are retained per state or experiment branch.",
        "degree": "Number of distinct neighbors in the undirected projection of directed relationship records.",
        "betweenness": "Normalized shortest-path betweenness in the undirected relationship projection; Brandes costs O(VE) per tick.",
        "network_density": "Observed undirected social edges divided by possible node pairs.",
    }


@app.get("/traces")
def traces(
    request: Request,
    agent_id: str | None = Query(default=None),
    tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> list[dict[str, object]]:
    service = get_service(request)
    with service.lock:
        selected = list(service.state.traces)
    if agent_id:
        selected = [trace for trace in selected if trace.agent_id == agent_id]
    if tick is not None:
        selected = [trace for trace in selected if trace.tick == tick]
    return [trace.model_dump(mode="json") for trace in selected[-limit:]]


@app.get("/citizens")
def citizens(request: Request, universe_id: Literal["prime", "echo"] = "prime") -> dict[str, Any]:
    service = get_service(request)
    return {"citizens": projected_state(service, universe_id)["citizens"]}


@app.get("/citizens/{citizen_id}")
def citizen(citizen_id: str, request: Request, universe_id: Literal["prime", "echo"] = "prime") -> dict[str, Any]:
    service = get_service(request)
    with service.lock:
        simulation_state = service.universe(universe_id).state.model_copy(deep=True)
    if citizen_id not in simulation_state.agents:
        raise HTTPException(status_code=404, detail=f"unknown citizen_id: {citizen_id}")
    return citizen_view(simulation_state, citizen_id)


@app.get("/experiments")
def experiments(request: Request, universe_id: Literal["prime", "echo"] = "prime") -> dict[str, Any]:
    service = get_service(request)
    return {"experiments": projected_state(service, universe_id)["experiments"]}


def catalog_interventions(snapshot: SimulationState, experiment_id: str) -> tuple[list[Intervention], list[Intervention]]:
    """Build the small, documented catalog scenarios from one source frame."""

    topic = "fictional civic event"
    rumor = Intervention(
        kind="seed_post",
        topic=topic,
        text="SIMULATED CLAIM: the fictional civic event changed the moonlit transit schedule.",
        truth_label=False,
        strength=0.65,
    )
    if experiment_id == "rumor-counterevidence":
        control = [rumor]
        treatment = [
            rumor,
            Intervention(
                kind="counterevidence",
                topic=topic,
                text="SIMULATED EVIDENCE: the fictional civic ledger records no schedule change.",
                truth_label=True,
                strength=0.9,
                delay_ticks=2,
            ),
        ]
    elif experiment_id == "bridge-fact-check":
        control = [rumor]
        treatment = [rumor, Intervention(kind="fact_check", topic=topic, strength=0.9)]
    elif experiment_id == "trust-shift":
        control = [rumor]
        treatment = [
            rumor,
            Intervention(
                kind="trust_shift",
                target_agent_id=sorted(snapshot.agents)[0],
                delta=0.2,
            ),
        ]
    else:
        raise ValueError(f"unknown experiment_id: {experiment_id}")
    return control, treatment


def experiment_result_payload(result: ExperimentResult, experiment_id: str, universe_id: str) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload.update(
        {
            "experiment_id": experiment_id,
            "universe_id": universe_id,
            "earliest_divergence": _earliest_divergence(result.control, result.treatment),
        }
    )
    return payload


def legacy_comparison_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the original /experiments/run fields while adding Phase 5 data."""

    return {
        "experiment_id": result["experiment_id"],
        "universe_id": result["universe_id"],
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "seed": result["seed"],
        "ticks": result["ticks"],
        "control_metrics": result["control_metrics"],
        "treatment_metrics": result["treatment_metrics"],
        "metric_deltas": result["metric_deltas"],
        "earliest_divergence": result["earliest_divergence"],
        "branch_comparison": result.get("branch_comparison"),
        "impact_summary": result.get("impact_summary"),
        "hypothesis": result.get("hypothesis"),
        "config": result.get("config"),
        "config_hash": result.get("config_hash"),
        "source_hash": result.get("source_hash"),
        "manifest": result.get("manifest"),
    }


@app.post("/experiment-runs")
@app.post("/experiments/runs")
def create_experiment_run(payload: ExperimentRunCreateRequest, request: Request) -> dict[str, Any]:
    service = get_service(request)
    catalog_ids = {experiment["id"] for experiment in EXPERIMENT_CATALOG}
    if payload.experiment_id not in catalog_ids and not (payload.control_interventions or payload.treatment_interventions):
        raise HTTPException(status_code=404, detail=f"unknown experiment_id: {payload.experiment_id}")
    try:
        return service.create_experiment_run(
            universe_id=payload.universe_id,
            experiment_id=payload.experiment_id,
            ticks=payload.ticks,
            hypothesis=payload.hypothesis,
            control_interventions=None if payload.experiment_id in catalog_ids and not (payload.control_interventions or payload.treatment_interventions) else payload.control_interventions,
            treatment_interventions=None if payload.experiment_id in catalog_ids and not (payload.control_interventions or payload.treatment_interventions) else payload.treatment_interventions,
        )
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid experiment request") from error


@app.post("/perturbations")
@app.post("/causal/perturbations")
def create_perturbation(payload: PerturbationRequest, request: Request) -> dict[str, Any]:
    try:
        return get_service(request).create_perturbation(payload)
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid perturbation request") from error


@app.get("/experiment-runs")
@app.get("/experiments/runs")
def list_experiment_runs(
    request: Request,
    universe_id: Literal["prime", "echo"] | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_results: bool = True,
) -> dict[str, Any]:
    runs = get_service(request).store.list_experiment_runs(
        universe_id, status=status, limit=limit, include_results=include_results,
    )
    return {"runs": runs}


@app.post("/experiment-batches")
@app.post("/experiments/batches")
def create_experiment_batch(payload: BatchExperimentConfig, request: Request) -> dict[str, Any]:
    try:
        return get_service(request).create_experiment_batch(payload)
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid batch request") from error


@app.get("/experiment-batches")
@app.get("/experiments/batches")
def list_experiment_batches(
    request: Request,
    universe_id: Literal["prime", "echo"] | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    batches = get_service(request).store.list_experiment_batches(universe_id, status=status, limit=limit)
    return {"batches": batches}


def _get_experiment_batch_or_404(request: Request, batch_id: str) -> dict[str, Any]:
    batch = get_service(request).store.get_experiment_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment batch: {batch_id}")
    return batch


@app.get("/experiment-batches/{batch_id}/status")
@app.get("/experiments/batches/{batch_id}/status")
@app.get("/experiment-batches/{batch_id}/progress")
@app.get("/experiments/batches/{batch_id}/progress")
def experiment_batch_status(batch_id: str, request: Request) -> dict[str, Any]:
    batch = _get_experiment_batch_or_404(request, batch_id)
    return {
        "batch_id": batch["batch_id"],
        "status": batch["status"],
        "progress": batch.get("progress"),
        "completed_runs": batch.get("completed_runs", 0),
        "failed_runs": batch.get("failed_runs", 0),
        "total_runs": batch.get("total_runs", 0),
        "execution_mode": "serial_synchronous",
    }


@app.get("/experiment-batches/{batch_id}/runs")
@app.get("/experiments/batches/{batch_id}/runs")
def experiment_batch_runs(
    batch_id: str, request: Request, limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    batch = _get_experiment_batch_or_404(request, batch_id)
    runs = get_service(request).store.list_experiment_runs(
        batch.get("universe_id"), batch_id=batch_id, limit=limit,
    )
    return {"batch_id": batch_id, "runs": runs}


@app.get("/experiment-batches/{batch_id}")
@app.get("/experiments/batches/{batch_id}")
def get_experiment_batch(batch_id: str, request: Request) -> dict[str, Any]:
    return _get_experiment_batch_or_404(request, batch_id)


@app.get("/experiment-runs/{run_id}/export")
@app.get("/experiments/runs/{run_id}/export")
def export_experiment_run(run_id: str, request: Request) -> Response:
    run = get_service(request).store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    return Response(
        content=json.dumps(run, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=socionet-experiment-{run_id}.json"},
    )


@app.get("/experiment-runs/{run_id}/history")
@app.get("/experiments/runs/{run_id}/history")
def experiment_run_history(
    run_id: str,
    request: Request,
    branch: Literal["control", "treatment"] | None = Query(default=None),
    from_tick: int | None = Query(default=None, ge=0),
    to_tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=200, ge=1, le=200),
) -> dict[str, Any]:
    service = get_service(request)
    run = service.store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    branches = [branch] if branch else ["control", "treatment"]
    histories = {
        current_branch: {
            "samples": _history_samples(
                service,
                universe_id=str(run["universe_id"]),
                run_id=run_id,
                branch=current_branch,
                from_tick=from_tick,
                to_tick=to_tick,
                limit=limit,
            )
        }
        for current_branch in branches
    }
    return {
        "run_id": run_id,
        "experiment_id": run["experiment_id"],
        "universe_id": run["universe_id"],
        "seed": run.get("seed"),
        "ticks": run.get("ticks"),
        "branches": histories,
        "metric_definitions": metric_definitions(),
    }


@app.get("/causal/branch-comparison/{run_id}")
@app.get("/experiment-runs/{run_id}/branch-comparison")
@app.get("/experiment-runs/{run_id}/causal-comparison")
def causal_branch_comparison(run_id: str, request: Request) -> dict[str, Any]:
    service = get_service(request)
    run = service.store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    control, treatment = _run_branch_pair(run)
    return {
        "run_id": run_id,
        "experiment_id": run.get("experiment_id"),
        "universe_id": run.get("universe_id"),
        **compare_branches(control, treatment),
    }


@app.get("/causal/impact-summary/{run_id}")
@app.get("/experiment-runs/{run_id}/causal-impact-summary")
@app.get("/experiment-runs/{run_id}/impact-summary")
def causal_impact_summary(run_id: str, request: Request) -> dict[str, Any]:
    service = get_service(request)
    run = service.store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    control, treatment = _run_branch_pair(run)
    return {
        "run_id": run_id,
        "experiment_id": run.get("experiment_id"),
        "universe_id": run.get("universe_id"),
        **impact_summary(control, treatment),
    }


@app.get("/causal/graph")
@app.get("/causal/dependency-graph")
@app.get("/causal/graph/{run_id}")
@app.get("/experiment-runs/{run_id}/causal-graph")
def causal_dependency_graph(
    request: Request,
    run_id: str | None = None,
    universe_id: Literal["prime", "echo"] = "prime",
    branch: Literal["control", "treatment", "canonical"] = "canonical",
) -> dict[str, Any]:
    service = get_service(request)
    if run_id:
        run = service.store.get_experiment_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
        selected = _run_branch_state(run, branch if branch != "canonical" else "control")
        if selected is None:
            raise HTTPException(status_code=404, detail=f"branch history unavailable: {branch}")
    else:
        with service.lock:
            selected = service.universe(universe_id).state.model_copy(deep=True)
    effective_universe_id = str(run.get("universe_id")) if run_id and run else universe_id
    return {
        "run_id": run_id,
        "universe_id": effective_universe_id,
        "branch": branch if run_id else "canonical",
        **causal_graph(selected),
    }


@app.get("/experiment-runs/{run_id}")
@app.get("/experiments/runs/{run_id}")
def get_experiment_run(run_id: str, request: Request) -> dict[str, Any]:
    run = get_service(request).store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    return run


@app.post("/experiment-runs/{run_id}/rerun")
@app.post("/experiments/runs/{run_id}/rerun")
def rerun_experiment_run(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return get_service(request).rerun_experiment(run_id)
    except HTTPException:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail="saved experiment provenance is invalid") from error


@app.post("/experiments/run")
def run_catalog_experiment(payload: ExperimentRunRequest, request: Request) -> dict[str, Any]:
    service = get_service(request)
    if payload.experiment_id not in {experiment["id"] for experiment in EXPERIMENT_CATALOG}:
        raise HTTPException(status_code=404, detail=f"unknown experiment_id: {payload.experiment_id}")
    try:
        run = service.create_experiment_run(
            universe_id=payload.universe_id,
            experiment_id=payload.experiment_id,
            ticks=6,
        )
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid catalog experiment request") from error
    return legacy_comparison_payload({**run["result"], "run_id": run["run_id"], "status": run["status"]})


def _earliest_divergence(control: SimulationState, treatment: SimulationState) -> dict[str, Any] | None:
    """Compatibility projection of the Phase 5 semantic comparison."""

    return compare_branches(control, treatment)["earliest_divergence"]


@app.post("/experiments/side-by-side", response_model=ExperimentResult)
def side_by_side(payload: ExperimentRequest, request: Request) -> ExperimentResult:
    service = get_service(request)
    try:
        run = service.create_experiment_run(
            universe_id="prime",
            experiment_id="side-by-side",
            ticks=payload.ticks,
            control_interventions=payload.control_interventions,
            treatment_interventions=payload.treatment_interventions,
        )
        return ExperimentResult.model_validate(run["result"])
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail="invalid side-by-side experiment request") from error


@app.post("/state/save")
def save_state(request: Request) -> dict[str, object]:
    service = get_service(request)
    with service.lock:
        snapshot_id = service._save_canonical(service.state, "prime")
        backend = service.store.backend_name
    return {"snapshot_id": snapshot_id, "backend": backend}


@app.post("/state/load", response_model=SimulationState)
def load_state(request: Request, snapshot_id: int | None = Query(default=None, ge=1)) -> SimulationState:
    try:
        return get_service(request).load(snapshot_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="snapshot not found") from error


@app.get("/export/json")
def export_state(request: Request) -> Response:
    service = get_service(request)
    with service.lock:
        state = service.state.model_copy(deep=True)
    return Response(
        content=export_json(state),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=socionet-state.json"},
    )


def _mount_bundled_frontend() -> None:
    """Serve the built console when the single-container image enables it."""

    if os.getenv("SOCIONET_SERVE_FRONTEND", "0").lower() not in {"1", "true", "yes"}:
        return
    frontend_directory = Path(__file__).resolve().parents[1] / "static"
    if frontend_directory.is_dir():
        app.mount("/", StaticFiles(directory=frontend_directory, html=True), name="frontend")


app.include_router(multi_experiments_router)
_mount_bundled_frontend()
