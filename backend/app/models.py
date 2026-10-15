"""Pydantic models shared by the simulation, persistence, and API layers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContentKind(str, Enum):
    POST = "post"
    REPLY = "reply"
    FACT_CHECK = "fact_check"


class ActionKind(str, Enum):
    POST = "post"
    REPLY = "reply"
    FACT_CHECK = "fact_check"
    IGNORE = "ignore"
    FOLLOW = "follow"
    UNFOLLOW = "unfollow"
    JOIN_COMMUNITY = "join_community"
    LEAVE_COMMUNITY = "leave_community"


class LifecyclePhase(str, Enum):
    OBSERVE = "OBSERVE"
    RETRIEVE = "RETRIEVE"
    FORM_INTENT = "FORM INTENT"
    EVALUATE = "EVALUATE"
    ACT = "ACT"
    UPDATE = "UPDATE"
    RECORD = "RECORD"


class InterventionKind(str, Enum):
    COUNTEREVIDENCE = "counterevidence"
    SEED_POST = "seed_post"
    TRUST_SHIFT = "trust_shift"
    FACT_CHECK = "fact_check"


class Agent(BaseModel):
    """A fictional citizen and their mutable simulation state."""

    model_config = ConfigDict(validate_assignment=True)

    id: str
    name: str
    age: int = Field(ge=18, le=100)
    interests: list[str] = Field(min_length=1)
    ideology: float = Field(ge=-1, le=1)
    skepticism: float = Field(ge=0, le=1)
    curiosity: float = Field(ge=0, le=1)
    trust_baseline: float = Field(ge=0, le=1)
    beliefs: dict[str, "Belief"] = Field(default_factory=dict)
    memories: list["MemoryRecord"] = Field(default_factory=list)
    relationships: dict[str, "Relationship"] = Field(default_factory=dict)
    following: list[str] = Field(default_factory=list)
    community_ids: list[str] = Field(default_factory=list)
    action_counts: dict[str, int] = Field(default_factory=dict)
    last_action: ActionKind | None = None


class Belief(BaseModel):
    topic: str
    stance: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_for: int = Field(default=0, ge=0)
    evidence_against: int = Field(default=0, ge=0)


class MemoryRecord(BaseModel):
    memory_id: str
    agent_id: str
    content_id: str | None = None
    kind: str
    summary: str
    topic: str | None = None
    credibility: float = Field(ge=0, le=1)
    valence: float = Field(ge=-1, le=1)
    created_tick: int = Field(ge=0)
    recall_strength: float = Field(default=1.0, ge=0, le=1)


class Relationship(BaseModel):
    target_id: str
    affinity: float = Field(default=0, ge=-1, le=1)
    trust: float = Field(default=0.5, ge=0, le=1)
    interactions: int = Field(default=0, ge=0)
    follows: bool = False


class Community(BaseModel):
    id: str
    name: str
    topic: str
    description: str
    member_ids: list[str] = Field(default_factory=list)


class Content(BaseModel):
    id: str
    author_id: str
    kind: ContentKind
    topic: str
    text: str
    claim_truth: bool | None = None
    parent_id: str | None = None
    created_tick: int = Field(ge=0)
    credibility: float = Field(default=0.5, ge=0, le=1)
    likes: int = Field(default=0, ge=0)
    fact_checked: bool = False
    is_counterevidence: bool = False


class ActionEvent(BaseModel):
    event_id: str
    tick: int = Field(ge=0)
    actor_id: str
    action: ActionKind
    target_id: str | None = None
    content_id: str | None = None
    topic: str | None = None
    details: str = ""


class DecisionTrace(BaseModel):
    trace_id: str
    tick: int = Field(ge=0)
    agent_id: str
    phase: LifecyclePhase
    action: ActionKind | None = None
    content_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SimulationConfig(BaseModel):
    observation_window: int = Field(default=18, ge=1, le=100)
    memory_limit: int = Field(default=40, ge=5, le=200)
    fact_check_probability: float = Field(default=0.18, ge=0, le=1)
    post_probability: float = Field(default=0.62, ge=0, le=1)
    reply_probability: float = Field(default=0.35, ge=0, le=1)


class ScheduledIntervention(BaseModel):
    intervention_id: str
    apply_at_tick: int = Field(ge=0)
    intervention: "Intervention"


class Intervention(BaseModel):
    """Validated input for deterministic changes to a running simulation."""

    model_config = ConfigDict(extra="forbid")

    kind: InterventionKind
    topic: str | None = None
    agent_id: str | None = None
    target_agent_id: str | None = None
    delay_ticks: int = Field(default=0, ge=0, le=1000)
    strength: float = Field(default=0.7, ge=0, le=1)
    delta: float = Field(default=0, ge=-1, le=1)
    truth_label: bool | None = None
    text: str | None = None

    @field_validator("topic")
    @classmethod
    def topic_is_sanitized(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value.strip()) > 80):
            raise ValueError("topic must be 1-80 non-whitespace characters")
        return value.strip() if value else value

    @field_validator("text")
    @classmethod
    def text_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value.strip()) > 280):
            raise ValueError("text must be 1-280 non-whitespace characters")
        return value.strip() if value else value

    @model_validator(mode="after")
    def validate_shape(self) -> "Intervention":
        if self.kind in {InterventionKind.COUNTEREVIDENCE, InterventionKind.SEED_POST} and not self.topic:
            raise ValueError(f"{self.kind.value} intervention requires topic")
        if self.kind is InterventionKind.TRUST_SHIFT and not self.target_agent_id:
            raise ValueError("trust_shift intervention requires target_agent_id")
        if self.kind is InterventionKind.FACT_CHECK and not self.topic and not self.agent_id:
            raise ValueError("fact_check intervention requires topic or agent_id")
        if self.kind is InterventionKind.SEED_POST and not self.text:
            raise ValueError("seed_post intervention requires text")
        if self.kind is InterventionKind.TRUST_SHIFT and self.delta == 0:
            raise ValueError("trust_shift intervention requires a non-zero delta")
        return self


class SimulationState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    seed: int
    tick: int = Field(default=0, ge=0)
    agents: dict[str, Agent]
    communities: dict[str, Community]
    contents: list[Content] = Field(default_factory=list)
    traces: list[DecisionTrace] = Field(default_factory=list)
    events: list[ActionEvent] = Field(default_factory=list)
    scheduled_interventions: list[ScheduledIntervention] = Field(default_factory=list)
    applied_interventions: list[str] = Field(default_factory=list)
    config: SimulationConfig = Field(default_factory=SimulationConfig)


class TickRequest(BaseModel):
    ticks: int = Field(default=1, ge=1, le=100)


class ExperimentRequest(BaseModel):
    ticks: int = Field(default=3, ge=1, le=50)
    control_interventions: list[Intervention] = Field(default_factory=list)
    treatment_interventions: list[Intervention] = Field(default_factory=list)


class Metrics(BaseModel):
    tick: int
    population: int
    total_contents: int
    posts: int
    replies: int
    fact_checks: int
    ignored_decisions: int
    follows: int
    unfollows: int
    community_joins: int
    community_leaves: int
    active_agents: int
    average_belief_confidence: float
    average_relationship_trust: float
    polarization: float
    belief_accuracy: float
    engagement_rate: float
    topic_metrics: dict[str, dict[str, float | int]] = Field(default_factory=dict)


class ExperimentResult(BaseModel):
    seed: int
    ticks: int
    control: SimulationState
    treatment: SimulationState
    control_metrics: Metrics
    treatment_metrics: Metrics
    metric_deltas: dict[str, float]


Agent.model_rebuild()
ScheduledIntervention.model_rebuild()
