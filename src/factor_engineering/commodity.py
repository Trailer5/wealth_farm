"""Commodity-return sensitivity for cyclical assets and commodity ETFs."""

from __future__ import annotations

from datetime import date, datetime, timezone

from .models import FactorValue
from .sqlite_repository import SQLiteFactorRepository


COMMODITY_SENSITIVITY_VERSION = "commodity_sensitivity_v1"


class CommoditySensitivityService:
    def __init__(self, repository: SQLiteFactorRepository) -> None:
        self.repository = repository
        self.repository.initialize()

    def calculate_and_store(
        self,
        *,
        asset_id: str,
        asset_records: list[dict[str, object]],
        benchmarks: dict[str, list[dict[str, object]]],
        weights: dict[str, float],
        as_of_date: str,
    ) -> FactorValue:
        if set(benchmarks) != set(weights):
            raise ValueError(
                "Commodity benchmarks and weights must match"
            )
        if not weights or abs(sum(weights.values()) - 1.0) > 1e-9:
            raise ValueError("Commodity weights must sum to 1")
        if any(weight < 0 for weight in weights.values()):
            raise ValueError("Commodity weights must be non-negative")
        asset_prices = _prices(asset_records)
        benchmark_prices = {
            name: _prices(records)
            for name, records in benchmarks.items()
        }
        intervals = _matching_return_intervals(
            asset_prices=asset_prices,
            benchmark_prices=benchmark_prices,
            weights=weights,
            as_of_date=as_of_date,
        )
        if len(intervals) < 2:
            raise ValueError(
                "Commodity sensitivity requires two matching "
                "return intervals"
            )
        asset_returns = [interval[2] for interval in intervals]
        commodity_returns = [interval[3] for interval in intervals]
        mean_asset = sum(asset_returns) / len(asset_returns)
        mean_commodity = (
            sum(commodity_returns) / len(commodity_returns)
        )
        variance = sum(
            (value - mean_commodity) ** 2
            for value in commodity_returns
        ) / len(commodity_returns)
        if variance == 0:
            raise ValueError("Commodity return variance is zero")
        covariance = sum(
            (asset - mean_asset)
            * (commodity - mean_commodity)
            for asset, commodity in zip(
                asset_returns,
                commodity_returns,
            )
        ) / len(asset_returns)
        value = FactorValue(
            asset_id=asset_id,
            factor_name="commodity_sensitivity",
            as_of_date=intervals[-1][1],
            value=covariance / variance,
            calculation_version=COMMODITY_SENSITIVITY_VERSION,
            parameters={
                "weights": dict(sorted(weights.items())),
                "alignment": "matching_observation_intervals",
                "method": "return_beta",
            },
            input_start_date=intervals[0][0],
            input_end_date=intervals[-1][1],
            source_dataset=",".join(sorted(benchmarks)),
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.repository.upsert_values([value])
        return value


def _prices(
    records: list[dict[str, object]],
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for record in records:
        value_date = str(
            record.get("trade_date") or record.get("date") or ""
        )[:10]
        close = record.get("close")
        if value_date and close is not None:
            date.fromisoformat(value_date)
            value = float(close)
            if value <= 0:
                raise ValueError(
                    f"close must be positive on {value_date}"
                )
            if value_date in prices:
                raise ValueError(
                    f"duplicate trade_date: {value_date}"
                )
            prices[value_date] = value
    return prices


def _matching_return_intervals(
    *,
    asset_prices: dict[str, float],
    benchmark_prices: dict[str, dict[str, float]],
    weights: dict[str, float],
    as_of_date: str,
) -> list[tuple[str, str, float, float]]:
    date.fromisoformat(as_of_date)
    all_prices = {
        "asset": asset_prices,
        **benchmark_prices,
    }
    previous_dates = {
        name: _previous_dates(prices)
        for name, prices in all_prices.items()
    }
    common_dates = set(asset_prices)
    for prices in benchmark_prices.values():
        common_dates &= set(prices)
    intervals: list[tuple[str, str, float, float]] = []
    for current in sorted(
        value for value in common_dates if value <= as_of_date
    ):
        priors = {
            previous_dates[name].get(current)
            for name in all_prices
        }
        if None in priors or len(priors) != 1:
            continue
        previous = next(iter(priors))
        intervals.append(
            (
                previous,
                current,
                asset_prices[current]
                / asset_prices[previous]
                - 1.0,
                sum(
                    weights[name]
                    * (
                        benchmark_prices[name][current]
                        / benchmark_prices[name][previous]
                        - 1.0
                    )
                    for name in weights
                ),
            )
        )
    return intervals


def _previous_dates(
    prices: dict[str, float],
) -> dict[str, str]:
    dates = sorted(prices)
    return {
        current: previous
        for previous, current in zip(dates, dates[1:])
    }
