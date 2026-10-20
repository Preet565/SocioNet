"""Phase 6: portable, bounded experiments. No HTTP or live-universe dependencies.

Schedules use source-relative ticks; zero is applied before the first step.
Community targets are resolved against the captured source, never a live world:
trust shifts expand to its observers; seed posts select its first sorted member.
Only shared claims may be the explicit target of multi-arm counterevidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.analytics import calculate_metrics, metric_deltas, record_metric_sample
from app.causality import compare_branches
from app.interventions import apply_intervention, validate_intervention
from app.models import ArchetypeBehavior, Intervention, InterventionKind, Metrics, SimulationConfig, SimulationState
from app.persistence import StateStore
from app.simulation import Simulation, append_content, create_initial_state


CONFIG_VERSION = 1
ENGINE_VERSION = "socionet-phase6-v1"
MAX_CONFIG_BYTES = 65_536
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
# A measured 100-citizen/20-tick pair took ~81s and ~29MB per arm;
# 100/10 took ~21s and ~14MB. Keep synchronous work below that measured bound.
MAX_AGENT_TICKS = 2000
MAX_COMPUTATIONAL_BUDGET = 5000
MAX_PENDING_INTERVENTIONS = 64
MAX_INTERVENTION_WORK = 2000
MAX_SOURCE_CONTENTS = 2000
SOURCE_PROVENANCE = "multi_experiment_source"
ARM_PROVENANCE = "multi_experiment_arm"
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Count = Annotated[int, Field(strict=True, ge=0, le=100)]


def canonical_json(value: Any) -> str:
    """The hash contract: UTF-8, sorted keys, compact JSON, no NaN/Infinity."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _bounded_json(value: Any, maximum: int, label: str) -> None:
    if len(canonical_json(value).encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds {maximum} UTF-8 JSON bytes")


def validate_execution_limits(source: SimulationState, *, ticks: int, arm_count: int,
                              additional: list[Intervention] | None = None) -> None:
    """Account for captured work as well as the visible request's new schedule."""
    if len(source.agents) * ticks * arm_count > MAX_AGENT_TICKS:
        raise ValueError(f"agent_ticks exceeds hard cap {MAX_AGENT_TICKS}")
    _bounded_json(source.model_dump(mode="json"), MAX_SOURCE_BYTES, "source snapshot")
    if len(source.contents) > MAX_SOURCE_CONTENTS:
        raise ValueError("source content count exceeds resource limit")
    if len(source.scheduled_interventions) + len(additional or []) > MAX_PENDING_INTERVENTIONS:
        raise ValueError("pending/expanded interventions exceed resource limit")
    pending = [item.intervention for item in source.scheduled_interventions]
    work = sum(len(source.agents) if item.kind is InterventionKind.TRUST_SHIFT and not item.agent_id else 1
               for item in pending) * arm_count
    work += sum(len(source.agents) if item.kind is InterventionKind.TRUST_SHIFT and not item.agent_id else 1
                for item in additional or [])
    if work > MAX_INTERVENTION_WORK:
        raise ValueError("captured/expanded intervention work exceeds resource limit")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MultiSimulationConfig(SimulationConfig):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    population: int = Field(default=100, strict=True, ge=1, le=100)
    archetypes: dict[Identifier, ArchetypeBehavior] = Field(
        default_factory=lambda: SimulationConfig().archetypes, max_length=16,
    )


class ExperimentSource(StrictModel):
    mode: Literal["generated", "universe"]
    universe_id: Literal["prime", "echo"] = "prime"


class SharedClaim(StrictModel):
    id: Identifier
    topic: ShortText
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=280)]
    truth_label: bool = Field(strict=True)
    credibility: float = Field(ge=0, le=1, strict=True)
    author_id: Identifier
    visibility: float = Field(default=1.0, ge=0, le=1, strict=True)
    reshare_probability: float = Field(default=1.0, ge=0, le=1, strict=True)


class ExperimentIntervention(Intervention):
    """Narrow JSON typing for versioned configs, keeping the legacy engine input."""

    agent_id: Identifier | None = None
    target_agent_id: Identifier | None = None
    content_id: Identifier | None = None
    claim_id: Identifier | None = None
    delay_ticks: int = Field(default=0, strict=True, ge=0, le=0)
    strength: float = Field(default=0.7, strict=True, ge=0, le=1)
    delta: float = Field(default=0.0, strict=True, ge=-1, le=1)
    truth_label: bool | None = Field(default=None, strict=True)


class ArmScheduleEntry(StrictModel):
    at_tick: int = Field(strict=True, ge=0, le=20)
    intervention: ExperimentIntervention
    target_community_id: Identifier | None = None
    claim_id: Identifier | None = None

    @model_validator(mode="after")
    def one_schedule_clock(self) -> "ArmScheduleEntry":
        if self.intervention.delay_ticks:
            raise ValueError("schedule uses at_tick; intervention.delay_ticks must be zero")
        if self.intervention.claim_id and self.claim_id and self.intervention.claim_id != self.claim_id:
            raise ValueError("schedule claim_id conflicts with intervention.claim_id")
        # Normalize the explicit reference to the contract's schedule wrapper.
        if self.intervention.claim_id:
            self.claim_id = self.intervention.claim_id
            self.intervention = self.intervention.model_copy(update={"claim_id": None})
        return self


class ExperimentArm(StrictModel):
    id: Identifier
    name: ShortText
    schedule: list[ArmScheduleEntry] = Field(default_factory=list, max_length=16)


class MultiExperimentConfig(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, json_schema_extra={
        "allOf": [{
            "if": {"properties": {"source": {"properties": {"mode": {"const": "generated"}}}}},
            "then": {"required": ["seed", "simulation"],
                     "properties": {"seed": {"type": "integer"}, "simulation": {"type": "object"}}},
            "else": {"properties": {"agent_composition": {"maxProperties": 0}}},
        }],
    })
    schema_version: Literal[1]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    hypothesis: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
    # Universe requests may omit seed/simulation. If supplied, they must equal
    # the captured values. Normalized portable configs always contain both.
    seed: int | None = Field(default=None, strict=True, ge=-(2**63), le=2**63 - 1)
    ticks: int = Field(strict=True, ge=1, le=20)
    source: ExperimentSource
    simulation: MultiSimulationConfig | None = None
    agent_composition: dict[Identifier, Count] = Field(default_factory=dict, max_length=16)
    claims: list[SharedClaim] = Field(default_factory=list, max_length=16)
    baseline_arm_id: Identifier
    arms: list[ExperimentArm] = Field(min_length=2, max_length=6)
    computational_budget: int = Field(strict=True, ge=2, le=MAX_COMPUTATIONAL_BUDGET)

    @model_validator(mode="before")
    @classmethod
    def bounded_input(cls, value: Any) -> Any:
        if isinstance(value, dict):
            _bounded_json(value, MAX_CONFIG_BYTES, "config")
            if type(value.get("schema_version")) is not int:
                raise ValueError("schema_version must be integer 1")
        return value

    @model_validator(mode="after")
    def coherent_config(self) -> "MultiExperimentConfig":
        ids = [arm.id for arm in self.arms]
        if len(set(ids)) != len(ids) or self.baseline_arm_id not in ids:
            raise ValueError("arm IDs must be unique and baseline_arm_id must reference an arm")
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("shared claim IDs must be unique")
        if any(entry.at_tick > self.ticks for arm in self.arms for entry in arm.schedule):
            raise ValueError("schedule at_tick must not exceed ticks")
        if self.source.mode == "generated":
            if self.seed is None or self.simulation is None:
                raise ValueError("generated source requires seed and simulation")
            if self.agent_composition:
                if sum(self.agent_composition.values()) != self.simulation.population:
                    raise ValueError("agent_composition counts must sum exactly to population")
                if set(self.agent_composition) - set(self.simulation.archetypes):
                    raise ValueError("agent_composition contains unknown archetype keys")
        elif self.agent_composition:
            raise ValueError("universe source does not accept agent_composition overrides; use {}")
        if self.simulation is not None:
            cost = self.simulation.population * self.ticks * len(self.arms)
            if cost > min(MAX_AGENT_TICKS, self.computational_budget):
                raise ValueError(f"agent_ticks {cost} exceeds computational_budget or hard cap {MAX_AGENT_TICKS}")
        return self


@dataclass
class PreparedExperiment:
    config: MultiExperimentConfig
    source: SimulationState
    plan: dict[str, Any]
    # Each entry can expand to several trust-shift observers. The immutable
    # source decides membership once, so community evolution cannot retarget it.
    schedules: dict[str, list[tuple[int, Intervention]]]


def _expanded_entry(
    source: SimulationState, entry: ArmScheduleEntry, shared_ids: set[str],
) -> list[Intervention]:
    intervention = entry.intervention
    kind = intervention.kind
    supported_fields = {
        InterventionKind.COUNTEREVIDENCE: {"topic", "text", "agent_id", "truth_label", "strength"},
        InterventionKind.SEED_POST: {"topic", "text", "agent_id", "truth_label", "strength"},
        InterventionKind.TRUST_SHIFT: {"agent_id", "target_agent_id", "delta"},
        InterventionKind.FACT_CHECK: {"topic", "agent_id", "strength"},
        InterventionKind.SUPPRESS_CITIZEN: {"agent_id"},
        InterventionKind.SUPPRESS_POST: {"content_id"},
        InterventionKind.SUPPRESS_RELATIONSHIP: {"agent_id", "target_agent_id"},
    }[kind]
    unused = set(intervention.model_dump(exclude_defaults=True)) - supported_fields - {"kind"}
    if unused:
        raise ValueError(f"{kind.value} does not use fields: {', '.join(sorted(unused))}")
    if kind is InterventionKind.COUNTEREVIDENCE:
        if entry.claim_id not in shared_ids:
            raise ValueError("counterevidence requires claim_id referencing an explicit shared claim")
        intervention = intervention.model_copy(update={"claim_id": entry.claim_id})
    elif entry.claim_id is not None:
        raise ValueError("schedule claim_id is supported only by counterevidence")
    validate_intervention(source, intervention)
    if kind is InterventionKind.SUPPRESS_RELATIONSHIP:
        if intervention.target_agent_id not in source.agents[intervention.agent_id].relationships:
            raise ValueError("suppress_relationship must reference an existing directional source relationship")
    expanded = [intervention]
    if entry.target_community_id is not None:
        community = source.communities.get(entry.target_community_id)
        if community is None or not community.member_ids:
            raise ValueError("target_community_id must reference a nonempty source community")
        if kind not in {InterventionKind.TRUST_SHIFT, InterventionKind.SEED_POST}:
            raise ValueError("community targets support only trust_shift and seed_post")
        members = sorted(community.member_ids)
        if intervention.agent_id and intervention.agent_id not in members:
            raise ValueError("agent_id must belong to target_community_id")
        if kind is InterventionKind.SEED_POST:
            expanded = [intervention.model_copy(update={"agent_id": intervention.agent_id or members[0]})]
        else:
            observers = [intervention.agent_id] if intervention.agent_id else members
            expanded = [intervention.model_copy(update={"agent_id": observer})
                        for observer in observers if observer != intervention.target_agent_id]
    if not expanded or (kind is InterventionKind.TRUST_SHIFT and intervention.agent_id == intervention.target_agent_id):
        raise ValueError("trust_shift requires at least one distinct observer and target")
    return expanded


def prepare_experiment(
    config: MultiExperimentConfig,
    universe_source: SimulationState | None = None,
    *, saved_source: SimulationState | None = None,
) -> PreparedExperiment:
    """Validate all references and capture a copied source; never mutate callers.

    saved_source already includes the shared claims (rerun/import). It must not
    pass through injection again. Preview and execution use this same function.
    """
    config = config.model_copy(deep=True)
    if saved_source is not None:
        source = saved_source.model_copy(deep=True)
    elif config.source.mode == "universe":
        if universe_source is None:
            raise ValueError("universe source snapshot is required")
        source = universe_source.model_copy(deep=True)
    else:
        source = create_initial_state(config.seed, config=config.simulation,
                                      agent_composition=config.agent_composition)
    population = len(source.agents)
    if population > 100 or population < 1 or source.config.population != population:
        raise ValueError("source actual population must match config and be within 1-100")
    _bounded_json(source.model_dump(mode="json"), MAX_SOURCE_BYTES, "source snapshot")
    validate_execution_limits(source, ticks=config.ticks, arm_count=len(config.arms))
    validate_source_integrity(source)
    actual_config = MultiSimulationConfig.model_validate(source.config.model_dump(mode="json"))
    if config.seed is not None and config.seed != source.seed:
        raise ValueError("source seed overrides are not allowed")
    if config.simulation is not None and config.simulation.model_dump(mode="json") != actual_config.model_dump(mode="json"):
        raise ValueError("source simulation/config/population overrides are not allowed")
    config.seed = source.seed
    config.simulation = actual_config
    # Revalidate now that omitted universe inputs have been captured, including budget.
    config = MultiExperimentConfig.model_validate(config.model_dump(mode="json"))
    if saved_source is None:
        for claim in config.claims:
            if claim.author_id not in source.agents:
                raise ValueError(f"unknown shared claim author_id: {claim.author_id}")
            if claim.id in source.claims:
                raise ValueError(f"shared claim ID already exists in source: {claim.id}")
            append_content(
                source, author_id=claim.author_id, kind="post", topic=claim.topic,
                text=claim.text, claim_truth=claim.truth_label, credibility=claim.credibility,
                claim_id=claim.id, provenance="multi_experiment_shared",
                visibility=claim.visibility, reshare_probability=claim.reshare_probability,
            )
        # Hydrate additive registries on the copied source once, before its
        # immutable hash is captured. Fork construction is then idempotent.
        Simulation(state=source)
        record_metric_sample(source)
    else:
        for claim in config.claims:
            origin = source.claims.get(claim.id)
            content = next((item for item in source.contents
                            if origin and item.id == origin.origin_content_id), None)
            if not content or any((
                content.author_id != claim.author_id, content.topic != claim.topic,
                content.text != claim.text, content.claim_truth != claim.truth_label,
                content.credibility != claim.credibility, content.visibility != claim.visibility,
                content.reshare_probability != claim.reshare_probability,
                content.provenance != "multi_experiment_shared", content.created_tick != source.tick,
            )):
                raise ValueError(f"saved source shared claim mismatch: {claim.id}")
        if config.source.mode == "generated" and config.agent_composition:
            if Counter(agent.archetype for agent in source.agents.values()) != +Counter(config.agent_composition):
                raise ValueError("saved source composition mismatch")
    _bounded_json(source.model_dump(mode="json"), MAX_SOURCE_BYTES, "source snapshot")
    schedules: dict[str, list[tuple[int, Intervention]]] = {}
    warnings: list[str] = []
    for arm in config.arms:
        schedules[arm.id] = []
        for entry in sorted(arm.schedule, key=lambda item: item.at_tick):
            expanded = _expanded_entry(source, entry, {claim.id for claim in config.claims})
            schedules[arm.id].extend((entry.at_tick, intervention) for intervention in expanded)
            if entry.target_community_id:
                warnings.append(
                    f"Arm {arm.id} tick {entry.at_tick}: community {entry.target_community_id} uses captured "
                    + (f"observers ({len(expanded)} trust shifts)." if entry.intervention.kind is InterventionKind.TRUST_SHIFT
                       else f"messenger {expanded[0].agent_id} (one seed post).")
                )
    validate_execution_limits(source, ticks=config.ticks, arm_count=len(config.arms),
                              additional=[item for schedule in schedules.values() for _, item in schedule])
    plan = {
        "source_tick": source.tick, "source_hash": json_hash(source.model_dump(mode="json")),
        "population": population, "ticks": config.ticks,
        "arm_count": len(config.arms), "agent_ticks": population * config.ticks * len(config.arms),
        "baseline_arm_id": config.baseline_arm_id,
        "arms": [{"id": arm.id, "name": arm.name, "intervention_count": len(arm.schedule)} for arm in config.arms],
        "warnings": warnings,
    }
    return PreparedExperiment(config, source, plan, schedules)


def execute_arm(prepared: PreparedExperiment, arm_id: str) -> SimulationState:
    """The engine owns scheduling/stepping; each arm starts from the same copy."""
    simulation = Simulation(state=prepared.source.model_copy(deep=True))
    for at_tick, intervention in prepared.schedules[arm_id]:
        apply_intervention(simulation.state, intervention.model_copy(update={"delay_ticks": at_tick}))
    # Include tick-zero interventions in that arm's first sample.
    record_metric_sample(simulation.state)
    return simulation.run(prepared.config.ticks)


class MultiExperimentRunner:
    """Durable serial execution. Caller serializes access with its service lock.

    Artifacts and metric tables commit before their references are published.
    A crash can leave orphan artifacts, never a completed dangling reference.
    Running records survive; recover_interrupted marks them failed on restart.
    Persistence/corruption exceptions propagate; only execution failures become
    returned failed records. There is no fallback-to-empty corruption behavior.
    """

    def __init__(self, store: StateStore) -> None:
        self.store = store

    def recover_interrupted(self) -> None:
        for record in self.store.list_multi_experiments(status="running", limit=None):
            record["status"] = "failed"
            record["error"] = "Execution interrupted; rerun uses the saved exact source."
            for arm in record["arms"]:
                if arm["status"] in {"pending", "running"}:
                    arm["status"] = "failed"
                    arm["error"] = record["error"]
            self.store.save_multi_experiment(record)

    def create(self, prepared: PreparedExperiment) -> dict[str, Any]:
        # PreparedExperiment is a public, mutable Python object. Revalidate at
        # the execution boundary instead of trusting a stale caller-built plan.
        prepared = prepare_experiment(prepared.config, saved_source=prepared.source)
        config = prepared.config.model_dump(mode="json")
        source_hash = json_hash(prepared.source.model_dump(mode="json"))
        run_id = uuid4().hex
        record: dict[str, Any] = {
            "id": run_id, "status": "running", "name": prepared.config.name,
            "created_at": datetime.now(timezone.utc).isoformat(), "config": config,
            "config_hash": json_hash(config), "source_hash": source_hash,
            "source_snapshot_id": None, "source_tick": prepared.source.tick,
            "baseline_arm_id": prepared.config.baseline_arm_id,
            "universe_id": prepared.config.source.universe_id,
            "engine_version": ENGINE_VERSION,
            "manifest": {"engine_version": ENGINE_VERSION, "config_version": CONFIG_VERSION,
                          "storage_schema_version": 5, "hash_algorithm": "sha256",
                          "canonical_json": "utf8-sort_keys-compact-ensure_ascii_false",
                          "execution_mode": "serial_synchronous", "cancellation_supported": False},
            "arms": [{"id": arm.id, "name": arm.name, "status": "pending",
                       "initial_snapshot_hash": source_hash, "final_snapshot_id": None,
                       "final_snapshot_hash": None,
                       "metrics": None, "metric_deltas": None, "earliest_divergence": None}
                     for arm in prepared.config.arms],
        }
        with self.store.transaction():
            record["source_snapshot_id"] = self.store.save(
                prepared.source, provenance=SOURCE_PROVENANCE,
                universe_id=prepared.config.source.universe_id, experiment_run_id=run_id,
            )
            self.store.save_multi_experiment(record)
        try:
            return self._execute(record, prepared)
        except Exception:
            # A persistence failure leaves the durable record running. Restart
            # recovery will mark it failed after all references are reconciled.
            raise

    def _execute(self, record: dict[str, Any], prepared: PreparedExperiment) -> dict[str, Any]:
        run_id = record["id"]
        states: dict[str, SimulationState] = {}
        for arm in record["arms"]:
            arm["status"] = "running"
            self.store.save_multi_experiment(record)
            try:
                state = execute_arm(prepared, arm["id"])
                metrics = calculate_metrics(state).model_dump(mode="json")
            except Exception as error:
                arm.update(status="failed", error=safe_execution_error(error, prefix="Arm execution failed."))
                record["error"] = "One or more arms failed; inspect arm errors."
            else:
                with self.store.transaction():
                    snapshot_id = self.store.save(state, provenance=ARM_PROVENANCE,
                                                  universe_id=record["universe_id"], experiment_run_id=run_id)
                    self.store.save_metric_history(
                        [sample for sample in state.metric_history if sample.tick >= prepared.source.tick],
                        scope_id=run_id, branch=arm["id"], universe_id=record["universe_id"], run_id=run_id,
                    )
                    arm.update(status="completed", final_snapshot_id=snapshot_id,
                               final_snapshot_hash=json_hash(state.model_dump(mode="json")), metrics=metrics)
                    self.store.save_multi_experiment(record)
                states[arm["id"]] = state
            self.store.save_multi_experiment(record)
        baseline = states.get(record["baseline_arm_id"])
        if baseline is not None:
            baseline_metrics = Metrics.model_validate(next(
                arm["metrics"] for arm in record["arms"] if arm["id"] == record["baseline_arm_id"]
            ))
            for arm in record["arms"]:
                if arm["id"] in states:
                    try:
                        arm["metric_deltas"] = metric_deltas(baseline_metrics, Metrics.model_validate(arm["metrics"]))
                        if arm["id"] != record["baseline_arm_id"]:
                            arm["earliest_divergence"] = compare_branches(baseline, states[arm["id"]])["earliest_divergence"]
                    except Exception as error:
                        arm.update(status="failed", error=f"Comparison failed: {safe_execution_error(error)}")
                        record["error"] = "One or more arm comparisons failed; inspect arm errors."
        record["status"] = "completed" if all(arm["status"] == "completed" for arm in record["arms"]) else "failed"
        self.store.save_multi_experiment(record)
        return record

    def saved_prepared(self, record: dict[str, Any]) -> PreparedExperiment:
        if record["engine_version"] != ENGINE_VERSION:
            raise RuntimeError("stored experiment engine version is incompatible")
        config = record["config"]
        if json_hash(config) != record["config_hash"]:
            raise RuntimeError("stored multi-experiment config hash mismatch")
        source = self.store.load(record["source_snapshot_id"], provenance=SOURCE_PROVENANCE,
                                 universe_id=record["universe_id"])
        if json_hash(source.model_dump(mode="json")) != record["source_hash"]:
            raise RuntimeError("stored multi-experiment source hash mismatch")
        if record["source_tick"] != source.tick:
            raise RuntimeError("stored multi-experiment source tick mismatch")
        metadata = self.store.snapshot_metadata(record["source_snapshot_id"], provenance=SOURCE_PROVENANCE)
        if metadata.get("experiment_run_id") != record["id"] or config["source"]["universe_id"] != record["universe_id"]:
            raise RuntimeError("stored multi-experiment source ownership mismatch")
        prepared = prepare_experiment(MultiExperimentConfig.model_validate(config), saved_source=source)
        if json_hash(Simulation(state=source.model_copy(deep=True)).state.model_dump(mode="json")) != record["source_hash"]:
            raise RuntimeError("stored source changes during engine hydration")
        if prepared.config.source.mode == "generated":
            if json_hash(prepare_experiment(prepared.config).source.model_dump(mode="json")) != record["source_hash"]:
                raise RuntimeError("stored generated source does not match configuration")
        return prepared

    def export(self, record: dict[str, Any]) -> dict[str, Any]:
        prepared = self.saved_prepared(record)
        return {
            "bundle_version": 1, "engine_version": ENGINE_VERSION, "schema_version": CONFIG_VERSION,
            "hash_algorithm": "sha256", "canonical_json": "utf8-sort_keys-compact-ensure_ascii_false",
            "config": record["config"], "config_hash": record["config_hash"],
            "source_snapshot": prepared.source.model_dump(mode="json"), "source_hash": record["source_hash"],
            "results": record,
        }


Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class BundleArmResult(StrictModel):
    id: Identifier
    name: ShortText
    status: Literal["pending", "running", "completed", "failed"]
    initial_snapshot_hash: Hash
    final_snapshot_id: int | None = Field(ge=1, strict=True)
    final_snapshot_hash: Hash | None = None
    metrics: Metrics | None
    metric_deltas: dict[str, float] | None
    earliest_divergence: dict[str, Any] | None
    error: str | None = None

    @model_validator(mode="after")
    def complete_references(self) -> "BundleArmResult":
        if self.status == "completed" and (self.final_snapshot_id is None or self.metrics is None):
            raise ValueError("completed arm must have metrics and a final snapshot reference")
        return self


class BundleResults(StrictModel):
    id: Identifier
    status: Literal["running", "completed", "failed"]
    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    created_at: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    config: dict[str, Any]
    config_hash: Hash
    source_hash: Hash
    source_snapshot_id: int = Field(strict=True, ge=1)
    source_tick: int = Field(strict=True, ge=0)
    baseline_arm_id: Identifier
    universe_id: Literal["prime", "echo"]
    engine_version: Literal["socionet-phase6-v1"]
    arms: list[BundleArmResult] = Field(min_length=2, max_length=6)
    manifest: dict[str, Any] | None = None
    error: str | None = None


class ExperimentBundle(StrictModel):
    bundle_version: Literal[1]
    engine_version: Literal["socionet-phase6-v1"]
    schema_version: Literal[1]
    hash_algorithm: Literal["sha256"]
    canonical_json: Literal["utf8-sort_keys-compact-ensure_ascii_false"]
    config: dict[str, Any]
    config_hash: Hash
    source_snapshot: dict[str, Any]
    source_hash: Hash
    results: BundleResults

    @model_validator(mode="before")
    @classmethod
    def bounded_bundle(cls, value: Any) -> Any:
        _bounded_json(value, MAX_BUNDLE_BYTES, "bundle")
        if isinstance(value, dict) and any(type(value.get(key)) is not int for key in ("bundle_version", "schema_version")):
            raise ValueError("bundle_version and schema_version must be integers")
        return value

    def prepare(self) -> PreparedExperiment:
        if json_hash(self.config) != self.config_hash or json_hash(self.source_snapshot) != self.source_hash:
            raise ValueError("bundle config/source hash mismatch")
        config = MultiExperimentConfig.model_validate(self.config)
        if config.model_dump(mode="json") != self.config:
            raise ValueError("bundle config must be normalized")
        _bounded_json(self.source_snapshot, MAX_SOURCE_BYTES, "source snapshot")
        # Reject ignored/extra state fields and default hydration that would
        # change the canonical payload after import.
        source = SimulationState.model_validate(self.source_snapshot)
        if source.model_dump(mode="json") != self.source_snapshot:
            raise ValueError("bundle source must be a complete current-version snapshot")
        validate_source_integrity(source)
        if (self.results.config != self.config or self.results.config_hash != self.config_hash
            or self.results.source_hash != self.source_hash or self.results.source_tick != source.tick
            or self.results.name != config.name or self.results.universe_id != config.source.universe_id
            or self.results.baseline_arm_id != config.baseline_arm_id
            or [(arm.id, arm.name) for arm in self.results.arms] != [(arm.id, arm.name) for arm in config.arms]
            or any(arm.initial_snapshot_hash != self.source_hash for arm in self.results.arms)
            or (self.results.status == "completed" and any(arm.status != "completed" for arm in self.results.arms))):
            raise ValueError("bundle result metadata does not match config/source")
        if self.results.manifest is not None and self.results.manifest.get("engine_version") != self.engine_version:
            raise ValueError("bundle manifest engine version does not match bundle")
        prepared = prepare_experiment(config, saved_source=source)
        if json_hash(prepared.source.model_dump(mode="json")) != self.source_hash:
            raise ValueError("bundle source changed during normalization")
        hydrated = Simulation(state=source.model_copy(deep=True)).state
        if json_hash(hydrated.model_dump(mode="json")) != self.source_hash:
            raise ValueError("bundle source is missing current-version engine registries")
        if config.source.mode == "generated":
            generated = prepare_experiment(config)
            if json_hash(generated.source.model_dump(mode="json")) != self.source_hash:
                raise ValueError("generated bundle source does not match seed/simulation/composition/claims")
        return prepared


def safe_execution_error(error: Exception, *, prefix: str = "Execution failed") -> str:
    """Expose bounded operator context without returning exception internals."""
    text = str(error).strip()
    lowered = text.lower()
    sensitive = ("private", "secret", "token", "password", "credential", "path", "storage", "database")
    if text and len(text) <= 160 and not any(word in lowered for word in sensitive) and all(ord(char) >= 32 for char in text):
        return f"{type(error).__name__}: {text}"
    return prefix


def validate_source_integrity(source: SimulationState) -> None:
    """Reject structurally valid but dangling imported source references."""
    agents = set(source.agents)
    contents = {content.id: content for content in source.contents}
    event_ids = {event.event_id for event in source.events}
    trace_ids = {trace.trace_id for trace in source.traces}
    exposure_ids = {event.event_id for event in source.content_exposures}
    update_ids = {event.update_id for event in source.belief_updates}
    known_nodes = ({f"content:{item}" for item in contents} | {f"claim:{item}" for item in source.claims}
                   | {f"action:{item}" for item in event_ids} | {f"decision:{item}" for item in trace_ids}
                   | {f"exposure:{item}" for item in exposure_ids} | {f"belief_update:{item}" for item in update_ids}
                   | {f"intervention:{item.intervention_id}" for item in source.intervention_history}
                   | {f"intervention:{item.intervention_id}" for item in source.scheduled_interventions}
                   | {f"citizen:{item}" for item in agents}
                   | {f"relationship:{agent.id}->{target}" for agent in source.agents.values() for target in agent.relationships}
                   | {f"relationship:{item.source_agent_id}->{item.target_agent_id}" for item in source.suppressed_relationships})
    if len(contents) != len(source.contents):
        raise ValueError("source content IDs must be unique")
    for key, agent in source.agents.items():
        if key != agent.id or set(agent.relationships) - agents or set(agent.following) - agents:
            raise ValueError("source citizen/relationship references are invalid")
        if agent.archetype not in source.config.archetypes or set(agent.community_ids) - set(source.communities):
            raise ValueError("source archetype/community references are invalid")
        if any(key not in source.communities[community_id].member_ids for community_id in agent.community_ids):
            raise ValueError("source citizen memberships must be reciprocal")
        for target, relationship in agent.relationships.items():
            if target != relationship.target_id or target == key:
                raise ValueError("source relationship target is invalid")
        for memory in agent.memories:
            if memory.agent_id != key or (memory.content_id and memory.content_id not in contents):
                raise ValueError("source memory references are invalid")
    for key, community in source.communities.items():
        if (key != community.id or set(community.member_ids) - agents
            or len(set(community.member_ids)) != len(community.member_ids)):
            raise ValueError("source community members are invalid")
        if any(key not in source.agents[member].community_ids for member in community.member_ids):
            raise ValueError("source community memberships must be reciprocal")
    seen_contents: set[str] = set()
    for index, content in enumerate(source.contents, start=1):
        if (content.id != f"content-{index:06d}" or content.author_id not in agents
            or (content.parent_id and content.parent_id not in seen_contents)):
            raise ValueError("source content references are invalid")
        seen_contents.add(content.id)
        if content.claim_id and content.claim_id not in source.claims:
            raise ValueError("source content claim reference is invalid")
        if content.created_tick > source.tick:
            raise ValueError("source content is from a future tick")
    for key, claim in source.claims.items():
        if key != claim.claim_id or claim.origin_content_id not in contents or claim.origin_author_id not in agents:
            raise ValueError("source claim origin is invalid")
        if contents[claim.origin_content_id].claim_id != key:
            raise ValueError("source claim origin content mismatch")
    if set(source.suppressed_citizen_ids) - agents or set(source.suppressed_post_ids) - set(contents):
        raise ValueError("source suppressed targets are invalid")
    for relationship in source.suppressed_relationships:
        if relationship.source_agent_id not in agents or relationship.target_agent_id not in agents:
            raise ValueError("source suppressed relationship targets are invalid")
    for agent_id, policies in source.fact_check_policies.items():
        if agent_id not in agents or any(not 0 <= probability <= 1 for probability in policies.values()):
            raise ValueError("source fact-check policies are invalid")
    for event in source.events:
        if event.actor_id not in agents or event.tick > source.tick:
            raise ValueError("source action event references are invalid")
        if (event.content_id and event.content_id not in contents) or (event.claim_id and event.claim_id not in source.claims):
            raise ValueError("source action event content/claim references are invalid")
        if any(parent not in known_nodes for parent in event.parent_event_ids + event.provenance_event_ids):
            raise ValueError("source action ancestry references are invalid")
    for trace in source.traces:
        if trace.agent_id not in agents or (trace.content_id and trace.content_id not in contents):
            raise ValueError("source decision trace references are invalid")
        if any(parent not in known_nodes for parent in trace.parent_event_ids + trace.provenance_event_ids):
            raise ValueError("source decision ancestry references are invalid")
    for exposure in source.content_exposures:
        if (exposure.actor_id not in agents or exposure.content_id not in contents
                or (exposure.claim_id and exposure.claim_id not in source.claims)):
            raise ValueError("source exposure references are invalid")
    for update in source.belief_updates:
        if (update.agent_id not in agents or (update.content_id and update.content_id not in contents)
                or (update.claim_id and update.claim_id not in source.claims)):
            raise ValueError("source belief update references are invalid")
    for link in source.causal_links:
        if link.parent_event_id not in known_nodes or link.child_event_id not in known_nodes:
            raise ValueError("source causal link references are invalid")
    if len(source.scheduled_interventions) > MAX_PENDING_INTERVENTIONS:
        raise ValueError("source scheduled intervention count exceeds resource limit")
    for scheduled in source.scheduled_interventions:
        validate_intervention(source, scheduled.intervention)
        if scheduled.apply_at_tick < source.tick:
            raise ValueError("source scheduled intervention is in the past")
    for record in source.intervention_history:
        if (record.agent_id and record.agent_id not in agents
                or record.target_agent_id and record.target_agent_id not in agents
                or record.content_id and record.content_id not in contents
                or record.claim_id and record.claim_id not in source.claims):
            raise ValueError("source intervention history references are invalid")


def run_detail(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"universe_id", "engine_version"}}
