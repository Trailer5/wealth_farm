"""Common-date benchmark alignment and relative metrics."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any

from src.factor_engineering import FactorValue

from .models import (
    BENCHMARK_CALCULATION_VERSION,
    AlignedReturnSeries,
    BenchmarkCalculationResult,
    BenchmarkKind,
    BenchmarkMetricParameters,
    CompositeBenchmarkDefinition,
)


class BenchmarkMetricCalculator:
    def calculate(
        self,
        *,
        asset_id: str,
        benchmark_asset_id: str,
        benchmark_kind: BenchmarkKind,
        asset_records: list[dict[str, Any]],
        benchmark_records: list[dict[str, Any]],
        asset_dataset: str,
        benchmark_dataset: str,
        parameters: BenchmarkMetricParameters | None = None,
        benchmark_definition: CompositeBenchmarkDefinition | None = None,
        benchmark_metadata: dict[str, Any] | None = None,
    ) -> BenchmarkCalculationResult:
        config = parameters or BenchmarkMetricParameters()
        asset_closes = _closes_by_date(asset_records)
        benchmark_closes = _closes_by_date(benchmark_records)
        intervals = _matching_return_intervals(
            asset_closes,
            benchmark_closes,
        )
        if config.window is not None:
            intervals = intervals[-config.window :]
        if not intervals:
            raise ValueError(
                "Benchmark comparison requires a matching "
                "observation interval"
            )
        input_start_date = intervals[0][0]
        return_dates = [interval[1] for interval in intervals]
        asset_returns = [interval[2] for interval in intervals]
        benchmark_returns = [interval[3] for interval in intervals]
        asset_cumulative = _cumulative_returns(asset_returns)
        benchmark_cumulative = _cumulative_returns(
            benchmark_returns
        )
        aligned = AlignedReturnSeries(
            dates=tuple(return_dates),
            asset_returns=tuple(asset_returns),
            benchmark_returns=tuple(benchmark_returns),
            asset_cumulative=tuple(asset_cumulative),
            benchmark_cumulative=tuple(benchmark_cumulative),
        )
        parameters_dict = {
            **config.to_dict(),
            "benchmark_asset_id": benchmark_asset_id,
            "benchmark_kind": benchmark_kind.value,
            **(benchmark_metadata or {}),
        }
        source_dataset = (
            f"{asset_dataset}|{benchmark_dataset}"
        )
        computed_at = datetime.now(timezone.utc).isoformat()
        common = {
            "asset_id": asset_id,
            "calculation_version": BENCHMARK_CALCULATION_VERSION,
            "parameters": parameters_dict,
            "input_start_date": input_start_date,
            "input_end_date": return_dates[-1],
            "source_dataset": source_dataset,
            "computed_at": computed_at,
        }
        series: list[FactorValue] = []
        for index, trade_date in enumerate(return_dates):
            for name, value in (
                (
                    "asset_cumulative_return",
                    asset_cumulative[index],
                ),
                (
                    "benchmark_cumulative_return",
                    benchmark_cumulative[index],
                ),
                (
                    "active_return",
                    asset_returns[index]
                    - benchmark_returns[index],
                ),
            ):
                series.append(
                    FactorValue(
                        factor_name=name,
                        as_of_date=trade_date,
                        value=value,
                        **common,
                    )
                )

        metrics: list[FactorValue] = []
        metric_common = {
            **common,
            "as_of_date": return_dates[-1],
        }
        asset_total = asset_cumulative[-1]
        benchmark_total = benchmark_cumulative[-1]
        for name, value in (
            ("asset_total_return", asset_total),
            ("benchmark_total_return", benchmark_total),
            (
                "excess_total_return",
                asset_total - benchmark_total,
            ),
            (
                "cumulative_tracking_difference",
                asset_total - benchmark_total,
            ),
            (
                "common_observation_count",
                float(len(return_dates)),
            ),
        ):
            metrics.append(
                FactorValue(
                    factor_name=name,
                    value=value,
                    **metric_common,
                )
            )

        active_returns = [
            asset_return - benchmark_return
            for asset_return, benchmark_return in zip(
                asset_returns,
                benchmark_returns,
            )
        ]
        tracking_error_daily = _population_std(active_returns)
        tracking_error = tracking_error_daily * math.sqrt(
            config.annualization_days
        )
        metrics.append(
            FactorValue(
                factor_name="tracking_error",
                value=tracking_error,
                **metric_common,
            )
        )
        if tracking_error > 0:
            metrics.append(
                FactorValue(
                    factor_name="information_ratio",
                    value=(
                        _mean(active_returns)
                        * config.annualization_days
                        / tracking_error
                    ),
                    **metric_common,
                )
            )

        benchmark_variance = _population_variance(
            benchmark_returns
        )
        asset_std = _population_std(asset_returns)
        benchmark_std = math.sqrt(benchmark_variance)
        if benchmark_variance > 0:
            covariance = _population_covariance(
                asset_returns,
                benchmark_returns,
            )
            beta = covariance / benchmark_variance
            daily_risk_free = (
                (1.0 + config.risk_free_rate)
                ** (1.0 / config.annualization_days)
                - 1.0
            )
            alpha = (
                _mean(asset_returns)
                - daily_risk_free
                - beta
                * (
                    _mean(benchmark_returns)
                    - daily_risk_free
                )
            ) * config.annualization_days
            metrics.extend(
                [
                    FactorValue(
                        factor_name="beta",
                        value=beta,
                        **metric_common,
                    ),
                    FactorValue(
                        factor_name="alpha",
                        value=alpha,
                        **metric_common,
                    ),
                ]
            )
            if asset_std > 0 and benchmark_std > 0:
                metrics.append(
                    FactorValue(
                        factor_name="correlation",
                        value=(
                            covariance
                            / (asset_std * benchmark_std)
                        ),
                        **metric_common,
                    )
                )
        series.sort(
            key=lambda item: (item.as_of_date, item.factor_name)
        )
        metrics.sort(key=lambda item: item.factor_name)
        return BenchmarkCalculationResult(
            asset_id=asset_id,
            benchmark_asset_id=benchmark_asset_id,
            benchmark_kind=benchmark_kind,
            parameters=config,
            aligned=aligned,
            series=tuple(series),
            metrics=tuple(metrics),
            benchmark_definition=benchmark_definition,
        )


def _closes_by_date(
    records: list[dict[str, Any]],
) -> dict[str, float]:
    closes: dict[str, float] = {}
    for record in records:
        trade_date = str(record.get("trade_date", "")).strip()
        close_value = record.get("close")
        if not trade_date or close_value in (None, ""):
            continue
        date.fromisoformat(trade_date)
        close = float(close_value)
        if close <= 0:
            raise ValueError(f"close must be positive on {trade_date}")
        if trade_date in closes:
            raise ValueError(f"duplicate trade_date: {trade_date}")
        closes[trade_date] = close
    return closes


def _matching_return_intervals(
    asset_closes: dict[str, float],
    benchmark_closes: dict[str, float],
) -> list[tuple[str, str, float, float]]:
    asset_previous = _previous_dates(asset_closes)
    benchmark_previous = _previous_dates(benchmark_closes)
    intervals: list[tuple[str, str, float, float]] = []
    for current in sorted(set(asset_closes) & set(benchmark_closes)):
        asset_prior = asset_previous.get(current)
        benchmark_prior = benchmark_previous.get(current)
        if (
            asset_prior is None
            or asset_prior != benchmark_prior
        ):
            continue
        intervals.append(
            (
                asset_prior,
                current,
                asset_closes[current]
                / asset_closes[asset_prior]
                - 1.0,
                benchmark_closes[current]
                / benchmark_closes[benchmark_prior]
                - 1.0,
            )
        )
    return intervals


def _previous_dates(
    closes: dict[str, float],
) -> dict[str, str]:
    dates = sorted(closes)
    return {
        current: previous
        for previous, current in zip(dates, dates[1:])
    }


def _cumulative_returns(returns: list[float]) -> list[float]:
    cumulative = 1.0
    result = []
    for value in returns:
        cumulative *= 1.0 + value
        result.append(cumulative - 1.0)
    return result


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _population_variance(values: list[float]) -> float:
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / len(
        values
    )


def _population_std(values: list[float]) -> float:
    return math.sqrt(_population_variance(values))


def _population_covariance(
    left: list[float],
    right: list[float],
) -> float:
    left_mean = _mean(left)
    right_mean = _mean(right)
    return sum(
        (left_value - left_mean)
        * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    ) / len(left)
