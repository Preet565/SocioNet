"""Recorded causal ancestry, semantic branch comparison, and impact projections.

The simulator records dependencies as modelled rules.  These records make the
execution inspectable without turning a deterministic branch difference into a
statistical claim about a real population.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from app.analytics import calculate_metrics
from app.models import (
    ActionEvent,
    BeliefUpdateEvent,
    ClaimMetrics,
    Content,
    ContentExposureEvent,
    CausalLink,
    DecisionTrace,
    SimulationState,
)


MODELED_DEPENDENCY = "modeled dependency"
NOT_STATISTICALLY_ESTABLISHED = "not statistically established effect"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _ref(kind: str, identifier: str) -> str:
    return f"{kind}:{identifier}"


def content_ref(content_id: str) -> str:
    return _ref("content", content_id)


def exposure_ref(event_id: str) -> str:
    return _ref("exposure", event_id)


def decision_ref(trace_id: str) -> str:
    return _ref("decision", trace_id)


def belief_update_ref(update_id: str) -> str:
    return _ref("belief_update", update_id)


def action_ref(event_id: str) -> str:
    return _ref("action", event_id)


def intervention_ref(intervention_id: str) -> str:
    return _ref("intervention", intervention_id)


def citizen_ref(agent_id: str) -> str:
    return _ref("citizen", agent_id)


def relationship_ref(source_agent_id: str, target_agent_id: str) -> str:
    return _ref("relationship", f"{source_agent_id}->{target_agent_id}")


def claim_ref(claim_id: str) -> str:
    return _ref("claim", claim_id)


def _parent_semantic_key(content: Content | None, content_by_id: dict[str, Content]) -> str:
    if content is None:
        return "root"
    if content.semantic_key:
        return content.semantic_key
    parent = content_by_id.get(content.parent_id) if content.parent_id else None
    return _content_semantic_key(content, content_by_id, parent_override=parent)


def _content_semantic_key(
    content: Content,
    content_by_id: dict[str, Content],
    *,
    parent_override: Content | None = None,
) -> str:
    """Build a content identity from domain meaning, never its generated ID."""

    parent = parent_override or (content_by_id.get(content.parent_id) if content.parent_id else None)
    raw = "|".join(
        (
            str(content.created_tick),
            content.author_id,
            content.kind.value,
            content.topic,
            content.text,
            str(content.claim_truth),
            f"{content.credibility:.6f}",
            _parent_semantic_key(parent, content_by_id),
            str(content.is_counterevidence),
            str(content.is_reshare),
            content.provenance,
        )
    )
    return f"content:{_digest(raw)}"


def semantic_content_key(content: Content, content_by_id: dict[str, Content]) -> str:
    return content.semantic_key or _content_semantic_key(content, content_by_id)


def semantic_claim_key(claim_id: str | None, state: SimulationState) -> str | None:
    if not claim_id:
        return None
    claim = state.claims.get(claim_id)
    content_by_id = {content.id: content for content in state.contents}
    if claim is not None:
        origin = content_by_id.get(claim.origin_content_id)
        raw = "|".join((claim.topic, claim.text, str(claim.truth_label), semantic_content_key(origin, content_by_id) if origin else "root"))
        return f"claim:{_digest(raw)}"

    # A pre-Phase-2 snapshot can carry a claim ID on content without a claim
    # registry. Reconstruct the semantic identity from its earliest content so
    # branch comparison never falls back to an incidental generated ID.
    candidates = sorted(
        (content for content in state.contents if content.claim_id == claim_id),
        key=lambda content: (content.created_tick, content.id),
    )
    origin = candidates[0] if candidates else None
    if origin is None and claim_id.startswith("claim-"):
        origin = content_by_id.get(claim_id.removeprefix("claim-"))
    if origin is None:
        return f"claim:unresolved:{_digest(claim_id)}"
    raw = "|".join(
        (
            origin.topic,
            origin.text,
            str(origin.claim_truth),
            semantic_content_key(origin, content_by_id),
        )
    )
    return f"claim:{_digest(raw)}"


def semantic_event_signature(event: ActionEvent, state: SimulationState) -> dict[str, Any]:
    """Return a branch-comparable event projection with generated IDs removed."""

    content_by_id = {content.id: content for content in state.contents}
    produced = content_by_id.get(event.content_id) if event.content_id else None
    observed = content_by_id.get(event.observed_content_id) if event.observed_content_id else None

    def content_signature(content: Content | None) -> dict[str, Any] | None:
        if content is None:
            return None
        return {
            "author_id": content.author_id,
            "kind": content.kind.value,
            "topic": content.topic,
            "text": content.text,
            "claim_truth": content.claim_truth,
            "created_tick": content.created_tick,
            "credibility": round(content.credibility, 6),
            "parent": semantic_content_key(content_by_id[content.parent_id], content_by_id)
            if content.parent_id in content_by_id
            else "root",
            "counterevidence": content.is_counterevidence,
            "reshare": content.is_reshare,
            "provenance": content.provenance,
            "suppressed": content.suppressed,
        }

    target_id: str | None = event.target_id
    if target_id in content_by_id:
        target_id = semantic_content_key(content_by_id[target_id], content_by_id)
    elif target_id in state.agents:
        target_id = citizen_ref(target_id)
    elif target_id in state.communities:
        target_id = f"community:{target_id}"

    return {
        "tick": event.tick,
        "actor_id": event.actor_id,
        "action": event.action.value,
        "target_id": target_id,
        "topic": event.topic,
        "details": event.details,
        "observed": content_signature(observed),
        "produced": content_signature(produced),
        "claim": semantic_claim_key(event.claim_id, state),
    }


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _positioned_events(state: SimulationState) -> list[tuple[tuple[int, str, int], ActionEvent]]:
    """Position action events by semantic execution coordinates.

    There is one lifecycle action per citizen and tick in the current model.
    Grouping first by tick and actor, then sorting duplicate actions by their
    semantic projection, makes comparison independent of list order and
    generated event IDs while preserving the earliest domain position.
    """

    grouped: defaultdict[tuple[int, str], list[ActionEvent]] = defaultdict(list)
    for event in state.events:
        grouped[(event.tick, event.actor_id)].append(event)
    positioned: list[tuple[tuple[int, str, int], ActionEvent]] = []
    for key in sorted(grouped):
        events = sorted(
            grouped[key],
            key=lambda event: _canonical(semantic_event_signature(event, state)),
        )
        for sequence, event in enumerate(events):
            positioned.append(((key[0], key[1], sequence), event))
    return positioned


def _semantic_intervention_signature(record: Any, state: SimulationState) -> dict[str, Any]:
    """Project an intervention record without its generated intervention ID."""

    content_by_id = {content.id: content for content in state.contents}
    content_id = record.content_id
    if content_id in content_by_id:
        content_id = semantic_content_key(content_by_id[content_id], content_by_id)
    return {
        "kind": record.kind.value,
        "topic": record.topic,
        "text": record.text,
        "created_tick": record.created_tick,
        "apply_at_tick": record.apply_at_tick,
        "applied_tick": record.applied_tick,
        "status": record.status,
        "source": record.source,
        "agent_id": record.agent_id,
        "target_agent_id": record.target_agent_id,
        "content_id": content_id,
    }


def compare_branches(control: SimulationState, treatment: SimulationState) -> dict[str, Any]:
    """Compare recorded event meaning, ignoring generated database/list IDs."""

    left = _positioned_events(control)
    right = _positioned_events(treatment)
    left_by_position = {position: event for position, event in left}
    right_by_position = {position: event for position, event in right}
    positions = sorted(set(left_by_position) | set(right_by_position))
    earliest: dict[str, Any] | None = None
    matched = 0
    divergent = 0
    for index, position in enumerate(positions):
        left_event = left_by_position.get(position)
        right_event = right_by_position.get(position)
        left_signature = semantic_event_signature(left_event, control) if left_event else None
        right_signature = semantic_event_signature(right_event, treatment) if right_event else None
        if left_signature == right_signature:
            matched += 1
            continue
        divergent += 1
        if earliest is None:
            reason = "semantic event fields differ" if left_event and right_event else (
                "semantic event missing from treatment" if left_event else "semantic event missing from control"
            )
            earliest = {
                "event_index": index,
                "semantic_position": {
                    "tick": position[0],
                    "control_actor_id": left_event.actor_id if left_event else None,
                    "treatment_actor_id": right_event.actor_id if right_event else None,
                    "sequence": position[2],
                    "control_sequence": position[2] if left_event else None,
                    "treatment_sequence": position[2] if right_event else None,
                },
                "reason": reason,
                "control": left_signature,
                "treatment": right_signature,
            }

    # Some interventions change a stateful target without causing an action
    # event in the bounded run (for example suppressing an unobserved post).
    # Report that semantic divergence only after the action stream has been
    # shown to be equivalent, keeping the historical tick-1 suppression result
    # for a citizen whose first action is removed.
    if earliest is None:
        left_interventions = sorted(
            (_semantic_intervention_signature(record, control) for record in control.intervention_history),
            key=_canonical,
        )
        right_interventions = sorted(
            (_semantic_intervention_signature(record, treatment) for record in treatment.intervention_history),
            key=_canonical,
        )
        if left_interventions != right_interventions:
            earliest = {
                "event_index": len(positions),
                "semantic_position": {
                    "tick": min(
                        [item["created_tick"] for item in left_interventions + right_interventions],
                        default=0,
                    ),
                    "control_actor_id": None,
                    "treatment_actor_id": None,
                    "sequence": 0,
                },
                "reason": "semantic intervention state differs",
                "control": left_interventions,
                "treatment": right_interventions,
            }
            divergent += 1

    return {
        "comparison_type": "semantic_event_comparison",
        "equivalence": "identical" if earliest is None else "diverged",
        "control_event_count": len(left),
        "treatment_event_count": len(right),
        "matched_event_count": matched,
        "divergence_count": divergent,
        "divergent_event_count": divergent,
        "earliest_divergence": earliest,
        "id_policy": "Generated event, content, claim, intervention, and snapshot IDs are excluded from semantic equality.",
        "evidence": _evidence_block(),
    }


def _append_link(
    state: SimulationState,
    parent_event_id: str,
    child_event_id: str,
    relation: str,
    *,
    tick: int | None = None,
    provenance: str = "simulator_rule",
    details: str = "",
) -> None:
    if not parent_event_id or not child_event_id or parent_event_id == child_event_id:
        return
    raw = f"{parent_event_id}|{child_event_id}|{relation}"
    link_id = f"link-{_digest(raw)}"
    if any(link.link_id == link_id for link in state.causal_links):
        return
    state.causal_links.append(
        CausalLink(
            link_id=link_id,
            parent_event_id=parent_event_id,
            child_event_id=child_event_id,
            relation=relation,
            tick=state.tick if tick is None else tick,
            label="modeled_dependency",
            provenance=provenance,
            details=details,
        )
    )


def append_causal_link(
    state: SimulationState,
    parent_event_id: str,
    child_event_id: str,
    relation: str,
    *,
    tick: int | None = None,
    provenance: str = "simulator_rule",
    details: str = "",
) -> None:
    _append_link(
        state,
        parent_event_id,
        child_event_id,
        relation,
        tick=tick,
        provenance=provenance,
        details=details,
    )


def record_content_provenance(state: SimulationState, content: Content) -> None:
    """Connect an intervention/parent action to newly created content."""

    content_by_id = {item.id: item for item in state.contents}
    content.semantic_key = semantic_content_key(content, content_by_id)
    for parent in content.parent_event_ids + content.provenance_event_ids:
        _append_link(state, parent, content_ref(content.id), "provenance", tick=content.created_tick)
    if content.intervention_id:
        _append_link(
            state,
            intervention_ref(content.intervention_id),
            content_ref(content.id),
            "intervention_created_content",
            tick=content.created_tick,
        )
    if content.parent_id and content.parent_id in content_by_id:
        _append_link(state, content_ref(content.parent_id), content_ref(content.id), "content_parent", tick=content.created_tick)
    if content.claim_id:
        claim = state.claims.get(content.claim_id)
        _append_link(
            state,
            content_ref(content.id),
            claim_ref(content.claim_id),
            "claim_origin" if claim and claim.origin_content_id == content.id else "claim_provenance",
            tick=content.created_tick,
        )


def _ensure_content_keys(state: SimulationState) -> None:
    content_by_id = {content.id: content for content in state.contents}
    for content in sorted(state.contents, key=lambda item: (item.created_tick, item.id)):
        if not content.semantic_key:
            content.semantic_key = _content_semantic_key(content, content_by_id)


def ensure_causal_registry(state: SimulationState) -> None:
    """Hydrate additive ancestry records when loading an older snapshot."""

    _ensure_content_keys(state)
    content_by_id = {content.id: content for content in state.contents}

    # Old snapshots have action events but no explicit exposure records. The
    # observed content field is enough to rebuild this conservative ancestry.
    exposure_counts: defaultdict[tuple[int, str], int] = defaultdict(int)
    for action in state.events:
        if not action.observed_content_id or action.observed_content_id not in content_by_id:
            continue
        key = (action.tick, action.actor_id)
        ordinal = exposure_counts[key]
        exposure_counts[key] += 1
        exposure_id = f"exposure-{action.tick:04d}-{action.actor_id}-{ordinal:02d}"
        if any(item.event_id == exposure_id for item in state.content_exposures):
            continue
        observed = content_by_id[action.observed_content_id]
        exposure = ContentExposureEvent(
            event_id=exposure_id,
            semantic_key=f"exposure:{action.tick}:{action.actor_id}:{semantic_content_key(observed, content_by_id)}",
            tick=action.tick,
            actor_id=action.actor_id,
            content_id=observed.id,
            claim_id=observed.claim_id,
            topic=observed.topic,
            parent_event_ids=[content_ref(observed.id), *observed.parent_event_ids],
            provenance_event_ids=[action_ref(action.event_id)],
        )
        state.content_exposures.append(exposure)
        _append_link(state, content_ref(observed.id), exposure_ref(exposure.event_id), "exposure", tick=action.tick)
        _append_link(state, exposure_ref(exposure.event_id), action_ref(action.event_id), "informed_action", tick=action.tick)

    # Give legacy traces semantic keys and connect each lifecycle phase in the
    # recorded order. Phase names are domain labels, not database positions.
    grouped: defaultdict[tuple[int, str], list[DecisionTrace]] = defaultdict(list)
    for trace in state.traces:
        if not trace.semantic_key:
            trace.semantic_key = f"decision:{trace.tick}:{trace.agent_id}:{trace.phase.value}"
        grouped[(trace.tick, trace.agent_id)].append(trace)
    phase_order = {"OBSERVE": 0, "RETRIEVE": 1, "FORM INTENT": 2, "EVALUATE": 3, "ACT": 4, "UPDATE": 5, "RECORD": 6}
    for traces in grouped.values():
        ordered = sorted(traces, key=lambda item: (phase_order.get(item.phase.value, 99), item.trace_id))
        previous: DecisionTrace | None = None
        for trace in ordered:
            if previous is not None:
                _append_link(state, decision_ref(previous.trace_id), decision_ref(trace.trace_id), "decision_sequence", tick=trace.tick)
            previous = trace

    for action in state.events:
        action.semantic_key = action.semantic_key or f"action:{_digest(str(semantic_event_signature(action, state)))}"
        observed = next((item for item in state.content_exposures if item.provenance_event_ids and item.provenance_event_ids[-1] == action_ref(action.event_id)), None)
        if observed:
            _append_link(state, exposure_ref(observed.event_id), action_ref(action.event_id), "informed_action", tick=action.tick)

    # Rebuild additive ancestry from fields present in newer snapshots. This is
    # deliberately idempotent so loading the same snapshot repeatedly never
    # changes its causal projection.
    for content in state.contents:
        for parent in content.parent_event_ids + content.provenance_event_ids:
            _append_link(state, parent, content_ref(content.id), "provenance", tick=content.created_tick)
        if content.parent_id and content.parent_id in content_by_id:
            _append_link(state, content_ref(content.parent_id), content_ref(content.id), "content_parent", tick=content.created_tick)
        if content.intervention_id:
            _append_link(
                state,
                intervention_ref(content.intervention_id),
                content_ref(content.id),
                "intervention_created_content",
                tick=content.created_tick,
            )
        if content.claim_id:
            claim = state.claims.get(content.claim_id)
            _append_link(
                state,
                content_ref(content.id),
                claim_ref(content.claim_id),
                "claim_origin" if claim and claim.origin_content_id == content.id else "claim_provenance",
                tick=content.created_tick,
            )

    # Claims are durable semantic entities. Their origin and later lineage
    # remain connected even when a branch assigns different generated content
    # IDs after an intervention.
    for claim in sorted(state.claims.values(), key=lambda item: item.claim_id):
        claim_node = claim_ref(claim.claim_id)
        if claim.origin_content_id:
            _append_link(
                state,
                content_ref(claim.origin_content_id),
                claim_node,
                "claim_origin",
                tick=claim.created_tick,
            )
        for provenance in claim.provenance:
            _append_link(
                state,
                content_ref(provenance.content_id),
                claim_node,
                "claim_provenance",
                tick=provenance.tick,
            )

    for trace in state.traces:
        for parent in trace.parent_event_ids + trace.provenance_event_ids:
            _append_link(state, parent, decision_ref(trace.trace_id), "decision_dependency", tick=trace.tick)
    for update in state.belief_updates:
        for parent in update.parent_event_ids + update.provenance_event_ids:
            _append_link(state, parent, belief_update_ref(update.update_id), "informed_belief_update", tick=update.tick)
    for action in state.events:
        for parent in action.parent_event_ids + action.provenance_event_ids:
            _append_link(state, parent, action_ref(action.event_id), "caused_action", tick=action.tick)
    for record in state.intervention_history:
        intervention_id = intervention_ref(record.intervention_id)
        for parent in record.parent_event_ids + record.provenance_event_ids:
            _append_link(state, parent, intervention_id, "intervention_ancestry", tick=record.created_tick)
        if record.kind.value == "suppress_citizen" and record.agent_id:
            _append_link(state, intervention_id, citizen_ref(record.agent_id), "suppressed_citizen", tick=record.created_tick)
        elif record.kind.value == "suppress_post" and record.content_id:
            _append_link(state, intervention_id, content_ref(record.content_id), "suppressed_content", tick=record.created_tick)
        elif record.kind.value == "suppress_relationship" and record.agent_id and record.target_agent_id:
            _append_link(
                state,
                intervention_id,
                relationship_ref(record.agent_id, record.target_agent_id),
                "suppressed_relationship",
                tick=record.created_tick,
            )
        elif record.kind.value == "trust_shift" and record.target_agent_id:
            observers = [record.agent_id] if record.agent_id else sorted(state.agents)
            for observer_id in observers:
                if observer_id != record.target_agent_id:
                    _append_link(
                        state,
                        intervention_id,
                        relationship_ref(observer_id, record.target_agent_id),
                        "shifted_relationship_trust",
                        tick=record.created_tick,
                    )


def _evidence_block() -> dict[str, Any]:
    return {
        "modeled_dependency_label": MODELED_DEPENDENCY,
        "statistical_effect_label": NOT_STATISTICALLY_ESTABLISHED,
        "description": "Recorded links are simulator-declared dependencies. A single paired branch difference is descriptive, not a statistically established effect.",
    }


def _agent_impact(state: SimulationState, agent_id: str) -> dict[str, Any]:
    agent = state.agents.get(agent_id)
    events = [event for event in state.events if event.actor_id == agent_id]
    exposures = [event for event in state.content_exposures if event.actor_id == agent_id]
    updates = [event for event in state.belief_updates if event.agent_id == agent_id]
    authored = [content for content in state.contents if content.author_id == agent_id]
    beliefs = {
        topic: {
            "stance": round(belief.stance, 6),
            "confidence": round(belief.confidence, 6),
            "evidence_for": belief.evidence_for,
            "evidence_against": belief.evidence_against,
        }
        for topic, belief in sorted(agent.beliefs.items())
    } if agent else {}
    return {
        "action_count": len(events),
        "action_counts": dict(sorted(Counter(event.action.value for event in events).items())),
        "exposure_count": len(exposures),
        "authored_content_count": len(authored),
        "belief_update_count": len(updates),
        "beliefs": beliefs,
        "average_belief_confidence": round(sum(belief["confidence"] for belief in beliefs.values()) / max(1, len(beliefs)), 6),
        "average_relationship_trust": round(sum(item.trust for item in agent.relationships.values()) / max(1, len(agent.relationships)), 6) if agent else 0.0,
        "relationship_count": len(agent.relationships) if agent else 0,
        "community_ids": sorted(agent.community_ids) if agent else [],
        "suppressed": agent_id in state.suppressed_citizen_ids,
    }


def _delta(left: dict[str, Any], right: dict[str, Any], fields: tuple[str, ...]) -> dict[str, float]:
    return {field: round(float(right.get(field, 0)) - float(left.get(field, 0)), 6) for field in fields}


def _claim_impact(state: SimulationState) -> dict[str, dict[str, Any]]:
    metrics = calculate_metrics(state).claim_metrics
    impacts: dict[str, dict[str, Any]] = {}
    for claim_id, metric in sorted(metrics.items()):
        semantic_id = semantic_claim_key(claim_id, state) or f"claim:{_digest(claim_id)}"
        row = metric.model_dump(mode="json")
        row["semantic_claim_id"] = semantic_id
        row["branch_claim_id"] = claim_id
        impacts[semantic_id] = row
    return impacts


def _community_impact(state: SimulationState) -> dict[str, dict[str, Any]]:
    event_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for event in state.community_events:
        event_counts[event.community_id][event.kind.value] += 1
        for related_id in event.related_community_ids:
            event_counts[related_id][event.kind.value] += 1
    return {
        community_id: {
            "community_id": community_id,
            "name": community.name,
            "topic": community.topic,
            "member_count": len(community.member_ids),
            "member_ids": sorted(community.member_ids),
            "event_counts": dict(sorted(event_counts[community_id].items())),
        }
        for community_id, community in sorted(state.communities.items())
    }


def impact_summary(control: SimulationState, treatment: SimulationState) -> dict[str, Any]:
    """Summarize citizen, claim, and community consequences of two branches."""

    citizen_ids = sorted(set(control.agents) | set(treatment.agents))
    citizens: list[dict[str, Any]] = []
    numeric_fields = (
        "action_count",
        "exposure_count",
        "authored_content_count",
        "belief_update_count",
        "average_belief_confidence",
        "average_relationship_trust",
        "relationship_count",
    )
    for agent_id in citizen_ids:
        left = _agent_impact(control, agent_id)
        right = _agent_impact(treatment, agent_id)
        citizens.append(
            {
                "citizen_id": agent_id,
                "control": left,
                "treatment": right,
                "delta": _delta(left, right, numeric_fields),
                "changed": left != right,
                "evidence": _evidence_block(),
            }
        )

    left_claims = _claim_impact(control)
    right_claims = _claim_impact(treatment)
    claims: list[dict[str, Any]] = []
    claim_fields = ("exposures", "unique_exposed_agents", "reshares", "unique_resharers", "replies", "fact_checks", "corrections")
    for semantic_id in sorted(set(left_claims) | set(right_claims)):
        left = left_claims.get(semantic_id, {field: 0 for field in claim_fields})
        right = right_claims.get(semantic_id, {field: 0 for field in claim_fields})
        claims.append(
            {
                "claim_id": left.get("branch_claim_id") or right.get("branch_claim_id") or semantic_id,
                "semantic_claim_id": semantic_id,
                "control_claim_id": left.get("branch_claim_id"),
                "treatment_claim_id": right.get("branch_claim_id"),
                "control": left,
                "treatment": right,
                "delta": _delta(left, right, claim_fields),
                "changed": left != right,
                "evidence": _evidence_block(),
            }
        )

    left_communities = _community_impact(control)
    right_communities = _community_impact(treatment)
    communities: list[dict[str, Any]] = []
    for community_id in sorted(set(left_communities) | set(right_communities)):
        left = left_communities.get(community_id, {"member_count": 0, "member_ids": [], "event_counts": {}})
        right = right_communities.get(community_id, {"member_count": 0, "member_ids": [], "event_counts": {}})
        communities.append(
            {
                "community_id": community_id,
                "control": left,
                "treatment": right,
                "delta": {"member_count": right.get("member_count", 0) - left.get("member_count", 0)},
                "changed": left != right,
                "evidence": _evidence_block(),
            }
        )

    return {
        "summary_type": "branch_impact_summary",
        "evidence": _evidence_block(),
        "citizens": citizens,
        "claims": claims,
        "communities": communities,
        "changed_citizen_count": sum(item["changed"] for item in citizens),
        "changed_claim_count": sum(item["changed"] for item in claims),
        "changed_community_count": sum(item["changed"] for item in communities),
    }


def _node(node_id: str, node_type: str, *, tick: int, semantic_key: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "tick": tick,
        "semantic_key": semantic_key,
        **extra,
    }


def causal_graph(state: SimulationState) -> dict[str, Any]:
    """Project all persisted links into a typed dependency graph."""

    ensure_causal_registry(state)
    nodes: dict[str, dict[str, Any]] = {}
    for agent in sorted(state.agents.values(), key=lambda item: item.id):
        node_id = citizen_ref(agent.id)
        nodes[node_id] = _node(
            node_id,
            "citizen",
            tick=0,
            semantic_key=f"citizen:{agent.id}",
            citizen_id=agent.id,
            name=agent.name,
        )
        for relationship in sorted(agent.relationships.values(), key=lambda item: item.target_id):
            relationship_id = relationship_ref(agent.id, relationship.target_id)
            nodes[relationship_id] = _node(
                relationship_id,
                "relationship",
                tick=state.tick,
                semantic_key=f"relationship:{agent.id}->{relationship.target_id}",
                source_agent_id=agent.id,
                target_agent_id=relationship.target_id,
                follows=relationship.follows,
            )
    for suppressed in state.suppressed_relationships:
        relationship_id = relationship_ref(suppressed.source_agent_id, suppressed.target_agent_id)
        nodes.setdefault(
            relationship_id,
            _node(
                relationship_id,
                "relationship",
                tick=suppressed.created_tick,
                semantic_key=f"relationship:{suppressed.source_agent_id}->{suppressed.target_agent_id}",
                source_agent_id=suppressed.source_agent_id,
                target_agent_id=suppressed.target_agent_id,
                suppressed=True,
            ),
        )
    for record in state.intervention_history:
        node_id = intervention_ref(record.intervention_id)
        nodes[node_id] = _node(
            node_id,
            "intervention",
            tick=record.created_tick,
            semantic_key=record.semantic_key,
            intervention_id=record.intervention_id,
            kind=record.kind.value,
            agent_id=record.agent_id,
            target_agent_id=record.target_agent_id,
            content_id=record.content_id,
        )
    for claim in sorted(state.claims.values(), key=lambda item: item.claim_id):
        semantic_key = semantic_claim_key(claim.claim_id, state)
        node_id = claim_ref(claim.claim_id)
        nodes[node_id] = _node(
            node_id,
            "claim",
            tick=claim.created_tick,
            semantic_key=semantic_key,
            claim_id=claim.claim_id,
            topic=claim.topic,
            truth_label=claim.truth_label,
            status=claim.status.value,
            origin_content_id=claim.origin_content_id,
        )
    for content in state.contents:
        node_id = content_ref(content.id)
        nodes[node_id] = _node(node_id, "content", tick=content.created_tick, semantic_key=content.semantic_key, content_id=content.id, actor_id=content.author_id, claim_id=content.claim_id, topic=content.topic)
    for event in state.content_exposures:
        node_id = exposure_ref(event.event_id)
        nodes[node_id] = _node(node_id, "content_exposure", tick=event.tick, semantic_key=event.semantic_key, actor_id=event.actor_id, content_id=event.content_id, claim_id=event.claim_id)
    for trace in state.traces:
        node_id = decision_ref(trace.trace_id)
        nodes[node_id] = _node(node_id, "decision", tick=trace.tick, semantic_key=trace.semantic_key, actor_id=trace.agent_id, phase=trace.phase.value, action=trace.action.value if trace.action else None)
    for update in state.belief_updates:
        node_id = belief_update_ref(update.update_id)
        nodes[node_id] = _node(node_id, "belief_update", tick=update.tick, semantic_key=update.semantic_key, actor_id=update.agent_id, topic=update.topic, content_id=update.content_id, claim_id=update.claim_id)
    for event in state.events:
        node_id = action_ref(event.event_id)
        nodes[node_id] = _node(node_id, "action", tick=event.tick, semantic_key=event.semantic_key, actor_id=event.actor_id, action=event.action.value, content_id=event.content_id, claim_id=event.claim_id)

    edges = [
        link.model_dump(mode="json")
        for link in sorted(state.causal_links, key=lambda item: (item.tick, item.parent_event_id, item.child_event_id, item.relation))
    ]
    for edge in edges:
        for node_id in (edge["parent_event_id"], edge["child_event_id"]):
            nodes.setdefault(node_id, _node(node_id, "unresolved", tick=edge["tick"], semantic_key=None))
    return {
        "graph_type": "recorded_causal_dependency_graph",
        "evidence": _evidence_block(),
        "statistical_causal_effects": [],
        "nodes": sorted(nodes.values(), key=lambda item: (item["tick"], item["node_type"], item["node_id"])),
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def branch_causal_payload(control: SimulationState, treatment: SimulationState) -> dict[str, Any]:
    comparison = compare_branches(control, treatment)
    return {
        "comparison": comparison,
        "impact_summary": impact_summary(control, treatment),
        "graphs": {
            "control": causal_graph(control),
            "treatment": causal_graph(treatment),
        },
    }
