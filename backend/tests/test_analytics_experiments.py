from app.analytics import calculate_metrics
from app.experiments import run_side_by_side
from app.models import Intervention, InterventionKind
from app.simulation import Simulation


def test_analytics_calculates_population_and_action_metrics() -> None:
    simulation = Simulation(seed=12)
    simulation.run(2)
    metrics = calculate_metrics(simulation.state)
    assert metrics.population == 100
    assert metrics.total_contents == len(simulation.state.contents)
    assert metrics.engagement_rate >= 0
    assert metrics.topic_metrics


def test_experiment_forks_identical_snapshot_and_applies_delayed_treatment() -> None:
    snapshot = Simulation(seed=21).state
    treatment = Intervention(
        kind=InterventionKind.COUNTEREVIDENCE,
        topic="fictional transit",
        delay_ticks=2,
        text="A delayed fictional correction.",
        strength=1.0,
    )
    result = run_side_by_side(snapshot, ticks=3, treatment_interventions=[treatment])
    assert result.control.seed == result.treatment.seed == 21
    assert result.control.tick == result.treatment.tick == 3
    assert not any(content.is_counterevidence for content in result.control.contents)
    assert any(content.is_counterevidence for content in result.treatment.contents)
    assert result.metric_deltas["total_contents"] != 0
