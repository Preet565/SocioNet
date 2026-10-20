"""Deterministic relationship formation, decay, and following evolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models import Agent, ArchetypeBehavior, Community, Relationship, SimulationConfig
from app.utils import rng_for


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
    tick: int | None = None,
) -> Relationship:
    relationship = relationship_for(agent, target_id)
    relationship.interactions += 1
    direction = 1 if positive else -1
    relationship.affinity = max(-1.0, min(1.0, relationship.affinity + direction * trust_delta))
    relationship.trust = max(0.0, min(1.0, relationship.trust + direction * trust_delta * 0.5))
    if tick is not None:
        relationship.last_interaction_tick = tick
    return relationship


def set_following(
    agent: Agent,
    target_id: str,
    following: bool,
    *,
    max_following: int | None = None,
) -> Relationship:
    relationship = relationship_for(agent, target_id)
    if following and target_id not in agent.following:
        if max_following is not None and len(agent.following) >= max_following:
            relationship.follows = False
            return relationship
        agent.following.append(target_id)
        agent.following.sort()
    if not following and target_id in agent.following:
        agent.following.remove(target_id)
    relationship.follows = following and target_id in agent.following
    return relationship


def _archetype(agent: Agent, config: SimulationConfig) -> ArchetypeBehavior:
    return config.archetypes.get(agent.archetype, config.archetypes["civic_participant"])


def shared_community(agent: Agent, target: Agent) -> bool:
    return bool(set(agent.community_ids).intersection(target.community_ids))


def homophily_score(agent: Agent, target: Agent, *, config: SimulationConfig) -> float:
    """Return [0, 1] similarity from ideology, interests, and community overlap."""

    ideology_similarity = 1.0 - abs(agent.ideology - target.ideology) / 2.0
    left = set(agent.interests)
    right = set(target.interests)
    interest_similarity = len(left.intersection(right)) / max(1, len(left.union(right)))
    community_similarity = 1.0 if shared_community(agent, target) else 0.0
    base = (
        config.homophily_weight * ideology_similarity
        + (1.0 - config.homophily_weight) * interest_similarity
    )
    return max(0.0, min(1.0, base * 0.8 + community_similarity * 0.2))


def interaction_probability(agent: Agent, target: Agent, *, config: SimulationConfig) -> float:
    """Apply homophily and a bounded cross-community bridge allowance."""

    behavior = _archetype(agent, config)
    similarity = homophily_score(agent, target, config=config)
    if shared_community(agent, target):
        return max(0.0, min(1.0, 0.35 + 0.65 * similarity * behavior.homophily_bias))
    bridge = config.cross_community_interaction_probability * behavior.cross_community_bias
    # Dissimilarity makes a cross-community tie harder, while bridge-oriented
    # archetypes can still reach across a sparse topology.
    bridge *= (1.0 - config.homophily_weight) + config.homophily_weight * similarity
    return max(0.0, min(1.0, bridge))


def _relationship_affinity(agent: Agent, target: Agent, similarity: float) -> float:
    return round(max(-1.0, min(1.0, (agent.ideology + target.ideology) / 2 * 0.35 + (similarity - 0.5) * 0.5)), 4)


def _candidate_ids(agent: Agent, agents: dict[str, Agent]) -> list[str]:
    return [target_id for target_id in sorted(agents) if target_id != agent.id]


def initialize_relationships(
    seed: int,
    agents: dict[str, Agent],
    communities: dict[str, Community],
    config: SimulationConfig,
) -> None:
    """Build a bounded opening graph from a named deterministic structure."""

    network = config.initial_network
    ids = sorted(agents)
    positions = {agent_id: index for index, agent_id in enumerate(ids)}
    initial_limit = min(config.max_relationships, network.max_edges_per_agent)
    for index, agent_id in enumerate(ids):
        agent = agents[agent_id]
        candidates = _candidate_ids(agent, agents)
        for target_id in candidates:
            if len(agent.relationships) >= initial_limit:
                break
            target = agents[target_id]
            similarity = homophily_score(agent, target, config=config)
            same_community = shared_community(agent, target)
            rng = rng_for(seed, "initial-network", network.structure, agent_id, target_id)
            if network.structure == "empty":
                include = False
            elif network.structure == "ring":
                target_position = positions[target_id]
                distance = (target_position - index) % len(ids) if ids else 0
                include = 0 < distance <= network.ring_degree
            else:
                base_probability = network.edge_probability
                if network.structure == "community":
                    base_probability *= 1.35 if same_community else network.cross_community_probability
                else:
                    base_probability *= (
                        network.homophily_weight * similarity + (1.0 - network.homophily_weight) * 0.5
                    )
                include = rng.random() < min(1.0, base_probability)
            if include:
                relationship = relationship_for(agent, target_id)
                relationship.affinity = _relationship_affinity(agent, target, similarity)
                relationship.trust = round((agent.trust_baseline + target.trust_baseline) / 2, 4)
        enforce_relationship_bounds(agent, config)


def enforce_relationship_bounds(agent: Agent, config: SimulationConfig) -> None:
    """Keep old snapshots and newly formed graphs within configured bounds."""

    if len(agent.relationships) > config.max_relationships:
        ranked = sorted(
            agent.relationships.values(),
            key=lambda item: (item.follows, item.interactions, item.trust, item.affinity, item.target_id),
            reverse=True,
        )
        keep = {relationship.target_id for relationship in ranked[: config.max_relationships]}
        for target_id in sorted(set(agent.relationships) - keep):
            del agent.relationships[target_id]
            if target_id in agent.following:
                agent.following.remove(target_id)
    agent.following = sorted(
        target_id
        for target_id in agent.following
        if target_id in agent.relationships and agent.relationships[target_id].follows
    )[: config.max_following]
    for target_id, relationship in agent.relationships.items():
        relationship.follows = target_id in agent.following


@dataclass(frozen=True)
class RelationshipTransition:
    source_id: str
    target_id: str
    kind: str


def evolve_relationships(
    seed: int,
    tick: int,
    agents: dict[str, Agent],
    communities: dict[str, Community],
    config: SimulationConfig,
    suppressed_relationships: set[tuple[str, str]] | None = None,
    suppressed_agents: set[str] | None = None,
) -> list[RelationshipTransition]:
    """Decay stale ties, form compatible ties, and evolve follows in stable order.

    A suppressed citizen keeps its existing outgoing state but does not create,
    remove, or evolve outgoing relationships after suppression. Incoming ties
    remain available to the rest of the population, which keeps the
    perturbation focused on the citizen's participation.
    """

    transitions: list[RelationshipTransition] = []
    suppressed_relationships = suppressed_relationships or set()
    suppressed_agents = suppressed_agents or set()
    for agent_id in sorted(agents):
        if agent_id in suppressed_agents:
            continue
        agent = agents[agent_id]
        behavior = _archetype(agent, config)
        decay = min(1.0, config.relationship_decay_rate * behavior.relationship_decay_bias)
        for target_id in sorted(agent.relationships):
            if (agent_id, target_id) in suppressed_relationships:
                continue
            relationship = agent.relationships[target_id]
            if relationship.last_interaction_tick != tick:
                relationship.affinity = round(relationship.affinity * (1.0 - decay), 6)
                relationship.trust = round(
                    relationship.trust + (agent.trust_baseline - relationship.trust) * decay * 0.5,
                    6,
                )
            rng = rng_for(seed, "following-evolution", tick, agent_id, target_id)
            if relationship.follows:
                unfollow_probability = config.following_decay_probability * behavior.unfollow_bias
                unfollow_probability *= max(0.1, 1.0 - max(0.0, relationship.affinity))
                if rng.random() < min(1.0, unfollow_probability):
                    set_following(agent, target_id, False)
                    transitions.append(RelationshipTransition(agent_id, target_id, "unfollow"))
            elif len(agent.following) < config.max_following:
                follow_probability = config.following_adoption_probability * behavior.follow_bias
                follow_probability *= max(0.0, (relationship.trust + relationship.affinity + 1.0) / 2.0)
                if rng.random() < min(1.0, follow_probability):
                    set_following(agent, target_id, True, max_following=config.max_following)
                    if agent.relationships[target_id].follows:
                        transitions.append(RelationshipTransition(agent_id, target_id, "follow"))

        for target_id in _candidate_ids(agent, agents):
            if (agent_id, target_id) in suppressed_relationships:
                continue
            if len(agent.relationships) >= config.max_relationships:
                break
            if target_id in agent.relationships:
                continue
            target = agents[target_id]
            probability = config.relationship_formation_probability * behavior.relationship_formation_bias
            probability *= interaction_probability(agent, target, config=config)
            rng = rng_for(seed, "relationship-formation", tick, agent_id, target_id)
            if rng.random() >= min(1.0, probability):
                continue
            similarity = homophily_score(agent, target, config=config)
            relationship = relationship_for(agent, target_id)
            relationship.affinity = _relationship_affinity(agent, target, similarity)
            relationship.trust = round((agent.trust_baseline + target.trust_baseline) / 2, 4)
            transitions.append(RelationshipTransition(agent_id, target_id, "form"))
        enforce_relationship_bounds(agent, config)
    return transitions


def relationship_edges(agents: dict[str, Agent]) -> Iterable[tuple[str, Relationship]]:
    for agent_id in sorted(agents):
        for relationship in sorted(agents[agent_id].relationships.values(), key=lambda item: item.target_id):
            yield agent_id, relationship
