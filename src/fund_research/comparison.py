"""Actual-total-return comparison across ETF tools and indices."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .total_return import ETFTotalReturnResult


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class ReturnIndexPoint:
    observation_date: str
    value: Decimal

    def __post_init__(self) -> None:
        date.fromisoformat(self.observation_date)
        if (
            not isinstance(self.value, Decimal)
            or not self.value.is_finite()
            or self.value <= ZERO
        ):
            raise ValueError("value must be a positive finite Decimal")


@dataclass(frozen=True)
class ETFComparisonSeries:
    asset_id: str
    standard_symbol: str
    underlying_index_asset_id: str
    as_of_date: str
    available_at: str
    annual_expense_rate: Decimal
    market_price_return: tuple[ReturnIndexPoint, ...]
    nav_total_return: tuple[ReturnIndexPoint, ...]
    price_benchmark_return: tuple[ReturnIndexPoint, ...]
    benchmark_total_return: tuple[ReturnIndexPoint, ...]
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.standard_symbol, "standard_symbol"),
            (
                self.underlying_index_asset_id,
                "underlying_index_asset_id",
            ),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        date.fromisoformat(self.as_of_date)
        date.fromisoformat(self.available_at)
        if self.available_at > self.as_of_date:
            raise ValueError("available_at must not exceed as_of_date")
        if (
            not isinstance(self.annual_expense_rate, Decimal)
            or not self.annual_expense_rate.is_finite()
            or self.annual_expense_rate < ZERO
            or self.annual_expense_rate >= ONE
        ):
            raise ValueError(
                "annual_expense_rate must be a finite ratio in [0, 1)"
            )
        if not self.source_refs:
            raise ValueError("source_refs must not be empty")
        for points, name in (
            (self.market_price_return, "market_price_return"),
            (self.nav_total_return, "nav_total_return"),
        ):
            _validate_series(points, name, self.as_of_date)
        for points, name in (
            (self.price_benchmark_return, "price_benchmark_return"),
            (self.benchmark_total_return, "benchmark_total_return"),
        ):
            if points:
                _validate_series(points, name, self.as_of_date)

    @classmethod
    def from_total_return_result(
        cls,
        *,
        result: "ETFTotalReturnResult",
        standard_symbol: str,
        underlying_index_asset_id: str,
        available_at: str,
        annual_expense_rate: Decimal,
        price_benchmark_return: tuple[ReturnIndexPoint, ...],
        benchmark_total_return: tuple[ReturnIndexPoint, ...],
        source_refs: tuple[str, ...],
    ) -> "ETFComparisonSeries":
        return cls(
            asset_id=result.asset_id,
            standard_symbol=standard_symbol,
            underlying_index_asset_id=underlying_index_asset_id,
            as_of_date=result.as_of_date,
            available_at=available_at,
            annual_expense_rate=annual_expense_rate,
            market_price_return=tuple(
                ReturnIndexPoint(point.date, point.value)
                for point in result.market_price_return
            ),
            nav_total_return=tuple(
                ReturnIndexPoint(point.date, point.value)
                for point in result.total_return
            ),
            price_benchmark_return=price_benchmark_return,
            benchmark_total_return=benchmark_total_return,
            source_refs=source_refs,
        )


@dataclass(frozen=True)
class ETFComparisonRow:
    asset_id: str
    standard_symbol: str
    underlying_index_asset_id: str
    start_date: str
    end_date: str
    observation_count: int
    annual_expense_rate: Decimal
    market_price_return: Decimal
    nav_total_return: Decimal
    benchmark_total_return: Decimal | None
    annualized_nav_return: Decimal | None
    nav_max_drawdown: Decimal
    price_tracking_error: Decimal | None
    nav_tracking_error: Decimal | None
    nav_tracking_difference: Decimal | None
    same_index_rank: int
    source_refs: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ETFComparisonPanel:
    as_of_date: str
    rows: tuple[ETFComparisonRow, ...]
    methodology: tuple[str, ...]


class ETFComparisonService:
    """Compare realized ETF return paths without mixing index exposures."""

    def compare(
        self,
        *,
        as_of_date: str,
        series: tuple[ETFComparisonSeries, ...],
        annualization_days: int = 252,
    ) -> ETFComparisonPanel:
        date.fromisoformat(as_of_date)
        if not series:
            raise ValueError("series must not be empty")
        if annualization_days <= 0:
            raise ValueError("annualization_days must be positive")
        if len({item.asset_id for item in series}) != len(series):
            raise ValueError("asset_id must be unique")

        preliminary = [
            _calculate_row(
                item,
                as_of_date=as_of_date,
                annualization_days=annualization_days,
            )
            for item in series
        ]
        ranks: dict[str, int] = {}
        for index_id in {
            item.underlying_index_asset_id for item in preliminary
        }:
            comparable = sorted(
                (
                    item
                    for item in preliminary
                    if item.underlying_index_asset_id == index_id
                ),
                key=lambda item: (
                    _optional_metric_key(item.nav_tracking_error),
                    item.annual_expense_rate,
                    -item.nav_total_return,
                    item.standard_symbol,
                ),
            )
            for rank, item in enumerate(comparable, start=1):
                ranks[item.asset_id] = rank
        rows = tuple(
            ETFComparisonRow(
                **{
                    **item.__dict__,
                    "same_index_rank": ranks[item.asset_id],
                }
            )
            for item in sorted(
                preliminary,
                key=lambda item: (
                    item.underlying_index_asset_id,
                    ranks[item.asset_id],
                ),
            )
        )
        return ETFComparisonPanel(
            as_of_date=as_of_date,
            rows=rows,
            methodology=(
                "actual_cash_distribution_reinvested_total_return",
                "market_and_nav_tracking_quality_are_separate",
                "same_index_rank_only",
                "different_index_returns_are_descriptive_not_ranked",
                "annual_expense_rate_is_disclosed_not_double_deducted",
            ),
        )


def _calculate_row(
    item: ETFComparisonSeries,
    *,
    as_of_date: str,
    annualization_days: int,
) -> ETFComparisonRow:
    if item.as_of_date > as_of_date or item.available_at > as_of_date:
        raise ValueError(
            f"{item.asset_id} contains data unavailable at as_of_date"
        )
    market = _by_date(item.market_price_return)
    nav = _by_date(item.nav_total_return)
    price_benchmark = _by_date(item.price_benchmark_return)
    total_return_benchmark = _by_date(item.benchmark_total_return)
    common_dates = sorted(set(market) & set(nav))
    if len(common_dates) < 2:
        raise ValueError(
            f"{item.asset_id} requires at least two common observations"
        )
    market_values = [market[value_date] for value_date in common_dates]
    nav_values = [nav[value_date] for value_date in common_dates]
    nav_total_return = nav_values[-1] / nav_values[0] - ONE
    price_tracking_error, price_limitation = _aligned_tracking_error(
        market,
        price_benchmark,
        annualization_days,
        "price_benchmark_unavailable",
    )
    nav_tracking_error, nav_limitation = _aligned_tracking_error(
        nav,
        total_return_benchmark,
        annualization_days,
        "total_return_benchmark_unavailable",
    )
    total_common_dates = sorted(set(nav) & set(total_return_benchmark))
    benchmark_total_return = (
        total_return_benchmark[total_common_dates[-1]]
        / total_return_benchmark[total_common_dates[0]]
        - ONE
        if len(total_common_dates) >= 2
        else None
    )
    interval_count = len(common_dates) - 1
    annualized_nav_return = (
        (ONE + nav_total_return)
        ** (Decimal(annualization_days) / Decimal(interval_count))
        - ONE
        if nav_total_return > -ONE
        else None
    )
    return ETFComparisonRow(
        asset_id=item.asset_id,
        standard_symbol=item.standard_symbol,
        underlying_index_asset_id=item.underlying_index_asset_id,
        start_date=common_dates[0],
        end_date=common_dates[-1],
        observation_count=len(common_dates),
        annual_expense_rate=item.annual_expense_rate,
        market_price_return=market_values[-1] / market_values[0] - ONE,
        nav_total_return=nav_total_return,
        benchmark_total_return=benchmark_total_return,
        annualized_nav_return=annualized_nav_return,
        nav_max_drawdown=_max_drawdown(nav_values),
        price_tracking_error=price_tracking_error,
        nav_tracking_error=nav_tracking_error,
        nav_tracking_difference=(
            nav[total_common_dates[-1]] / nav[total_common_dates[0]]
            - ONE
            - benchmark_total_return
            if benchmark_total_return is not None
            else None
        ),
        same_index_rank=0,
        source_refs=item.source_refs,
        limitations=tuple(
            value
            for value in (price_limitation, nav_limitation)
            if value is not None
        ),
    )


def _validate_series(
    points: tuple[ReturnIndexPoint, ...],
    name: str,
    as_of_date: str,
) -> None:
    if len(points) < 2:
        raise ValueError(f"{name} must contain at least two points")
    dates = [point.observation_date for point in points]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError(f"{name} dates must be unique and increasing")
    if dates[-1] > as_of_date:
        raise ValueError(f"{name} exceeds as_of_date")


def _by_date(
    points: tuple[ReturnIndexPoint, ...],
) -> dict[str, Decimal]:
    return {point.observation_date: point.value for point in points}


def _period_returns(values: list[Decimal]) -> list[Decimal]:
    return [
        current / previous - ONE
        for previous, current in zip(values, values[1:])
    ]


def _max_drawdown(values: list[Decimal]) -> Decimal:
    peak = values[0]
    worst = ZERO
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - ONE)
    return worst


def _aligned_tracking_error(
    asset_values: dict[str, Decimal],
    benchmark_values: dict[str, Decimal],
    annualization_days: int,
    missing_limitation: str,
) -> tuple[Decimal | None, str | None]:
    common_dates = sorted(set(asset_values) & set(benchmark_values))
    if len(common_dates) < 2:
        return None, missing_limitation
    return (
        _tracking_error(
            _period_returns(
                [asset_values[value_date] for value_date in common_dates]
            ),
            _period_returns(
                [
                    benchmark_values[value_date]
                    for value_date in common_dates
                ]
            ),
            annualization_days,
        ),
        None,
    )


def _optional_metric_key(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("Infinity")


def _tracking_error(
    asset_returns: list[Decimal],
    benchmark_returns: list[Decimal],
    annualization_days: int,
) -> Decimal:
    active = [
        float(asset - benchmark)
        for asset, benchmark in zip(asset_returns, benchmark_returns)
    ]
    mean = sum(active) / len(active)
    variance = sum((value - mean) ** 2 for value in active) / len(
        active
    )
    return Decimal(
        str(math.sqrt(variance) * math.sqrt(annualization_days))
    )
