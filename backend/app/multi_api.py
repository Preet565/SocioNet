"""HTTP adapter for Phase 6. The simulation and runner never import this module."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from app.multi_experiments import (
    ARM_PROVENANCE,
    MAX_AGENT_TICKS,
    MAX_BUNDLE_BYTES,
    MAX_CONFIG_BYTES,
    MAX_SOURCE_BYTES,
    ExperimentBundle,
    MultiExperimentConfig,
    PreparedExperiment,
    canonical_json,
    prepare_experiment,
    run_detail,
    json_hash,
)


router = APIRouter(prefix="/multi-experiments", tags=["multi-experiments"])


def _validation_error(error: ValueError) -> RequestValidationError:
    if isinstance(error, ValidationError):
        return RequestValidationError([
            {key: value for key, value in item.items() if key not in {"ctx", "input"}}
            | {"loc": ("body", *item["loc"])} for item in error.errors()
        ])
    return RequestValidationError([{"type": "value_error", "loc": ("body",), "msg": str(error), "input": None}])


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _invalid_number(value: str) -> Any:
    raise ValueError(f"invalid JSON number {value}")


async def _read_body(request: Request, maximum: int) -> Any:
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > maximum:
            raise _validation_error(ValueError(f"request body exceeds {maximum} bytes"))
    try:
        return json.loads(payload, object_pairs_hook=_unique_object, parse_constant=_invalid_number)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _validation_error(ValueError(str(error))) from error


async def _config_body(request: Request) -> MultiExperimentConfig:
    try:
        return MultiExperimentConfig.model_validate(await _read_body(request, MAX_CONFIG_BYTES))
    except ValueError as error:
        raise _validation_error(error) from error


async def _bundle_body(request: Request) -> ExperimentBundle:
    try:
        return ExperimentBundle.model_validate(await _read_body(request, MAX_BUNDLE_BYTES))
    except ValueError as error:
        raise _validation_error(error) from error


ConfigBody = Annotated[MultiExperimentConfig, Depends(_config_body)]
BundleBody = Annotated[ExperimentBundle, Depends(_bundle_body)]


def _prepared(service: Any, config: MultiExperimentConfig) -> Any:
    source = service.universe(config.source.universe_id).state if config.source.mode == "universe" else None
    try:
        return prepare_experiment(config, universe_source=source)
    except ValueError as error:
        raise _validation_error(error) from error


def _prepared_bundle(bundle: ExperimentBundle) -> PreparedExperiment:
    try:
        return bundle.prepare()
    except ValueError as error:
        raise _validation_error(error) from error


def _get(service: Any, run_id: str) -> dict[str, Any]:
    record = service.store.get_multi_experiment(run_id)
    if record is None:
        raise HTTPException(404, detail=f"unknown multi-experiment: {run_id}")
    return record


def _attachment(value: Any, filename: str) -> Response:
    return Response(canonical_json(value), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/schema")
def schema() -> dict[str, Any]:
    result = MultiExperimentConfig.model_json_schema()
    result["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    result["x-limits"] = {"config_bytes": MAX_CONFIG_BYTES, "agent_ticks": MAX_AGENT_TICKS,
                          "source_bytes": MAX_SOURCE_BYTES, "bundle_bytes": MAX_BUNDLE_BYTES}
    result["description"] = (
        "Version 1. Generated sources require seed/simulation. Universe inputs may omit them; "
        "supplied values must match the captured world; composition must be {}. at_tick is source-relative "
        "and 0 is immediate; delay_ticks must be 0. Community trust_shift uses captured observers; "
        "community seed_post chooses a captured messenger. Counterevidence requires a shared claim_id."
    )
    return result


@router.post("/validate")
def validate(config: ConfigBody, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        prepared = _prepared(service, config)
        return {"valid": True, "config": prepared.config.model_dump(mode="json"), "plan": prepared.plan}


@router.post("")
def create(
    config: ConfigBody, request: Request,
    expected_source_hash: str | None = Query(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        prepared = _prepared(service, config)
        if expected_source_hash is not None and expected_source_hash != prepared.plan["source_hash"]:
            raise HTTPException(409, detail="Source changed since validation; validate again.")
        return run_detail(service.multi.create(prepared))


@router.post("/import/validate")
def validate_bundle(bundle: BundleBody, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        prepared = _prepared_bundle(bundle)
        return {"valid": True, "config": prepared.config.model_dump(mode="json"), "plan": prepared.plan}


@router.post("/import")
def import_bundle(bundle: BundleBody, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        return run_detail(service.multi.create(_prepared_bundle(bundle)))


@router.get("")
def list_runs(
    request: Request,
    universe_id: Literal["prime", "echo"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        records = service.store.list_multi_experiments(universe_id, limit=limit)
        return {"experiments": [
            {key: record[key] for key in ("id", "status", "name", "created_at", "source_tick", "baseline_arm_id",
                                         "config_hash", "source_hash", "universe_id")}
            | {"arm_count": len(record["arms"]), "ticks": record["config"]["ticks"]}
            for record in records
        ]}


@router.get("/{run_id}")
def detail(run_id: str, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        return run_detail(_get(service, run_id))


@router.get("/{run_id}/config")
def download_config(run_id: str, request: Request) -> Response:
    service = request.app.state.service
    with service.lock:
        record = _get(service, run_id)
        return _attachment(record["config"], f"socionet-multi-{record['id']}-config.json")


@router.get("/{run_id}/export")
def export(run_id: str, request: Request) -> Response:
    service = request.app.state.service
    with service.lock:
        record = _get(service, run_id)
        return _attachment(service.multi.export(record), f"socionet-multi-{record['id']}.json")


@router.post("/{run_id}/rerun")
def rerun(run_id: str, request: Request) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        prepared = service.multi.saved_prepared(_get(service, run_id))
        return run_detail(service.multi.create(prepared))


def _window(rows: list[Any], start: int, end: int, limit: int) -> list[Any]:
    return sorted((row for row in rows if start <= row.tick <= end), key=lambda row: row.tick)[:limit]


@router.get("/{run_id}/arms/{arm_id}")
def arm_detail(
    run_id: str, arm_id: str, request: Request,
    from_tick: int | None = Query(default=None, ge=0),
    to_tick: int | None = Query(default=None, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    event_limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    service = request.app.state.service
    with service.lock:
        record = _get(service, run_id)
        arm = next((arm for arm in record["arms"] if arm["id"] == arm_id), None)
        if arm is None:
            raise HTTPException(404, detail=f"unknown arm: {arm_id}")
        start = record["source_tick"] if from_tick is None else from_tick
        end = record["source_tick"] + record["config"]["ticks"] if to_tick is None else to_tick
        if start > end:
            raise _validation_error(ValueError("from_tick must not exceed to_tick"))
        samples = service.store.list_metric_history(scope_id=run_id, branch=arm_id,
                                                   from_tick=start, to_tick=end, limit=limit)
        state_payload = None
        events: list[dict[str, Any]] = []
        if arm["final_snapshot_id"] is not None:
            metadata = service.store.snapshot_metadata(arm["final_snapshot_id"], provenance=ARM_PROVENANCE)
            if metadata.get("experiment_run_id") != run_id or metadata.get("universe_id") != record["universe_id"]:
                raise HTTPException(409, detail="arm snapshot provenance is invalid")
            state = service.store.load(arm["final_snapshot_id"], provenance=ARM_PROVENANCE,
                                       universe_id=record["universe_id"])
            if arm.get("final_snapshot_hash") and json_hash(state.model_dump(mode="json")) != arm["final_snapshot_hash"]:
                raise HTTPException(409, detail="arm snapshot integrity check failed")
            events = [event.model_dump(mode="json") for event in _window(state.events, start, end, event_limit)]
            # Core state is the final branch frame. Embedded histories share the
            # window/caps so they cannot bypass the bounded top-level events.
            state_payload = state.model_dump(mode="json", exclude={
                "events", "traces", "content_exposures", "belief_updates", "causal_links", "community_events",
                "metric_history", "network_history", "community_history",
            })
            state_payload["events"] = events
            for field in ("traces", "content_exposures", "belief_updates", "causal_links", "community_events"):
                state_payload[field] = [item.model_dump(mode="json")
                                        for item in _window(getattr(state, field), start, end, event_limit)]
            for field in ("network_history", "community_history"):
                state_payload[field] = [item.model_dump(mode="json")
                                        for item in _window(getattr(state, field), start, end, limit)]
            state_payload["metric_history"] = samples
        return {"id": arm["id"], "name": arm["name"], "metrics": arm["metrics"], "samples": samples,
                "events": events, "state": state_payload, "metric_deltas": arm["metric_deltas"],
                "earliest_divergence": arm["earliest_divergence"],
                **({"error": arm["error"]} if "error" in arm else {})}
