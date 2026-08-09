"""Deterministic ETF dual-momentum trend research rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Sequence


ETF_DUAL_MOMENTUM_STRATEGY_ID = "etf_dual_momentum_trend"
ETF_DUAL_MOMENTUM_VERSION = "v1"
ETF_DUAL_MOMENTUM_MATURITY = "research_rule"

ETF_DUAL_MOMENTUM_LIMITATIONS = (
    "initial research assumptions only",
    "not backtested",
    "ETF trend not individual-stock momentum",
)

RotationStatus = Literal["risk_on", "defensive", "cash"]


@dataclass(frozen=True)
class ETFPriceSeries:
    """Validated daily closing-price observations for one ETF."""

    asset_id: str
    dates: tuple[str, ...]
    closes: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")

        dates = tuple(self.dates)
        closes = tuple(self.closes)
        object.__setattr__(self, "dates", dates)
        object.__setattr__(self, "closes", closes)

        if not dates:
            raise ValueError("price series must not be empty")
        if len(dates) != len(closes):
            raise ValueError("dates and closes must have equal length")

        parsed_dates = tuple(_strict_iso_date(value, "dates") for value in dates)
        if any(
            current <= previous
            for previous, current in zip(parsed_dates, parsed_dates[1:])
        ):
            raise ValueError("dates must be strictly increasing and unique")

        for close in closes:
            if (
                not isinstance(close, Decimal)
                or not close.is_finite()
                or close <= 0
            ):
                raise ValueError("closes must contain positive finite Decimals")


@dataclass(frozen=True)
class ETFRotationParameters:
    """Fixed, explicit research parameters; no parameter fitting is performed."""

    short_window: int = 126
    long_window: int = 252
    trend_window: int = 200
    volatility_window: int = 60
    short_weight: Decimal = Decimal("0.5")
    long_weight: Decimal = Decimal("0.5")
    top_n: int = 2
    annualization_days: int = 252
    defensive_asset_id: str | None = None

    def __post_init__(self) -> None:
        integer_fields = (
            (self.short_window, "short_window"),
            (self.long_window, "long_window"),
            (self.trend_window, "trend_window"),
            (self.volatility_window, "volatility_window"),
            (self.top_n, "top_n"),
            (self.annualization_days, "annualization_days"),
        )
        for value, name in integer_fields:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        if self.short_window > self.long_window:
            raise ValueError("short_window must not exceed long_window")
        if self.trend_window > self.long_window + 1:
            raise ValueError("trend_window must not exceed long_window + 1")
        if self.volatility_window > self.long_window:
            raise ValueError("volatility_window must not exceed long_window")

        for value, name in (
            (self.short_weight, "short_weight"),
            (self.long_weight, "long_weight"),
        ):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                or value > 1
            ):
                raise ValueError(
                    f"{name} must be a finite Decimal between 0 and 1"
                )
        if self.short_weight + self.long_weight != Decimal("1"):
            raise ValueError("short_weight and long_weight must sum to 1")

        if self.defensive_asset_id is not None and (
            not isinstance(self.defensive_asset_id, str)
            or not self.defensive_asset_id.strip()
        ):
            raise ValueError("defensive_asset_id must be None or non-blank")


@dataclass(frozen=True)
class ETFTrendMetrics:
    """Point-in-time ETF trend and momentum measurements."""

    asset_id: str
    as_of: str
    return_short: Decimal | None
    return_long: Decimal | None
    sma_ratio: Decimal | None
    annualized_volatility: Decimal | None
    relative_score: Decimal | None
    eligible: bool
    reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")
        _strict_iso_date(self.as_of, "as_of")
        if not isinstance(self.eligible, bool):
            raise TypeError("eligible must be bool")

        values = (
            self.return_short,
            self.return_long,
            self.sma_ratio,
            self.annualized_volatility,
            self.relative_score,
        )
        if any(
            value is not None
            and (
                not isinstance(value, Decimal)
                or not value.is_finite()
            )
            for value in values
        ):
            raise ValueError("metric values must be finite Decimals or None")
        if self.annualized_volatility is not None and (
            self.annualized_volatility < 0
        ):
            raise ValueError("annualized_volatility must not be negative")
        if self.eligible:
            if any(value is None for value in values):
                raise ValueError("eligible metrics must contain every value")
            if self.reason is not None:
                raise ValueError("eligible metrics must not have a reason")
        elif not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("ineligible metrics require a reason")
        if self.reason == "insufficient_history" and any(
            value is not None for value in values
        ):
            raise ValueError("insufficient-history metrics must be None")


@dataclass(frozen=True)
class ETFRotationSignal:
    """Validated research signal effective no earlier than the next session."""

    as_of_date: str
    effective_date: str
    status: RotationStatus
    target_weights: dict[str, Decimal]
    ranked_assets: tuple[str, ...]
    metrics: tuple[ETFTrendMetrics, ...]
    limitations: tuple[str, ...]
    strategy_id: str = ETF_DUAL_MOMENTUM_STRATEGY_ID
    version: str = ETF_DUAL_MOMENTUM_VERSION
    maturity: str = ETF_DUAL_MOMENTUM_MATURITY

    def __post_init__(self) -> None:
        as_of = _strict_iso_date(self.as_of_date, "as_of_date")
        effective = _strict_iso_date(self.effective_date, "effective_date")
        if effective <= as_of:
            raise ValueError("effective_date must be later than as_of_date")
        if self.strategy_id != ETF_DUAL_MOMENTUM_STRATEGY_ID:
            raise ValueError("strategy_id must use the ETF dual-momentum id")
        if self.version != ETF_DUAL_MOMENTUM_VERSION:
            raise ValueError("version must be v1")
        if self.maturity != ETF_DUAL_MOMENTUM_MATURITY:
            raise ValueError("maturity must be research_rule")
        if self.status not in ("risk_on", "defensive", "cash"):
            raise ValueError("status must be risk_on, defensive, or cash")
        if not isinstance(self.target_weights, dict):
            raise TypeError("target_weights must be a dict")

        weights = dict(self.target_weights)
        object.__setattr__(self, "target_weights", weights)
        for asset_id, weight in weights.items():
            if not isinstance(asset_id, str) or not asset_id.strip():
                raise ValueError("target weight asset ids must not be blank")
            if (
                not isinstance(weight, Decimal)
                or not weight.is_finite()
                or weight < 0
            ):
                raise ValueError(
                    "target weights must be non-negative finite Decimals"
                )
        total_weight = sum(weights.values(), Decimal("0"))
        required_total = (
            Decimal("0") if self.status == "cash" else Decimal("1")
        )
        if total_weight != required_total:
            raise ValueError(
                f"{self.status} target weights must sum to {required_total}"
            )
        if self.status != "cash" and not weights:
            raise ValueError(f"{self.status} target weights must not be empty")

        ranked_assets = tuple(self.ranked_assets)
        metrics = tuple(self.metrics)
        limitations = tuple(self.limitations)
        object.__setattr__(self, "ranked_assets", ranked_assets)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "limitations", limitations)
        if len(ranked_assets) != len(set(ranked_assets)):
            raise ValueError("ranked_assets must be unique")
        metric_ids = [metric.asset_id for metric in metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metrics must contain unique asset ids")
        eligible_ids = {
            metric.asset_id for metric in metrics if metric.eligible
        }
        if any(asset_id not in eligible_ids for asset_id in ranked_assets):
            raise ValueError("ranked_assets must reference eligible metrics")
        missing_limitations = set(ETF_DUAL_MOMENTUM_LIMITATIONS) - set(
            limitations
        )
        if missing_limitations:
            raise ValueError(
                "limitations must state research assumptions, backtest status, "
                "and ETF scope"
            )


class ETFDualMomentumTrendStrategy:
    """Evaluate the v1 ETF dual-momentum trend research rule."""

    @staticmethod
    def evaluate(
        series: Sequence[ETFPriceSeries],
        as_of_date: str,
        next_session_date: str,
        parameters: ETFRotationParameters,
    ) -> ETFRotationSignal:
        as_of = _strict_iso_date(as_of_date, "as_of_date")
        next_session = _strict_iso_date(
            next_session_date,
            "next_session_date",
        )
        if next_session <= as_of:
            raise ValueError("effective_date must be later than as_of_date")
        if not isinstance(parameters, ETFRotationParameters):
            raise TypeError("parameters must be ETFRotationParameters")

        by_asset: dict[str, ETFPriceSeries] = {}
        for item in series:
            if not isinstance(item, ETFPriceSeries):
                raise TypeError("series must contain ETFPriceSeries values")
            if item.asset_id in by_asset:
                raise ValueError("series must contain unique asset ids")
            by_asset[item.asset_id] = item

        metrics: list[ETFTrendMetrics] = []
        for asset_id in sorted(by_asset):
            item = by_asset[asset_id]
            if as_of_date not in item.dates:
                continue
            as_of_index = item.dates.index(as_of_date)
            closes = item.closes[: as_of_index + 1]
            metrics.append(
                _calculate_metrics(
                    asset_id,
                    as_of_date,
                    closes,
                    parameters,
                )
            )

        risk_metrics = [
            metric
            for metric in metrics
            if metric.eligible
            and metric.asset_id != parameters.defensive_asset_id
        ]
        ranked = sorted(
            risk_metrics,
            key=lambda metric: (-metric.relative_score, metric.asset_id),
        )
        ranked_assets = tuple(metric.asset_id for metric in ranked)

        if ranked:
            selected = ranked[: parameters.top_n]
            target_weights = _inverse_volatility_weights(selected)
            status: RotationStatus = "risk_on"
        elif (
            parameters.defensive_asset_id is not None
            and parameters.defensive_asset_id in by_asset
            and as_of_date
            in by_asset[parameters.defensive_asset_id].dates
        ):
            target_weights = {
                parameters.defensive_asset_id: Decimal("1")
            }
            status = "defensive"
        else:
            target_weights = {}
            status = "cash"

        return ETFRotationSignal(
            strategy_id=ETF_DUAL_MOMENTUM_STRATEGY_ID,
            version=ETF_DUAL_MOMENTUM_VERSION,
            maturity=ETF_DUAL_MOMENTUM_MATURITY,
            as_of_date=as_of_date,
            effective_date=next_session_date,
            status=status,
            target_weights=target_weights,
            ranked_assets=ranked_assets,
            metrics=tuple(metrics),
            limitations=ETF_DUAL_MOMENTUM_LIMITATIONS,
        )


def _calculate_metrics(
    asset_id: str,
    as_of_date: str,
    closes: tuple[Decimal, ...],
    parameters: ETFRotationParameters,
) -> ETFTrendMetrics:
    if len(closes) < parameters.long_window + 1:
        return ETFTrendMetrics(
            asset_id=asset_id,
            as_of=as_of_date,
            return_short=None,
            return_long=None,
            sma_ratio=None,
            annualized_volatility=None,
            relative_score=None,
            eligible=False,
            reason="insufficient_history",
        )

    latest = closes[-1]
    return_short = (
        latest / closes[-parameters.short_window - 1] - Decimal("1")
    )
    return_long = (
        latest / closes[-parameters.long_window - 1] - Decimal("1")
    )
    trend_closes = closes[-parameters.trend_window :]
    sma = sum(trend_closes, Decimal("0")) / Decimal(
        parameters.trend_window
    )
    sma_ratio = latest / sma - Decimal("1")

    volatility_closes = closes[-parameters.volatility_window - 1 :]
    simple_returns = tuple(
        current / previous - Decimal("1")
        for previous, current in zip(
            volatility_closes,
            volatility_closes[1:],
        )
    )
    mean_return = sum(simple_returns, Decimal("0")) / Decimal(
        parameters.volatility_window
    )
    variance = sum(
        (value - mean_return) ** 2 for value in simple_returns
    ) / Decimal(parameters.volatility_window)
    annualized_volatility = variance.sqrt() * Decimal(
        parameters.annualization_days
    ).sqrt()
    relative_score = (
        parameters.short_weight * return_short
        + parameters.long_weight * return_long
    )
    eligible = (
        return_long > 0
        and sma_ratio > 0
        and annualized_volatility > 0
    )
    return ETFTrendMetrics(
        asset_id=asset_id,
        as_of=as_of_date,
        return_short=return_short,
        return_long=return_long,
        sma_ratio=sma_ratio,
        annualized_volatility=annualized_volatility,
        relative_score=relative_score,
        eligible=eligible,
        reason=None if eligible else "failed_trend_filter",
    )


def _inverse_volatility_weights(
    selected: Sequence[ETFTrendMetrics],
) -> dict[str, Decimal]:
    inverse_volatilities = tuple(
        Decimal("1") / metric.annualized_volatility
        for metric in selected
        if metric.annualized_volatility is not None
    )
    inverse_total = sum(inverse_volatilities, Decimal("0"))
    weights: dict[str, Decimal] = {}
    assigned = Decimal("0")
    for index, (metric, inverse_volatility) in enumerate(
        zip(selected, inverse_volatilities)
    ):
        if index == len(selected) - 1:
            weight = Decimal("1") - assigned
        else:
            weight = inverse_volatility / inverse_total
            assigned += weight
        weights[metric.asset_id] = weight
    return weights


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
