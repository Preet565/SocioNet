"""Community membership rules and seeded fictional communities."""

from __future__ import annotations

from app.models import Agent, Community
from app.utils import rng_for


COMMUNITY_SPECS = (
    ("community-transit", "Transit Tinkerers", "fictional transit", "Ideas for an imaginary local transit map."),
    ("community-gardens", "Garden Circle", "neighborhood gardens", "Shared notes from fictional neighborhood gardens."),
    ("community-libraries", "Library Lantern", "library funding", "Friendly discussion of imaginary library projects."),
    ("community-radio", "Radio Signal", "community radio", "Programming and signal experiments in a fictional town."),
    ("community-market", "Night Market", "night market rules", "A place to discuss made-up market etiquette."),
    ("community-art", "Public Canvas", "public art", "Collaborative planning for fictional public art."),
)


def create_communities(seed: int, agents: dict[str, Agent]) -> dict[str, Community]:
    communities = {
        community_id: Community(id=community_id, name=name, topic=topic, description=description)
        for community_id, name, topic, description in COMMUNITY_SPECS
    }
    for agent_id, agent in agents.items():
        rng = rng_for(seed, "community", agent_id)
        preferred = [community for community in communities.values() if community.topic in agent.interests]
        chosen = preferred[0] if preferred else list(communities.values())[rng.randrange(len(communities))]
        join_community(agent, chosen)
        if rng.random() < 0.35:
            second = list(communities.values())[rng.randrange(len(communities))]
            join_community(agent, second)
    return communities


def join_community(agent: Agent, community: Community) -> bool:
    if agent.id in community.member_ids:
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
