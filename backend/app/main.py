"""FastAPI application for the local NPC Internet simulation."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from app.analytics import calculate_metrics
from app.experiments import run_side_by_side
from app.interventions import InterventionValidationError, apply_intervention
from app.models import (
    ExperimentRequest,
    ExperimentResult,
    Intervention,
    Metrics,
    SimulationState,
    TickRequest,
)
from app.persistence import StateStore, export_json
from app.presentation import EXPERIMENT_CATALOG, citizen_view, frontend_state
from app.simulation import Simulation


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
            self.store.save(self.universes["prime"].state)

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
            self.store.save(self.state)
            return self.state

    def tick_universe(self, universe_id: str, ticks: int) -> SimulationState:
        with self.lock:
            simulation = self.universe(universe_id)
            simulation.run(ticks)
            if universe_id == "prime":
                self.store.save(simulation.state)
            return simulation.state

    def reset(self, seed: int) -> SimulationState:
        with self.lock:
            self.simulation = Simulation(seed=seed)
            self.universes["prime"] = self.simulation
            self.store.save(self.state)
            return self.state

    def reset_universe(self, universe_id: str, seed: int | None = None) -> SimulationState:
        with self.lock:
            current = self.universe(universe_id)
            next_simulation = Simulation(seed=current.state.seed if seed is None else seed)
            self.universes[universe_id] = next_simulation
            if universe_id == "prime":
                self.simulation = next_simulation
                self.store.save(next_simulation.state)
            return next_simulation.state

    def apply(self, intervention: Intervention) -> SimulationState:
        with self.lock:
            apply_intervention(self.state, intervention)
            self.store.save(self.state)
            return self.state

    def apply_universe(self, universe_id: str, intervention: Intervention) -> SimulationState:
        with self.lock:
            simulation = self.universe(universe_id)
            apply_intervention(simulation.state, intervention)
            if universe_id == "prime":
                self.store.save(simulation.state)
            return simulation.state

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        with self.lock:
            self.simulation = Simulation(state=self.store.load(snapshot_id))
            self.universes["prime"] = self.simulation
            return self.state


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
        completed = {experiment_id for (run_universe, experiment_id) in service.experiment_runs if run_universe == universe_id}
        return frontend_state(service.universe(universe_id).state, universe_id, completed)
    return service.state


@app.post("/simulate/tick", response_model=SimulationState)
def simulate_tick(payload: TickRequest, request: Request) -> SimulationState:
    return get_service(request).tick(payload.ticks)


@app.post("/tick")
def compatibility_tick(payload: UniverseTickRequest, request: Request) -> dict[str, Any]:
    service = get_service(request)
    simulation_state = service.tick_universe(payload.universe_id, payload.steps)
    return frontend_state(simulation_state, payload.universe_id)


@app.post("/reset", response_model=None)
def compatibility_reset(payload: ResetRequest, request: Request) -> dict[str, Any] | SimulationState:
    service = get_service(request)
    if payload.universe_id is None:
        return service.reset(payload.seed)
    simulation_state = service.reset_universe(payload.universe_id)
    return frontend_state(simulation_state, payload.universe_id)


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
        completed = {experiment_id for (run_universe, experiment_id) in service.experiment_runs if run_universe == console.universe_id}
        return frontend_state(state, console.universe_id, completed)
    except (InterventionValidationError, ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/analytics", response_model=Metrics)
def analytics(request: Request) -> Metrics:
    return calculate_metrics(get_service(request).state)


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
    completed = {experiment_id for (run_universe, experiment_id) in service.experiment_runs if run_universe == universe_id}
    return {"citizens": frontend_state(service.universe(universe_id).state, universe_id, completed)["citizens"]}


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
    completed = {experiment_id for (run_universe, experiment_id) in service.experiment_runs if run_universe == universe_id}
    return {"experiments": frontend_state(service.universe(universe_id).state, universe_id, completed)["experiments"]}


@app.post("/experiments/run")
def run_catalog_experiment(payload: ExperimentRunRequest, request: Request) -> dict[str, Any]:
    service = get_service(request)
    if payload.experiment_id not in {experiment["id"] for experiment in EXPERIMENT_CATALOG}:
        raise HTTPException(status_code=404, detail=f"unknown experiment_id: {payload.experiment_id}")
    snapshot = service.universe(payload.universe_id).state
    topic = "fictional civic event"
    rumor = Intervention(
        kind="seed_post",
        topic=topic,
        text="SIMULATED CLAIM: the fictional civic event changed the moonlit transit schedule.",
        truth_label=False,
        strength=0.65,
    )
    if payload.experiment_id == "rumor-counterevidence":
        control = [rumor]
        treatment = [rumor, Intervention(
            kind="counterevidence",
            topic=topic,
            text="SIMULATED EVIDENCE: the fictional civic ledger records no schedule change.",
            truth_label=True,
            strength=0.9,
            delay_ticks=2,
        )]
    elif payload.experiment_id == "bridge-fact-check":
        control = [rumor]
        treatment = [rumor, Intervention(kind="fact_check", topic=topic, strength=0.9)]
    else:
        control = [rumor]
        treatment = [rumor, Intervention(
            kind="trust_shift",
            target_agent_id=sorted(snapshot.agents)[0],
            delta=0.2,
        )]
    try:
        result = run_side_by_side(snapshot, ticks=6, control_interventions=control, treatment_interventions=treatment)
    except InterventionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    service.experiment_runs[(payload.universe_id, payload.experiment_id)] = result
    return {
        "experiment_id": payload.experiment_id,
        "universe_id": payload.universe_id,
        "seed": result.seed,
        "ticks": result.ticks,
        "control_metrics": result.control_metrics.model_dump(mode="json"),
        "treatment_metrics": result.treatment_metrics.model_dump(mode="json"),
        "metric_deltas": result.metric_deltas,
        "earliest_divergence": _earliest_divergence(result.control, result.treatment),
    }


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
        return run_side_by_side(
            service.state,
            ticks=payload.ticks,
            control_interventions=payload.control_interventions,
            treatment_interventions=payload.treatment_interventions,
        )
    except InterventionValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/state/save")
def save_state(request: Request) -> dict[str, object]:
    service = get_service(request)
    return {"snapshot_id": service.store.save(service.state), "backend": service.store.backend_name}


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
