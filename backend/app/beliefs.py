"""Belief update rules for fictional evidence and counterevidence."""

from __future__ import annotations

from app.models import Agent, Belief, Content


def update_belief(agent: Agent, topic: str, *, truth: bool, weight: float) -> Belief:
    belief = agent.beliefs.setdefault(topic, Belief(topic=topic, stance=0, confidence=0.35))
    bounded_weight = max(0.0, min(1.0, weight))
    direction = 1.0 if truth else -1.0
    belief.stance = max(-1.0, min(1.0, belief.stance * (1 - bounded_weight) + direction * bounded_weight))
    belief.confidence = max(0.0, min(1.0, belief.confidence + bounded_weight * 0.18))
    if truth:
        belief.evidence_for += 1
    else:
        belief.evidence_against += 1
    return belief


def learn_from_content(agent: Agent, content: Content, *, trust: float, skepticism: float) -> Belief | None:
    if content.claim_truth is None:
        return None
    # Trust makes evidence persuasive; skepticism attenuates unverified claims.
    weight = content.credibility * (0.55 + trust * 0.45)
    if content.kind.value != "fact_check":
        weight *= 1.0 - skepticism * 0.25
    return update_belief(agent, content.topic, truth=content.claim_truth, weight=weight)


def belief_accuracy(agent: Agent) -> float:
    known = [belief for belief in agent.beliefs.values() if belief.evidence_for + belief.evidence_against]
    if not known:
        return 0.0
    correct = sum(belief.evidence_for for belief in known)
    total = sum(belief.evidence_for + belief.evidence_against for belief in known)
    return correct / total if total else 0.0
