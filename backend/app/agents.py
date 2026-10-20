"""Deterministic generation of fictional citizens."""

from __future__ import annotations

from app.models import Agent, Belief, SimulationConfig
from app.utils import rng_for


TOPICS = (
    "fictional transit",
    "neighborhood gardens",
    "library funding",
    "community radio",
    "night market rules",
    "public art",
    "school science fairs",
    "coastal cleanup",
)


def _archetype_for(ideology: float, skepticism: float, curiosity: float, trust_baseline: float) -> str:
    """Assign a stable behavioral archetype from generated traits.

    The archetype is a lookup key for configurable behavior.  Presentation
    labels are derived separately and never drive the simulation.
    """

    if skepticism >= 0.68:
        return "evidence_keeper"
    if curiosity >= 0.78:
        return "signal_seeker"
    if trust_baseline >= 0.72:
        return "community_steward"
    if curiosity >= 0.62 and trust_baseline >= 0.52:
        return "bridge_builder"
    if ideology < -0.3:
        return "contrarian"
    return "civic_participant"


def create_citizens(seed: int, count: int = 100, *, config: SimulationConfig | None = None) -> dict[str, Agent]:
    """Create the same citizens for the same seed, independent of hash randomization."""

    effective_count = config.population if config is not None else count
    citizens: dict[str, Agent] = {}
    for index in range(effective_count):
        citizen_id = f"citizen-{index + 1:03d}"
        rng = rng_for(seed, "citizen", index)
        ideology = round(rng.uniform(-0.9, 0.9), 4)
        interests = sorted(rng.sample(list(TOPICS), 3))
        beliefs = {}
        for topic in TOPICS:
            stance = max(-1.0, min(1.0, ideology * 0.45 + rng.uniform(-0.4, 0.4)))
            beliefs[topic] = Belief(
                topic=topic,
                stance=round(stance, 4),
                confidence=round(rng.uniform(0.35, 0.8), 4),
            )
        citizens[citizen_id] = Agent(
            id=citizen_id,
            name=f"Citizen {index + 1:03d}",
            age=rng.randint(18, 78),
            interests=interests,
            ideology=ideology,
            skepticism=round(rng.uniform(0.15, 0.85), 4),
            curiosity=round(rng.uniform(0.2, 0.95), 4),
            trust_baseline=round(rng.uniform(0.35, 0.85), 4),
            beliefs=beliefs,
        )
        citizens[citizen_id].archetype = _archetype_for(
            citizens[citizen_id].ideology,
            citizens[citizen_id].skepticism,
            citizens[citizen_id].curiosity,
            citizens[citizen_id].trust_baseline,
        )
    return citizens
