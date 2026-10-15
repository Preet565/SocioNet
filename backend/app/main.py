"""FastAPI application for the local NPC Internet simulation."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

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
from app.simulation import Simulation


class ResetRequest(BaseModel):
    seed: int = Field(default=20261016, ge=0)


class SimulationService:
    def __init__(self, seed: int = 20261016, database_path: str | None = None) -> None:
        path = database_path or os.getenv("SOCIONET_DB_PATH", "./data/socionet.sqlite3")
        self.store = StateStore(path)
        self.simulation = Simulation(seed=seed)

    @property
    def state(self) -> SimulationState:
        return self.simulation.state

    def tick(self, ticks: int) -> SimulationState:
        self.simulation.run(ticks)
        self.store.save(self.state)
        return self.state

    def reset(self, seed: int) -> SimulationState:
        self.simulation = Simulation(seed=seed)
        self.store.save(self.state)
        return self.state

    def apply(self, intervention: Intervention) -> SimulationState:
        apply_intervention(self.state, intervention)
        self.store.save(self.state)
        return self.state

    def load(self, snapshot_id: int | None = None) -> SimulationState:
        self.simulation = Simulation(state=self.store.load(snapshot_id))
        return self.state


app = FastAPI(
    title="NPC Internet: The Butterfly Effect",
    version="0.1.0",
    description="A local, deterministic simulation of fictional citizens and information flow.",
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


@app.get("/state", response_model=SimulationState)
def state(request: Request) -> SimulationState:
    return get_service(request).state


@app.post("/simulate/tick", response_model=SimulationState)
def simulate_tick(payload: TickRequest, request: Request) -> SimulationState:
    return get_service(request).tick(payload.ticks)


@app.post("/reset", response_model=SimulationState)
def reset(payload: ResetRequest, request: Request) -> SimulationState:
    return get_service(request).reset(payload.seed)


@app.post("/interventions", response_model=SimulationState)
def intervention(payload: Intervention, request: Request) -> SimulationState:
    try:
        return get_service(request).apply(payload)
    except InterventionValidationError as error:
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
