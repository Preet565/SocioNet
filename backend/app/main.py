"""FastAPI application for the local NPC Internet simulation."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.analytics import calculate_metrics
from app.batch_experiments import aggregate_paired_results, derive_seed
from app.experiments import run_side_by_side
from app.interventions import InterventionValidationError, apply_intervention
from app.models import (
    ExperimentRequest,
    ExperimentResult,
    BatchExperimentConfig,
    BatchProgress,
    Intervention,
    Metrics,
    SimulationState,
    TickRequest,
)
from app.persistence import (
    EXPERIMENT_CONTROL_PROVENANCE,
    EXPERIMENT_SOURCE_PROVENANCE,
    EXPERIMENT_TREATMENT_PROVENANCE,
    StateStore,
    export_json,
)
from app.presentation import EXPERIMENT_CATALOG, citizen_view, frontend_state
from app.simulation import Simulation, create_initial_state, ensure_claim_registry


class ResetRequest(BaseModel):
    seed: int = Field(default=20261016, ge=0)
    universe_id: Literal["prime", "echo"] | None = None


class UniverseRequest(BaseModel):
    universe_id: Literal["prime", "echo"] = "prime"


class UniverseTickRequest(UniverseRequest):
    steps: int = Field(default=1, ge=1, le=100)


class ConsoleInterventionRequest(UniverseRequest):
    action: Literal["context_card", "bridge_prompt", "slow_signal", "highlight_care"]
    target: str = Field(min_length=1, max_length=100)
    intensity: float = Field(default=0.35, ge=0, le=1)
    rationale: str = Field(min_length=5, max_length=280)


class ExperimentRunRequest(UniverseRequest):
    experiment_id: str = Field(min_length=1, max_length=80)


class ExperimentRunCreateRequest(UniverseRequest):
    experiment_id: str = Field(min_length=1, max_length=80)
    ticks: int = Field(default=6, ge=1, le=50)
    control_interventions: list[Intervention] = Field(default_factory=list)
    treatment_interventions: list[Intervention] = Field(default_factory=list)


class SimulationService:
    def __init__(self, seed: int = 20261016, database_path: str | None = None) -> None:
        path = database_path or os.getenv("SOCIONET_DB_PATH", "./data/socionet.sqlite3")
        self.store = StateStore(path)
        try:
            persisted = self.store.load()
        except FileNotFoundError:
            persisted = None
        self.universes: dict[str, Simulation] = {
            "prime": Simulation(state=persisted) if persisted is not None else Simulation(seed=seed),
            "echo": Simulation(seed=persisted.seed if persisted is not None else seed),
        }
        # Keep the original single-world API stable for scripts and tests.
        self.simulation = self.universes["prime"]
        self.experiment_runs: dict[tuple[str, str], Any] = {}
        self.lock = RLock()
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
        with self.lock:
            self.simulation.run(ticks)
            self._save_canonical(self.state, "prime")
            return self.state

    def tick_universe(self, universe_id: str, ticks: int) -> SimulationState:
        with self.lock:
            simulation = self.universe(universe_id)
            simulation.run(ticks)
            if universe_id == "prime":
                self._save_canonical(simulation.state, universe_id)
            return simulation.state

    def reset(self, seed: int) -> SimulationState:
        with self.lock:
            self.simulation = Simulation(seed=seed)
            self.universes["prime"] = self.simulation
            self._save_canonical(self.state, "prime")
            return self.state

    def reset_universe(self, universe_id: str, seed: int | None = None) -> SimulationState:
        with self.lock:
            current = self.universe(universe_id)
            next_simulation = Simulation(seed=current.state.seed if seed is None else seed)
            self.universes[universe_id] = next_simulation
            if universe_id == "prime":
                self.simulation = next_simulation
                self._save_canonical(next_simulation.state, universe_id)
            return next_simulation.state

    def apply(self, intervention: Intervention) -> SimulationState:
        with self.lock:
            apply_intervention(self.state, intervention)
            self._save_canonical(self.state, "prime")
            return self.state

    def apply_universe(self, universe_id: str, intervention: Intervention) -> SimulationState:
        with self.lock:
            simulation = self.universe(universe_id)
            apply_intervention(simulation.state, intervention)
            if universe_id == "prime":
                self._save_canonical(simulation.state, universe_id)
            return simulation.state

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        with self.lock:
            self.simulation = Simulation(state=self.store.load(snapshot_id))
            self.universes["prime"] = self.simulation
            self.store.save_metric_history(
                self.simulation.state.metric_history,
                scope_id="canonical:prime",
                branch="canonical",
                universe_id="prime",
            )
            return self.state

    def _save_canonical(self, state: SimulationState, universe_id: str) -> int:
        snapshot_id = self.store.save(state, universe_id=universe_id)
        self.store.save_metric_history(
            state.metric_history,
            scope_id=f"canonical:{universe_id}",
            branch="canonical",
            universe_id=universe_id,
        )
        return snapshot_id

    def catalog_statuses(self, universe_id: str) -> dict[str, dict[str, Any]]:
        """Return latest durable run status for each catalog experiment."""

        statuses: dict[str, dict[str, Any]] = {}
        for run in self.store.list_experiment_runs(universe_id):
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
    ) -> dict[str, Any]:
        """Persist one pair, optionally as a child of a multi-seed batch."""

        run_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        source_snapshot_id = self.store.save(
            source,
            provenance=EXPERIMENT_SOURCE_PROVENANCE,
            universe_id=universe_id,
            experiment_run_id=run_id,
        )
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
            "source_snapshot_id": source_snapshot_id,
            "control_snapshot_id": None,
            "treatment_snapshot_id": None,
            "created_at": created_at,
            "updated_at": created_at,
            "source_snapshot": self._snapshot_reference(source_snapshot_id, EXPERIMENT_SOURCE_PROVENANCE),
        }
        self.store.save_experiment_run(record)
        try:
            if control_interventions is None and treatment_interventions is None:
                control, treatment = catalog_interventions(source, experiment_id)
            else:
                control = control_interventions or []
                treatment = treatment_interventions or []
            result = run_side_by_side(
                source,
                ticks=ticks,
                control_interventions=control,
                treatment_interventions=treatment,
            )
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
                    "updated_at": updated_at,
                }
            )
            self.store.save_experiment_run(record)
            self.experiment_runs[(universe_id, experiment_id)] = result
            return record
        except Exception as error:
            record.update(
                {
                    "status": "failed",
                    "error": str(error),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.store.save_experiment_run(record)
            if raise_on_error:
                raise
            return record

    def create_experiment_batch(self, config: BatchExperimentConfig) -> dict[str, Any]:
        """Execute a bounded batch serially and persist progress after every pair."""

        with self.lock:
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
    simulation_state = service.universe(universe_id).state
    return frontend_state(
        simulation_state,
        universe_id,
        experiment_statuses=service.catalog_statuses(universe_id),
    )


@app.get("/health")
def health(request: Request) -> dict[str, object]:
    service = get_service(request)
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
    return service.state


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
        return service.reset(payload.seed)
    simulation_state = service.reset_universe(payload.universe_id)
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
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/analytics", response_model=Metrics)
def analytics(request: Request) -> Metrics:
    return calculate_metrics(get_service(request).state)


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
        state = service.universe(universe_id).state
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


def _run_branch_state(run: dict[str, Any], branch: str) -> SimulationState | None:
    result = run.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(branch), dict):
        return None
    try:
        state = SimulationState.model_validate(result[branch])
        ensure_claim_registry(state)
        return state
    except ValidationError:
        return None


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
        state = service.universe(universe_id).state
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
        state = service.universe(universe_id).state
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
    }


@app.get("/traces")
def traces(
    request: Request,
    agent_id: str | None = Query(default=None),
    tick: int | None = Query(default=None, ge=0),
) -> list[dict[str, object]]:
    selected = get_service(request).state.traces
    if agent_id:
        selected = [trace for trace in selected if trace.agent_id == agent_id]
    if tick is not None:
        selected = [trace for trace in selected if trace.tick == tick]
    return [trace.model_dump(mode="json") for trace in selected]


@app.get("/citizens")
def citizens(request: Request, universe_id: Literal["prime", "echo"] = "prime") -> dict[str, Any]:
    service = get_service(request)
    return {"citizens": projected_state(service, universe_id)["citizens"]}


@app.get("/citizens/{citizen_id}")
def citizen(citizen_id: str, request: Request, universe_id: Literal["prime", "echo"] = "prime") -> dict[str, Any]:
    service = get_service(request)
    simulation_state = service.universe(universe_id).state
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
    """Keep the original /experiments/run response shape stable."""

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
            control_interventions=None if payload.experiment_id in catalog_ids and not (payload.control_interventions or payload.treatment_interventions) else payload.control_interventions,
            treatment_interventions=None if payload.experiment_id in catalog_ids and not (payload.control_interventions or payload.treatment_interventions) else payload.treatment_interventions,
        )
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/experiment-runs")
@app.get("/experiments/runs")
def list_experiment_runs(
    request: Request,
    universe_id: Literal["prime", "echo"] | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    runs = get_service(request).store.list_experiment_runs(universe_id)
    if status is not None:
        runs = [run for run in runs if run.get("status") == status]
    return {"runs": runs}


@app.post("/experiment-batches")
@app.post("/experiments/batches")
def create_experiment_batch(payload: BatchExperimentConfig, request: Request) -> dict[str, Any]:
    try:
        return get_service(request).create_experiment_batch(payload)
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/experiment-batches")
@app.get("/experiments/batches")
def list_experiment_batches(
    request: Request,
    universe_id: Literal["prime", "echo"] | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    batches = get_service(request).store.list_experiment_batches(universe_id)
    if status is not None:
        batches = [batch for batch in batches if batch.get("status") == status]
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
def experiment_batch_runs(batch_id: str, request: Request) -> dict[str, Any]:
    batch = _get_experiment_batch_or_404(request, batch_id)
    runs = [run for run in get_service(request).store.list_experiment_runs(batch.get("universe_id")) if run.get("batch_id") == batch_id]
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


@app.get("/experiment-runs/{run_id}")
@app.get("/experiments/runs/{run_id}")
def get_experiment_run(run_id: str, request: Request) -> dict[str, Any]:
    run = get_service(request).store.get_experiment_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    return run


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
        raise HTTPException(status_code=422, detail=str(error)) from error
    return legacy_comparison_payload({**run["result"], "run_id": run["run_id"], "status": run["status"]})


def _earliest_divergence(control: SimulationState, treatment: SimulationState) -> dict[str, Any] | None:
    """Find the first recorded event that differs between two actual branches."""

    for index, (left, right) in enumerate(zip(control.events, treatment.events)):
        if left.model_dump(mode="json") != right.model_dump(mode="json"):
            return {"event_index": index, "control": left.model_dump(mode="json"), "treatment": right.model_dump(mode="json")}
    if len(control.events) != len(treatment.events):
        return {"event_index": min(len(control.events), len(treatment.events)), "reason": "branch event counts diverged"}
    return None


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
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/state/save")
def save_state(request: Request) -> dict[str, object]:
    service = get_service(request)
    return {"snapshot_id": service._save_canonical(service.state, "prime"), "backend": service.store.backend_name}


@app.post("/state/load", response_model=SimulationState)
def load_state(request: Request, snapshot_id: int | None = Query(default=None, ge=1)) -> SimulationState:
    try:
        return get_service(request).load(snapshot_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/export/json")
def export_state(request: Request) -> Response:
    return Response(
        content=export_json(get_service(request).state),
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


_mount_bundled_frontend()
