"""Stable, read-only projections used by the command-center frontend.

The simulation state is intentionally richer than the UI payload. This module
keeps the browser contract small while deriving every displayed value from the
actual agents, relationships, content, and event log.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from app.analytics import calculate_metrics
from app.models import Agent, SimulationState
from app.utils import rng_for


EXPERIMENT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "id": "rumor-counterevidence",
        "name": "One rumor, two universes",
        "description": "Hold the fictional civic-event claim constant; expose credible counterevidence only in the Echo branch after a delay.",
        "variables": ["shared seed", "rumor exposure", "counterevidence delay"],
        "projected_impact": "Measured divergence",
    },
    {
        "id": "bridge-fact-check",
        "name": "A bridge checks the claim",
        "description": "Compare an unverified seeded post with a fact-check from a high-trust citizen in the same starting snapshot.",
        "variables": ["source credibility", "trust graph", "fact-check timing"],
        "projected_impact": "Correction reach",
    },
    {
        "id": "trust-shift",
        "name": "Move one trust signal",
        "description": "Shift one relationship without changing the claim, then observe whether the cascade changes direction.",
        "variables": ["relationship trust", "shared claim", "attention budget"],
        "projected_impact": "Trust sensitivity",
    },
)


def _mean(values: list[float], fallback: float = 0.0) -> float:
    return round(fmean(values), 6) if values else fallback


def _influence(agent: Agent) -> float:
    """Documented UI influence: actions, follows, and weighted ties."""

    actions = sum(count for kind, count in agent.action_counts.items() if kind != "ignore")
    tie_weight = sum(max(0.0, relationship.trust) for relationship in agent.relationships.values())
    score = 0.08 + actions * 0.025 + len(agent.following) * 0.035 + tie_weight * 0.012
    return round(max(0.0, min(1.0, score)), 4)


def _sentiment(agent: Agent) -> float:
    if not agent.beliefs:
        return 0.5
    return round((fmean(belief.stance for belief in agent.beliefs.values()) + 1) / 2, 4)


def _archetype(agent: Agent) -> str:
    labels = {
        "evidence_keeper": "Evidence Keeper",
        "signal_seeker": "Signal Seeker",
        "community_steward": "Community Steward",
        "bridge_builder": "Bridge Builder",
        "contrarian": "Contrarian",
        "civic_participant": "Civic Participant",
    }
    return labels.get(agent.archetype, "Civic Participant")


def _status(agent: Agent, influence: float) -> str:
    if influence >= 0.55:
        return "influential"
    if agent.last_action is None:
        return "observed"
    if agent.last_action.value == "ignore":
        return "quiet"
    return "active"


def _belief_labels(agent: Agent) -> list[str]:
    return [
        f"{topic} · {round(belief.confidence * 100)}% confidence"
        for topic, belief in sorted(agent.beliefs.items(), key=lambda item: (-item[1].confidence, item[0]))[:4]
    ]


def _agent_view(
    agent: Agent,
    influence: float,
    behavior_parameters: dict[str, float] | None = None,
) -> dict[str, Any]:
    relationships = list(agent.relationships.values())
    return {
        "id": agent.id,
        "name": agent.name,
        "handle": f"@{agent.id.replace('citizen-', 'citizen_')}",
        "archetype": _archetype(agent),
        "archetypeKey": agent.archetype,
        "behaviorParameters": behavior_parameters or {},
        "location": f"Fictional district {int(agent.id.split('-')[-1]) % 12 + 1:02d}",
        "status": _status(agent, influence),
        "sentiment": _sentiment(agent),
        "trust": _mean([relationship.trust for relationship in relationships], agent.trust_baseline),
        "reach": influence,
        "beliefs": _belief_labels(agent),
        "lastAction": agent.last_action.value if agent.last_action else "No action recorded yet",
        "age": agent.age,
        "interests": agent.interests,
        "ideology": agent.ideology,
        "skepticism": agent.skepticism,
        "curiosity": agent.curiosity,
        "communityIds": agent.community_ids,
        "memoryCount": len(agent.memories),
        "relationshipCount": len(agent.relationships),
        "beliefDetail": [belief.model_dump(mode="json") for belief in agent.beliefs.values()],
    }


def _sources(state: SimulationState) -> list[dict[str, Any]]:
    by_topic = Counter(content.topic for content in state.contents)
    sources: list[dict[str, Any]] = []
    for community in sorted(state.communities.values(), key=lambda item: item.id):
        members = len(community.member_ids)
        contents = [content for content in state.contents if content.topic == community.topic]
        credibility = _mean([content.credibility for content in contents], 0.5)
        velocity = min(1.0, by_topic[community.topic] / max(1, state.tick * 8))
        sources.append(
            {
                "id": community.id,
                "name": community.name,
                "type": "Community channel",
                "reach": round(members / max(1, len(state.agents)), 4),
                "velocity": round(velocity, 4),
                "credibility": credibility,
                "signal": "rising" if velocity >= 0.55 else "stable" if velocity >= 0.2 else "fading",
                "topic": community.topic,
            }
        )
    return sources


def _minds(state: SimulationState) -> list[dict[str, Any]]:
    minds: list[dict[str, Any]] = []
    for agent in sorted(state.agents.values(), key=lambda item: item.id):
        last = agent.last_action.value if agent.last_action else "observing"
        confidence = _mean([belief.confidence for belief in agent.beliefs.values()], 0.5)
        minds.append(
            {
                "id": agent.id,
                "name": agent.name,
                "role": _archetype(agent),
                "mood": "watchful" if last == "ignore" else "engaged" if last in {"post", "reply"} else "checking" if last == "fact_check" else "forming intent",
                "attention": round(min(1.0, 0.35 + agent.curiosity * 0.55 + min(0.1, len(agent.memories) / 400)), 4),
                "openness": round(1 - agent.skepticism * 0.62, 4),
                "conviction": confidence,
                "memory": [memory.summary for memory in agent.memories[-3:]][::-1],
                "activeDrivers": [f"interest: {interest}" for interest in agent.interests],
                "latestTrace": next(
                    (
                        trace.model_dump(mode="json")
                        for trace in reversed(state.traces)
                        if trace.agent_id == agent.id and trace.phase.value == "EVALUATE"
                    ),
                    None,
                ),
            }
        )
    return minds


def _interventions(state: SimulationState) -> list[dict[str, Any]]:
    return [
        {
            "id": record.intervention_id,
            "label": record.kind.value.replace("_", " "),
            "target": record.topic or "world state",
            "state": "observed" if record.status == "applied" and record.applied_tick is not None else "queued",
            "timestamp": f"tick {record.created_tick}",
            "source": record.source,
            "effect": record.text or f"scheduled for tick {record.apply_at_tick}",
        }
        for record in reversed(state.intervention_history)
    ]


def _posts(state: SimulationState) -> list[dict[str, Any]]:
    return [
        {
            **content.model_dump(mode="json"),
            "author": state.agents.get(content.author_id).name if content.author_id in state.agents else content.author_id,
            "exposureCount": sum(1 for event in state.events if event.content_id == content.id),
        }
        for content in reversed(state.contents[-80:])
    ]


def frontend_state(
    state: SimulationState,
    universe_id: str,
    completed_experiments: set[str] | None = None,
    experiment_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a browser-friendly, calculated projection of a real state."""

    metrics = calculate_metrics(state)
    agents = sorted(state.agents.values(), key=lambda item: item.id)
    influences = {agent.id: _influence(agent) for agent in agents}
    nodes: list[dict[str, Any]] = []
    for index, agent in enumerate(agents):
        rng = rng_for(state.seed, "graph-position", agent.id)
        community_name = next(
            (state.communities[community_id].name for community_id in agent.community_ids if community_id in state.communities),
            "unclustered",
        )
        nodes.append(
            {
                "id": agent.id,
                "label": agent.name,
                "kind": "citizen",
                "influence": influences[agent.id],
                "sentiment": _sentiment(agent),
                "degree": metrics.degree.get(agent.id, 0),
                "betweenness": metrics.betweenness.get(agent.id, 0.0),
                "x": round(8 + rng.random() * 84, 4),
                "y": round(8 + rng.random() * 84, 4),
                "group": community_name,
                "index": index,
            }
        )
    edges = [
        {
            "source": agent.id,
            "target": relationship.target_id,
            "weight": round(max(0.02, (relationship.trust + relationship.affinity + 1) / 2), 4),
            "relation": "follows" if relationship.follows else "trust tie",
        }
        for agent in agents
        for relationship in sorted(agent.relationships.values(), key=lambda item: item.target_id)
        if relationship.target_id in state.agents
    ]
    trust = metrics.average_relationship_trust or _mean([agent.trust_baseline for agent in agents], 0.5)
    active = metrics.active_agents or (len(agents) if state.tick == 0 else 0)
    social_cohesion = _mean([max(0.0, (relationship.affinity + 1) / 2) for agent in agents for relationship in agent.relationships.values()], 0.5)
    entropy = min(1.0, max(0.0, 1 - metrics.belief_accuracy))
    return {
        "universeId": universe_id,
        "label": "Prime / baseline" if universe_id == "prime" else "Echo / counterfactual",
        "phase": "Initial conditions" if state.tick == 0 else "Signal propagation",
        "tick": state.tick,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "population": len(agents),
            "activeAgents": active,
            "socialCohesion": round(social_cohesion, 4),
            "trustIndex": round(trust, 4),
            "informationEntropy": round(entropy, 4),
            "interventionBudget": round(max(0.0, 1 - len(state.intervention_history) / 20), 4),
        },
        "communities": [community.model_dump(mode="json") for community in sorted(state.communities.values(), key=lambda item: item.id)],
        "nodes": nodes,
        "edges": edges,
        "citizens": [
            _agent_view(
                agent,
                influences[agent.id],
                state.config.archetypes.get(agent.archetype).model_dump(mode="json")
                if agent.archetype in state.config.archetypes
                else None,
            )
            for agent in agents
        ],
        "sources": _sources(state),
        "experiments": [
            {
                **experiment,
                "status": (
                    (experiment_statuses or {}).get(experiment["id"], {}).get("status")
                    or ("complete" if experiment["id"] in (completed_experiments or set()) else "ready")
                ),
                "last_run": (
                    (experiment_statuses or {}).get(experiment["id"], {}).get("last_run")
                    or (f"tick {state.tick}" if experiment["id"] in (completed_experiments or set()) else None)
                ),
                "last_run_id": (experiment_statuses or {}).get(experiment["id"], {}).get("run_id"),
                "last_run_status": (experiment_statuses or {}).get(experiment["id"], {}).get("run_status"),
            }
            for experiment in EXPERIMENT_CATALOG
        ],
        "interventions": _interventions(state),
        "minds": _minds(state),
        "posts": _posts(state),
        "events": [event.model_dump(mode="json") for event in state.events[-120:]],
        "communityEvents": [event.model_dump(mode="json") for event in state.community_events[-120:]],
        "networkHistory": [sample.model_dump(mode="json") for sample in state.network_history[-20:]],
        "communityHistory": [sample.model_dump(mode="json") for sample in state.community_history[-20:]],
        "graphMetrics": {
            "degree": metrics.degree,
            "betweenness": metrics.betweenness,
            "averageDegree": metrics.average_degree,
            "networkDensity": metrics.network_density,
        },
        "metricsDetail": metrics.model_dump(mode="json"),
    }


def citizen_view(state: SimulationState, citizen_id: str) -> dict[str, Any]:
    agent = state.agents[citizen_id]
    behavior = state.config.archetypes.get(agent.archetype)
    view = _agent_view(
        agent,
        _influence(agent),
        behavior.model_dump(mode="json") if behavior is not None else None,
    )
    view.update(
        {
            "memories": [memory.model_dump(mode="json") for memory in reversed(agent.memories)],
            "relationships": [
                relationship.model_dump(mode="json")
                for relationship in sorted(agent.relationships.values(), key=lambda item: item.target_id)
            ],
            "decisionTrace": [trace.model_dump(mode="json") for trace in state.traces if trace.agent_id == citizen_id][-70:],
            "recentPosts": [content.model_dump(mode="json") for content in state.contents if content.author_id == citizen_id][-30:],
        }
    )
    return view
