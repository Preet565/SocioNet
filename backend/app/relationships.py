"""Trust and affinity updates between citizens."""

from __future__ import annotations

from app.models import Agent, Relationship


def relationship_for(agent: Agent, target_id: str) -> Relationship:
    return agent.relationships.setdefault(
        target_id,
        Relationship(target_id=target_id, trust=agent.trust_baseline),
    )


def record_interaction(
    agent: Agent,
    target_id: str,
    *,
    positive: bool,
    trust_delta: float = 0.03,
) -> Relationship:
    relationship = relationship_for(agent, target_id)
    relationship.interactions += 1
    direction = 1 if positive else -1
    relationship.affinity = max(-1.0, min(1.0, relationship.affinity + direction * trust_delta))
    relationship.trust = max(0.0, min(1.0, relationship.trust + direction * trust_delta * 0.5))
    return relationship


def set_following(agent: Agent, target_id: str, following: bool) -> Relationship:
    relationship = relationship_for(agent, target_id)
    relationship.follows = following
    if following and target_id not in agent.following:
        agent.following.append(target_id)
    if not following and target_id in agent.following:
        agent.following.remove(target_id)
    return relationship
