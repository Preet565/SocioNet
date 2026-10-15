"""Side-by-side experiments from one identical serialized snapshot."""

from __future__ import annotations

from app.analytics import calculate_metrics, metric_deltas
from app.interventions import apply_intervention
from app.models import ExperimentResult, Intervention, SimulationState
from app.simulation import Simulation


def run_side_by_side(
    snapshot: SimulationState,
    *,
    ticks: int,
    control_interventions: list[Intervention] | None = None,
    treatment_interventions: list[Intervention] | None = None,
) -> ExperimentResult:
    """Fork control and treatment before either branch advances a tick."""

    control_state = snapshot.model_copy(deep=True)
    treatment_state = snapshot.model_copy(deep=True)
    control = Simulation(state=control_state)
    treatment = Simulation(state=treatment_state)
    for intervention in control_interventions or []:
        apply_intervention(control.state, intervention)
    for intervention in treatment_interventions or []:
        apply_intervention(treatment.state, intervention)
    control.run(ticks)
    treatment.run(ticks)
    control_metrics = calculate_metrics(control.state)
    treatment_metrics = calculate_metrics(treatment.state)
    return ExperimentResult(
        seed=snapshot.seed,
        ticks=ticks,
        control=control.state,
        treatment=treatment.state,
        control_metrics=control_metrics,
        treatment_metrics=treatment_metrics,
        metric_deltas=metric_deltas(control_metrics, treatment_metrics),
    )
