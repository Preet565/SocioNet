"""The OBSERVE-to-RECORD loop for a deterministic population of citizens."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents import TOPICS, create_citizens
from app.analytics import MAX_CLAIM_PROVENANCE, graph_metrics_for_state, record_metric_sample
from app.causality import (
    action_ref,
    append_causal_link,
    belief_update_ref,
    content_ref,
    decision_ref,
    ensure_causal_registry,
    exposure_ref,
    intervention_ref,
    record_content_provenance,
    semantic_content_key,
    semantic_event_signature,
)
from app.beliefs import learn_from_content
from app.communities import (
    create_communities,
    evolve_communities,
    join_community,
    leave_community,
    record_community_event,
)
from app.memory import remember, retrieve
from app.models import (
    ActionEvent,
    ActionKind,
    Agent,
    BeliefUpdateEvent,
    Claim,
    ClaimLifecycleEvent,
    ClaimProvenanceRecord,
    ClaimStatus,
    Content,
    ContentExposureEvent,
    ContentKind,
    CommunityEventKind,
    DecisionTrace,
    LifecyclePhase,
    CommunityHistorySample,
    NetworkHistorySample,
    NetworkTie,
    SimulationConfig,
    SimulationState,
)
from app.relationships import (
    evolve_relationships,
    initialize_relationships,
    interaction_probability,
    record_interaction,
    relationship_for,
    set_following,
)
from app.utils import rng_for


@dataclass
class ActionResult:
    action: ActionKind
    target_id: str | None = None
    content_id: str | None = None
    topic: str | None = None
    details: str = ""
    parent_event_ids: list[str] | None = None
    provenance_event_ids: list[str] | None = None


def create_initial_state(
    seed: int = 20261016,
    *,
    count: int | None = None,
    config: SimulationConfig | None = None,
) -> SimulationState:
    if config is None:
        config = SimulationConfig(population=count if count is not None else 100)
    elif count is not None and config.population != count:
        config = SimulationConfig.model_validate({**config.model_dump(), "population": count})
    agents = create_citizens(seed, count=config.population, config=config)
    communities = create_communities(seed, agents, config=config)
    initialize_relationships(seed, agents, communities, config)
    state = SimulationState(
        seed=seed,
        agents=agents,
        communities=communities,
        config=config,
    )
    ensure_claim_registry(state)
    ensure_causal_registry(state)
    sample = record_metric_sample(state)
    record_dynamic_history(
        state,
        graph={
            "degree": sample.metrics.degree,
            "betweenness": sample.metrics.betweenness,
        },
    )
    return state


def record_dynamic_history(state: SimulationState, *, graph: dict[str, object] | None = None) -> None:
    """Record bounded topology and membership snapshots in stable order."""

    graph = graph or graph_metrics_for_state(state)
    ties = [
        NetworkTie(
            source_id=agent.id,
            target_id=relationship.target_id,
            affinity=relationship.affinity,
            trust=relationship.trust,
            follows=relationship.follows,
        )
        for agent in sorted(state.agents.values(), key=lambda item: item.id)
        for relationship in sorted(agent.relationships.values(), key=lambda item: item.target_id)
        if relationship.target_id in state.agents
    ]
    network_sample = NetworkHistorySample(
        tick=state.tick,
        ties=ties,
        degree=graph["degree"],
        betweenness=graph["betweenness"],
    )
    state.network_history = [sample for sample in state.network_history if sample.tick != state.tick]
    state.network_history.append(network_sample)
    state.network_history = sorted(state.network_history, key=lambda sample: sample.tick)[-state.config.history_limit :]
    memberships = {
        community.id: sorted(community.member_ids)
        for community in sorted(state.communities.values(), key=lambda item: item.id)
    }
    community_sample = CommunityHistorySample(tick=state.tick, memberships=memberships)
    state.community_history = [sample for sample in state.community_history if sample.tick != state.tick]
    state.community_history.append(community_sample)
    state.community_history = sorted(state.community_history, key=lambda sample: sample.tick)[-state.config.history_limit :]


def stable_claim_id(content_id: str) -> str:
    """Claim IDs are derived from deterministic origin content IDs and survive forks."""

    return f"claim-{content_id}"


def _latest_claim_content(state: SimulationState, topic: str | None) -> Content | None:
    matching = [
        content
        for content in state.contents
        if content.claim_id and (topic is None or content.topic == topic)
    ]
    return max(matching, key=lambda content: (content.created_tick, content.id), default=None)


def _claim_status_for_evidence(claim: Claim, content: Content) -> tuple[ClaimStatus, str] | None:
    if content.kind is not ContentKind.FACT_CHECK and not content.is_counterevidence:
        return None
    if content.claim_truth is None:
        return None
    if content.claim_truth != claim.truth_label:
        return (
            ClaimStatus.CORRECTED if claim.truth_label is False and content.claim_truth is True else ClaimStatus.DISPUTED,
            "evidence contradicts the origin claim",
        )
    return ClaimStatus.CONFIRMED, "fact-check agrees with the origin claim"


def _register_claim_content(
    state: SimulationState,
    content: Content,
    *,
    claim_id: str,
    source: str,
    intervention_id: str | None,
) -> None:
    claim = state.claims.get(claim_id)
    if claim is None:
        claim = Claim(
            claim_id=claim_id,
            topic=content.topic,
            text=content.text,
            truth_label=bool(content.claim_truth),
            origin_content_id=content.id,
            origin_author_id=content.author_id,
            created_tick=content.created_tick,
            origin_source=source,
            origin_intervention_id=intervention_id,
            lifecycle=[
                ClaimLifecycleEvent(
                    status=ClaimStatus.ACTIVE,
                    tick=content.created_tick,
                    content_id=content.id,
                    actor_id=content.author_id,
                    reason="claim originated",
                )
            ],
        )
        state.claims[claim_id] = claim
    elif any(record.content_id == content.id for record in claim.provenance):
        return
    claim.provenance.append(
        ClaimProvenanceRecord(
            content_id=content.id,
            kind=content.kind,
            actor_id=content.author_id,
            tick=content.created_tick,
            parent_content_id=content.parent_id,
            source=source,
            intervention_id=intervention_id,
            parent_event_ids=[*content.parent_event_ids, *content.provenance_event_ids],
        )
    )
    claim.provenance = claim.provenance[-MAX_CLAIM_PROVENANCE:]
    transition = _claim_status_for_evidence(claim, content)
    if transition and transition[0] is not claim.status:
        claim.status = transition[0]
        claim.lifecycle.append(
            ClaimLifecycleEvent(
                status=transition[0],
                tick=content.created_tick,
                content_id=content.id,
                actor_id=content.author_id,
                reason=transition[1],
            )
        )
        claim.lifecycle = claim.lifecycle[-MAX_CLAIM_PROVENANCE:]


def ensure_claim_registry(state: SimulationState) -> None:
    """Hydrate additive claim fields when loading a pre-Phase-2 snapshot."""

    content_by_id = {content.id: content for content in state.contents}
    for content in sorted(state.contents, key=lambda item: (item.created_tick, item.id)):
        if content.claim_truth is None:
            continue
        if content.claim_id is None:
            parent = content_by_id.get(content.parent_id) if content.parent_id else None
            content.claim_id = parent.claim_id if parent and parent.claim_id else stable_claim_id(content.id)
        _register_claim_content(
            state,
            content,
            claim_id=content.claim_id,
            source=content.provenance,
            intervention_id=content.intervention_id,
        )


def append_content(
    state: SimulationState,
    *,
    author_id: str,
    kind: ContentKind,
    topic: str,
    text: str,
    claim_truth: bool | None,
    parent_id: str | None = None,
    credibility: float = 0.5,
    is_counterevidence: bool = False,
    claim_id: str | None = None,
    provenance: str = "organic",
    intervention_id: str | None = None,
    is_reshare: bool = False,
    parent_event_ids: list[str] | None = None,
    provenance_event_ids: list[str] | None = None,
) -> Content:
    content_id = f"content-{len(state.contents) + 1:06d}"
    parent = next((item for item in reversed(state.contents) if item.id == parent_id), None) if parent_id else None
    resolved_claim_id = claim_id or (parent.claim_id if parent else None)
    if resolved_claim_id is None and claim_truth is not None:
        resolved_claim_id = stable_claim_id(content_id)
    content = Content(
        id=content_id,
        author_id=author_id,
        kind=kind,
        topic=topic,
        text=text[:280],
        claim_truth=claim_truth,
        parent_id=parent_id,
        created_tick=state.tick,
        credibility=max(0.0, min(1.0, credibility)),
        is_counterevidence=is_counterevidence,
        claim_id=resolved_claim_id,
        is_reshare=is_reshare,
        provenance=provenance,
        intervention_id=intervention_id,
        parent_event_ids=parent_event_ids or [],
        provenance_event_ids=provenance_event_ids or [],
    )
    state.contents.append(content)
    if resolved_claim_id is not None and claim_truth is not None:
        _register_claim_content(
            state,
            content,
            claim_id=resolved_claim_id,
            source=provenance,
            intervention_id=intervention_id,
        )
    record_content_provenance(state, content)
    return content


class Simulation:
    """Mutable façade around a serializable ``SimulationState``."""

    def __init__(
        self,
        seed: int = 20261016,
        state: SimulationState | None = None,
        config: SimulationConfig | None = None,
    ) -> None:
        self.state = state or create_initial_state(seed, config=config)
        ensure_claim_registry(self.state)
        ensure_causal_registry(self.state)
        current_sample = next(
            (sample for sample in self.state.metric_history if sample.tick == self.state.tick),
            None,
        )
        if current_sample is None or (self.state.agents and not current_sample.metrics.degree):
            sample = record_metric_sample(self.state)
        else:
            sample = current_sample
        if not any(sample.tick == self.state.tick for sample in self.state.network_history):
            record_dynamic_history(
                self.state,
                graph={
                    "degree": sample.metrics.degree,
                    "betweenness": sample.metrics.betweenness,
                },
            )
        if not any(sample.tick == self.state.tick for sample in self.state.community_history):
            record_dynamic_history(self.state)

    def step(self) -> SimulationState:
        self.state.tick += 1
        self._apply_due_interventions()
        for agent_id in sorted(self.state.agents):
            if agent_id in self.state.suppressed_citizen_ids:
                continue
            self._run_agent_lifecycle(self.state.agents[agent_id])
        evolve_relationships(
            self.state.seed,
            self.state.tick,
            self.state.agents,
            self.state.communities,
            self.state.config,
            suppressed_relationships={
                (item.source_agent_id, item.target_agent_id)
                for item in self.state.suppressed_relationships
            },
            suppressed_agents=set(self.state.suppressed_citizen_ids),
        )
        evolve_communities(self.state)
        sample = record_metric_sample(self.state)
        record_dynamic_history(
            self.state,
            graph={
                "degree": sample.metrics.degree,
                "betweenness": sample.metrics.betweenness,
            },
        )
        return self.state

    def run(self, ticks: int) -> SimulationState:
        for _ in range(ticks):
            self.step()
        return self.state

    def _apply_due_interventions(self) -> None:
        due = [item for item in self.state.scheduled_interventions if item.apply_at_tick <= self.state.tick]
        if not due:
            return
        from app.interventions import apply_now

        for scheduled in sorted(due, key=lambda item: (item.apply_at_tick, item.intervention_id)):
            apply_now(self.state, scheduled.intervention, intervention_id=scheduled.intervention_id)
            for record in self.state.intervention_history:
                if record.intervention_id == scheduled.intervention_id:
                    record.applied_tick = self.state.tick
                    record.status = "applied"
            self.state.scheduled_interventions.remove(scheduled)

    def _run_agent_lifecycle(self, agent: Agent) -> None:
        rng = rng_for(self.state.seed, "decision", self.state.tick, agent.id)
        content = self._observe(agent, rng)
        exposure = self._record_exposure(agent, content) if content else None
        observe_trace = self._trace(agent, LifecyclePhase.OBSERVE, content_id=content.id if content else None, details={
            "visible": content is not None,
            "content_count": len(self.state.contents),
        }, parent_event_ids=[exposure_ref(exposure.event_id)] if exposure else [], provenance_event_ids=content.provenance_event_ids if content else [])

        memories = retrieve(agent, content.topic if content else None, limit=5)
        retrieve_trace = self._trace(agent, LifecyclePhase.RETRIEVE, content_id=content.id if content else None, details={
            "memory_ids": [memory.memory_id for memory in memories],
            "matched_topic": content.topic if content else None,
        }, parent_event_ids=[decision_ref(observe_trace.trace_id)])

        action = self._form_intent(agent, content, rng)
        intent_trace = self._trace(agent, LifecyclePhase.FORM_INTENT, action=action, content_id=content.id if content else None, details={
            "candidate": action.value,
            "curiosity": agent.curiosity,
        }, parent_event_ids=[decision_ref(retrieve_trace.trace_id)])

        evaluation = self._evaluate(agent, content, action)
        evaluation_trace = self._trace(agent, LifecyclePhase.EVALUATE, action=action, content_id=content.id if content else None, details=evaluation, parent_event_ids=[decision_ref(intent_trace.trace_id)])

        prior_update = next((item for item in reversed(self.state.belief_updates) if item.agent_id == agent.id), None)
        action_parents = [decision_ref(evaluation_trace.trace_id)]
        if exposure:
            action_parents.append(exposure_ref(exposure.event_id))
        if prior_update:
            action_parents.append(belief_update_ref(prior_update.update_id))
        result = self._act(agent, content, action, rng, parent_event_ids=action_parents)
        act_trace = self._trace(agent, LifecyclePhase.ACT, action=result.action, content_id=result.content_id, details={
            "target_id": result.target_id,
            "topic": result.topic,
            "result": result.details,
        }, parent_event_ids=[decision_ref(evaluation_trace.trace_id)], provenance_event_ids=content.provenance_event_ids if content else [])

        update = self._update(agent, content, result, parent_event_ids=[decision_ref(act_trace.trace_id), *([exposure_ref(exposure.event_id)] if exposure else [])])
        update_trace = self._trace(agent, LifecyclePhase.UPDATE, action=result.action, content_id=result.content_id, details={
            "memory_count": len(agent.memories),
            "relationship_count": len(agent.relationships),
            "belief_topics": len(agent.beliefs),
        }, parent_event_ids=[decision_ref(act_trace.trace_id), *([belief_update_ref(update.update_id)] if update else [])])

        agent.last_action = result.action
        agent.action_counts[result.action.value] = agent.action_counts.get(result.action.value, 0) + 1
        event = ActionEvent(
            event_id=f"event-{self.state.tick:04d}-{len(self.state.events) + 1:06d}",
            tick=self.state.tick,
            actor_id=agent.id,
            action=result.action,
            target_id=result.target_id,
            content_id=result.content_id,
            observed_content_id=content.id if content else None,
            claim_id=content.claim_id if content and content.claim_id else self._claim_id_for_result(result),
            topic=result.topic,
            details=result.details,
            parent_event_ids=[*(result.parent_event_ids or []), decision_ref(act_trace.trace_id)],
            provenance_event_ids=content.provenance_event_ids if content else [],
        )
        event.semantic_key = f"action:{_semantic_digest(semantic_event_signature(event, self.state))}"
        self.state.events.append(event)
        for parent in event.parent_event_ids:
            append_causal_link(self.state, parent, action_ref(event.event_id), "caused_action", tick=self.state.tick)
        for parent in event.provenance_event_ids:
            append_causal_link(self.state, parent, action_ref(event.event_id), "action_provenance", tick=self.state.tick)
        if result.content_id:
            append_causal_link(self.state, action_ref(event.event_id), content_ref(result.content_id), "created_content", tick=self.state.tick)
        if update:
            append_causal_link(self.state, action_ref(event.event_id), belief_update_ref(update.update_id), "updated_belief", tick=self.state.tick)
        record_trace = self._trace(agent, LifecyclePhase.RECORD, action=result.action, content_id=result.content_id, details={
            "event_id": event.event_id,
            "recorded": True,
        }, parent_event_ids=[action_ref(event.event_id), *([belief_update_ref(update.update_id)] if update else [])], provenance_event_ids=event.provenance_event_ids)
        append_causal_link(self.state, action_ref(event.event_id), decision_ref(record_trace.trace_id), "recorded_as", tick=self.state.tick)

    def _claim_id_for_result(self, result: ActionResult) -> str | None:
        if result.content_id is None:
            return None
        created = next((item for item in reversed(self.state.contents) if item.id == result.content_id), None)
        return created.claim_id if created else None

    def _observe(self, agent: Agent, rng) -> Content | None:
        visible = [
            content
            for content in self.state.contents[-self.state.config.observation_window:]
            if content.author_id != agent.id
            and not content.suppressed
            and content.id not in self.state.suppressed_post_ids
            and not self._relationship_suppressed(agent.id, content.author_id)
        ]
        if not visible:
            return None
        related = [content for content in visible if content.topic in agent.interests]
        accessible: list[Content] = []
        for candidate in visible:
            author = self.state.agents.get(candidate.author_id)
            if author is None:
                continue
            relationship = agent.relationships.get(author.id)
            if relationship and relationship.follows:
                accessible.append(candidate)
                continue
            probability = interaction_probability(agent, author, config=self.state.config)
            cross_rng = rng_for(self.state.seed, "cross-community", self.state.tick, agent.id, candidate.id)
            if cross_rng.random() < probability:
                accessible.append(candidate)
        pool = [candidate for candidate in related if candidate in accessible] or accessible or related or visible
        return pool[rng.randrange(len(pool))]

    def _relationship_suppressed(self, source_agent_id: str, target_agent_id: str) -> bool:
        return any(
            item.source_agent_id == source_agent_id and item.target_agent_id == target_agent_id
            for item in self.state.suppressed_relationships
        )

    def _record_exposure(self, agent: Agent, content: Content):
        ordinal = sum(
            1
            for item in self.state.content_exposures
            if item.tick == self.state.tick and item.actor_id == agent.id
        )
        exposure_id = f"exposure-{self.state.tick:04d}-{agent.id}-{ordinal:02d}"
        content_by_id = {item.id: item for item in self.state.contents}
        exposure = ContentExposureEvent(
            event_id=exposure_id,
            semantic_key=f"exposure:{self.state.tick}:{agent.id}:{semantic_content_key(content, content_by_id)}",
            tick=self.state.tick,
            actor_id=agent.id,
            content_id=content.id,
            claim_id=content.claim_id,
            topic=content.topic,
            parent_event_ids=[content_ref(content.id), *content.parent_event_ids],
            provenance_event_ids=content.provenance_event_ids,
        )
        self.state.content_exposures.append(exposure)
        for parent in exposure.parent_event_ids:
            append_causal_link(self.state, parent, exposure_ref(exposure.event_id), "content_exposed", tick=self.state.tick)
        for parent in content.provenance_event_ids:
            append_causal_link(self.state, parent, exposure_ref(exposure.event_id), "exposure_provenance", tick=self.state.tick)
        return exposure

    def _form_intent(self, agent: Agent, content: Content | None, rng) -> ActionKind:
        behavior = self.state.config.archetypes.get(
            agent.archetype,
            self.state.config.archetypes["civic_participant"],
        )
        if content is None:
            post_probability = self.state.config.post_probability * (0.45 + agent.curiosity * 0.55) * behavior.post_bias
            return ActionKind.POST if rng.random() < min(1.0, post_probability) else ActionKind.IGNORE
        if rng.random() < min(
            1.0,
            self.state.config.post_probability * (0.45 + agent.curiosity * 0.55) * behavior.post_bias,
        ):
            return ActionKind.POST
        if content.claim_truth is not None and rng.random() < min(
            1.0,
            (self.state.config.fact_check_probability + agent.skepticism * 0.18) * behavior.fact_check_bias,
        ):
            return ActionKind.FACT_CHECK
        if rng.random() < min(1.0, self.state.config.reply_probability * behavior.reply_bias):
            return ActionKind.REPLY
        target_id = content.author_id
        relationship = agent.relationships.get(target_id)
        if relationship and relationship.follows and rng.random() < min(1.0, 0.16 * behavior.unfollow_bias):
            return ActionKind.UNFOLLOW
        if (not relationship or not relationship.follows) and rng.random() < min(1.0, 0.2 * behavior.follow_bias):
            return ActionKind.FOLLOW
        if rng.random() < min(1.0, 0.18 * behavior.join_bias):
            if len(agent.community_ids) > 1 and rng.random() < min(1.0, behavior.leave_bias):
                return ActionKind.LEAVE_COMMUNITY
            if len(agent.community_ids) < self.state.config.community_evolution.max_communities_per_agent:
                return ActionKind.JOIN_COMMUNITY
        return ActionKind.IGNORE

    def _evaluate(self, agent: Agent, content: Content | None, action: ActionKind) -> dict[str, object]:
        if content is None:
            return {"credibility": 0.0, "trust": agent.trust_baseline, "reason": "no prior content"}
        relationship = agent.relationships.get(content.author_id)
        trust = relationship.trust if relationship else agent.trust_baseline
        return {
            "credibility": round(content.credibility, 4),
            "trust": round(trust, 4),
            "skepticism": agent.skepticism,
            "selected_action": action.value,
        }

    def _act(self, agent: Agent, content: Content | None, action: ActionKind, rng, *, parent_event_ids: list[str]) -> ActionResult:
        if action is ActionKind.POST:
            topic = agent.interests[rng.randrange(len(agent.interests))]
            truth = rng.random() >= 0.22
            created = append_content(
                self.state,
                author_id=agent.id,
                kind=ContentKind.POST,
                topic=topic,
                text=f"{agent.name} shares a fictional observation about {topic}.",
                claim_truth=truth,
                credibility=0.48 + agent.trust_baseline * 0.35,
                parent_event_ids=parent_event_ids,
            )
            return ActionResult(ActionKind.POST, content_id=created.id, topic=topic, details="published a fictional post")
        if content is None:
            return ActionResult(ActionKind.IGNORE, details="nothing available to act on")
        target_id = content.author_id
        if action is ActionKind.REPLY:
            created = append_content(
                self.state,
                author_id=agent.id,
                kind=ContentKind.REPLY,
                topic=content.topic,
                text=f"{agent.name} replies with a fictional perspective on {content.topic}.",
                claim_truth=content.claim_truth,
                parent_id=content.id,
                credibility=0.42 + agent.trust_baseline * 0.3,
                is_reshare=content.claim_id is not None,
                parent_event_ids=[*parent_event_ids, content_ref(content.id)],
            )
            content.likes += 1
            return ActionResult(ActionKind.REPLY, target_id=target_id, content_id=created.id, topic=content.topic, details="replied to content")
        if action is ActionKind.FACT_CHECK:
            content.fact_checked = True
            content.credibility = min(1.0, content.credibility + 0.08)
            created = append_content(
                self.state,
                author_id=agent.id,
                kind=ContentKind.FACT_CHECK,
                topic=content.topic,
                text=f"{agent.name} fact-checks a fictional claim about {content.topic}.",
                claim_truth=content.claim_truth,
                parent_id=content.id,
                credibility=0.9,
                parent_event_ids=[*parent_event_ids, content_ref(content.id)],
            )
            return ActionResult(ActionKind.FACT_CHECK, target_id=target_id, content_id=created.id, topic=content.topic, details="recorded a fact-check")
        if action is ActionKind.FOLLOW:
            relationship = set_following(
                agent,
                target_id,
                True,
                max_following=self.state.config.max_following,
            )
            details = "followed the author" if relationship.follows else "following limit reached"
            return ActionResult(ActionKind.FOLLOW, target_id=target_id, content_id=content.id, topic=content.topic, details=details)
        if action is ActionKind.UNFOLLOW:
            set_following(agent, target_id, False)
            return ActionResult(ActionKind.UNFOLLOW, target_id=target_id, content_id=content.id, topic=content.topic, details="unfollowed the author")
        if action in {ActionKind.JOIN_COMMUNITY, ActionKind.LEAVE_COMMUNITY}:
            community = self._community_for(agent, content.topic, action)
            if community:
                changed = (
                    join_community(
                        agent,
                        community,
                        max_communities=self.state.config.community_evolution.max_communities_per_agent,
                    )
                    if action is ActionKind.JOIN_COMMUNITY
                    else leave_community(agent, community)
                )
                if changed:
                    record_community_event(
                        self.state,
                        kind=(
                            CommunityEventKind.JOIN
                            if action is ActionKind.JOIN_COMMUNITY
                            else CommunityEventKind.LEAVE
                        ),
                        community_id=community.id,
                        agent_id=agent.id,
                        details=f"{action.value} decision applied",
                    )
                return ActionResult(action, target_id=community.id, content_id=content.id, topic=community.topic, details=f"community membership changed={changed}")
        return ActionResult(ActionKind.IGNORE, target_id=target_id, content_id=content.id, topic=content.topic, details="ignored the content")

    def _community_for(self, agent: Agent, topic: str, action: ActionKind):
        matching = sorted((community for community in self.state.communities.values() if community.topic == topic), key=lambda item: item.id)
        if action is ActionKind.JOIN_COMMUNITY:
            candidates = [community for community in matching if community.id not in agent.community_ids]
            return (candidates or sorted(self.state.communities.values(), key=lambda item: item.id))[0]
        candidates = [community for community in self.state.communities.values() if community.id in agent.community_ids]
        candidates.sort(key=lambda item: item.id)
        return candidates[-1] if candidates else None

    def _update(self, agent: Agent, content: Content | None, result: ActionResult, *, parent_event_ids: list[str]) -> object | None:
        update = None
        if content is not None:
            previous = agent.beliefs.get(content.topic)
            prior_stance = previous.stance if previous else None
            prior_confidence = previous.confidence if previous else None
            relationship = agent.relationships.get(content.author_id)
            trust = relationship.trust if relationship else agent.trust_baseline
            learn_from_content(agent, content, trust=trust, skepticism=agent.skepticism)
            belief = agent.beliefs.get(content.topic)
            if belief is not None:
                update = BeliefUpdateEvent(
                    update_id=f"belief-update-{self.state.tick:04d}-{agent.id}-{content.topic.replace(' ', '-')}",
                    semantic_key=f"belief:{self.state.tick}:{agent.id}:{content.topic}",
                    tick=self.state.tick,
                    agent_id=agent.id,
                    topic=content.topic,
                    content_id=content.id,
                    claim_id=content.claim_id,
                    prior_stance=prior_stance,
                    prior_confidence=prior_confidence,
                    stance=belief.stance,
                    confidence=belief.confidence,
                    evidence_for=belief.evidence_for,
                    evidence_against=belief.evidence_against,
                    parent_event_ids=parent_event_ids,
                    provenance_event_ids=content.provenance_event_ids,
                )
                self.state.belief_updates.append(update)
                for parent in parent_event_ids:
                    append_causal_link(self.state, parent, belief_update_ref(update.update_id), "informed_belief_update", tick=self.state.tick)
            positive = result.action in {ActionKind.REPLY, ActionKind.FOLLOW, ActionKind.FACT_CHECK}
            if (
                result.action is not ActionKind.POST
                and content.author_id != agent.id
                and not self._relationship_suppressed(agent.id, content.author_id)
            ):
                record_interaction(agent, content.author_id, positive=positive, tick=self.state.tick)
            remember(
                agent,
                content=content,
                kind="observation" if result.action is ActionKind.IGNORE else result.action.value,
                summary=content.text,
                tick=self.state.tick,
                credibility=content.credibility,
                valence=1.0 if positive else -0.1,
                limit=self.state.config.memory_limit,
            )
        if result.action in {ActionKind.POST, ActionKind.REPLY, ActionKind.FACT_CHECK} and result.content_id:
            created = next(item for item in reversed(self.state.contents) if item.id == result.content_id)
            remember(
                agent,
                content=created,
                kind="created",
                summary=created.text,
                tick=self.state.tick,
                credibility=created.credibility,
                valence=0.2,
                limit=self.state.config.memory_limit,
            )
        return update

    def _trace(
        self,
        agent: Agent,
        phase: LifecyclePhase,
        *,
        action: ActionKind | None = None,
        content_id: str | None = None,
        details: dict[str, object],
        parent_event_ids: list[str] | None = None,
        provenance_event_ids: list[str] | None = None,
    ) -> DecisionTrace:
        content = next((item for item in reversed(self.state.contents) if item.id == content_id), None) if content_id else None
        trace = DecisionTrace(
                trace_id=f"trace-{self.state.tick:04d}-{agent.id}-{phase.value.lower().replace(' ', '-')}",
                tick=self.state.tick,
                agent_id=agent.id,
                phase=phase,
                action=action,
                content_id=content_id,
                details=details,
                semantic_key=f"decision:{self.state.tick}:{agent.id}:{phase.value}:{action.value if action else ''}:{semantic_content_key(content, {item.id: item for item in self.state.contents}) if content else ''}",
                parent_event_ids=parent_event_ids or [],
                provenance_event_ids=provenance_event_ids or [],
            )
        self.state.traces.append(trace)
        for parent in trace.parent_event_ids:
            append_causal_link(self.state, parent, decision_ref(trace.trace_id), "decision_dependency", tick=self.state.tick)
        for parent in trace.provenance_event_ids:
            append_causal_link(self.state, parent, decision_ref(trace.trace_id), "decision_provenance", tick=self.state.tick)
        return trace


def _semantic_digest(value: object) -> str:
    import hashlib

    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:16]


def run_seeded(seed: int, ticks: int = 1) -> SimulationState:
    return Simulation(seed=seed).run(ticks)
