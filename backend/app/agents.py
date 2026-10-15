"""Deterministic generation of fictional citizens."""

from __future__ import annotations

from app.models import Agent, Belief
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


def create_citizens(seed: int, count: int = 100) -> dict[str, Agent]:
    """Create the same citizens for the same seed, independent of hash randomization."""

    citizens: dict[str, Agent] = {}
    for index in range(count):
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
    return citizens
