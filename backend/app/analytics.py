"""Calculated metrics over simulation state, events, and belief outcomes."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from math import sqrt
from typing import Iterable

from app.models import (
    ActionKind,
    Claim,
    ClaimMetrics,
    ClaimStatus,
    Content,
    Metrics,
    MetricSample,
    SimulationState,
)


MAX_METRIC_HISTORY = 200
MAX_CLAIM_PROVENANCE = 400


def calculate_graph_metrics(
    nodes: Iterable[str],
    edges: Iterable[tuple[str, str]],
) -> dict[str, object]:
    """Calculate degree and normalized betweenness on an undirected projection.

    Relationship records are directed, but this social-topology projection
    treats each directed tie as one undirected connection. The Brandes
    shortest-path algorithm costs O(VE) per calculation for an unweighted
    graph, so this function is intentionally called once per recorded tick.
    Self-loops and unknown endpoints are ignored; output keys are stable.
    """

    node_ids = sorted(set(nodes))
    adjacency = {node_id: set() for node_id in node_ids}
    for source, target in edges:
        if source == target or source not in adjacency or target not in adjacency:
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)

    degree = {node_id: len(adjacency[node_id]) for node_id in node_ids}
    betweenness = {node_id: 0.0 for node_id in node_ids}
    for source in node_ids:
        stack: list[str] = []
        predecessors = {node_id: [] for node_id in node_ids}
        paths = {node_id: 0.0 for node_id in node_ids}
        paths[source] = 1.0
        distance = {node_id: -1 for node_id in node_ids}
        distance[source] = 0
        queue: deque[str] = deque([source])
        while queue:
            current = queue.popleft()
            stack.append(current)
            for neighbor in sorted(adjacency[current]):
                if distance[neighbor] < 0:
                    queue.append(neighbor)
                    distance[neighbor] = distance[current] + 1
                if distance[neighbor] == distance[current] + 1:
                    paths[neighbor] += paths[current]
                    predecessors[neighbor].append(current)
        dependency = {node_id: 0.0 for node_id in node_ids}
        for current in reversed(stack):
            for predecessor in predecessors[current]:
                if paths[current]:
                    dependency[predecessor] += (
                        paths[predecessor] / paths[current]
                    ) * (1.0 + dependency[current])
            if current != source:
                betweenness[current] += dependency[current]

    if len(node_ids) > 2:
        denominator = (len(node_ids) - 1) * (len(node_ids) - 2) / 2
        betweenness = {
            node_id: round(value / 2 / denominator, 6)
            for node_id, value in betweenness.items()
        }
    else:
        betweenness = {node_id: 0.0 for node_id in node_ids}
    edge_count = sum(degree.values()) // 2
    possible_edges = len(node_ids) * (len(node_ids) - 1) // 2
    return {
        "degree": degree,
        "betweenness": betweenness,
        "average_degree": round(sum(degree.values()) / max(1, len(node_ids)), 6),
        "network_density": round(edge_count / max(1, possible_edges), 6),
    }


def graph_metrics_for_state(state: SimulationState) -> dict[str, object]:
    edges = [
        (agent.id, relationship.target_id)
        for agent in sorted(state.agents.values(), key=lambda item: item.id)
        for relationship in sorted(agent.relationships.values(), key=lambda item: item.target_id)
        if relationship.target_id in state.agents
    ]
    return calculate_graph_metrics(sorted(state.agents), edges)


def _claim_id_for_content(content: Content, content_by_id: dict[str, Content]) -> str | None:
    if content.claim_id:
        return content.claim_id
    if content.parent_id and content.parent_id in content_by_id:
        return content_by_id[content.parent_id].claim_id
    return None


def _claims_for_state(state: SimulationState) -> dict[str, Claim]:
    """Return the registry, deriving a read-only compatibility view for old snapshots."""

    if state.claims:
        return state.claims
    content_by_id = {content.id: content for content in state.contents}
    derived: dict[str, Claim] = {}
    for content in state.contents:
        claim_id = _claim_id_for_content(content, content_by_id)
        if claim_id is None or content.claim_truth is None:
            continue
        if claim_id not in derived:
            derived[claim_id] = Claim(
                claim_id=claim_id,
                topic=content.topic,
                text=content.text,
                truth_label=content.claim_truth,
                origin_content_id=content.id,
                origin_author_id=content.author_id,
                created_tick=content.created_tick,
            )
    return derived


def claim_metrics(state: SimulationState) -> dict[str, ClaimMetrics]:
    """Calculate bounded claim-specific counts from content and recorded events."""

    claims = _claims_for_state(state)
    content_by_id = {content.id: content for content in state.contents}
    contents_by_claim: dict[str, list[Content]] = defaultdict(list)
    for content in state.contents:
        claim_id = _claim_id_for_content(content, content_by_id)
        if claim_id is not None and content.claim_truth is not None:
            contents_by_claim[claim_id].append(content)

    exposure_agents: dict[str, set[str]] = defaultdict(set)
    exposure_counts: Counter[str] = Counter()
    for event in state.events:
        claim_id = event.claim_id
        if claim_id is None and event.observed_content_id:
            observed = content_by_id.get(event.observed_content_id)
            claim_id = _claim_id_for_content(observed, content_by_id) if observed else None
        if claim_id is not None and event.observed_content_id:
            exposure_counts[claim_id] += 1
            exposure_agents[claim_id].add(event.actor_id)

    calculated: dict[str, ClaimMetrics] = {}
    for claim_id in sorted(claims):
        claim = claims[claim_id]
        related = contents_by_claim.get(claim_id, [])
        related.sort(key=lambda content: (content.created_tick, content.id))
        replies = [content for content in related if content.kind.value == "reply"]
        fact_checks = [content for content in related if content.kind.value == "fact_check"]
        corrections = [
            content
            for content in fact_checks
            if content.claim_truth is not None and content.claim_truth != claim.truth_label
        ]
        reshares = [content for content in related if content.is_reshare or content.kind.value == "reply"]
        calculated[claim_id] = ClaimMetrics(
            claim_id=claim_id,
            topic=claim.topic,
            status=claim.status,
            truth_label=claim.truth_label,
            first_tick=min((content.created_tick for content in related), default=claim.created_tick),
            last_tick=max((content.created_tick for content in related), default=claim.created_tick),
            exposures=exposure_counts[claim_id],
            unique_exposed_agents=len(exposure_agents[claim_id]),
            reshares=len(reshares),
            unique_resharers=len({content.author_id for content in reshares}),
            replies=len(replies),
            fact_checks=len(fact_checks),
            corrections=len(corrections),
        )
    return calculated


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
    exposure_events = [event for event in state.events if event.content_id]
    exposure_rate = len(exposure_events) / max(1, len(agents) * max(1, state.tick))
    reshare_rate = counts[ActionKind.REPLY] / max(1, contents_by_kind["post"])
    counterevidence_ids = {content.id for content in state.contents if content.is_counterevidence}
    correction_events = {event.actor_id for event in state.events if event.content_id in counterevidence_ids}
    correction_reach = len(correction_events) / max(1, len(agents))
    memberships = [len(community.member_ids) for community in state.communities.values()]
    community_fragmentation = 1 - max(memberships, default=0) / max(1, sum(memberships))
    action_influence = {
        agent.id: sum(count for kind, count in agent.action_counts.items() if kind != ActionKind.IGNORE.value)
        + len(agent.following)
        for agent in agents
    }
    influence_total = sum(action_influence.values())
    influence_distribution = {
        "top_10_percent": round(sum(sorted(action_influence.values(), reverse=True)[: max(1, len(agents) // 10)]) / max(1, influence_total), 6),
        "bottom_50_percent": round(sum(sorted(action_influence.values())[: max(1, len(agents) // 2)]) / max(1, influence_total), 6),
    }
    claim_ticks = [content.created_tick for content in state.contents if content.claim_truth is not None]
    last_claim_tick = max(claim_ticks, default=0)
    time_until_claim_stops_spreading = max(0, state.tick - last_claim_tick) if claim_ticks else 0

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

    graph = graph_metrics_for_state(state)
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
        exposure_rate=round(exposure_rate, 6),
        reshare_rate=round(reshare_rate, 6),
        correction_reach=round(correction_reach, 6),
        community_fragmentation=round(community_fragmentation, 6),
        influence_distribution=influence_distribution,
        time_until_claim_stops_spreading=time_until_claim_stops_spreading,
        topic_metrics=dict(sorted(topics.items())),
        claim_metrics=claim_metrics(state),
        degree=graph["degree"],
        betweenness=graph["betweenness"],
        average_degree=float(graph["average_degree"]),
        network_density=float(graph["network_density"]),
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
        "exposure_rate",
        "reshare_rate",
        "correction_reach",
        "community_fragmentation",
        "time_until_claim_stops_spreading",
        "average_degree",
        "network_density",
    )
    return {name: round(float(getattr(treatment, name)) - float(getattr(control, name)), 6) for name in names}


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def record_metric_sample(state: SimulationState) -> MetricSample:
    """Record one replace-on-tick sample, retaining only the bounded recent window."""

    sample = MetricSample(tick=state.tick, metrics=calculate_metrics(state))
    history = [item for item in state.metric_history if item.tick != state.tick]
    history.append(sample)
    state.metric_history = sorted(history, key=lambda item: item.tick)[-MAX_METRIC_HISTORY:]
    return sample
