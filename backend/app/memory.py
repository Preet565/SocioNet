"""Bounded memory storage and deterministic retrieval."""

from __future__ import annotations

from app.models import Agent, Content, MemoryRecord


def remember(
    agent: Agent,
    *,
    content: Content | None,
    kind: str,
    summary: str,
    tick: int,
    credibility: float,
    valence: float = 0.0,
    limit: int = 40,
) -> MemoryRecord:
    memory = MemoryRecord(
        memory_id=f"memory-{agent.id}-{tick:04d}-{len(agent.memories):03d}",
        agent_id=agent.id,
        content_id=content.id if content else None,
        kind=kind,
        summary=summary[:280],
        topic=content.topic if content else None,
        credibility=max(0.0, min(1.0, credibility)),
        valence=max(-1.0, min(1.0, valence)),
        created_tick=tick,
        recall_strength=1.0,
    )
    agent.memories.append(memory)
    if len(agent.memories) > limit:
        agent.memories.sort(key=lambda item: (item.created_tick, item.memory_id))
        del agent.memories[: len(agent.memories) - limit]
    return memory


def retrieve(agent: Agent, topic: str | None, limit: int = 5) -> list[MemoryRecord]:
    """Prefer matching memories, then recent high-recall memories."""

    def rank(memory: MemoryRecord) -> tuple[int, float, int, str]:
        match = 1 if topic and memory.topic == topic else 0
        return (match, memory.recall_strength * memory.credibility, memory.created_tick, memory.memory_id)

    return sorted(agent.memories, key=rank, reverse=True)[:limit]
