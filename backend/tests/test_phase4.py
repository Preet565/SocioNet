from __future__ import annotations

from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.analytics import calculate_graph_metrics
from app.communities import evolve_communities, record_community_event
from app.main import SimulationService, app
from app.models import CommunityEventKind, SimulationConfig
from app.relationships import evolve_relationships, relationship_for, set_following
from app.simulation import Simulation, create_initial_state


def _config(**updates: object) -> SimulationConfig:
    base = SimulationConfig()
    payload = base.model_dump(mode="python")
    payload.update(updates)
    return SimulationConfig.model_validate(payload)


def _all_archetypes(**updates: float) -> dict[str, dict[str, float]]:
    return {
        name: {**behavior.model_dump(), **updates}
        for name, behavior in SimulationConfig().archetypes.items()
    }


def test_archetype_behavior_parameters_change_decisions() -> None:
    quiet_config = _config(
        population=20,
        post_probability=1.0,
        initial_network={"structure": "empty", "max_edges_per_agent": 0},
        max_relationships=0,
        max_following=0,
        archetypes=_all_archetypes(post_bias=0.0),
    )
    active_config = _config(
        population=20,
        post_probability=1.0,
        initial_network={"structure": "empty", "max_edges_per_agent": 0},
        max_relationships=0,
        max_following=0,
        archetypes=_all_archetypes(post_bias=2.0),
    )

    quiet = Simulation(seed=501, config=quiet_config).step()
    active = Simulation(seed=501, config=active_config).step()

    assert sum(event.action.value == "post" for event in quiet.events) == 0
    assert sum(event.action.value == "post" for event in active.events) == 20
    assert all(trace.details.get("candidate") in {"post", "ignore"} for trace in quiet.traces if trace.phase.value == "FORM INTENT")


def test_relationship_formation_decay_following_and_density_are_bounded() -> None:
    config = _config(
        population=24,
        initial_network={"structure": "empty", "max_edges_per_agent": 0},
        max_relationships=4,
        max_following=2,
        relationship_formation_probability=1.0,
        relationship_decay_rate=0.25,
        following_adoption_probability=1.0,
        cross_community_interaction_probability=1.0,
    )
    state = create_initial_state(502, config=config)
    first = state.agents["citizen-001"]
    second = state.agents["citizen-002"]
    relationship = relationship_for(first, second.id)
    relationship.affinity = 1.0
    relationship.trust = 1.0
    set_following(first, second.id, True, max_following=config.max_following)
    relationship.last_interaction_tick = None

    evolve_relationships(state.seed, 1, state.agents, state.communities, config)
    assert first.relationships[second.id].affinity < 1.0
    assert len(first.relationships) <= config.max_relationships
    assert len(first.following) <= config.max_following
    assert any(agent.relationships for agent in state.agents.values())

    repeated = create_initial_state(502, config=config)
    repeated_first = repeated.agents["citizen-001"]
    repeated_relationship = relationship_for(repeated_first, "citizen-002")
    repeated_relationship.affinity = 1.0
    repeated_relationship.trust = 1.0
    set_following(repeated_first, "citizen-002", True, max_following=config.max_following)
    evolve_relationships(repeated.seed, 1, repeated.agents, repeated.communities, config)
    assert [agent.model_dump(mode="json") for agent in state.agents.values()] == [
        agent.model_dump(mode="json") for agent in repeated.agents.values()
    ]


def test_community_split_merge_and_membership_events_are_recorded() -> None:
    config = _config(
        population=12,
        initial_network={"structure": "empty", "max_edges_per_agent": 0},
        max_relationships=0,
        max_following=0,
        community_evolution={
            "enabled": True,
            "split_enabled": True,
            "merge_enabled": True,
            "max_communities_per_agent": 3,
            "split_size": 2,
            "split_min_size": 1,
            "split_ideology_gap": 0.0,
            "merge_similarity": 0.0,
        },
    )
    state = create_initial_state(503, config=config)
    largest = max(state.communities.values(), key=lambda community: (len(community.member_ids), community.id))
    assert len(largest.member_ids) >= 2
    evolve_communities(state)
    assert any(event.kind is CommunityEventKind.SPLIT for event in state.community_events)
    assert any(event.kind is CommunityEventKind.MERGE for event in state.community_events)
    split_event = next(event for event in state.community_events if event.kind is CommunityEventKind.SPLIT)
    assert split_event.related_community_ids

    record_community_event(
        state,
        kind=CommunityEventKind.JOIN,
        community_id=sorted(state.communities)[0],
        agent_id="citizen-001",
        details="test membership event",
    )
    assert state.community_events[-1].kind is CommunityEventKind.JOIN
    assert [event.event_id for event in state.community_events] == sorted(
        event.event_id for event in state.community_events
    )


def test_graph_metrics_match_known_path_graph() -> None:
    metrics = calculate_graph_metrics(
        ["a", "b", "c", "d"],
        [("a", "b"), ("b", "c"), ("c", "d")],
    )
    assert metrics["degree"] == {"a": 1, "b": 2, "c": 2, "d": 1}
    assert metrics["betweenness"] == pytest.approx({"a": 0.0, "b": 2 / 3, "c": 2 / 3, "d": 0.0})
    assert metrics["average_degree"] == 1.5
    assert metrics["network_density"] == 0.5


def test_phase4_configuration_validates_population_and_graph_bounds() -> None:
    with pytest.raises(ValidationError):
        SimulationConfig(population=0)
    with pytest.raises(ValidationError):
        SimulationConfig(max_relationships=1, max_following=2)
    with pytest.raises(ValidationError):
        SimulationConfig(
            initial_network={"structure": "ring", "max_edges_per_agent": 3},
            max_relationships=2,
        )


def test_dynamic_state_and_histories_are_deterministic_and_api_exposed(tmp_path) -> None:
    config = _config(
        population=18,
        initial_network={"structure": "ring", "max_edges_per_agent": 4, "ring_degree": 2},
        max_relationships=4,
        max_following=2,
    )
    left = Simulation(seed=504, config=config).run(3).model_dump(mode="json")
    right = Simulation(seed=504, config=config).run(3).model_dump(mode="json")
    assert left == right
    assert len(left["network_history"]) == 4
    assert len(left["community_history"]) == 4
    assert all(len(agent["relationships"]) <= 4 for agent in left["agents"].values())
    assert all(len(agent["memories"]) <= config.memory_limit for agent in left["agents"].values())

    original = app.state.service
    app.state.service = SimulationService(seed=505, database_path=str(tmp_path / "phase4.sqlite3"), config=config)
    try:
        client = TestClient(app)
        reset = client.post(
            "/reset",
            json={
                "seed": 505,
                "config": {
                    "population": 18,
                    "max_relationships": 4,
                    "max_following": 2,
                    "initial_network": {"structure": "ring", "max_edges_per_agent": 4, "ring_degree": 2},
                },
            },
        )
        assert reset.status_code == 200
        assert reset.json()["config"]["population"] == 18
        invalid = client.post(
            "/reset",
            json={"config": {"max_relationships": 1, "max_following": 2}},
        )
        assert invalid.status_code == 422
        assert client.post("/simulate/tick", json={"ticks": 1}).status_code == 200
        network = client.get("/network/history?universe_id=prime")
        communities = client.get("/communities/history?universe_id=prime")
        assert network.status_code == 200
        assert network.json()["samples"]
        assert "O(VE)" in network.json()["metric_cost"]
        assert communities.status_code == 200
        assert communities.json()["samples"]
    finally:
        app.state.service = original


def test_phase4_small_population_run_stays_within_performance_bound() -> None:
    config = _config(
        population=40,
        initial_network={"structure": "community", "max_edges_per_agent": 6},
        max_relationships=6,
        max_following=3,
    )
    started = perf_counter()
    state = Simulation(seed=506, config=config).run(3)
    elapsed = perf_counter() - started
    assert elapsed < 5.0
    assert state.tick == 3
    assert max((len(agent.relationships) for agent in state.agents.values()), default=0) <= 6
