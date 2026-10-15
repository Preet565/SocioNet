import pytest
from pydantic import ValidationError

from app.interventions import InterventionValidationError, apply_intervention
from app.models import Intervention, InterventionKind
from app.simulation import Simulation


def test_invalid_intervention_models_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Intervention(kind=InterventionKind.SEED_POST, topic="public art")
    with pytest.raises(ValidationError):
        Intervention(kind=InterventionKind.TRUST_SHIFT, target_agent_id="citizen-001", delta=0)


def test_invalid_agent_reference_is_rejected_against_state() -> None:
    simulation = Simulation(seed=5)
    intervention = Intervention(
        kind=InterventionKind.SEED_POST,
        topic="public art",
        text="A fictional seeded observation.",
        agent_id="citizen-999",
    )
    with pytest.raises(InterventionValidationError):
        apply_intervention(simulation.state, intervention)


def test_counterevidence_can_be_delayed_until_a_future_tick() -> None:
    simulation = Simulation(seed=5)
    intervention = Intervention(
        kind=InterventionKind.COUNTEREVIDENCE,
        topic="public art",
        delay_ticks=2,
        text="A fictional correction arrives later.",
    )
    identifier = apply_intervention(simulation.state, intervention)
    assert identifier == "intervention-00001"
    assert len(simulation.state.contents) == 0
    simulation.step()
    assert not any(content.is_counterevidence for content in simulation.state.contents)
    simulation.step()
    counterevidence = [content for content in simulation.state.contents if content.is_counterevidence]
    assert len(counterevidence) == 1
    assert counterevidence[0].created_tick == 2
