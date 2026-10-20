"""Validated, deterministic interventions applied to a simulation snapshot."""

from __future__ import annotations

from app.models import (
    Intervention,
    InterventionKind,
    InterventionRecord,
    Relationship,
    ScheduledIntervention,
    SimulationState,
    SuppressedRelationship,
)
from app.causality import (
    append_causal_link,
    citizen_ref,
    content_ref,
    intervention_ref,
    relationship_ref,
)
from app.simulation import _latest_claim_content, append_content


class InterventionValidationError(ValueError):
    """Raised when an intervention cannot be applied to a simulation."""


def validate_intervention(state: SimulationState, intervention: Intervention) -> None:
    if intervention.agent_id and intervention.agent_id not in state.agents:
        raise InterventionValidationError(f"unknown agent_id: {intervention.agent_id}")
    if intervention.target_agent_id and intervention.target_agent_id not in state.agents:
        raise InterventionValidationError(f"unknown target_agent_id: {intervention.target_agent_id}")
    if (
        intervention.kind is InterventionKind.SUPPRESS_RELATIONSHIP
        and intervention.agent_id == intervention.target_agent_id
    ):
        raise InterventionValidationError("suppress_relationship requires two distinct agents")
    if intervention.kind is InterventionKind.SUPPRESS_POST:
        content = next((content for content in state.contents if content.id == intervention.content_id), None)
        if content is None:
            raise InterventionValidationError(f"unknown content_id: {intervention.content_id}")
        if content.kind.value != "post":
            raise InterventionValidationError("suppress_post requires a top-level post content_id")
    if intervention.kind is InterventionKind.COUNTEREVIDENCE and intervention.strength <= 0:
        raise InterventionValidationError("counterevidence strength must be greater than zero")
    if intervention.kind is InterventionKind.SEED_POST and intervention.strength <= 0:
        raise InterventionValidationError("seed_post strength must be greater than zero")
    if intervention.kind is InterventionKind.TRUST_SHIFT and not intervention.target_agent_id:
        raise InterventionValidationError("trust_shift requires target_agent_id")
    if intervention.kind is InterventionKind.FACT_CHECK and intervention.strength <= 0:
        raise InterventionValidationError("fact_check strength must be greater than zero")


def apply_intervention(state: SimulationState, intervention: Intervention) -> str:
    """Validate and apply now or schedule for a future simulation tick."""

    validate_intervention(state, intervention)
    intervention_id = f"intervention-{len(state.applied_interventions) + len(state.scheduled_interventions) + 1:05d}"
    if intervention.delay_ticks:
        state.scheduled_interventions.append(
            ScheduledIntervention(
                intervention_id=intervention_id,
                apply_at_tick=state.tick + intervention.delay_ticks,
                intervention=intervention,
            )
        )
        state.intervention_history.append(
            InterventionRecord(
                intervention_id=intervention_id,
                kind=intervention.kind,
                topic=intervention.topic,
                text=intervention.text,
                agent_id=intervention.agent_id,
                target_agent_id=intervention.target_agent_id,
                content_id=intervention.content_id,
                created_tick=state.tick,
                apply_at_tick=state.tick + intervention.delay_ticks,
                semantic_key=f"intervention:{intervention_id}:{intervention.kind.value}",
            )
        )
        return intervention_id
    apply_now(state, intervention, intervention_id=intervention_id)
    state.intervention_history.append(
        InterventionRecord(
            intervention_id=intervention_id,
            kind=intervention.kind,
            topic=intervention.topic,
            text=intervention.text,
            agent_id=intervention.agent_id,
            target_agent_id=intervention.target_agent_id,
            content_id=intervention.content_id,
            created_tick=state.tick,
            apply_at_tick=state.tick,
            applied_tick=state.tick,
            status="applied",
            semantic_key=f"intervention:{intervention_id}:{intervention.kind.value}",
        )
    )
    return intervention_id


def apply_now(state: SimulationState, intervention: Intervention, *, intervention_id: str) -> None:
    validate_intervention(state, intervention)
    if intervention.kind is InterventionKind.COUNTEREVIDENCE:
        author_id = intervention.agent_id or sorted(state.agents)[0]
        parent = _latest_claim_content(state, intervention.topic)
        append_content(
            state,
            author_id=author_id,
            kind="fact_check",
            topic=intervention.topic or "fictional evidence",
            text=intervention.text or f"A delayed fictional counterpoint addresses {intervention.topic}.",
            claim_truth=intervention.truth_label if intervention.truth_label is not None else True,
            parent_id=parent.id if parent else None,
            credibility=0.6 + intervention.strength * 0.4,
            is_counterevidence=True,
            claim_id=parent.claim_id if parent else None,
            provenance="intervention",
            intervention_id=intervention_id,
            provenance_event_ids=[intervention_ref(intervention_id)],
        )
    elif intervention.kind is InterventionKind.SEED_POST:
        author_id = intervention.agent_id or sorted(state.agents)[0]
        append_content(
            state,
            author_id=author_id,
            kind="post",
            topic=intervention.topic or "fictional evidence",
            text=intervention.text or "A fictional seeded post.",
            claim_truth=intervention.truth_label,
            credibility=0.4 + intervention.strength * 0.5,
            provenance="intervention",
            intervention_id=intervention_id,
            provenance_event_ids=[intervention_ref(intervention_id)],
        )
    elif intervention.kind is InterventionKind.TRUST_SHIFT:
        target_id = intervention.target_agent_id
        observers = [state.agents[intervention.agent_id]] if intervention.agent_id else list(state.agents.values())
        for observer in observers:
            if observer.id == target_id:
                continue
            relationship = observer.relationships.setdefault(
                target_id,
                Relationship(
                    target_id=target_id,
                    trust=observer.trust_baseline,
                ),
            )
            relationship.trust = max(0.0, min(1.0, relationship.trust + intervention.delta))
    elif intervention.kind is InterventionKind.FACT_CHECK:
        state.config.fact_check_probability = max(
            0.0,
            min(1.0, state.config.fact_check_probability + intervention.strength * 0.35),
        )
    elif intervention.kind is InterventionKind.SUPPRESS_CITIZEN:
        if intervention.agent_id not in state.suppressed_citizen_ids:
            state.suppressed_citizen_ids.append(intervention.agent_id)
            state.suppressed_citizen_ids.sort()
        append_causal_link(
            state,
            intervention_ref(intervention_id),
            citizen_ref(intervention.agent_id),
            "suppressed_citizen",
            tick=state.tick,
        )
    elif intervention.kind is InterventionKind.SUPPRESS_POST:
        content = next(content for content in state.contents if content.id == intervention.content_id)
        content.suppressed = True
        if content.id not in state.suppressed_post_ids:
            state.suppressed_post_ids.append(content.id)
            state.suppressed_post_ids.sort()
        append_causal_link(
            state,
            intervention_ref(intervention_id),
            content_ref(content.id),
            "suppressed_content",
            tick=state.tick,
        )
    elif intervention.kind is InterventionKind.SUPPRESS_RELATIONSHIP:
        source = state.agents[intervention.agent_id]
        source.relationships.pop(intervention.target_agent_id, None)
        if intervention.target_agent_id in source.following:
            source.following.remove(intervention.target_agent_id)
        if not any(
            item.source_agent_id == intervention.agent_id
            and item.target_agent_id == intervention.target_agent_id
            for item in state.suppressed_relationships
        ):
            state.suppressed_relationships.append(
                SuppressedRelationship(
                    source_agent_id=intervention.agent_id,
                    target_agent_id=intervention.target_agent_id,
                    intervention_id=intervention_id,
                    created_tick=state.tick,
                )
            )
        append_causal_link(
            state,
            intervention_ref(intervention_id),
            relationship_ref(intervention.agent_id, intervention.target_agent_id),
            "suppressed_relationship",
            tick=state.tick,
        )
    state.applied_interventions.append(intervention_id)
