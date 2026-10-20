"""Calculated metrics over simulation state, events, and belief outcomes."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
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
    """Resolve the whole ancestry, including unhydrated legacy snapshots.

    Use the same origin-derived ID as the simulator, without hydrating or
    mutating state. Malformed parent cycles must not hang a metrics request.
    """

    visited: set[str] = set()
    origin: Content | None = None
    current: Content | None = content
    while current is not None:
        if current.id in visited:
            return None
        visited.add(current.id)
        if current.claim_id:
            return current.claim_id
        if current.claim_truth is not None:
            origin = current
        current = content_by_id.get(current.parent_id) if current.parent_id else None
    return f"claim-{origin.id}" if origin else None


def _claims_for_state(state: SimulationState) -> dict[str, Claim]:
    """Return the registry, deriving a read-only compatibility view for old snapshots."""

    content_by_id = {content.id: content for content in state.contents}
    # A partial registry must not hide other claims. Existing lifecycle records
    # remain authoritative; only compatibility claims are reconstructed here.
    derived = dict(state.claims)
    contents_by_claim: dict[str, list[Content]] = defaultdict(list)
    for content in state.contents:
        claim_id = _claim_id_for_content(content, content_by_id)
        if claim_id is None or content.claim_truth is None:
            continue
        contents_by_claim[claim_id].append(content)

    def origin_for(contents: list[Content]) -> Content:
        members = {content.id for content in contents}
        roots = [content for content in contents if not content.parent_id or content.parent_id not in members]
        return min(roots or contents, key=lambda item: (item.created_tick, item.id))

    for claim_id, contents in sorted(contents_by_claim.items()):
        if claim_id not in derived:
            origin = origin_for(contents)
            derived[claim_id] = Claim(
                claim_id=claim_id,
                topic=origin.topic,
                text=origin.text,
                truth_label=origin.claim_truth is True,
                origin_content_id=origin.id,
                origin_author_id=origin.author_id,
                created_tick=origin.created_tick,
            )
        if claim_id not in state.claims:
            claim = derived[claim_id]
            for content in sorted(contents, key=lambda item: (item.created_tick, item.id)):
                if content.kind.value != "fact_check" and not content.is_counterevidence:
                    continue
                if content.claim_truth == claim.truth_label:
                    claim.status = ClaimStatus.CONFIRMED
                elif not claim.truth_label and content.claim_truth:
                    claim.status = ClaimStatus.CORRECTED
                else:
                    claim.status = ClaimStatus.DISPUTED
    return derived


# tick, observer, observed content ID, resolved claim ID
Observation = tuple[int, str, str, str | None]


def _observations(state: SimulationState, content_by_id: dict[str, Content]) -> list[Observation]:
    """Merge explicit exposures and older action observations once per record.

    An action mirrors an exposure when tick/actor/observed content match. Keep
    additional explicit exposures (including repeated observations) and fill
    gaps from action-only history. A content-creating action's result ID is
    never evidence that its author observed the result.
    """

    observations: list[Observation] = []
    explicit_keys: set[tuple[int, str, str]] = set()
    seen: set[str] = set()
    for event in state.content_exposures:
        if event.event_id in seen or event.actor_id not in state.agents or event.tick > state.tick:
            continue
        seen.add(event.event_id)
        observed = content_by_id.get(event.content_id)
        claim_id = _claim_id_for_content(observed, content_by_id) if observed else event.claim_id
        observations.append((event.tick, event.actor_id, event.content_id, claim_id))
        explicit_keys.add((event.tick, event.actor_id, event.content_id))

    seen = set()
    creating_actions = {ActionKind.POST, ActionKind.REPLY, ActionKind.FACT_CHECK}
    for event in state.events:
        if event.event_id in seen or event.actor_id not in state.agents or event.tick > state.tick:
            continue
        seen.add(event.event_id)
        observed_id = event.observed_content_id
        if observed_id is None and event.action not in creating_actions:
            observed_id = event.content_id
        if observed_id is None or (event.tick, event.actor_id, observed_id) in explicit_keys:
            continue
        observed = content_by_id.get(observed_id)
        claim_id = _claim_id_for_content(observed, content_by_id) if observed else event.claim_id
        observations.append((event.tick, event.actor_id, observed_id, claim_id))
    return observations


def analytics_diagnostics(state: SimulationState) -> dict[str, object]:
    """Additive, JSON-safe diagnostics awaiting the public model contract.

    Beliefs are topic-scoped, with no proposition-level position/ground truth.
    Evidence-label frequencies therefore cannot score belief accuracy. A finite
    observation window also cannot establish when a claim will stop spreading.
    Keep these unidentified quantities null rather than inventing measurements.
    """

    content_by_id = {content.id: content for content in state.contents}
    contents: dict[str, list[int]] = defaultdict(list)
    exposures: dict[str, list[int]] = defaultdict(list)
    for content in state.contents:
        claim_id = _claim_id_for_content(content, content_by_id)
        if claim_id is not None:
            contents[claim_id].append(content.created_tick)
    for tick, _, _, claim_id in _observations(state, content_by_id):
        if claim_id is not None:
            exposures[claim_id].append(tick)
    claims = {}
    for claim_id, claim in sorted(_claims_for_state(state).items()):
        lifecycle_ticks = [event.tick for event in claim.lifecycle]
        correction_ticks = [event.tick for event in claim.lifecycle if event.status is ClaimStatus.CORRECTED]
        if not correction_ticks:
            correction_ticks.extend(
                content.created_tick
                for content in state.contents
                if _claim_id_for_content(content, content_by_id) == claim_id
                and (content.kind.value == "fact_check" or content.is_counterevidence)
                and content.claim_truth is not None
                and content.claim_truth != claim.truth_label
            )
        content_tick = max(contents[claim_id], default=claim.created_tick)
        exposure_tick = max(exposures[claim_id], default=None)
        lifecycle_tick = max(lifecycle_ticks, default=None)
        last_tick = max(claim.created_tick, content_tick,
                        exposure_tick if exposure_tick is not None else claim.created_tick,
                        lifecycle_tick if lifecycle_tick is not None else claim.created_tick)
        claims[claim_id] = {
            "first_tick": claim.created_tick,
            "last_content_tick": content_tick,
            "last_exposure_tick": exposure_tick,
            "last_lifecycle_tick": lifecycle_tick,
            "last_activity_tick": last_tick,
            "ticks_since_last_activity": max(0, state.tick - last_tick),
            "observed_lifetime_ticks": max(0, last_tick - claim.created_tick),
            "time_to_first_correction": min(correction_ticks) - claim.created_tick if correction_ticks else None,
            "stopped_spreading": None,
        }
    beliefs = [belief for agent in state.agents.values() for belief in agent.beliefs.values()]
    evidence_count = sum(belief.evidence_for + belief.evidence_against for belief in beliefs)
    return {
        "belief_accuracy": None,
        "accuracy_evaluable_beliefs": 0,
        "evidence_truth_fraction": sum(belief.evidence_for for belief in beliefs) / evidence_count if evidence_count else None,
        "evidence_count": evidence_count,
        "claims": claims,
    }


def claim_metrics(state: SimulationState) -> dict[str, ClaimMetrics]:
    """Calculate bounded claim-specific counts from content and recorded events."""

    claims = _claims_for_state(state)
    content_by_id = {content.id: content for content in state.contents}
    contents_by_claim: dict[str, list[Content]] = defaultdict(list)
    for content in state.contents:
        claim_id = _claim_id_for_content(content, content_by_id)
        if claim_id is not None:
            contents_by_claim[claim_id].append(content)

    exposure_agents: dict[str, set[str]] = defaultdict(set)
    exposure_counts: Counter[str] = Counter()
    exposure_ticks: dict[str, list[int]] = defaultdict(list)
    for tick, actor_id, _, claim_id in _observations(state, content_by_id):
        if claim_id is not None:
            exposure_counts[claim_id] += 1
            exposure_agents[claim_id].add(actor_id)
            exposure_ticks[claim_id].append(tick)

    calculated: dict[str, ClaimMetrics] = {}
    for claim_id in sorted(claims):
        claim = claims[claim_id]
        related = contents_by_claim.get(claim_id, [])
        related.sort(key=lambda content: (content.created_tick, content.id))
        replies = [content for content in related if content.kind.value == "reply"]
        fact_checks = [content for content in related if content.kind.value == "fact_check"]
        corrections = [
            content
            for content in related
            if (content.kind.value == "fact_check" or content.is_counterevidence)
            if content.claim_truth is not None and content.claim_truth != claim.truth_label
        ]
        reshares = [content for content in related if content.is_reshare or content.kind.value == "reply"]
        calculated[claim_id] = ClaimMetrics(
            claim_id=claim_id,
            topic=claim.topic,
            status=claim.status,
            truth_label=claim.truth_label,
            first_tick=min([claim.created_tick, *(content.created_tick for content in related)]),
            last_tick=max([claim.created_tick, *(content.created_tick for content in related),
                           *exposure_ticks[claim_id], *(event.tick for event in claim.lifecycle)]),
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
    agents = sorted(state.agents.values(), key=lambda agent: agent.id)
    all_beliefs = [belief for agent in agents for _, belief in sorted(agent.beliefs.items())]
    all_relationships = [relationship for agent in agents for relationship in agent.relationships.values()]
    average_confidence = _average([belief.confidence for belief in all_beliefs])
    average_trust = _average([relationship.trust for relationship in all_relationships])
    beliefs_by_topic = defaultdict(list)
    for belief in all_beliefs:
        beliefs_by_topic[belief.topic].append(belief)
    topic_polarization = {}
    for topic, beliefs in sorted(beliefs_by_topic.items()):
        mean_stance = _average([belief.stance for belief in beliefs])
        topic_polarization[topic] = _average([abs(belief.stance - mean_stance) for belief in beliefs])
    # Each observed agent/topic belief receives one unit of weight. Missing
    # beliefs are not neutral votes; topic positions are not pooled together.
    polarization = sum(topic_polarization[topic] * len(beliefs)
                       for topic, beliefs in beliefs_by_topic.items()) / max(1, len(all_beliefs))
    evidence_total = sum(belief.evidence_for + belief.evidence_against for belief in all_beliefs)
    evidence_for = sum(belief.evidence_for for belief in all_beliefs)
    active_agents = len({event.actor_id for event in state.events if event.action is not ActionKind.IGNORE})
    non_ignores = len(state.events) - counts[ActionKind.IGNORE]
    engagement_rate = non_ignores / max(1, len(agents) * max(1, state.tick))
    exposure_events = _observations(state, {content.id: content for content in state.contents})
    exposure_rate = len(exposure_events) / max(1, len(agents) * max(1, state.tick))
    reshare_rate = counts[ActionKind.REPLY] / max(1, contents_by_kind["post"])
    counterevidence_ids = {content.id for content in state.contents if content.is_counterevidence}
    correction_events = {actor_id for _, actor_id, content_id, _ in exposure_events if content_id in counterevidence_ids}
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
    calculated_claims = claim_metrics(state)
    last_claim_tick = max((claim.last_tick for claim in calculated_claims.values()), default=state.tick)
    # Legacy wire name: this is ticks since recorded claim activity, not an
    # estimate of future cessation. See analytics_diagnostics for honest names.
    time_until_claim_stops_spreading = max(0, state.tick - last_claim_tick)

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
    for topic, beliefs in sorted(beliefs_by_topic.items()):
        topics[topic]["belief_count"] = len(beliefs)
        topics[topic]["polarization"] = round(topic_polarization[topic], 6)
        total = sum(belief.evidence_for + belief.evidence_against for belief in beliefs)
        topics[topic]["evidence_count"] = total
        if total:
            topics[topic]["evidence_truth_fraction"] = sum(belief.evidence_for for belief in beliefs) / total

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
        # Deprecated compatibility scalar: the schema requires a float. It is
        # the evidence truth fraction, NOT validated belief accuracy. The
        # nullable accuracy/denominator contract is in analytics_diagnostics.
        belief_accuracy=round(evidence_for / evidence_total if evidence_total else 0.0, 6),
        engagement_rate=round(engagement_rate, 6),
        exposure_rate=round(exposure_rate, 6),
        reshare_rate=round(reshare_rate, 6),
        correction_reach=round(correction_reach, 6),
        community_fragmentation=round(community_fragmentation, 6),
        influence_distribution=influence_distribution,
        time_until_claim_stops_spreading=time_until_claim_stops_spreading,
        topic_metrics=dict(sorted(topics.items())),
        claim_metrics=calculated_claims,
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
