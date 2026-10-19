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
    return float(value)


def _binomial_cdf(n: int, k: int) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return sum(math.comb(n, index) for index in range(k + 1)) / (2**n)


def _distribution_free_median_interval(values: list[float], confidence_level: float) -> BatchConfidenceInterval | None:
    """Return an exact sign/order-statistic interval for the population median.

    The interval uses the binomial sign distribution, so it does not assume a
    normal or any other parametric sampling distribution. Small samples are
    intentionally conservative and often return the observed min/max.
    """

    if not values:
        return None
    ordered = sorted(values)
    alpha = 1.0 - confidence_level
    lower_rank = 1
    for candidate in range(1, (len(ordered) // 2) + 1):
        if _binomial_cdf(len(ordered), candidate - 1) <= alpha / 2:
            lower_rank = candidate
    upper_rank = len(ordered) - lower_rank + 1
    return BatchConfidenceInterval(
        lower=ordered[lower_rank - 1],
        upper=ordered[upper_rank - 1],
        level=confidence_level,
        method="exact distribution-free order-statistic interval for the paired median",
    )


def descriptive_statistics(values: Iterable[float], confidence_level: float = 0.95) -> BatchDescriptiveStatistics:
    """Calculate stable descriptive statistics and a distribution-free CI."""

    observed = [float(value) for value in values]
    if not observed:
        return BatchDescriptiveStatistics(count=0)
    return BatchDescriptiveStatistics(
        count=len(observed),
        mean=fmean(observed),
        median=median(observed),
        variance=variance(observed) if len(observed) > 1 else 0.0,
        standard_deviation=stdev(observed) if len(observed) > 1 else 0.0,
        confidence_interval=_distribution_free_median_interval(observed, confidence_level),
    )


def paired_effect_sizes(differences: Iterable[float]) -> BatchEffectSizes:
    """Return paired standardized and rank-free effect sizes."""

    observed = [float(value) for value in differences]
    if not observed:
        return BatchEffectSizes()
    average = fmean(observed)
    deviation = stdev(observed) if len(observed) > 1 else 0.0
    # A constant non-zero paired effect has no within-pair variation to
    # standardize; report zero rather than leaking infinity into JSON.
    paired_cohens_d = average / deviation if deviation else 0.0
    wins = sum(value > 0 for value in observed)
    losses = sum(value < 0 for value in observed)
    return BatchEffectSizes(
        paired_cohens_d=paired_cohens_d,
        cliffs_delta=(wins - losses) / len(observed),
        probability_treatment_better=wins / len(observed),
    )


def aggregate_paired_results(
    results: Iterable[ExperimentResult],
    *,
    objective: BatchObjective,
    confidence_level: float = 0.95,
    failed_runs: int = 0,
) -> BatchAggregate:
    """Aggregate final control/treatment metrics over successful pairs."""

    successful = list(results)
    metric_summaries: dict[str, BatchMetricSummary] = {}
    for metric in SCALAR_METRICS:
        control = [_metric_value(result.control_metrics, metric) for result in successful]
        treatment = [_metric_value(result.treatment_metrics, metric) for result in successful]
        differences = [right - left for left, right in zip(control, treatment)]
        metric_summaries[metric] = BatchMetricSummary(
            control=descriptive_statistics(control, confidence_level),
            treatment=descriptive_statistics(treatment, confidence_level),
            paired_difference=descriptive_statistics(differences, confidence_level),
            effect_size=paired_effect_sizes(differences),
        )

    objective_differences = [
        _metric_value(result.treatment_metrics, objective.metric)
        - _metric_value(result.control_metrics, objective.metric)
        for result in successful
    ]
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
