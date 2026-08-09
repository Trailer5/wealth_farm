"""Strict models for A-share market-wide factor calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal


A_SHARE_MARKET_CALCULATION_VERSION = "a_share_market_v1"
SizeBucket = Literal["small", "large"]


@dataclass(frozen=True)
class MarketCrossSectionRow:
    symbol: str
    trade_date: str
    current_close: float | None
    previous_close: float | None
    amount: float | None
    tradable: bool
    history_closes: tuple[float, ...] = ()
    limit_state: int = 0
    market_cap: float | None = None
    size_bucket: SizeBucket | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must not be blank")
        _parse_date(self.trade_date, "trade_date")
        _validate_optional_number(
            self.current_close,
            "current_close",
            positive=True,
        )
        _validate_optional_number(
            self.previous_close,
            "previous_close",
            positive=True,
        )
        _validate_optional_number(self.amount, "amount", positive=False)
        if not isinstance(self.tradable, bool):
            raise TypeError("tradable must be bool")
        if (
            isinstance(self.limit_state, bool)
            or self.limit_state not in {-1, 0, 1}
        ):
            raise ValueError("limit_state must be -1, 0, or 1")
        closes = tuple(self.history_closes)
        for close in closes:
            _validate_number(close, "history close", positive=True)
        object.__setattr__(self, "history_closes", closes)
        _validate_optional_number(
            self.market_cap,
            "market_cap",
            positive=True,
        )
        if self.size_bucket not in {None, "small", "large"}:
            raise ValueError("size_bucket must be small, large, or None")


@dataclass(frozen=True)
class MarketFactorParameters:
    scope_id: str
    universe_id: str
    universe_version: str
    universe_effective_date: str
    as_of_date: str
    benchmark_return: float
    historical_turnover_amounts: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.scope_id, "scope_id"),
            (self.universe_id, "universe_id"),
            (self.universe_version, "universe_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        effective_date = _parse_date(
            self.universe_effective_date,
            "universe_effective_date",
        )
        as_of_date = _parse_date(self.as_of_date, "as_of_date")
        if as_of_date < effective_date:
            raise ValueError(
                "as_of_date must not be earlier than "
                "universe_effective_date"
            )
        _validate_number(
            self.benchmark_return,
            "benchmark_return",
            positive=None,
        )
        amounts = tuple(self.historical_turnover_amounts)
        for amount in amounts:
            _validate_number(
                amount,
                "historical turnover amount",
                positive=False,
            )
        object.__setattr__(
            self,
            "historical_turnover_amounts",
            amounts,
        )


@dataclass(frozen=True)
class MarketFactorValue:
    scope_id: str
    universe_id: str
    universe_version: str
    factor_name: str
    as_of_date: str
    value: float
    calculation_version: str
    parameters: dict[str, Any] = field(default_factory=dict)
    computed_at: str = ""

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.scope_id, "scope_id"),
            (self.universe_id, "universe_id"),
            (self.universe_version, "universe_version"),
            (self.factor_name, "factor_name"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        _parse_date(self.as_of_date, "as_of_date")
        _validate_number(self.value, "value", positive=None)
        if self.calculation_version != A_SHARE_MARKET_CALCULATION_VERSION:
            raise ValueError(
                "calculation_version must be "
                f"{A_SHARE_MARKET_CALCULATION_VERSION}"
            )
        required_parameters = {
            "sample_count",
            "excluded_non_tradable",
            "excluded_missing",
            "scope_id",
            "universe_id",
            "universe_version",
        }
        if not required_parameters.issubset(self.parameters):
            raise ValueError(
                "parameters are missing required sample or universe fields"
            )
        if self.computed_at:
            datetime.fromisoformat(
                self.computed_at.replace("Z", "+00:00")
            )


@dataclass(frozen=True)
class MarketFactorSnapshot:
    parameters: MarketFactorParameters
    values: tuple[MarketFactorValue, ...]

    def __post_init__(self) -> None:
        values = tuple(self.values)
        names = [value.factor_name for value in values]
        if len(names) != len(set(names)):
            raise ValueError("snapshot factor names must be unique")
        for value in values:
            if (
                value.scope_id != self.parameters.scope_id
                or value.universe_id != self.parameters.universe_id
                or value.universe_version
                != self.parameters.universe_version
                or value.as_of_date != self.parameters.as_of_date
            ):
                raise ValueError(
                    "snapshot values must match parameter identity"
                )
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class MarketSeriesObservation:
    scope_asset_id: str
    series_name: str
    observation_date: str
    available_at: str
    value: Decimal
    unit: str
    methodology: str
    source: str
    acquired_at: str
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.scope_asset_id, "scope_asset_id"),
            (self.series_name, "series_name"),
            (self.unit, "unit"),
            (self.methodology, "methodology"),
            (self.source, "source"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        observation_date = _parse_date(
            self.observation_date,
            "observation_date",
        )
        available_at = _parse_date(
            self.available_at,
            "available_at",
        )
        if available_at < observation_date:
            raise ValueError(
                "available_at must not precede observation_date"
            )
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("value must be a finite Decimal")
        datetime.fromisoformat(
            self.acquired_at.replace("Z", "+00:00")
        )


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must be a valid ISO date"
        ) from exc


def _validate_optional_number(
    value: float | None,
    field_name: str,
    *,
    positive: bool,
) -> None:
    if value is not None:
        _validate_number(value, field_name, positive=positive)


def _validate_number(
    value: float,
    field_name: str,
    *,
    positive: bool | None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    if positive is True and value <= 0:
        raise ValueError(f"{field_name} must be positive")
    if positive is False and value < 0:
        raise ValueError(f"{field_name} must be non-negative")
