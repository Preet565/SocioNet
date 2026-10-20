"""Independent arithmetic and exact binomial coverage for small batches."""

import json
import math
import os
import subprocess
import sys

import pytest

from app.analytics import calculate_metrics
from app.batch_experiments import (
    aggregate_paired_results, derive_seed, descriptive_statistics, paired_effect_sizes,
)
from app.experiments import run_side_by_side
from app.models import BatchObjective, ExperimentResult, SimulationState
from app.simulation import append_content, create_initial_state


def pair(left=0, right=0, seed=1):
    state = SimulationState(seed=seed, tick=1, agents={}, communities={})
    baseline = calculate_metrics(state)
    return ExperimentResult(
        seed=seed, ticks=1, control=state, treatment=state.model_copy(deep=True),
        control_metrics=baseline.model_copy(update={"total_contents": left}),
        treatment_metrics=baseline.model_copy(update={"total_contents": right}),
        metric_deltas={},
    )


def test_descriptive_sample_variance_and_singleton_are_not_population_variance():
    summary = descriptive_statistics([1, 2, 4, 7])
    assert summary.mean == 3.5
    assert summary.median == 3
    assert summary.variance == 7  # squared deviations sum=21; divide by n-1=3
    assert summary.standard_deviation == pytest.approx(math.sqrt(7))
    singleton = descriptive_statistics([7])
    assert singleton.variance is None
    assert singleton.standard_deviation is None
    assert singleton.confidence_interval is None
    empty = descriptive_statistics([])
    assert empty.count == 0
    assert empty.mean is empty.median is empty.variance is None


@pytest.mark.parametrize("n,coverage", [(2, 0.5), (3, 0.75), (4, 0.875), (5, 0.9375),
                                        (6, 0.96875), (10, 0.978515625)])
def test_median_interval_reports_actual_exact_coverage(n, coverage):
    interval = descriptive_statistics(range(n), 0.95).confidence_interval
    assert interval is not None
    # n<6 cannot yield a finite, distribution-free 95% interval. In particular,
    # min/max for n=3 covers only 6 of the 8 equiprobable sign patterns.
    assert interval.level == coverage
    if n < 6:
        assert "unattainable" in interval.method
    else:
        assert interval.level >= 0.95
    if n == 10:
        assert (interval.lower, interval.upper) == (1, 8)
    assert "paired" not in interval.method  # also used for marginal arm medians


@pytest.mark.parametrize("confidence", [0, 0.5, 1, float("nan"), float("inf")])
def test_invalid_confidence_levels_are_rejected_even_for_empty_data(confidence):
    with pytest.raises(ValueError, match="confidence"):
        descriptive_statistics([], confidence)


@pytest.mark.parametrize("values", [[1, float("nan")], [float("inf")], [-float("inf")]])
def test_nonfinite_samples_are_rejected(values):
    with pytest.raises(ValueError, match="finite"):
        descriptive_statistics(values)
    with pytest.raises(ValueError, match="finite"):
        paired_effect_sizes(values)


@pytest.mark.parametrize("differences", [[2], [2, 2, 2], [0, 0, 0]])
def test_standardized_effect_is_undefined_without_sample_variation(differences):
    assert paired_effect_sizes(differences).paired_cohens_d is None


def test_aggregate_cliffs_delta_uses_all_cross_arm_comparisons():
    # Three paired wins; across 9 independent comparisons there are 6 wins,
    # 3 losses, hence Cliff's delta=1/3 (paired sign effect would be 1).
    results = [pair(0, 1, 1), pair(100, 101, 2), pair(200, 201, 3)]
    aggregate = aggregate_paired_results(results, objective=BatchObjective(metric="total_contents"))
    effects = aggregate.metrics["total_contents"].effect_size
    assert effects.cliffs_delta == pytest.approx(1 / 3)
    assert effects.paired_cohens_d is None
    assert effects.probability_treatment_better == 1


def test_differences_alone_do_not_identify_cross_arm_cliffs_delta():
    from app.batch_experiments import paired_effect_diagnostics

    effects = paired_effect_sizes([1, 2, 3])
    assert effects.paired_cohens_d == 2
    assert effects.cliffs_delta is None
    assert paired_effect_diagnostics([-2, 0, 1, -1]) == {
        "paired_sign_effect": -0.25,
        "probability_treatment_greater": 0.25,
        "probability_treatment_lower": 0.5,
        "tie_fraction": 0.25,
        "evaluated_pairs": 4,
    }


def test_zero_threshold_objective_includes_ties_but_strict_win_probability_does_not():
    aggregate = aggregate_paired_results(
        [pair(2, 2, 1), pair(2, 3, 2)],
        objective=BatchObjective(metric="total_contents", threshold=0),
    )
    assert aggregate.objective.success_rate == 1
    assert aggregate.metrics["total_contents"].effect_size.probability_treatment_better == 0.5
    # Other metrics have no pre-registered preference: don't call increases "better".
    assert aggregate.metrics["polarization"].effect_size.probability_treatment_better is None


def test_duplicate_seeds_are_not_independent_repetitions():
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_paired_results([pair(), pair()], objective=BatchObjective())


def test_paired_probability_is_direction_aware_for_decrease_objective():
    aggregate = aggregate_paired_results(
        [pair(3, 1, 1), pair(3, 3, 2), pair(1, 2, 3), pair(2, 1, 4)],
        objective=BatchObjective(metric="total_contents", direction="decrease", threshold=1),
        failed_runs=2,
    )
    effects = aggregate.metrics["total_contents"].effect_size
    assert effects.probability_treatment_better == 0.5
    assert effects.paired_cohens_d == pytest.approx(-0.5 / math.sqrt(5 / 3))
    assert aggregate.completed_pairs == 4
    assert aggregate.failed_runs == 2
    assert aggregate.objective.evaluated == 4
    assert aggregate.objective.success_rate == 0.5
    # This is explicitly conditional on successful pairs; failures are not zeros.
    assert aggregate.metrics["total_contents"].paired_difference.count == 4


def test_missing_defaulted_metric_is_not_a_measured_zero():
    result = pair()
    payload = result.treatment_metrics.model_dump()
    payload.pop("correction_reach")
    result.treatment_metrics = type(result.treatment_metrics).model_validate(payload)
    aggregate = aggregate_paired_results([result], objective=BatchObjective(metric="correction_reach"))
    assert aggregate.completed_pairs == 1
    summary = aggregate.metrics["correction_reach"]
    assert summary.control.count == summary.treatment.count == summary.paired_difference.count == 0
    assert aggregate.objective.evaluated == 0
    assert summary.effect_size.paired_cohens_d is None


def test_incomplete_pair_is_counted_and_excluded_as_a_unit():
    aggregate = aggregate_paired_results([pair(1, 4), None],
                                       objective=BatchObjective(metric="total_contents"), failed_runs=1)
    assert aggregate.completed_pairs == 1
    assert aggregate.failed_runs == 2
    assert aggregate.objective.evaluated == 1
    assert aggregate.metrics["total_contents"].paired_difference.mean == 3


def test_no_evaluable_pairs_is_not_zero_success_probability_in_diagnostics():
    from app.batch_experiments import aggregate_diagnostics

    aggregate = aggregate_paired_results([], objective=BatchObjective(), failed_runs=2)
    diagnostics = aggregate_diagnostics(aggregate, expected_pairs=4)
    assert diagnostics["objective_success_rate"] is None
    assert diagnostics["missing_pairs"] == 2
    assert diagnostics["completed_fraction"] == 0
    assert diagnostics["objective_evaluation_fraction"] == 0


def test_pair_seed_tick_and_nonfinite_values_are_validated():
    objective = BatchObjective(metric="total_contents")
    mismatch = pair()
    mismatch.treatment.seed = 2
    with pytest.raises(ValueError, match="seed"):
        aggregate_paired_results([mismatch], objective=objective)
    mismatch = pair()
    mismatch.treatment_metrics.tick = 2
    with pytest.raises(ValueError, match="tick"):
        aggregate_paired_results([mismatch], objective=objective)
    with pytest.raises(ValueError, match="finite"):
        aggregate_paired_results([pair(0, float("nan"))], objective=objective)
    with pytest.raises(ValueError, match="failed_runs"):
        aggregate_paired_results([], objective=objective, failed_runs=-1)


def test_seed_derivation_matches_known_digest_across_hash_randomization():
    # SHA-256('socionet:phase3:1234:1') first four bytes, masked to 31 bits.
    assert derive_seed(1234, 1) == 1610272713
    code = "from app.batch_experiments import derive_seed; print(derive_seed(1234, 1))"
    outputs = [subprocess.check_output([sys.executable, "-c", code], text=True,
                                      env={**os.environ, "PYTHONHASHSEED": seed}).strip()
               for seed in ("1", "777")]
    assert outputs == [str(derive_seed(1234, 1))] * 2


def test_tiny_paired_batch_is_reproducible_and_does_not_mutate_source():
    source = create_initial_state(derive_seed(42, 1), count=3)
    append_content(source, author_id=sorted(source.agents)[0], kind="post", topic="transit",
                   text="A seeded false fictional claim", claim_truth=False)
    before = source.model_dump_json()
    first = run_side_by_side(source, ticks=1)
    second = run_side_by_side(source, ticks=1)
    objective = BatchObjective(metric="total_contents", threshold=0)
    left = aggregate_paired_results([first], objective=objective).model_dump(mode="json")
    right = aggregate_paired_results([second], objective=objective).model_dump(mode="json")
    assert left == right
    assert first.control_metrics.exposure_rate == round(len(first.control.content_exposures) / 3, 6)
    assert first.control_metrics.claim_metrics["claim-content-000001"].exposures > 0
    json.dumps(left, allow_nan=False)
    assert source.model_dump_json() == before
