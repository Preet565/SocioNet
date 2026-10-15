from app.models import ActionKind, LifecyclePhase
from app.simulation import Simulation, create_initial_state, run_seeded


def test_tick_runs_complete_lifecycle_for_all_citizens() -> None:
    simulation = Simulation(seed=41)
    state = simulation.step()
    assert state.tick == 1
    assert len(state.agents) == 100
    assert len(state.traces) == 100 * 7
    phases = [trace.phase for trace in state.traces[:7]]
    assert phases == list(LifecyclePhase)
    assert len(state.events) == 100
    assert any(event.action is ActionKind.POST for event in state.events)


def test_tick_changes_beliefs_and_relationships_after_content_exists() -> None:
    simulation = Simulation(seed=42)
    before = simulation.state.model_copy(deep=True)
    simulation.run(3)
    assert any(agent.memories for agent in simulation.state.agents.values())
    assert any(agent.relationships for agent in simulation.state.agents.values())
    assert any(agent.beliefs != before.agents[agent.id].beliefs for agent in simulation.state.agents.values())
    assert len(simulation.state.contents) > 100


def test_same_seed_and_ticks_produce_identical_serialized_state() -> None:
    left = run_seeded(99, 3).model_dump(mode="json")
    right = run_seeded(99, 3).model_dump(mode="json")
    assert left == right


def test_initial_state_can_be_created_with_small_population_for_unit_usage() -> None:
    state = create_initial_state(9, count=2)
    assert len(state.agents) == 2
