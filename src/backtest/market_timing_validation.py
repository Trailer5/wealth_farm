"""Continuous-score validation for SSE multi-horizon market timing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import random
from typing import Literal, Sequence

from src.strategy.sse_market_timing import (
    HorizonTimingSignal,
    SSEIndexSeries,
    SSEMarketTimingResult,
)


MARKET_TIMING_VALIDATION_VERSION = "market_timing_validation_v1"
MARKET_TIMING_VALIDATION_SEED = 20260809

Horizon = Literal["short", "medium_short", "medium_long"]
HORIZONS: tuple[Horizon, ...] = (
    "short",
    "medium_short",
    "medium_long",
)
ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class MarketTimingValidationParameters:
    """Frozen v1 validation design; callers cannot tune the study."""

    quantile_count: int = 5
    short_forward_sessions: int = 10
    medium_short_forward_sessions: int = 60
    medium_long_forward_sessions: int = 252
    bootstrap_iterations: int = 1000
    block_length: int = 20
    walk_forward_folds: int = 4
    seed: int = MARKET_TIMING_VALIDATION_SEED

    def __post_init__(self) -> None:
        expected = {
            "quantile_count": 5,
            "short_forward_sessions": 10,
            "medium_short_forward_sessions": 60,
            "medium_long_forward_sessions": 252,
            "bootstrap_iterations": 1000,
            "block_length": 20,
            "walk_forward_folds": 4,
            "seed": MARKET_TIMING_VALIDATION_SEED,
        }
        for name, required in expected.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value != required:
                raise ValueError(f"{name} is frozen at {required} for v1")

    def forward_sessions(self, horizon: Horizon) -> int:
        _validate_horizon(horizon)
        return {
            "short": self.short_forward_sessions,
            "medium_short": self.medium_short_forward_sessions,
            "medium_long": self.medium_long_forward_sessions,
        }[horizon]


@dataclass(frozen=True)
class MarketTimingObservation:
    """One continuous signal score paired with its fixed-window return."""

    signal_date: str
    effective_date: str
    evaluation_date: str
    horizon: Horizon
    low_position_score: Decimal
    high_position_score: Decimal
    directional_score: Decimal
    entry_close: Decimal
    exit_close: Decimal
    forward_return: Decimal

    def __post_init__(self) -> None:
        signal_date = _strict_iso_date(self.signal_date, "signal_date")
        effective_date = _strict_iso_date(
            self.effective_date,
            "effective_date",
        )
        evaluation_date = _strict_iso_date(
            self.evaluation_date,
            "evaluation_date",
        )
        if not signal_date < effective_date < evaluation_date:
            raise ValueError(
                "dates must satisfy signal_date < effective_date "
                "< evaluation_date"
            )
        _validate_horizon(self.horizon)
        for value, name in (
            (self.low_position_score, "low_position_score"),
            (self.high_position_score, "high_position_score"),
            (self.directional_score, "directional_score"),
            (self.forward_return, "forward_return"),
        ):
            _finite_decimal(value, name)
        _positive_decimal(self.entry_close, "entry_close")
        _positive_decimal(self.exit_close, "exit_close")
        if (
            self.directional_score
            != self.low_position_score - self.high_position_score
        ):
            raise ValueError(
                "directional_score must equal low_position_score "
                "minus high_position_score"
            )
        if self.forward_return != self.exit_close / self.entry_close - ONE:
            raise ValueError(
                "forward_return must equal exit_close / entry_close - 1"
            )


@dataclass(frozen=True)
class MarketTimingQuantileMetrics:
    """Return summary for one equal-count score quantile."""

    quantile: int
    count: int
    score_min: Decimal | None
    score_max: Decimal | None
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
            self.score_min,
            self.score_max,
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
        for value, name in (
            (self.score_min, "score_min"),
            (self.score_max, "score_max"),
            (self.mean_forward_return, "mean_forward_return"),
            (self.median_forward_return, "median_forward_return"),
            (self.hit_rate, "hit_rate"),
        ):
            _finite_decimal(value, name)
        assert self.score_min is not None
        assert self.score_max is not None
        assert self.hit_rate is not None
        if self.score_min > self.score_max:
            raise ValueError("score_min must not exceed score_max")
        if not ZERO <= self.hit_rate <= ONE:
            raise ValueError("hit_rate must be between 0 and 1")


@dataclass(frozen=True)
class MarketTimingWalkForwardFold:
    """One chronological stability segment with no fitted threshold."""

    fold: int
    start_date: str | None
    end_date: str | None
    count: int
    spearman_correlation: Decimal | None

    def __post_init__(self) -> None:
        if (
            isinstance(self.fold, bool)
            or not isinstance(self.fold, int)
            or not 1 <= self.fold <= 4
        ):
            raise ValueError("fold must be an integer from 1 through 4")
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("count must be a non-negative integer")
        if self.count == 0:
            if self.start_date is not None or self.end_date is not None:
                raise ValueError("empty folds must not have dates")
        else:
            if self.start_date is None or self.end_date is None:
                raise ValueError("non-empty folds require dates")
            start = _strict_iso_date(self.start_date, "start_date")
            end = _strict_iso_date(self.end_date, "end_date")
            if start > end:
                raise ValueError("start_date must not exceed end_date")
        _optional_correlation(
            self.spearman_correlation,
            "spearman_correlation",
        )


@dataclass(frozen=True)
class MarketTimingHorizonValidation:
    """Continuous-score validation results for one horizon."""

    horizon: Horizon
    forward_sessions: int
    count: int
    observations: tuple[MarketTimingObservation, ...]
    spearman_correlation: Decimal | None
    quantiles: tuple[MarketTimingQuantileMetrics, ...]
    bootstrap_ci: tuple[Decimal, Decimal] | None
    walk_forward_folds: tuple[MarketTimingWalkForwardFold, ...]

    def __post_init__(self) -> None:
        _validate_horizon(self.horizon)
        required_sessions = {
            "short": 10,
            "medium_short": 60,
            "medium_long": 252,
        }[self.horizon]
        if self.forward_sessions != required_sessions:
            raise ValueError(
                f"forward_sessions must be {required_sessions} "
                f"for {self.horizon}"
            )
        observations = tuple(self.observations)
        quantiles = tuple(self.quantiles)
        folds = tuple(self.walk_forward_folds)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "quantiles", quantiles)
        object.__setattr__(self, "walk_forward_folds", folds)
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 0
        ):
            raise ValueError("count must be a non-negative integer")
        if self.count != len(observations):
            raise ValueError("count must equal the observation count")
        if any(
            not isinstance(item, MarketTimingObservation)
            or item.horizon != self.horizon
            for item in observations
        ):
            raise TypeError(
                "observations must contain matching "
                "MarketTimingObservation values"
            )
        dates = tuple(item.effective_date for item in observations)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise ValueError(
                "observation effective dates must be increasing and unique"
            )
        _optional_correlation(
            self.spearman_correlation,
            "spearman_correlation",
        )
        if any(
            not isinstance(item, MarketTimingQuantileMetrics)
            for item in quantiles
        ):
            raise TypeError(
                "quantiles must contain MarketTimingQuantileMetrics values"
            )
        if len(quantiles) != 5 or tuple(
            item.quantile for item in quantiles
        ) != (1, 2, 3, 4, 5):
            raise ValueError("quantiles must contain quantiles 1 through 5")
        if sum(item.count for item in quantiles) != self.count:
            raise ValueError("quantile counts must equal count")
        if any(
            not isinstance(item, MarketTimingWalkForwardFold)
            for item in folds
        ):
            raise TypeError(
                "walk_forward_folds must contain "
                "MarketTimingWalkForwardFold values"
            )
        if len(folds) != 4 or tuple(item.fold for item in folds) != (
            1,
            2,
            3,
            4,
        ):
            raise ValueError("walk_forward_folds must contain folds 1 through 4")
        if sum(item.count for item in folds) != self.count:
            raise ValueError("walk-forward fold counts must equal count")
        if self.bootstrap_ci is not None:
            if (
                not isinstance(self.bootstrap_ci, tuple)
                or len(self.bootstrap_ci) != 2
            ):
                raise TypeError("bootstrap_ci must be a two-value tuple")
            lower, upper = self.bootstrap_ci
            _correlation(lower, "bootstrap_ci lower")
            _correlation(upper, "bootstrap_ci upper")
            if lower > upper:
                raise ValueError(
                    "bootstrap_ci lower must not exceed upper"
                )
        if self.spearman_correlation is None and self.bootstrap_ci is not None:
            raise ValueError(
                "bootstrap_ci must be null when correlation is null"
            )


@dataclass(frozen=True)
class MarketTimingValidationSkipped:
    """A horizon omitted because its score or future window is unavailable."""

    signal_date: str
    effective_date: str
    horizon: Horizon
    reason: str

    def __post_init__(self) -> None:
        signal_date = _strict_iso_date(self.signal_date, "signal_date")
        effective_date = _strict_iso_date(
            self.effective_date,
            "effective_date",
        )
        if signal_date >= effective_date:
            raise ValueError("signal_date must be before effective_date")
        _validate_horizon(self.horizon)
        if self.reason not in (
            "missing_directional_scores",
            "incomplete_forward_window",
        ):
            raise ValueError("invalid skipped reason")


@dataclass(frozen=True)
class MarketTimingValidationRun:
    """Complete fixed-design validation output without an admission status."""

    asset_id: str
    version: str
    parameters: MarketTimingValidationParameters
    short: MarketTimingHorizonValidation
    medium_short: MarketTimingHorizonValidation
    medium_long: MarketTimingHorizonValidation
    skipped: tuple[MarketTimingValidationSkipped, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")
        if self.version != MARKET_TIMING_VALIDATION_VERSION:
            raise ValueError(
                "version must be market_timing_validation_v1"
            )
        if not isinstance(self.parameters, MarketTimingValidationParameters):
            raise TypeError(
                "parameters must be MarketTimingValidationParameters"
            )
        for value, horizon in (
            (self.short, "short"),
            (self.medium_short, "medium_short"),
            (self.medium_long, "medium_long"),
        ):
            if (
                not isinstance(value, MarketTimingHorizonValidation)
                or value.horizon != horizon
            ):
                raise TypeError(
                    f"{horizon} must be its matching horizon validation"
                )
        skipped = tuple(self.skipped)
        object.__setattr__(self, "skipped", skipped)
        if any(
            not isinstance(item, MarketTimingValidationSkipped)
            for item in skipped
        ):
            raise TypeError(
                "skipped must contain MarketTimingValidationSkipped values"
            )

    @property
    def horizons(self) -> dict[Horizon, MarketTimingHorizonValidation]:
        return {
            "short": self.short,
            "medium_short": self.medium_short,
            "medium_long": self.medium_long,
        }


class MarketTimingValidationEngine:
    """Validate every usable continuous horizon score against future returns."""

    @staticmethod
    def run(
        series: SSEIndexSeries,
        results: Sequence[SSEMarketTimingResult],
        parameters: MarketTimingValidationParameters | None = None,
    ) -> MarketTimingValidationRun:
        if not isinstance(series, SSEIndexSeries):
            raise TypeError("series must be SSEIndexSeries")
        if parameters is None:
            parameters = MarketTimingValidationParameters()
        if not isinstance(parameters, MarketTimingValidationParameters):
            raise TypeError(
                "parameters must be MarketTimingValidationParameters"
            )

        indexed_results = _validated_results(series, results)
        bars = series.bars
        index_by_date = {
            bar.trade_date: index for index, bar in enumerate(bars)
        }
        by_horizon: dict[Horizon, list[MarketTimingObservation]] = {
            horizon: [] for horizon in HORIZONS
        }
        skipped: list[MarketTimingValidationSkipped] = []

        for result in indexed_results:
            effective_index = index_by_date[result.effective_date]
            signals = {
                signal.horizon: signal for signal in result.signals
            }
            for horizon in HORIZONS:
                signal = signals[horizon]
                scores = _directional_scores(signal)
                if scores is None:
                    skipped.append(
                        _skipped(
                            result,
                            horizon,
                            "missing_directional_scores",
                        )
                    )
                    continue
                forward_sessions = parameters.forward_sessions(horizon)
                evaluation_index = effective_index + forward_sessions
                if evaluation_index >= len(bars):
                    skipped.append(
                        _skipped(
                            result,
                            horizon,
                            "incomplete_forward_window",
                        )
                    )
                    continue
                low_score, high_score = scores
                entry_close = bars[effective_index].close
                exit_close = bars[evaluation_index].close
                by_horizon[horizon].append(
                    MarketTimingObservation(
                        signal_date=result.as_of_date,
                        effective_date=result.effective_date,
                        evaluation_date=bars[evaluation_index].trade_date,
                        horizon=horizon,
                        low_position_score=low_score,
                        high_position_score=high_score,
                        directional_score=low_score - high_score,
                        entry_close=entry_close,
                        exit_close=exit_close,
                        forward_return=exit_close / entry_close - ONE,
                    )
                )

        validations = {
            horizon: _validate_horizon_observations(
                horizon,
                tuple(by_horizon[horizon]),
                parameters,
                horizon_index,
            )
            for horizon_index, horizon in enumerate(HORIZONS)
        }
        return MarketTimingValidationRun(
            asset_id=series.asset_id,
            version=MARKET_TIMING_VALIDATION_VERSION,
            parameters=parameters,
            short=validations["short"],
            medium_short=validations["medium_short"],
            medium_long=validations["medium_long"],
            skipped=tuple(skipped),
        )


def validate_market_timing(
    series: SSEIndexSeries,
    results: Sequence[SSEMarketTimingResult],
    parameters: MarketTimingValidationParameters | None = None,
) -> MarketTimingValidationRun:
    """Convenience entry point for the fixed v1 validation."""

    return MarketTimingValidationEngine.run(series, results, parameters)


def _validated_results(
    series: SSEIndexSeries,
    results: Sequence[SSEMarketTimingResult],
) -> tuple[SSEMarketTimingResult, ...]:
    index_by_date = {
        bar.trade_date: index for index, bar in enumerate(series.bars)
    }
    validated: list[SSEMarketTimingResult] = []
    seen_effective_dates: set[str] = set()
    for result in results:
        if not isinstance(result, SSEMarketTimingResult):
            raise TypeError(
                "results must contain SSEMarketTimingResult values"
            )
        if result.asset_id != series.asset_id:
            raise ValueError(
                "result asset_id does not match validation series: "
                f"{result.asset_id}"
            )
        if result.effective_date in seen_effective_dates:
            raise ValueError(
                "duplicate result effective_date: "
                f"{result.effective_date}"
            )
        seen_effective_dates.add(result.effective_date)
        as_of_index = index_by_date.get(result.as_of_date)
        if as_of_index is None:
            raise ValueError(
                "result as_of_date has no index bar: "
                f"{result.as_of_date}"
            )
        effective_index = index_by_date.get(result.effective_date)
        if effective_index is None:
            raise ValueError(
                "result effective_date has no index bar: "
                f"{result.effective_date}"
            )
        if as_of_index >= effective_index:
            raise ValueError(
                "result dates are not chronological in validation series"
            )
        validated.append(result)
    return tuple(
        sorted(
            validated,
            key=lambda item: (
                index_by_date[item.effective_date],
                item.as_of_date,
            ),
        )
    )


def _directional_scores(
    signal: HorizonTimingSignal,
) -> tuple[Decimal, Decimal] | None:
    low_score = signal.metrics.get("low_position_score")
    high_score = signal.metrics.get("high_position_score")
    if low_score is None or high_score is None:
        return None
    _finite_decimal(low_score, "low_position_score")
    _finite_decimal(high_score, "high_position_score")
    return low_score, high_score


def _skipped(
    result: SSEMarketTimingResult,
    horizon: Horizon,
    reason: str,
) -> MarketTimingValidationSkipped:
    return MarketTimingValidationSkipped(
        signal_date=result.as_of_date,
        effective_date=result.effective_date,
        horizon=horizon,
        reason=reason,
    )


def _validate_horizon_observations(
    horizon: Horizon,
    observations: tuple[MarketTimingObservation, ...],
    parameters: MarketTimingValidationParameters,
    horizon_index: int,
) -> MarketTimingHorizonValidation:
    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (
                item.effective_date,
                item.signal_date,
            ),
        )
    )
    pairs = tuple(
        (item.directional_score, item.forward_return) for item in ordered
    )
    correlation = _spearman_correlation(pairs)
    return MarketTimingHorizonValidation(
        horizon=horizon,
        forward_sessions=parameters.forward_sessions(horizon),
        count=len(ordered),
        observations=ordered,
        spearman_correlation=correlation,
        quantiles=_quantile_metrics(ordered, parameters.quantile_count),
        bootstrap_ci=_moving_block_bootstrap_ci(
            pairs,
            parameters.bootstrap_iterations,
            parameters.block_length,
            parameters.seed + horizon_index,
        ),
        walk_forward_folds=_walk_forward_folds(
            ordered,
            parameters.walk_forward_folds,
        ),
    )


def _quantile_metrics(
    observations: tuple[MarketTimingObservation, ...],
    quantile_count: int,
) -> tuple[MarketTimingQuantileMetrics, ...]:
    ordered = tuple(
        sorted(
            observations,
            key=lambda item: (
                item.directional_score,
                item.effective_date,
                item.signal_date,
            ),
        )
    )
    groups = _equal_contiguous_groups(ordered, quantile_count)
    output: list[MarketTimingQuantileMetrics] = []
    for index, group in enumerate(groups, start=1):
        if not group:
            output.append(
                MarketTimingQuantileMetrics(
                    quantile=index,
                    count=0,
                    score_min=None,
                    score_max=None,
                    mean_forward_return=None,
                    median_forward_return=None,
                    hit_rate=None,
                )
            )
            continue
        returns = tuple(item.forward_return for item in group)
        output.append(
            MarketTimingQuantileMetrics(
                quantile=index,
                count=len(group),
                score_min=group[0].directional_score,
                score_max=group[-1].directional_score,
                mean_forward_return=sum(returns, ZERO)
                / Decimal(len(returns)),
                median_forward_return=_median(returns),
                hit_rate=Decimal(sum(value > ZERO for value in returns))
                / Decimal(len(returns)),
            )
        )
    return tuple(output)


def _walk_forward_folds(
    observations: tuple[MarketTimingObservation, ...],
    fold_count: int,
) -> tuple[MarketTimingWalkForwardFold, ...]:
    groups = _equal_contiguous_groups(observations, fold_count)
    output: list[MarketTimingWalkForwardFold] = []
    for index, group in enumerate(groups, start=1):
        pairs = tuple(
            (item.directional_score, item.forward_return) for item in group
        )
        output.append(
            MarketTimingWalkForwardFold(
                fold=index,
                start_date=group[0].effective_date if group else None,
                end_date=group[-1].effective_date if group else None,
                count=len(group),
                spearman_correlation=_spearman_correlation(pairs),
            )
        )
    return tuple(output)


def _equal_contiguous_groups(
    values: tuple,
    group_count: int,
) -> tuple[tuple, ...]:
    quotient, remainder = divmod(len(values), group_count)
    groups: list[tuple] = []
    offset = 0
    for index in range(group_count):
        size = quotient + (1 if index < remainder else 0)
        groups.append(values[offset : offset + size])
        offset += size
    return tuple(groups)


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
    score_variance = sum(
        (value * value for value in score_centered),
        ZERO,
    )
    return_variance = sum(
        (value * value for value in return_centered),
        ZERO,
    )
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
    correlation = covariance / (score_variance * return_variance).sqrt()
    return max(-ONE, min(ONE, correlation))


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


def _moving_block_bootstrap_ci(
    pairs: tuple[tuple[Decimal, Decimal], ...],
    iterations: int,
    block_length: int,
    seed: int,
) -> tuple[Decimal, Decimal] | None:
    if _spearman_correlation(pairs) is None:
        return None
    score_ranks = _average_ranks(
        tuple(pair[0] for pair in pairs)
    )
    return_ranks = _average_ranks(
        tuple(pair[1] for pair in pairs)
    )
    rank_pairs = tuple(zip(score_ranks, return_ranks))
    sample_size = len(pairs)
    actual_block_length = min(block_length, sample_size)
    random_generator = random.Random(seed)
    correlations: list[Decimal] = []
    for _ in range(iterations):
        sample: list[tuple[Decimal, Decimal]] = []
        while len(sample) < sample_size:
            block_start = random_generator.randrange(
                sample_size - actual_block_length + 1
            )
            sample.extend(
                rank_pairs[
                    block_start : block_start + actual_block_length
                ]
            )
        correlation = _pearson_correlation(
            tuple(sample[:sample_size])
        )
        if correlation is not None:
            correlations.append(correlation)
    if not correlations:
        return None
    correlations.sort()
    return (
        _percentile(correlations, Decimal("0.025")),
        _percentile(correlations, Decimal("0.975")),
    )


def _pearson_correlation(
    pairs: tuple[tuple[Decimal, Decimal], ...],
) -> Decimal | None:
    if len(pairs) <= 1:
        return None
    left_mean = sum((item[0] for item in pairs), ZERO) / Decimal(
        len(pairs)
    )
    right_mean = sum((item[1] for item in pairs), ZERO) / Decimal(
        len(pairs)
    )
    left_centered = tuple(item[0] - left_mean for item in pairs)
    right_centered = tuple(item[1] - right_mean for item in pairs)
    left_variance = sum(
        (value * value for value in left_centered),
        ZERO,
    )
    right_variance = sum(
        (value * value for value in right_centered),
        ZERO,
    )
    if left_variance == ZERO or right_variance == ZERO:
        return None
    covariance = sum(
        (
            left_value * right_value
            for left_value, right_value in zip(
                left_centered,
                right_centered,
            )
        ),
        ZERO,
    )
    return covariance / (left_variance * right_variance).sqrt()


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


def _validate_horizon(value: str) -> None:
    if value not in HORIZONS:
        raise ValueError(
            "horizon must be short, medium_short, or medium_long"
        )


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


def _positive_decimal(value: Decimal, name: str) -> None:
    _finite_decimal(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _optional_correlation(value: Decimal | None, name: str) -> None:
    if value is not None:
        _correlation(value, name)


def _correlation(value: Decimal, name: str) -> None:
    _finite_decimal(value, name)
    if not -ONE <= value <= ONE:
        raise ValueError(f"{name} must be between -1 and 1")
