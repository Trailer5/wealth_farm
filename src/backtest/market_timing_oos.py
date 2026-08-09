"""Frozen v1 expanding-window OOS and final-holdout evaluation."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import random
from typing import Literal

from .market_timing_validation import (
    MarketTimingHorizonValidation,
    MarketTimingObservation,
)


MARKET_TIMING_OOS_VERSION = "market_timing_oos_v1"
MARKET_TIMING_OOS_SEED = 20260809
ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class MarketTimingOOSParameters:
    """The non-tunable v1 OOS design."""

    min_train_observations: int = 1008
    test_observations: int = 252
    step_observations: int = 252
    holdout_observations: int = 252
    quantile_count: int = 5
    bootstrap_iterations: int = 1000
    block_length: int = 20
    seed: int = MARKET_TIMING_OOS_SEED

    def __post_init__(self) -> None:
        expected = {
            "min_train_observations": 1008,
            "test_observations": 252,
            "step_observations": 252,
            "holdout_observations": 252,
            "quantile_count": 5,
            "bootstrap_iterations": 1000,
            "block_length": 20,
            "seed": MARKET_TIMING_OOS_SEED,
        }
        for name, required in expected.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value != required:
                raise ValueError(f"{name} is frozen at {required} for v1")


@dataclass(frozen=True)
class OOSQuantileMetrics:
    """Test-return metrics after applying training-derived score boundaries."""

    quantile: int
    count: int
    mean_forward_return: Decimal | None
    median_forward_return: Decimal | None
    hit_rate: Decimal | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.quantile, bool)
            or not isinstance(self.quantile, int)
            or not 1 <= self.quantile <= 5
        ):
            raise ValueError("quantile must be an integer from 1 through 5")
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("count must be a non-negative integer")
        values = (
            self.mean_forward_return,
            self.median_forward_return,
            self.hit_rate,
        )
        if self.count == 0:
            if any(value is not None for value in values):
                raise ValueError("empty quantiles must have null metrics")
            return
        if any(value is None for value in values):
            raise ValueError("non-empty quantiles require all metrics")
        assert self.mean_forward_return is not None
        assert self.median_forward_return is not None
        assert self.hit_rate is not None
        _finite_decimal(self.mean_forward_return, "mean_forward_return")
        _finite_decimal(self.median_forward_return, "median_forward_return")
        _finite_decimal(self.hit_rate, "hit_rate")
        if not ZERO <= self.hit_rate <= ONE:
            raise ValueError("hit_rate must be between 0 and 1")


@dataclass(frozen=True)
class OOSFold:
    """One expanding-window test or the separately evaluated final holdout.

    A ``holdout`` object has ``evaluated_once=True`` to make its intended
    semantics explicit: its result is final evidence and must not be used to
    tune the strategy, score, boundaries, or OOS design.
    """

    fold: int | None
    role: Literal["walk_forward", "holdout"]
    evaluated_once: bool
    train_start_date: str
    train_end_date: str
    train_count: int
    test_start_date: str
    test_end_date: str
    test_count: int
    purged_count: int
    quantile_boundaries: tuple[Decimal, ...]
    spearman_correlation: Decimal | None
    quantiles: tuple[OOSQuantileMetrics, ...]
    q5_minus_q1: Decimal | None
    bootstrap_ci: tuple[Decimal, Decimal] | None

    def __post_init__(self) -> None:
        boundaries = tuple(self.quantile_boundaries)
        quantiles = tuple(self.quantiles)
        object.__setattr__(self, "quantile_boundaries", boundaries)
        object.__setattr__(self, "quantiles", quantiles)
        if self.role == "walk_forward":
            if (
                isinstance(self.fold, bool)
                or not isinstance(self.fold, int)
                or self.fold < 1
            ):
                raise ValueError("walk-forward fold must be a positive integer")
            if self.evaluated_once:
                raise ValueError("walk-forward folds cannot be holdout evaluations")
        elif self.role == "holdout":
            if self.fold is not None:
                raise ValueError("holdout fold must be null")
            if self.evaluated_once is not True:
                raise ValueError("holdout must have evaluated_once=True")
        else:
            raise ValueError("role must be walk_forward or holdout")

        train_start = _strict_iso_date(
            self.train_start_date,
            "train_start_date",
        )
        train_end = _strict_iso_date(self.train_end_date, "train_end_date")
        test_start = _strict_iso_date(self.test_start_date, "test_start_date")
        test_end = _strict_iso_date(self.test_end_date, "test_end_date")
        if not train_start <= train_end < test_start <= test_end:
            raise ValueError(
                "dates must satisfy train_start <= train_end "
                "< test_start <= test_end"
            )
        for name in ("train_count", "test_count", "purged_count"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        if self.train_count == 0 or self.test_count == 0:
            raise ValueError("train_count and test_count must be positive")
        if len(boundaries) != 4:
            raise ValueError("quantile_boundaries must contain four values")
        for boundary in boundaries:
            _finite_decimal(boundary, "quantile boundary")
        if boundaries != tuple(sorted(boundaries)):
            raise ValueError("quantile_boundaries must be non-decreasing")
        if any(not isinstance(item, OOSQuantileMetrics) for item in quantiles):
            raise TypeError(
                "quantiles must contain OOSQuantileMetrics values"
            )
        if (
            len(quantiles) != 5
            or tuple(item.quantile for item in quantiles) != (1, 2, 3, 4, 5)
        ):
            raise ValueError("quantiles must contain OOS quantiles 1 through 5")
        if sum(item.count for item in quantiles) != self.test_count:
            raise ValueError("quantile counts must equal test_count")
        _optional_correlation(
            self.spearman_correlation,
            "spearman_correlation",
        )
        _optional_decimal(self.q5_minus_q1, "q5_minus_q1")
        expected_spread = _quantile_spread(quantiles)
        if self.q5_minus_q1 != expected_spread:
            raise ValueError("q5_minus_q1 must equal Q5 mean minus Q1 mean")
        _validate_ci(self.bootstrap_ci)
        if self.spearman_correlation is None and self.bootstrap_ci is not None:
            raise ValueError(
                "bootstrap_ci must be null when correlation is null"
            )


@dataclass(frozen=True)
class OOSRun:
    """Complete v1 OOS result, without a fixed pass/fail threshold."""

    asset_id: str
    version: str
    parameters: MarketTimingOOSParameters
    folds: tuple[OOSFold, ...]
    holdout: OOSFold
    median_spearman: Decimal | None
    positive_fold_ratio: Decimal | None
    median_spread: Decimal | None
    holdout_scope: Literal["retrospective"] = "retrospective"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.asset_id, str)
            or not self.asset_id
            or self.asset_id != self.asset_id.strip()
        ):
            raise ValueError("asset_id must be a non-blank exact identity")
        if self.version != MARKET_TIMING_OOS_VERSION:
            raise ValueError("version must be market_timing_oos_v1")
        if self.holdout_scope != "retrospective":
            raise ValueError(
                "historical holdout_scope must be retrospective"
            )
        if not isinstance(self.parameters, MarketTimingOOSParameters):
            raise TypeError("parameters must be MarketTimingOOSParameters")
        folds = tuple(self.folds)
        object.__setattr__(self, "folds", folds)
        if not folds:
            raise ValueError("folds must contain at least one OOS fold")
        if any(
            not isinstance(item, OOSFold) or item.role != "walk_forward"
            for item in folds
        ):
            raise TypeError("folds must contain walk-forward OOSFold values")
        if tuple(item.fold for item in folds) != tuple(
            range(1, len(folds) + 1)
        ):
            raise ValueError("walk-forward fold numbers must be consecutive")
        if any(
            left.test_start_date >= right.test_start_date
            for left, right in zip(folds, folds[1:])
        ):
            raise ValueError("walk-forward folds must be chronological")
        if not isinstance(self.holdout, OOSFold) or self.holdout.role != "holdout":
            raise TypeError("holdout must be a holdout OOSFold")
        if folds[-1].test_end_date >= self.holdout.test_start_date:
            raise ValueError("holdout must not enter walk-forward folds")
        correlations = tuple(
            item.spearman_correlation
            for item in folds
            if item.spearman_correlation is not None
        )
        spreads = tuple(
            item.q5_minus_q1
            for item in folds
            if item.q5_minus_q1 is not None
        )
        expected_median_spearman = _median(correlations) if correlations else None
        expected_positive_ratio = (
            Decimal(sum(value > ZERO for value in correlations))
            / Decimal(len(correlations))
            if correlations
            else None
        )
        expected_median_spread = _median(spreads) if spreads else None
        if self.median_spearman != expected_median_spearman:
            raise ValueError("median_spearman does not match fold results")
        if self.positive_fold_ratio != expected_positive_ratio:
            raise ValueError("positive_fold_ratio does not match fold results")
        if self.median_spread != expected_median_spread:
            raise ValueError("median_spread does not match fold results")


class MarketTimingOOSEngine:
    """Evaluate already-produced medium-long observations without refitting."""

    @staticmethod
    def run(
        asset_id: str,
        validation: MarketTimingHorizonValidation,
        parameters: MarketTimingOOSParameters | None = None,
    ) -> OOSRun:
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or asset_id != asset_id.strip()
        ):
            raise ValueError("asset_id must be a non-blank exact identity")
        if not isinstance(validation, MarketTimingHorizonValidation):
            raise TypeError(
                "validation must be MarketTimingHorizonValidation"
            )
        if validation.horizon != "medium_long" or validation.forward_sessions != 252:
            raise ValueError(
                "v1 OOS accepts only medium_long validation with forward=252"
            )
        if parameters is None:
            parameters = MarketTimingOOSParameters()
        if not isinstance(parameters, MarketTimingOOSParameters):
            raise TypeError("parameters must be MarketTimingOOSParameters")

        observations = _validate_observations(validation.observations)
        holdout_size = parameters.holdout_observations
        if len(observations) <= holdout_size:
            raise ValueError("insufficient observations to form final holdout")
        development = observations[:-holdout_size]
        holdout_test = observations[-holdout_size:]

        folds: list[OOSFold] = []
        test_start_index = parameters.min_train_observations
        while test_start_index + parameters.test_observations <= len(development):
            test = development[
                test_start_index : test_start_index
                + parameters.test_observations
            ]
            train, purged_count = _purged_train(
                development[:test_start_index],
                test[0].effective_date,
            )
            if len(train) >= parameters.min_train_observations:
                folds.append(
                    _evaluate_fold(
                        fold=len(folds) + 1,
                        role="walk_forward",
                        train=train,
                        test=test,
                        purged_count=purged_count,
                        parameters=parameters,
                    )
                )
            test_start_index += parameters.step_observations
        if not folds:
            raise ValueError(
                "insufficient observations to form a walk-forward fold "
                "after purging overlapping labels"
            )

        holdout_train, holdout_purged = _purged_train(
            development,
            holdout_test[0].effective_date,
        )
        if len(holdout_train) < parameters.min_train_observations:
            raise ValueError(
                "insufficient development observations to train the final "
                "holdout after purging overlapping labels"
            )
        holdout = _evaluate_fold(
            fold=None,
            role="holdout",
            train=holdout_train,
            test=holdout_test,
            purged_count=holdout_purged,
            parameters=parameters,
        )
        correlations = tuple(
            item.spearman_correlation
            for item in folds
            if item.spearman_correlation is not None
        )
        spreads = tuple(
            item.q5_minus_q1
            for item in folds
            if item.q5_minus_q1 is not None
        )
        return OOSRun(
            asset_id=asset_id,
            version=MARKET_TIMING_OOS_VERSION,
            parameters=parameters,
            folds=tuple(folds),
            holdout=holdout,
            median_spearman=_median(correlations) if correlations else None,
            positive_fold_ratio=(
                Decimal(sum(value > ZERO for value in correlations))
                / Decimal(len(correlations))
                if correlations
                else None
            ),
            median_spread=_median(spreads) if spreads else None,
        )


def evaluate_market_timing_oos(
    asset_id: str,
    validation: MarketTimingHorizonValidation,
    parameters: MarketTimingOOSParameters | None = None,
) -> OOSRun:
    """Run the frozen v1 expanding-window and final-holdout evaluation."""

    return MarketTimingOOSEngine.run(asset_id, validation, parameters)


def _validate_observations(
    observations: tuple[MarketTimingObservation, ...],
) -> tuple[MarketTimingObservation, ...]:
    ordered = tuple(observations)
    if any(
        not isinstance(item, MarketTimingObservation)
        or item.horizon != "medium_long"
        for item in ordered
    ):
        raise TypeError(
            "observations must contain medium_long MarketTimingObservation values"
        )
    effective_dates = tuple(item.effective_date for item in ordered)
    if (
        effective_dates != tuple(sorted(effective_dates))
        or len(effective_dates) != len(set(effective_dates))
    ):
        raise ValueError(
            "observation effective dates must be strictly increasing and unique"
        )
    return ordered


def _purged_train(
    candidates: tuple[MarketTimingObservation, ...],
    test_start_date: str,
) -> tuple[tuple[MarketTimingObservation, ...], int]:
    test_start = _strict_iso_date(test_start_date, "test_start_date")
    retained = tuple(
        item
        for item in candidates
        if _strict_iso_date(item.evaluation_date, "evaluation_date") < test_start
    )
    return retained, len(candidates) - len(retained)


def _evaluate_fold(
    *,
    fold: int | None,
    role: Literal["walk_forward", "holdout"],
    train: tuple[MarketTimingObservation, ...],
    test: tuple[MarketTimingObservation, ...],
    purged_count: int,
    parameters: MarketTimingOOSParameters,
) -> OOSFold:
    boundaries = _training_quantile_boundaries(
        train,
        parameters.quantile_count,
    )
    quantiles = _test_quantile_metrics(
        test,
        boundaries,
        parameters.quantile_count,
    )
    pairs = tuple(
        (item.directional_score, item.forward_return) for item in test
    )
    seed_offset = fold if fold is not None else 1_000_000
    return OOSFold(
        fold=fold,
        role=role,
        evaluated_once=role == "holdout",
        train_start_date=train[0].effective_date,
        train_end_date=train[-1].effective_date,
        train_count=len(train),
        test_start_date=test[0].effective_date,
        test_end_date=test[-1].effective_date,
        test_count=len(test),
        purged_count=purged_count,
        quantile_boundaries=boundaries,
        spearman_correlation=_spearman_correlation(pairs),
        quantiles=quantiles,
        q5_minus_q1=_quantile_spread(quantiles),
        bootstrap_ci=_moving_block_bootstrap_ci(
            pairs,
            parameters.bootstrap_iterations,
            parameters.block_length,
            parameters.seed + seed_offset,
        ),
    )


def _training_quantile_boundaries(
    train: tuple[MarketTimingObservation, ...],
    quantile_count: int,
) -> tuple[Decimal, ...]:
    ordered_scores = sorted(item.directional_score for item in train)
    return tuple(
        ordered_scores[
            (quantile * len(ordered_scores) + quantile_count - 1)
            // quantile_count
            - 1
        ]
        for quantile in range(1, quantile_count)
    )


def _test_quantile_metrics(
    test: tuple[MarketTimingObservation, ...],
    boundaries: tuple[Decimal, ...],
    quantile_count: int,
) -> tuple[OOSQuantileMetrics, ...]:
    groups: list[list[MarketTimingObservation]] = [
        [] for _ in range(quantile_count)
    ]
    for observation in test:
        quantile_index = bisect_left(boundaries, observation.directional_score)
        groups[quantile_index].append(observation)
    output: list[OOSQuantileMetrics] = []
    for index, group in enumerate(groups, start=1):
        if not group:
            output.append(
                OOSQuantileMetrics(
                    quantile=index,
                    count=0,
                    mean_forward_return=None,
                    median_forward_return=None,
                    hit_rate=None,
                )
            )
            continue
        returns = tuple(item.forward_return for item in group)
        output.append(
            OOSQuantileMetrics(
                quantile=index,
                count=len(group),
                mean_forward_return=sum(returns, ZERO) / Decimal(len(returns)),
                median_forward_return=_median(returns),
                hit_rate=Decimal(sum(value > ZERO for value in returns))
                / Decimal(len(returns)),
            )
        )
    return tuple(output)


def _quantile_spread(
    quantiles: tuple[OOSQuantileMetrics, ...],
) -> Decimal | None:
    first = quantiles[0].mean_forward_return
    fifth = quantiles[-1].mean_forward_return
    if first is None or fifth is None:
        return None
    return fifth - first


def _moving_block_bootstrap_ci(
    pairs: tuple[tuple[Decimal, Decimal], ...],
    iterations: int,
    block_length: int,
    seed: int,
) -> tuple[Decimal, Decimal] | None:
    if _spearman_correlation(pairs) is None:
        return None
    sample_size = len(pairs)
    actual_block_length = min(block_length, sample_size)
    generator = random.Random(seed)
    correlations: list[Decimal] = []
    for _ in range(iterations):
        sample: list[tuple[Decimal, Decimal]] = []
        while len(sample) < sample_size:
            start = generator.randrange(
                sample_size - actual_block_length + 1
            )
            sample.extend(pairs[start : start + actual_block_length])
        correlation = _spearman_correlation(tuple(sample[:sample_size]))
        if correlation is not None:
            correlations.append(correlation)
    if not correlations:
        return None
    correlations.sort()
    return (
        _percentile(correlations, Decimal("0.025")),
        _percentile(correlations, Decimal("0.975")),
    )


def _spearman_correlation(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> Decimal | None:
    if len(pairs) <= 1:
        return None
    score_ranks = _average_ranks(tuple(pair[0] for pair in pairs))
    return_ranks = _average_ranks(tuple(pair[1] for pair in pairs))
    score_mean = sum(score_ranks, ZERO) / Decimal(len(score_ranks))
    return_mean = sum(return_ranks, ZERO) / Decimal(len(return_ranks))
    score_centered = tuple(value - score_mean for value in score_ranks)
    return_centered = tuple(value - return_mean for value in return_ranks)
    score_variance = sum((value * value for value in score_centered), ZERO)
    return_variance = sum((value * value for value in return_centered), ZERO)
    if score_variance == ZERO or return_variance == ZERO:
        return None
    covariance = sum(
        (
            score_value * return_value
            for score_value, return_value in zip(
                score_centered,
                return_centered,
            )
        ),
        ZERO,
    )
    return covariance / (score_variance * return_variance).sqrt()


def _average_ranks(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [ZERO] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average_rank = (
            Decimal(start + 1) + Decimal(end)
        ) / Decimal("2")
        for position in range(start, end):
            ranks[indexed[position][0]] = average_rank
        start = end
    return tuple(ranks)


def _percentile(
    ordered_values: list[Decimal],
    probability: Decimal,
) -> Decimal:
    position = Decimal(len(ordered_values) - 1) * probability
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered_values) - 1)
    fraction = position - Decimal(lower_index)
    return (
        ordered_values[lower_index] * (ONE - fraction)
        + ordered_values[upper_index] * fraction
    )


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")


def _strict_iso_date(value: str, name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must use YYYY-MM-DD format")
    return parsed


def _finite_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _optional_decimal(value: Decimal | None, name: str) -> None:
    if value is not None:
        _finite_decimal(value, name)


def _optional_correlation(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    _finite_decimal(value, name)
    if not -ONE <= value <= ONE:
        raise ValueError(f"{name} must be between -1 and 1")


def _validate_ci(value: tuple[Decimal, Decimal] | None) -> None:
    if value is None:
        return
    if not isinstance(value, tuple) or len(value) != 2:
        raise TypeError("bootstrap_ci must be a two-value tuple")
    lower, upper = value
    _optional_correlation(lower, "bootstrap_ci lower")
    _optional_correlation(upper, "bootstrap_ci upper")
    if lower > upper:
        raise ValueError("bootstrap_ci lower must not exceed upper")
