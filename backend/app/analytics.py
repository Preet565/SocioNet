"""Calculated metrics over simulation state, events, and belief outcomes."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import sqrt

from app.models import ActionKind, Metrics, SimulationState


def calculate_metrics(state: SimulationState) -> Metrics:
    counts = Counter(event.action for event in state.events)
    contents_by_kind = Counter(content.kind.value for content in state.contents)
    agents = list(state.agents.values())
    all_beliefs = [belief for agent in agents for belief in agent.beliefs.values()]
    all_relationships = [relationship for agent in agents for relationship in agent.relationships.values()]
    average_confidence = _average([belief.confidence for belief in all_beliefs])
    average_trust = _average([relationship.trust for relationship in all_relationships])
    stances = [belief.stance for belief in all_beliefs]
    polarization = _average([abs(stance - _average(stances)) for stance in stances])
    evidence_total = sum(belief.evidence_for + belief.evidence_against for belief in all_beliefs)
    evidence_for = sum(belief.evidence_for for belief in all_beliefs)
    active_agents = len({event.actor_id for event in state.events if event.action is not ActionKind.IGNORE})
    non_ignores = len(state.events) - counts[ActionKind.IGNORE]
    engagement_rate = non_ignores / max(1, len(agents) * max(1, state.tick))

    topics: dict[str, dict[str, float | int]] = defaultdict(lambda: {
        "contents": 0,
        "fact_checks": 0,
        "counterevidence": 0,
        "average_credibility": 0.0,
    })
    for content in state.contents:
        metrics = topics[content.topic]
        metrics["contents"] = int(metrics["contents"]) + 1
        metrics["fact_checks"] = int(metrics["fact_checks"]) + (1 if content.kind.value == "fact_check" else 0)
        metrics["counterevidence"] = int(metrics["counterevidence"]) + (1 if content.is_counterevidence else 0)
        metrics["average_credibility"] = float(metrics["average_credibility"]) + content.credibility
    for topic, topic_metrics in topics.items():
        topic_metrics["average_credibility"] = round(
            float(topic_metrics["average_credibility"]) / max(1, int(topic_metrics["contents"])),
            6,
        )

    return Metrics(
        tick=state.tick,
        population=len(agents),
        total_contents=len(state.contents),
        posts=contents_by_kind["post"],
        replies=contents_by_kind["reply"],
        fact_checks=contents_by_kind["fact_check"],
        ignored_decisions=counts[ActionKind.IGNORE],
        follows=counts[ActionKind.FOLLOW],
        unfollows=counts[ActionKind.UNFOLLOW],
        community_joins=counts[ActionKind.JOIN_COMMUNITY],
        community_leaves=counts[ActionKind.LEAVE_COMMUNITY],
        active_agents=active_agents,
        average_belief_confidence=round(average_confidence, 6),
        average_relationship_trust=round(average_trust, 6),
        polarization=round(polarization, 6),
        belief_accuracy=round(evidence_for / evidence_total if evidence_total else 0.0, 6),
        engagement_rate=round(engagement_rate, 6),
        topic_metrics=dict(sorted(topics.items())),
    )


def metric_deltas(control: Metrics, treatment: Metrics) -> dict[str, float]:
    names = (
        "total_contents",
        "posts",
        "replies",
        "fact_checks",
        "ignored_decisions",
        "follows",
        "unfollows",
        "community_joins",
        "community_leaves",
        "active_agents",
        "average_belief_confidence",
        "average_relationship_trust",
        "polarization",
        "belief_accuracy",
        "engagement_rate",
    )
    return {name: round(float(getattr(treatment, name)) - float(getattr(control, name)), 6) for name in names}


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
