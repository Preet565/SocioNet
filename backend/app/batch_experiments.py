"""Deterministic multi-seed pairing and distribution-free batch summaries."""

from __future__ import annotations

import hashlib
import math
from statistics import fmean, median, stdev, variance
from typing import Iterable

from app.models import (
    BatchAggregate,
    BatchConfidenceInterval,
    BatchDescriptiveStatistics,
    BatchEffectSizes,
    BatchMetricSummary,
    BatchObjective,
    BatchObjectiveResult,
    ExperimentResult,
    Metrics,
    SUPPORTED_BATCH_METRICS,
)


def derive_seed(base_seed: int, repetition: int) -> int:
    """Derive a stable 31-bit seed without depending on Python hash randomization.

    ``repetition`` is one-based in persisted batch records.  Control and
    treatment receive the same derived value; the arm is deliberately not part
    of this function so the pair cannot accidentally start from different
    initial states.
    """

    if base_seed < 0:
        raise ValueError("base_seed must be non-negative")
    if repetition < 1:
        raise ValueError("repetition must be one-based")
    digest = hashlib.sha256(f"socionet:phase3:{base_seed}:{repetition}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


# This explicit list keeps nested topic/claim dictionaries out of the batch
# contract and makes objective validation deterministic across Python versions.
SCALAR_METRICS: tuple[str, ...] = tuple(sorted(SUPPORTED_BATCH_METRICS))


def _metric_value(metrics: Metrics, metric: str) -> float:
    value = getattr(metrics, metric)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"metric {metric!r} is not a scalar numeric metric")
    if not math.isfinite(value):
        raise ValueError(f"metric {metric!r} must be finite")
    return float(value)


def _validate_confidence(confidence_level: float) -> None:
    if not math.isfinite(confidence_level) or not 0.5 < confidence_level < 1:
        raise ValueError("confidence_level must be finite and strictly between 0.5 and 1")


def _finite_values(values: Iterable[float]) -> list[float]:
    observed = [float(value) for value in values]
    if not all(math.isfinite(value) for value in observed):
        raise ValueError("statistical samples must be finite")
    return observed


def _binomial_cdf(n: int, k: int) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, index) for index in range(k + 1)) / (2**n)


def _distribution_free_median_interval(values: list[float], confidence_level: float) -> BatchConfidenceInterval | None:
    """Return an exact sign/order-statistic interval for the population median.

    The interval uses the binomial sign distribution, so it does not assume a
    normal or any other parametric sampling distribution. ``level`` reports
    achieved binomial coverage (a lower bound in the presence of ties), not the
    requested nominal level. For n=2..5 the min/max fallback cannot achieve
    95%; say so explicitly. For n=1 no nontrivial finite interval is available.
    """

    _validate_confidence(confidence_level)
    if len(values) < 2:
        return None
    ordered = sorted(values)
    alpha = 1.0 - confidence_level
    lower_rank = 1
    for candidate in range(1, (len(ordered) // 2) + 1):
        if _binomial_cdf(len(ordered), candidate - 1) <= alpha / 2:
            lower_rank = candidate
    upper_rank = len(ordered) - lower_rank + 1
    coverage = 1 - 2 * _binomial_cdf(len(ordered), lower_rank - 1)
    method = "exact distribution-free order-statistic interval for the population median"
    if coverage < confidence_level:
        method += f"; requested level {confidence_level:g} unattainable with finite order statistics"
    return BatchConfidenceInterval(
        lower=ordered[lower_rank - 1],
        upper=ordered[upper_rank - 1],
        level=coverage,
        method=method,
    )


def descriptive_statistics(values: Iterable[float], confidence_level: float = 0.95) -> BatchDescriptiveStatistics:
    """Calculate stable descriptive statistics and a distribution-free CI."""

    _validate_confidence(confidence_level)
    observed = _finite_values(values)
    if not observed:
        return BatchDescriptiveStatistics(count=0)
    return BatchDescriptiveStatistics(
        count=len(observed),
        mean=fmean(observed),
        median=median(observed),
        variance=variance(observed) if len(observed) > 1 else None,
        standard_deviation=stdev(observed) if len(observed) > 1 else None,
        confidence_interval=_distribution_free_median_interval(observed, confidence_level),
    )


def paired_effect_sizes(
    differences: Iterable[float],
    *,
    control: Iterable[float] | None = None,
    treatment: Iterable[float] | None = None,
    direction: str | None = "increase",
) -> BatchEffectSizes:
    """Return Cohen's dz, marginal Cliff's delta, and strict paired win rate.

    Cohen's dz always uses raw treatment-minus-control differences. Cliff's
    delta compares *all* cross-arm combinations, not paired signs. For legacy
    differences-only callers its reference distribution is the point mass at
    zero: that value is the paired sign effect, NOT Cliff's delta between arms.
    Use paired_effect_diagnostics for an unambiguous additive field name.

    A win excludes ties and uses the supplied preferred direction. With no
    registered direction, "better" is undefined and is returned as null.
    """

    observed = _finite_values(differences)
    if direction not in (None, "increase", "decrease"):
        raise ValueError("direction must be increase, decrease, or None")
    if (control is None) != (treatment is None):
        raise ValueError("both control and treatment samples are required")
    left = _finite_values(control) if control is not None else None
    right = _finite_values(treatment) if treatment is not None else None
    if left is not None and right is not None:
        if len(left) != len(right) or len(left) != len(observed):
            raise ValueError("paired samples must have equal lengths")
        if any(after - before != difference for before, after, difference in zip(left, right, observed)):
            raise ValueError("differences must match treatment - control")
    if not observed:
        return BatchEffectSizes()
    average = fmean(observed)
    deviation = stdev(observed) if len(observed) > 1 else 0.0
    # Zero variation or one pair cannot define a standardized effect, including
    # 0/0 for all-zero differences. JSON null preserves that distinction.
    paired_cohens_d = average / deviation if deviation else None
    wins = sum(value > 0 for value in observed)
    losses = sum(value < 0 for value in observed)
    # Cliff's delta is a marginal cross-arm statistic. Paired differences alone
    # do not contain the control/treatment distributions needed to calculate it.
    cliffs_delta = (
        sum((after > before) - (after < before) for after in right for before in left) / (len(left) * len(right))
        if left is not None and right is not None else None
    )
    return BatchEffectSizes(
        paired_cohens_d=paired_cohens_d,
        cliffs_delta=cliffs_delta,
        probability_treatment_better=(
            (wins if direction == "increase" else losses) / len(observed) if direction else None
        ),
    )


def paired_effect_diagnostics(differences: Iterable[float]) -> dict[str, float | int | None]:
    """Named paired quantities that the current BatchEffectSizes model lacks."""

    observed = _finite_values(differences)
    n = len(observed)
    wins = sum(value > 0 for value in observed)
    losses = sum(value < 0 for value in observed)
    return {
        "paired_sign_effect": (wins - losses) / n if n else None,
        "probability_treatment_greater": wins / n if n else None,
        "probability_treatment_lower": losses / n if n else None,
        "tie_fraction": (n - wins - losses) / n if n else None,
        "evaluated_pairs": n,
    }


def aggregate_paired_results(
    results: Iterable[ExperimentResult | None],
    *,
    objective: BatchObjective,
    confidence_level: float = 0.95,
    failed_runs: int = 0,
) -> BatchAggregate:
    """Aggregate complete, same-seed/tick pairs; never impute absent metrics.

    ``failed_runs`` counts failed paired repetitions already omitted by the
    caller. Optional None placeholders are additional incomplete repetitions.
    Objective success rates condition on evaluable pairs, not attempted runs.
    """

    _validate_confidence(confidence_level)
    if isinstance(failed_runs, bool) or not isinstance(failed_runs, int) or failed_runs < 0:
        raise ValueError("failed_runs must be a non-negative integer")
    if not math.isfinite(objective.threshold):
        raise ValueError("objective threshold must be finite")
    successful = []
    seeds: set[int] = set()
    for result in results:
        if result is None:
            failed_runs += 1
            continue
        if result.seed != result.control.seed or result.seed != result.treatment.seed:
            raise ValueError("paired result and both arms must share a seed")
        if result.seed in seeds:
            raise ValueError("duplicate pair seed would repeat a deterministic observation")
        seeds.add(result.seed)
        if len({result.control.tick, result.treatment.tick,
                result.control_metrics.tick, result.treatment_metrics.tick}) != 1:
            raise ValueError("paired states and metrics must share a final tick")
        successful.append(result)
    successful.sort(key=lambda result: result.seed)
    metric_summaries: dict[str, BatchMetricSummary] = {}
    differences_by_metric = {}
    for metric in SCALAR_METRICS:
        # Additive model defaults in legacy payloads are not observations. Use
        # only pairs with both explicitly measured values, keeping paired and
        # marginal sample counts identical for each metric.
        measured = [result for result in successful
                    if metric in result.control_metrics.model_fields_set
                    and metric in result.treatment_metrics.model_fields_set
                    and getattr(result.control_metrics, metric) is not None
                    and getattr(result.treatment_metrics, metric) is not None]
        control = [_metric_value(result.control_metrics, metric) for result in measured]
        treatment = [_metric_value(result.treatment_metrics, metric) for result in measured]
        differences = _finite_values(right - left for left, right in zip(control, treatment))
        differences_by_metric[metric] = differences
        metric_summaries[metric] = BatchMetricSummary(
            control=descriptive_statistics(control, confidence_level),
            treatment=descriptive_statistics(treatment, confidence_level),
            paired_difference=descriptive_statistics(differences, confidence_level),
            effect_size=paired_effect_sizes(
                differences, control=control, treatment=treatment,
                direction=objective.direction if metric == objective.metric else None,
            ),
        )

    objective_differences = differences_by_metric[objective.metric]
    if objective.direction == "increase":
        successes = sum(value >= objective.threshold for value in objective_differences)
        rule = f"treatment - control >= {objective.threshold:g}"
    else:
        successes = sum(value <= -objective.threshold for value in objective_differences)
        rule = f"treatment - control <= {-objective.threshold:g}"
    evaluated = len(objective_differences)
    objective_result = BatchObjectiveResult(
        metric=objective.metric,
        direction=objective.direction,
        threshold=objective.threshold,
        successes=successes,
        evaluated=evaluated,
        success_rate=successes / evaluated if evaluated else 0.0,
        definition=rule,
    )
    return BatchAggregate(
        completed_pairs=len(successful),
        failed_runs=failed_runs,
        objective=objective_result,
        objective_success_rate=objective_result.success_rate,
        metrics=metric_summaries,
    )


def aggregate_diagnostics(aggregate: BatchAggregate, *, expected_pairs: int) -> dict[str, object]:
    """Nullable rates and missingness for callers adopting an additive contract.

    The legacy aggregate model requires non-null success rates, even when no
    objective is evaluable, and does not carry the planned repetition count.
    This helper does not conflate that placeholder zero with an estimate.
    """

    accounted = aggregate.completed_pairs + aggregate.failed_runs
    if isinstance(expected_pairs, bool) or not isinstance(expected_pairs, int) or expected_pairs < accounted:
        raise ValueError("expected_pairs must be an integer covering all completed and failed pairs")
    evaluated = aggregate.objective.evaluated
    return {
        "expected_pairs": expected_pairs,
        "failed_pairs": aggregate.failed_runs,
        "missing_pairs": expected_pairs - accounted,
        "completed_fraction": aggregate.completed_pairs / expected_pairs if expected_pairs else None,
        "objective_evaluation_fraction": evaluated / expected_pairs if expected_pairs else None,
        "objective_success_rate": aggregate.objective.success_rate if evaluated else None,
        "success_rate_denominator": "evaluable paired repetitions only",
        "unevaluable_pairs_by_metric": {
            metric: expected_pairs - summary.paired_difference.count
            for metric, summary in sorted(aggregate.metrics.items())
        },
    }
