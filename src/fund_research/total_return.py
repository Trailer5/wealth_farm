"""Point-in-time ETF NAV, corporate-action, and total-return calculations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypeVar

from src.benchmark_analysis import (
    BenchmarkCalculationResult,
    BenchmarkKind,
    BenchmarkMetricCalculator,
    BenchmarkMetricParameters,
)


ZERO = Decimal("0")
ONE = Decimal("1")
INDEX_BASE = Decimal("100")
ETF_TOTAL_RETURN_VERSION = "etf_total_return_v1"
T = TypeVar("T")


@dataclass(frozen=True)
class ETFNavPoint:
    asset_id: str
    date: str
    unit_nav: Decimal
    accumulated_nav: Decimal | None
    available_at: str
    source: str

    def __post_init__(self) -> None:
        _validate_identity(self.asset_id, self.source)
        observation_date = _parse_date(self.date, "date")
        available_at = _parse_date(self.available_at, "available_at")
        if available_at < observation_date:
            raise ValueError("available_at must not precede NAV date")
        _validate_positive_decimal(self.unit_nav, "unit_nav")
        if self.accumulated_nav is not None:
            _validate_positive_decimal(
                self.accumulated_nav,
                "accumulated_nav",
            )


@dataclass(frozen=True)
class ETFDistributionEvent:
    ex_date: str
    cash_per_share: Decimal
    available_at: str
    source: str

    def __post_init__(self) -> None:
        _parse_date(self.ex_date, "ex_date")
        _parse_date(self.available_at, "available_at")
        _validate_source(self.source)
        _validate_positive_decimal(
            self.cash_per_share,
            "cash_per_share",
        )


@dataclass(frozen=True)
class ETFSplitEvent:
    effective_date: str
    ratio: Decimal
    available_at: str
    source: str

    def __post_init__(self) -> None:
        _parse_date(self.effective_date, "effective_date")
        _parse_date(self.available_at, "available_at")
        _validate_source(self.source)
        _validate_positive_decimal(self.ratio, "ratio")


@dataclass(frozen=True)
class ETFMarketPricePoint:
    asset_id: str
    date: str
    close: Decimal
    available_at: str
    source: str

    def __post_init__(self) -> None:
        _validate_identity(self.asset_id, self.source)
        observation_date = _parse_date(self.date, "date")
        available_at = _parse_date(self.available_at, "available_at")
        if available_at < observation_date:
            raise ValueError("available_at must not precede market date")
        _validate_positive_decimal(self.close, "close")


@dataclass(frozen=True)
class ETFReturnIndexPoint:
    date: str
    value: Decimal

    def __post_init__(self) -> None:
        _parse_date(self.date, "date")
        _validate_positive_decimal(self.value, "value")


@dataclass(frozen=True)
class ETFTotalReturnPoint:
    date: str
    market_close: Decimal
    adjusted_nav: Decimal
    total_return_index: Decimal
    premium_discount: Decimal

    def __post_init__(self) -> None:
        _parse_date(self.date, "date")
        _validate_positive_decimal(self.market_close, "market_close")
        _validate_positive_decimal(self.adjusted_nav, "adjusted_nav")
        _validate_positive_decimal(
            self.total_return_index,
            "total_return_index",
        )
        _validate_finite_decimal(
            self.premium_discount,
            "premium_discount",
        )


@dataclass(frozen=True)
class ETFTotalReturnCoverage:
    nav_observations: int
    market_price_observations: int
    aligned_observations: int
    distribution_events: int
    split_events: int
    distribution_history_complete: bool
    split_history_complete: bool
    complete: bool
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class ETFTotalReturnResult:
    asset_id: str
    as_of_date: str
    points: tuple[ETFTotalReturnPoint, ...]
    market_price_return: tuple[ETFReturnIndexPoint, ...]
    nav_return: tuple[ETFReturnIndexPoint, ...]
    total_return: tuple[ETFReturnIndexPoint, ...]
    coverage: ETFTotalReturnCoverage
    calculation_version: str = ETF_TOTAL_RETURN_VERSION

    @property
    def limitations(self) -> tuple[str, ...]:
        return self.coverage.limitations


@dataclass(frozen=True)
class ETFTrackingQualityResult:
    """Independent price and NAV-total-return benchmark comparisons."""

    market_price: BenchmarkCalculationResult
    nav_total_return: BenchmarkCalculationResult

    @property
    def price_tracking_error(self) -> float:
        return _metric_value(self.market_price, "tracking_error")

    @property
    def nav_tracking_error(self) -> float:
        return _metric_value(self.nav_total_return, "tracking_error")


class ETFTotalReturnCalculator:
    """Build separate market-price, NAV, and reinvested total-return paths."""

    def calculate(
        self,
        *,
        asset_id: str,
        as_of_date: str,
        nav_points: Sequence[ETFNavPoint],
        market_prices: Sequence[ETFMarketPricePoint],
        distributions: Sequence[ETFDistributionEvent] = (),
        splits: Sequence[ETFSplitEvent] = (),
        distribution_history_complete: bool = False,
        split_history_complete: bool = False,
    ) -> ETFTotalReturnResult:
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id must not be blank")
        _parse_date(as_of_date, "as_of_date")
        if not nav_points:
            raise ValueError("nav_points must not be empty")
        if not market_prices:
            raise ValueError("market_prices must not be empty")
        if any(point.asset_id != asset_id for point in nav_points):
            raise ValueError("NAV points must match asset_id")
        if any(point.asset_id != asset_id for point in market_prices):
            raise ValueError("market prices must match asset_id")
        _reject_unavailable_inputs(
            as_of_date,
            nav_points,
            market_prices,
            distributions,
            splits,
        )

        nav_by_date = _unique_by_date(nav_points, "date", "NAV")
        price_by_date = _unique_by_date(
            market_prices,
            "date",
            "market price",
        )
        ordered_dates = sorted(nav_by_date)
        first_date = ordered_dates[0]
        last_date = ordered_dates[-1]
        distribution_by_date = _events_by_date(
            distributions,
            "ex_date",
        )
        split_by_date = _events_by_date(splits, "effective_date")
        limitations = _coverage_limitations(
            distribution_history_complete,
            split_history_complete,
            distributions,
            splits,
        )

        nav_date_set = set(ordered_dates)
        for event_date in sorted(distribution_by_date):
            if first_date <= event_date <= last_date and event_date not in nav_date_set:
                limitations.append(
                    f"distribution_event_without_nav:{event_date}"
                )
        for event_date in sorted(split_by_date):
            if first_date <= event_date <= last_date and event_date not in nav_date_set:
                limitations.append(
                    f"split_event_without_nav:{event_date}"
                )
        if first_date in distribution_by_date:
            limitations.append(
                f"distribution_on_initial_nav_not_reconstructed:{first_date}"
            )
        if first_date in split_by_date:
            limitations.append(
                f"split_on_initial_nav_not_reconstructed:{first_date}"
            )

        missing_prices = nav_date_set - set(price_by_date)
        if missing_prices:
            limitations.append(
                f"market_price_missing_on_nav_dates:{len(missing_prices)}"
            )

        initial_nav = nav_by_date[first_date].unit_nav
        split_factor = ONE
        previous_nav = initial_nav
        running_total_index = INDEX_BASE
        adjusted_nav_by_date: dict[str, Decimal] = {}
        nav_index_by_date: dict[str, Decimal] = {}
        total_index_by_date: dict[str, Decimal] = {}
        split_factor_by_date: dict[str, Decimal] = {}

        for value_date in ordered_dates:
            nav = nav_by_date[value_date].unit_nav
            if value_date != first_date:
                date_splits = split_by_date.get(value_date, ())
                ratio = _product(event.ratio for event in date_splits)
                cash_per_share = sum(
                    (
                        event.cash_per_share
                        for event in distribution_by_date.get(
                            value_date,
                            (),
                        )
                    ),
                    ZERO,
                )
                running_total_index *= (
                    nav * ratio + cash_per_share
                ) / previous_nav
                split_factor *= ratio
            adjusted_nav = nav * split_factor
            adjusted_nav_by_date[value_date] = adjusted_nav
            split_factor_by_date[value_date] = split_factor
            nav_index_by_date[value_date] = (
                adjusted_nav / initial_nav * INDEX_BASE
            )
            total_index_by_date[value_date] = (
                running_total_index
            )
            previous_nav = nav

        common_dates = sorted(nav_date_set & set(price_by_date))
        if not common_dates:
            raise ValueError(
                "NAV and market prices require a common observation date"
            )
        initial_adjusted_price = (
            price_by_date[common_dates[0]].close
            * split_factor_by_date[common_dates[0]]
        )
        market_index_by_date = {
            value_date: (
                price_by_date[value_date].close
                * split_factor_by_date[value_date]
                / initial_adjusted_price
                * INDEX_BASE
            )
            for value_date in common_dates
        }
        points = tuple(
            ETFTotalReturnPoint(
                date=value_date,
                market_close=price_by_date[value_date].close,
                adjusted_nav=adjusted_nav_by_date[value_date],
                total_return_index=total_index_by_date[value_date],
                premium_discount=(
                    price_by_date[value_date].close
                    / nav_by_date[value_date].unit_nav
                    - ONE
                ),
            )
            for value_date in common_dates
        )
        limitations_tuple = tuple(dict.fromkeys(limitations))
        coverage = ETFTotalReturnCoverage(
            nav_observations=len(nav_points),
            market_price_observations=len(market_prices),
            aligned_observations=len(points),
            distribution_events=len(distributions),
            split_events=len(splits),
            distribution_history_complete=distribution_history_complete,
            split_history_complete=split_history_complete,
            complete=not limitations_tuple,
            limitations=limitations_tuple,
        )
        return ETFTotalReturnResult(
            asset_id=asset_id,
            as_of_date=as_of_date,
            points=points,
            market_price_return=_series(
                common_dates,
                market_index_by_date,
            ),
            nav_return=_series(common_dates, nav_index_by_date),
            total_return=_series(common_dates, total_index_by_date),
            coverage=coverage,
        )


class ETFTrackingQualityCalculator:
    """Compare two explicitly separate ETF and benchmark return paths."""

    def __init__(
        self,
        calculator: BenchmarkMetricCalculator | None = None,
    ) -> None:
        self.calculator = calculator or BenchmarkMetricCalculator()

    def calculate(
        self,
        *,
        asset_id: str,
        price_benchmark_asset_id: str,
        total_return_benchmark_asset_id: str,
        market_price_series: Sequence[ETFReturnIndexPoint],
        price_benchmark_series: Sequence[ETFReturnIndexPoint],
        nav_total_return_series: Sequence[ETFReturnIndexPoint],
        total_return_benchmark_series: Sequence[ETFReturnIndexPoint],
        benchmark_kind: BenchmarkKind = BenchmarkKind.INDEX,
        parameters: BenchmarkMetricParameters | None = None,
    ) -> ETFTrackingQualityResult:
        price_result = self.calculator.calculate(
            asset_id=asset_id,
            benchmark_asset_id=price_benchmark_asset_id,
            benchmark_kind=benchmark_kind,
            asset_records=_benchmark_records(market_price_series),
            benchmark_records=_benchmark_records(
                price_benchmark_series
            ),
            asset_dataset="etf_market_price_return_index",
            benchmark_dataset="price_return_benchmark_index",
            parameters=parameters,
        )
        nav_result = self.calculator.calculate(
            asset_id=asset_id,
            benchmark_asset_id=total_return_benchmark_asset_id,
            benchmark_kind=benchmark_kind,
            asset_records=_benchmark_records(nav_total_return_series),
            benchmark_records=_benchmark_records(
                total_return_benchmark_series
            ),
            asset_dataset="etf_nav_total_return_index",
            benchmark_dataset="total_return_benchmark_index",
            parameters=parameters,
        )
        return ETFTrackingQualityResult(
            market_price=price_result,
            nav_total_return=nav_result,
        )


def _reject_unavailable_inputs(
    as_of_date: str,
    nav_points: Sequence[ETFNavPoint],
    market_prices: Sequence[ETFMarketPricePoint],
    distributions: Sequence[ETFDistributionEvent],
    splits: Sequence[ETFSplitEvent],
) -> None:
    dated_inputs = [
        *((point.date, point.available_at) for point in nav_points),
        *((point.date, point.available_at) for point in market_prices),
        *((event.ex_date, event.available_at) for event in distributions),
        *((event.effective_date, event.available_at) for event in splits),
    ]
    if any(value_date > as_of_date for value_date, _ in dated_inputs):
        raise ValueError("input observation date exceeds as_of_date")
    if any(available_at > as_of_date for _, available_at in dated_inputs):
        raise ValueError("input available_at exceeds as_of_date")


def _coverage_limitations(
    distribution_history_complete: bool,
    split_history_complete: bool,
    distributions: Sequence[ETFDistributionEvent],
    splits: Sequence[ETFSplitEvent],
) -> list[str]:
    limitations: list[str] = []
    if not distribution_history_complete:
        limitations.append(
            "distribution_history_incomplete_or_unverified"
        )
        if not distributions:
            limitations.append(
                "distribution_events_absent_but_not_confirmed_none"
            )
    if not split_history_complete:
        limitations.append("split_history_incomplete_or_unverified")
        if not splits:
            limitations.append(
                "split_events_absent_but_not_confirmed_none"
            )
    return limitations


def _series(
    dates: Sequence[str],
    values: dict[str, Decimal],
) -> tuple[ETFReturnIndexPoint, ...]:
    return tuple(
        ETFReturnIndexPoint(value_date, values[value_date])
        for value_date in dates
    )


def _benchmark_records(
    points: Sequence[ETFReturnIndexPoint],
) -> list[dict[str, object]]:
    return [
        {"trade_date": point.date, "close": float(point.value)}
        for point in points
    ]


def _events_by_date(
    events: Sequence[T],
    date_attribute: str,
) -> dict[str, tuple[T, ...]]:
    grouped: dict[str, list[T]] = {}
    for event in events:
        grouped.setdefault(
            str(getattr(event, date_attribute)),
            [],
        ).append(event)
    return {
        value_date: tuple(date_events)
        for value_date, date_events in grouped.items()
    }


def _unique_by_date(
    values: Sequence[T],
    date_attribute: str,
    label: str,
) -> dict[str, T]:
    result: dict[str, T] = {}
    for value in values:
        value_date = str(getattr(value, date_attribute))
        if value_date in result:
            raise ValueError(f"duplicate {label} date: {value_date}")
        result[value_date] = value
    return result


def _product(values: Iterable[Decimal]) -> Decimal:
    result = ONE
    for value in values:
        result *= value
    return result


def _metric_value(
    result: BenchmarkCalculationResult,
    name: str,
) -> float:
    return next(
        float(metric.value)
        for metric in result.metrics
        if metric.factor_name == name
    )


def _validate_identity(asset_id: str, source: str) -> None:
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("asset_id must not be blank")
    _validate_source(source)


def _validate_source(source: str) -> None:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must not be blank")


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _validate_positive_decimal(
    value: Decimal,
    field_name: str,
) -> None:
    _validate_finite_decimal(value, field_name)
    if value <= ZERO:
        raise ValueError(f"{field_name} must be positive")


def _validate_finite_decimal(
    value: Decimal,
    field_name: str,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
    ):
        raise ValueError(f"{field_name} must be a finite Decimal")
