"""Community membership rules, evolution, and seeded fictional communities."""

from __future__ import annotations

from statistics import fmean

from app.models import (
    Agent,
    Community,
    CommunityEvent,
    CommunityEventKind,
    CommunityEvolutionConfig,
    SimulationConfig,
    SimulationState,
)
from app.utils import rng_for


COMMUNITY_SPECS = (
    ("community-transit", "Transit Tinkerers", "fictional transit", "Ideas for an imaginary local transit map."),
    ("community-gardens", "Garden Circle", "neighborhood gardens", "Shared notes from fictional neighborhood gardens."),
    ("community-libraries", "Library Lantern", "library funding", "Friendly discussion of imaginary library projects."),
    ("community-radio", "Radio Signal", "community radio", "Programming and signal experiments in a fictional town."),
    ("community-market", "Night Market", "night market rules", "A place to discuss made-up market etiquette."),
    ("community-art", "Public Canvas", "public art", "Collaborative planning for fictional public art."),
)


def create_communities(
    seed: int,
    agents: dict[str, Agent],
    *,
    config: SimulationConfig | None = None,
) -> dict[str, Community]:
    communities = {
        community_id: Community(id=community_id, name=name, topic=topic, description=description)
        for community_id, name, topic, description in COMMUNITY_SPECS
    }
    max_memberships = (
        config.community_evolution.max_communities_per_agent if config is not None else 3
    )
    for agent_id in sorted(agents):
        agent = agents[agent_id]
        rng = rng_for(seed, "community", agent_id)
        available = sorted(communities.values(), key=lambda community: community.id)
        preferred = [community for community in available if community.topic in agent.interests]
        chosen = preferred[0] if preferred else available[rng.randrange(len(available))]
        join_community(agent, chosen, max_communities=max_memberships)
        if rng.random() < 0.35 and len(agent.community_ids) < max_memberships:
            second = available[rng.randrange(len(available))]
            join_community(agent, second, max_communities=max_memberships)
    return communities


def join_community(agent: Agent, community: Community, *, max_communities: int | None = None) -> bool:
    if agent.id in community.member_ids:
        return False
    if max_communities is not None and len(agent.community_ids) >= max_communities:
        return False
    community.member_ids.append(agent.id)
    community.member_ids.sort()
    if community.id not in agent.community_ids:
        agent.community_ids.append(community.id)
        agent.community_ids.sort()
    return True


def leave_community(agent: Agent, community: Community) -> bool:
    if agent.id not in community.member_ids:
        return False
    community.member_ids.remove(agent.id)
    if community.id in agent.community_ids:
        agent.community_ids.remove(community.id)
    return True


def record_community_event(
    state: SimulationState,
    *,
    kind: CommunityEventKind,
    community_id: str,
    agent_id: str | None = None,
    related_community_ids: list[str] | None = None,
    member_ids: list[str] | None = None,
    details: str = "",
) -> CommunityEvent:
    event = CommunityEvent(
        event_id=f"community-event-{state.tick:04d}-{len(state.community_events) + 1:06d}",
        tick=state.tick,
        kind=kind,
        community_id=community_id,
        agent_id=agent_id,
        related_community_ids=sorted(related_community_ids or []),
        member_ids=sorted(member_ids or []),
        details=details,
    )
    state.community_events.append(event)
    return event


def _community_mean_ideology(community: Community, agents: dict[str, Agent]) -> float:
    values = [agents[agent_id].ideology for agent_id in community.member_ids if agent_id in agents]
    return fmean(values) if values else 0.0


def _split_community(state: SimulationState, community: Community, config: CommunityEvolutionConfig) -> bool:
    members = sorted(community.member_ids)
    if len(members) < config.split_size:
        return False
    values = [state.agents[agent_id].ideology for agent_id in members if agent_id in state.agents]
    if not values or max(values) - min(values) < config.split_ideology_gap:
        return False
    ordered = sorted(members, key=lambda agent_id: (state.agents[agent_id].ideology, agent_id))
    midpoint = len(ordered) // 2
    left = sorted(ordered[:midpoint])
    right = sorted(ordered[midpoint:])
    if len(left) < config.split_min_size or len(right) < config.split_min_size:
        return False
    new_id = f"{community.id}-split-{state.tick:04d}"
    if new_id in state.communities:
        return False
    community.member_ids = left
    split = Community(
        id=new_id,
        name=f"{community.name} / new circle",
        topic=community.topic,
        description=community.description,
        member_ids=right,
    )
    state.communities[new_id] = split
    for agent_id in right:
        agent = state.agents[agent_id]
        agent.community_ids = sorted(
            community_id for community_id in agent.community_ids if community_id != community.id
        )
        agent.community_ids.append(new_id)
        agent.community_ids.sort()
    record_community_event(
        state,
        kind=CommunityEventKind.SPLIT,
        community_id=community.id,
        related_community_ids=[new_id],
        member_ids=right,
        details=f"split into {community.id} and {new_id} by ideology median",
    )
    return True


def _merge_communities(state: SimulationState, config: CommunityEvolutionConfig) -> bool:
    communities = sorted(state.communities.values(), key=lambda community: community.id)
    for index, left in enumerate(communities):
        for right in communities[index + 1 :]:
            if left.topic != right.topic:
                continue
            left_mean = _community_mean_ideology(left, state.agents)
            right_mean = _community_mean_ideology(right, state.agents)
            similarity = 1.0 - abs(left_mean - right_mean) / 2.0
            if similarity < config.merge_similarity:
                continue
            merged_members = sorted(set(left.member_ids).union(right.member_ids))
            left.member_ids = merged_members
            for agent_id in right.member_ids:
                if agent_id in state.agents:
                    agent = state.agents[agent_id]
                    agent.community_ids = sorted(
                        {
                            left.id if community_id == right.id else community_id
                            for community_id in agent.community_ids
                        }
                    )
            del state.communities[right.id]
            record_community_event(
                state,
                kind=CommunityEventKind.MERGE,
                community_id=left.id,
                related_community_ids=[right.id],
                member_ids=merged_members,
                details=f"merged {right.id} into {left.id} at similarity {similarity:.4f}",
            )
            return True
    return False


def evolve_communities(state: SimulationState) -> list[CommunityEvent]:
    """Apply at most one split and one merge per tick in stable community order."""

    config = state.config.community_evolution
    if not config.enabled:
        return []
    before = len(state.community_events)
    if config.split_enabled:
        for community in sorted(list(state.communities.values()), key=lambda item: item.id):
            if _split_community(state, community, config):
                break
    if config.merge_enabled:
        _merge_communities(state, config)
    return state.community_events[before:]
