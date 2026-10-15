"""The OBSERVE-to-RECORD loop for a deterministic population of citizens."""

from __future__ import annotations

from dataclasses import dataclass

from app.agents import TOPICS, create_citizens
from app.beliefs import learn_from_content
from app.communities import create_communities, join_community, leave_community
from app.memory import remember, retrieve
from app.models import (
    ActionEvent,
    ActionKind,
    Agent,
    Content,
    ContentKind,
    DecisionTrace,
    LifecyclePhase,
    SimulationConfig,
    SimulationState,
)
from app.relationships import record_interaction, set_following
from app.utils import rng_for


@dataclass
class ActionResult:
    action: ActionKind
    target_id: str | None = None
    content_id: str | None = None
    topic: str | None = None
    details: str = ""


def create_initial_state(seed: int = 20261016, *, count: int = 100) -> SimulationState:
    agents = create_citizens(seed, count=count)
    communities = create_communities(seed, agents)
    return SimulationState(
        seed=seed,
        agents=agents,
        communities=communities,
        config=SimulationConfig(),
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
) -> Content:
    content = Content(
        id=f"content-{len(state.contents) + 1:06d}",
        author_id=author_id,
        kind=kind,
        topic=topic,
        text=text[:280],
        claim_truth=claim_truth,
        parent_id=parent_id,
        created_tick=state.tick,
        credibility=max(0.0, min(1.0, credibility)),
        is_counterevidence=is_counterevidence,
    )
    state.contents.append(content)
    return content


class Simulation:
    """Mutable façade around a serializable ``SimulationState``."""

    def __init__(self, seed: int = 20261016, state: SimulationState | None = None) -> None:
        self.state = state or create_initial_state(seed)

    def step(self) -> SimulationState:
        self.state.tick += 1
        self._apply_due_interventions()
        for agent_id in sorted(self.state.agents):
            self._run_agent_lifecycle(self.state.agents[agent_id])
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
            self.state.scheduled_interventions.remove(scheduled)

    def _run_agent_lifecycle(self, agent: Agent) -> None:
        rng = rng_for(self.state.seed, "decision", self.state.tick, agent.id)
        content = self._observe(agent, rng)
        self._trace(agent, LifecyclePhase.OBSERVE, content_id=content.id if content else None, details={
            "visible": content is not None,
            "content_count": len(self.state.contents),
        })

        memories = retrieve(agent, content.topic if content else None, limit=5)
        self._trace(agent, LifecyclePhase.RETRIEVE, content_id=content.id if content else None, details={
            "memory_ids": [memory.memory_id for memory in memories],
            "matched_topic": content.topic if content else None,
        })

        action = self._form_intent(agent, content, rng)
        self._trace(agent, LifecyclePhase.FORM_INTENT, action=action, content_id=content.id if content else None, details={
            "candidate": action.value,
            "curiosity": agent.curiosity,
        })

        evaluation = self._evaluate(agent, content, action)
        self._trace(agent, LifecyclePhase.EVALUATE, action=action, content_id=content.id if content else None, details=evaluation)

        result = self._act(agent, content, action, rng)
        self._trace(agent, LifecyclePhase.ACT, action=result.action, content_id=result.content_id, details={
            "target_id": result.target_id,
            "topic": result.topic,
            "result": result.details,
        })

        self._update(agent, content, result)
        self._trace(agent, LifecyclePhase.UPDATE, action=result.action, content_id=result.content_id, details={
            "memory_count": len(agent.memories),
            "relationship_count": len(agent.relationships),
            "belief_topics": len(agent.beliefs),
        })

        agent.last_action = result.action
        agent.action_counts[result.action.value] = agent.action_counts.get(result.action.value, 0) + 1
        event = ActionEvent(
            event_id=f"event-{self.state.tick:04d}-{len(self.state.events) + 1:06d}",
            tick=self.state.tick,
            actor_id=agent.id,
            action=result.action,
            target_id=result.target_id,
            content_id=result.content_id,
            topic=result.topic,
            details=result.details,
        )
        self.state.events.append(event)
        self._trace(agent, LifecyclePhase.RECORD, action=result.action, content_id=result.content_id, details={
            "event_id": event.event_id,
            "recorded": True,
        })

    def _observe(self, agent: Agent, rng) -> Content | None:
        visible = [content for content in self.state.contents[-self.state.config.observation_window:] if content.author_id != agent.id]
        if not visible:
            return None
        related = [content for content in visible if content.topic in agent.interests]
        pool = related or visible
        return pool[rng.randrange(len(pool))]

    def _form_intent(self, agent: Agent, content: Content | None, rng) -> ActionKind:
        if content is None:
            return ActionKind.POST
        if rng.random() < self.state.config.post_probability * (0.45 + agent.curiosity * 0.55):
            return ActionKind.POST
        if content.claim_truth is not None and rng.random() < min(
            1.0, self.state.config.fact_check_probability + agent.skepticism * 0.18
        ):
            return ActionKind.FACT_CHECK
        if rng.random() < self.state.config.reply_probability:
            return ActionKind.REPLY
        target_id = content.author_id
        relationship = agent.relationships.get(target_id)
        if relationship and relationship.follows and rng.random() < 0.16:
            return ActionKind.UNFOLLOW
        if (not relationship or not relationship.follows) and rng.random() < 0.2:
            return ActionKind.FOLLOW
        if rng.random() < 0.18:
            return ActionKind.LEAVE_COMMUNITY if len(agent.community_ids) > 1 else ActionKind.JOIN_COMMUNITY
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

    def _act(self, agent: Agent, content: Content | None, action: ActionKind, rng) -> ActionResult:
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
            )
            return ActionResult(ActionKind.FACT_CHECK, target_id=target_id, content_id=created.id, topic=content.topic, details="recorded a fact-check")
        if action is ActionKind.FOLLOW:
            set_following(agent, target_id, True)
            return ActionResult(ActionKind.FOLLOW, target_id=target_id, content_id=content.id, topic=content.topic, details="followed the author")
        if action is ActionKind.UNFOLLOW:
            set_following(agent, target_id, False)
            return ActionResult(ActionKind.UNFOLLOW, target_id=target_id, content_id=content.id, topic=content.topic, details="unfollowed the author")
        if action in {ActionKind.JOIN_COMMUNITY, ActionKind.LEAVE_COMMUNITY}:
            community = self._community_for(agent, content.topic, action)
            if community:
                changed = join_community(agent, community) if action is ActionKind.JOIN_COMMUNITY else leave_community(agent, community)
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

    def _update(self, agent: Agent, content: Content | None, result: ActionResult) -> None:
        if content is not None:
            relationship = agent.relationships.get(content.author_id)
            trust = relationship.trust if relationship else agent.trust_baseline
            learn_from_content(agent, content, trust=trust, skepticism=agent.skepticism)
            positive = result.action in {ActionKind.REPLY, ActionKind.FOLLOW, ActionKind.FACT_CHECK}
            if result.action is not ActionKind.POST and content.author_id != agent.id:
                record_interaction(agent, content.author_id, positive=positive)
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

    def _trace(
        self,
        agent: Agent,
        phase: LifecyclePhase,
        *,
        action: ActionKind | None = None,
        content_id: str | None = None,
        details: dict[str, object],
    ) -> None:
        self.state.traces.append(
            DecisionTrace(
                trace_id=f"trace-{self.state.tick:04d}-{agent.id}-{phase.value.lower().replace(' ', '-')}",
                tick=self.state.tick,
                agent_id=agent.id,
                phase=phase,
                action=action,
                content_id=content_id,
                details=details,
            )
        )


def run_seeded(seed: int, ticks: int = 1) -> SimulationState:
    return Simulation(seed=seed).run(ticks)
