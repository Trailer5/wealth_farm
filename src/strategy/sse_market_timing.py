"""Point-in-time SSE multi-horizon turning-point research rule."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Sequence


SSE_MARKET_TIMING_STRATEGY_ID = "sse_market_turning_points"
SSE_MARKET_TIMING_VERSION = "v1"
SSE_MARKET_TIMING_MATURITY = "research_rule"

SSE_MARKET_TIMING_LIMITATIONS = (
    "signal regions are not exact tops or bottoms",
    "research assumptions only; not validated",
    "ERP regimes can be unstable",
    "QVIX is third-party data",
)

Horizon = Literal["short", "medium_short", "medium_long"]
Direction = Literal["low", "high", "neutral", "insufficient"]
Stage = Literal["watch", "confirmed", "neutral", "insufficient"]

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class SSEIndexBar:
    """Validated daily OHLC observation for the SSE index."""

    trade_date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        _strict_iso_date(self.trade_date, "trade_date")
        for value, name in (
            (self.open, "open"),
            (self.high, "high"),
            (self.low, "low"),
            (self.close, "close"),
        ):
            _positive_decimal(value, name)
        if self.low > self.open or self.open > self.high:
            raise ValueError("open must be between low and high")
        if self.low > self.close or self.close > self.high:
            raise ValueError("close must be between low and high")


@dataclass(frozen=True)
class SSEIndexSeries:
    """Chronological SSE index bars for one stable asset identity."""

    asset_id: str
    bars: tuple[SSEIndexBar, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")
        bars = tuple(self.bars)
        object.__setattr__(self, "bars", bars)
        if not bars:
            raise ValueError("bars must not be empty")
        if any(not isinstance(bar, SSEIndexBar) for bar in bars):
            raise TypeError("bars must contain SSEIndexBar values")
        dates = tuple(
            _strict_iso_date(bar.trade_date, "bar trade_date") for bar in bars
        )
        if any(
            current <= previous
            for previous, current in zip(dates, dates[1:])
        ):
            raise ValueError("bar dates must be strictly increasing and unique")


@dataclass(frozen=True)
class SSEMarketAuxPoint:
    """Optional point-in-time market valuation, risk, and breadth inputs."""

    trade_date: str
    market_pe: Decimal | None = None
    ten_year_yield_percent: Decimal | None = None
    qvix_close: Decimal | None = None
    advance_ratio: Decimal | None = None
    above_ma20_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        _strict_iso_date(self.trade_date, "trade_date")
        values = (
            self.market_pe,
            self.ten_year_yield_percent,
            self.qvix_close,
            self.advance_ratio,
            self.above_ma20_ratio,
        )
        if all(value is None for value in values):
            raise ValueError("an auxiliary point requires at least one value")
        for value in values:
            if value is not None:
                _finite_decimal(value, "auxiliary values")
        for value, name in (
            (self.market_pe, "market_pe"),
            (self.qvix_close, "qvix_close"),
        ):
            if value is not None and value <= ZERO:
                raise ValueError(f"{name} must be positive")
        if (
            self.ten_year_yield_percent is not None
            and self.ten_year_yield_percent < ZERO
        ):
            raise ValueError("ten_year_yield_percent must be non-negative")
        for value, name in (
            (self.advance_ratio, "advance_ratio"),
            (self.above_ma20_ratio, "above_ma20_ratio"),
        ):
            if value is not None and not ZERO <= value <= ONE:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class SSEMarketAuxSeries:
    """Reusable chronological auxiliary history for repeated evaluations."""

    points: tuple[SSEMarketAuxPoint, ...]
    _dates: tuple[date, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        points = tuple(self.points)
        if any(not isinstance(point, SSEMarketAuxPoint) for point in points):
            raise TypeError(
                "points must contain SSEMarketAuxPoint values"
            )
        dates = tuple(
            date.fromisoformat(point.trade_date) for point in points
        )
        if any(
            current <= previous
            for previous, current in zip(dates, dates[1:])
        ):
            raise ValueError(
                "auxiliary point dates must be strictly increasing and unique"
            )
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "_dates", dates)

    def through(self, as_of: date) -> tuple[SSEMarketAuxPoint, ...]:
        return self.points[: bisect_right(self._dates, as_of)]


@dataclass(frozen=True)
class SSEMarketTimingParameters:
    """Explicit v1 windows; the research rule performs no fitting."""

    short_channel: int = 20
    rsi_window: int = 14
    short_reversal_window: int = 3
    qvix_window: int = 252
    medium_window: int = 120
    medium_sma: int = 60
    medium_reversal_window: int = 20
    long_window: int = 1008
    long_trend_sma: int = 200
    long_confirmation_sma: int = 60
    long_drawdown_window: int = 252
    valuation_lookback_days: int = 1461
    min_valuation_observations: int = 48
    max_valuation_staleness_days: int = 45
    max_aux_staleness_days: int = 5

    def __post_init__(self) -> None:
        fields = (
            (self.short_channel, "short_channel"),
            (self.rsi_window, "rsi_window"),
            (self.short_reversal_window, "short_reversal_window"),
            (self.qvix_window, "qvix_window"),
            (self.medium_window, "medium_window"),
            (self.medium_sma, "medium_sma"),
            (self.medium_reversal_window, "medium_reversal_window"),
            (self.long_window, "long_window"),
            (self.long_trend_sma, "long_trend_sma"),
            (self.long_confirmation_sma, "long_confirmation_sma"),
            (self.long_drawdown_window, "long_drawdown_window"),
            (self.valuation_lookback_days, "valuation_lookback_days"),
            (
                self.min_valuation_observations,
                "min_valuation_observations",
            ),
            (
                self.max_valuation_staleness_days,
                "max_valuation_staleness_days",
            ),
            (self.max_aux_staleness_days, "max_aux_staleness_days"),
        )
        for value, name in fields:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        relationships = (
            (self.rsi_window, self.short_channel, "rsi_window"),
            (
                self.short_reversal_window,
                self.short_channel,
                "short_reversal_window",
            ),
            (self.medium_sma, self.medium_window, "medium_sma"),
            (
                self.medium_reversal_window,
                self.medium_window,
                "medium_reversal_window",
            ),
            (self.long_trend_sma, self.long_window, "long_trend_sma"),
            (
                self.long_confirmation_sma,
                self.long_window,
                "long_confirmation_sma",
            ),
            (
                self.long_drawdown_window,
                self.long_window,
                "long_drawdown_window",
            ),
            (self.short_channel, self.medium_window, "short_channel"),
            (self.medium_window, self.long_window, "medium_window"),
        )
        for smaller, larger, name in relationships:
            if smaller > larger:
                raise ValueError(f"{name} exceeds its containing window")


@dataclass(frozen=True)
class HorizonTimingSignal:
    """One independently evaluated horizon signal."""

    horizon: Horizon
    direction: Direction
    stage: Stage
    confidence: Decimal
    evidence: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    metrics: dict[str, Decimal | None]

    def __post_init__(self) -> None:
        if self.horizon not in ("short", "medium_short", "medium_long"):
            raise ValueError("invalid horizon")
        if self.direction not in ("low", "high", "neutral", "insufficient"):
            raise ValueError("invalid direction")
        if self.stage not in ("watch", "confirmed", "neutral", "insufficient"):
            raise ValueError("invalid stage")
        _finite_decimal(self.confidence, "confidence")
        if not ZERO <= self.confidence <= ONE:
            raise ValueError("confidence must be between 0 and 1")
        if (self.direction == "insufficient") != (self.stage == "insufficient"):
            raise ValueError("insufficient direction and stage must agree")
        if self.stage in ("watch", "confirmed") and self.direction not in (
            "low",
            "high",
        ):
            raise ValueError("watch and confirmed stages require low or high")
        if self.stage == "neutral" and self.direction != "neutral":
            raise ValueError("neutral stage requires neutral direction")
        evidence = tuple(self.evidence)
        missing = tuple(self.missing_inputs)
        metrics = dict(self.metrics)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "missing_inputs", missing)
        object.__setattr__(self, "metrics", metrics)
        if any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise ValueError("evidence entries must not be blank")
        if any(not isinstance(item, str) or not item.strip() for item in missing):
            raise ValueError("missing_inputs entries must not be blank")
        for value in metrics.values():
            if value is not None:
                _finite_decimal(value, "metric values")


@dataclass(frozen=True)
class SSEMarketTimingResult:
    """Three independent horizon assessments for one as-of date."""

    as_of_date: str
    effective_date: str
    signals: tuple[HorizonTimingSignal, ...]
    conflicts: tuple[str, ...]
    limitations: tuple[str, ...]
    asset_id: str = "index:cn_a:sh:000001"
    strategy_id: str = SSE_MARKET_TIMING_STRATEGY_ID
    version: str = SSE_MARKET_TIMING_VERSION
    maturity: str = SSE_MARKET_TIMING_MATURITY

    def __post_init__(self) -> None:
        as_of = _strict_iso_date(self.as_of_date, "as_of_date")
        effective = _strict_iso_date(self.effective_date, "effective_date")
        if effective <= as_of:
            raise ValueError("effective_date must be later than as_of_date")
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")
        if self.strategy_id != SSE_MARKET_TIMING_STRATEGY_ID:
            raise ValueError("invalid strategy_id")
        if self.version != "v1":
            raise ValueError("version must be v1")
        if self.maturity != "research_rule":
            raise ValueError("maturity must be research_rule")
        signals = tuple(self.signals)
        conflicts = tuple(self.conflicts)
        limitations = tuple(self.limitations)
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "conflicts", conflicts)
        object.__setattr__(self, "limitations", limitations)
        if any(not isinstance(signal, HorizonTimingSignal) for signal in signals):
            raise TypeError("signals must contain HorizonTimingSignal values")
        horizons = tuple(signal.horizon for signal in signals)
        if len(signals) != 3 or set(horizons) != {
            "short",
            "medium_short",
            "medium_long",
        }:
            raise ValueError("signals must contain each horizon exactly once")
        missing_limitations = set(SSE_MARKET_TIMING_LIMITATIONS) - set(
            limitations
        )
        if missing_limitations:
            raise ValueError("required research limitations are missing")


class SSEMarketTimingStrategy:
    """Evaluate fixed, transparent SSE high/low-region rules."""

    @staticmethod
    def evaluate(
        series: SSEIndexSeries,
        aux_points: Sequence[SSEMarketAuxPoint] | SSEMarketAuxSeries,
        as_of_date: str,
        effective_date: str,
        parameters: SSEMarketTimingParameters | None = None,
    ) -> SSEMarketTimingResult:
        as_of = _strict_iso_date(as_of_date, "as_of_date")
        effective = _strict_iso_date(effective_date, "effective_date")
        if effective <= as_of:
            raise ValueError("effective_date must be later than as_of_date")
        if not isinstance(series, SSEIndexSeries):
            raise TypeError("series must be SSEIndexSeries")
        if parameters is None:
            parameters = SSEMarketTimingParameters()
        if not isinstance(parameters, SSEMarketTimingParameters):
            raise TypeError("parameters must be SSEMarketTimingParameters")

        if isinstance(aux_points, SSEMarketAuxSeries):
            points = aux_points.through(as_of)
        else:
            points = tuple(aux_points)
            if any(
                not isinstance(point, SSEMarketAuxPoint)
                for point in points
            ):
                raise TypeError(
                    "aux_points must contain SSEMarketAuxPoint values"
                )
            points = tuple(
                point
                for point in sorted(
                    points,
                    key=lambda item: item.trade_date,
                )
                if date.fromisoformat(point.trade_date) <= as_of
            )
        point_dates = tuple(date.fromisoformat(point.trade_date) for point in points)
        if len(point_dates) != len(set(point_dates)):
            raise ValueError("auxiliary point dates must be unique")

        eligible_bars = tuple(
            bar
            for bar in series.bars
            if date.fromisoformat(bar.trade_date) <= as_of
        )
        if not eligible_bars or eligible_bars[-1].trade_date != as_of_date:
            raise ValueError("as_of_date must have an index bar")
        closes = tuple(bar.close for bar in eligible_bars)

        signals = (
            _short_signal(closes, points, as_of, parameters),
            _medium_short_signal(closes, points, as_of, parameters),
            _medium_long_signal(closes, points, as_of, parameters),
        )
        conflicts = _confirmed_conflicts(signals)
        return SSEMarketTimingResult(
            as_of_date=as_of_date,
            effective_date=effective_date,
            signals=signals,
            conflicts=conflicts,
            limitations=SSE_MARKET_TIMING_LIMITATIONS,
            asset_id=series.asset_id,
        )


def evaluate_sse_market_timing(
    series: SSEIndexSeries,
    aux_points: Sequence[SSEMarketAuxPoint] | SSEMarketAuxSeries,
    as_of_date: str,
    effective_date: str,
    parameters: SSEMarketTimingParameters | None = None,
) -> SSEMarketTimingResult:
    """Convenience entry point for the v1 rule."""

    return SSEMarketTimingStrategy.evaluate(
        series,
        aux_points,
        as_of_date,
        effective_date,
        parameters,
    )


def _short_signal(
    closes: tuple[Decimal, ...],
    points: tuple[SSEMarketAuxPoint, ...],
    as_of: date,
    parameters: SSEMarketTimingParameters,
) -> HorizonTimingSignal:
    required = max(
        parameters.short_channel,
        parameters.rsi_window + 1,
        parameters.short_reversal_window + 1,
    )
    if len(closes) < required:
        return _insufficient("short", "short_price_history")

    channel = closes[-parameters.short_channel :]
    rsi = _rsi(closes, parameters.rsi_window)
    price_percentile = _percentile(channel, closes[-1])
    zscore = _zscore(channel, closes[-1])
    reversal_return = _simple_return(closes, parameters.short_reversal_window)
    qvix = _current_field(
        points, "qvix_close", as_of, parameters.max_aux_staleness_days
    )
    qvix_history = tuple(
        point.qvix_close
        for point in points
        if point.qvix_close is not None
    )[-parameters.qvix_window :]
    qvix_percentile = (
        _percentile(qvix_history, qvix) if qvix is not None else None
    )

    low = (
        (rsi <= Decimal("30"), Decimal("0.25"), "RSI <= 30"),
        (
            price_percentile <= Decimal("0.10"),
            Decimal("0.20"),
            "20-window price percentile <= 0.10",
        ),
        (
            zscore is not None and zscore <= Decimal("-1.5"),
            Decimal("0.15"),
            "20-window close z-score <= -1.5",
        ),
        (
            qvix_percentile is not None
            and qvix_percentile >= Decimal("0.90"),
            Decimal("0.15"),
            "QVIX percentile >= 0.90",
        ),
    )
    high = (
        (rsi >= Decimal("70"), Decimal("0.25"), "RSI >= 70"),
        (
            price_percentile >= Decimal("0.90"),
            Decimal("0.20"),
            "20-window price percentile >= 0.90",
        ),
        (
            zscore is not None and zscore >= Decimal("1.5"),
            Decimal("0.15"),
            "20-window close z-score >= 1.5",
        ),
        (
            qvix_percentile is not None
            and qvix_percentile <= Decimal("0.10"),
            Decimal("0.15"),
            "QVIX percentile <= 0.10",
        ),
    )
    missing = () if qvix_percentile is not None else ("qvix_close",)
    metrics = {
        "rsi": rsi,
        "price_percentile": price_percentile,
        "close_zscore": zscore,
        "qvix_percentile": qvix_percentile,
        "reversal_return": reversal_return,
        "overall_return_stddev": _return_stddev(channel),
    }
    return _score_signal(
        "short",
        low,
        high,
        reversal_return > ZERO,
        reversal_return < ZERO,
        Decimal("0.25"),
        Decimal("0.35"),
        Decimal("0.60"),
        missing,
        metrics,
    )


def _medium_short_signal(
    closes: tuple[Decimal, ...],
    points: tuple[SSEMarketAuxPoint, ...],
    as_of: date,
    parameters: SSEMarketTimingParameters,
) -> HorizonTimingSignal:
    required = max(
        parameters.medium_window,
        parameters.medium_sma,
        parameters.medium_reversal_window + 1,
    )
    if len(closes) < required:
        return _insufficient("medium_short", "medium_price_history")

    window = closes[-parameters.medium_window :]
    price_percentile = _percentile(window, closes[-1])
    drawdown = closes[-1] / max(window) - ONE
    rise_from_low = closes[-1] / min(window) - ONE
    sma_ratio = closes[-1] / _sma(closes, parameters.medium_sma) - ONE
    reversal_return = _simple_return(
        closes, parameters.medium_reversal_window
    )
    breadth = _current_field(
        points,
        "above_ma20_ratio",
        as_of,
        parameters.max_aux_staleness_days,
    )
    advance_ratio = _current_field(
        points,
        "advance_ratio",
        as_of,
        parameters.max_aux_staleness_days,
    )
    low = (
        (
            price_percentile <= Decimal("0.15"),
            Decimal("0.25"),
            "120-window price percentile <= 0.15",
        ),
        (
            drawdown <= Decimal("-0.15"),
            Decimal("0.25"),
            "drawdown from 120-window high <= -15%",
        ),
        (
            sma_ratio <= Decimal("-0.08"),
            Decimal("0.20"),
            "close versus SMA60 <= -8%",
        ),
        (
            breadth is not None and breadth <= Decimal("0.25"),
            Decimal("0.10"),
            "above-MA20 ratio <= 0.25",
        ),
    )
    high = (
        (
            price_percentile >= Decimal("0.85"),
            Decimal("0.25"),
            "120-window price percentile >= 0.85",
        ),
        (
            rise_from_low >= Decimal("0.20"),
            Decimal("0.25"),
            "rise from 120-window low >= 20%",
        ),
        (
            sma_ratio >= Decimal("0.08"),
            Decimal("0.20"),
            "close versus SMA60 >= 8%",
        ),
        (
            breadth is not None and breadth >= Decimal("0.75"),
            Decimal("0.10"),
            "above-MA20 ratio >= 0.75",
        ),
    )
    missing = () if breadth is not None else ("above_ma20_ratio",)
    metrics = {
        "price_percentile": price_percentile,
        "drawdown_from_high": drawdown,
        "rise_from_low": rise_from_low,
        "sma_ratio": sma_ratio,
        "reversal_return": reversal_return,
        "above_ma20_ratio": breadth,
        "advance_ratio": advance_ratio,
    }
    return _score_signal(
        "medium_short",
        low,
        high,
        reversal_return > ZERO,
        reversal_return < ZERO,
        Decimal("0.20"),
        Decimal("0.45"),
        Decimal("0.65"),
        missing,
        metrics,
    )


def _medium_long_signal(
    closes: tuple[Decimal, ...],
    points: tuple[SSEMarketAuxPoint, ...],
    as_of: date,
    parameters: SSEMarketTimingParameters,
) -> HorizonTimingSignal:
    valuation_start = as_of - timedelta(
        days=parameters.valuation_lookback_days
    )
    valuation_points = tuple(
        point
        for point in points
        if date.fromisoformat(point.trade_date) >= valuation_start
    )
    pe_history = tuple(
        point.market_pe
        for point in valuation_points
        if point.market_pe is not None
    )
    current_pe = _current_field(
        points,
        "market_pe",
        as_of,
        parameters.max_valuation_staleness_days,
    )
    missing_required = []
    if len(closes) < parameters.long_window + 1:
        missing_required.append("long_price_history")
    if len(pe_history) < parameters.min_valuation_observations:
        missing_required.append("market_pe_history")
    if current_pe is None:
        missing_required.append("market_pe")
    if missing_required:
        return _insufficient("medium_long", *missing_required)

    price_window = closes[-parameters.long_window :]
    drawdown_window = closes[-parameters.long_drawdown_window :]
    price_percentile = _percentile(price_window, closes[-1])
    pe_percentile = _percentile(
        pe_history, current_pe
    )
    drawdown = closes[-1] / max(drawdown_window) - ONE
    rise_from_low = closes[-1] / min(drawdown_window) - ONE
    confirmation_sma_ratio = (
        closes[-1] / _sma(closes, parameters.long_confirmation_sma) - ONE
    )
    trend_sma_ratio = (
        closes[-1] / _sma(closes, parameters.long_trend_sma) - ONE
    )
    reversal_return = _simple_return(
        closes, parameters.medium_reversal_window
    )

    current_erp = _current_erp(
        points,
        as_of,
        parameters.max_valuation_staleness_days,
    )
    erp_history = tuple(
        ONE / point.market_pe - point.ten_year_yield_percent / Decimal("100")
        for point in valuation_points
        if point.market_pe is not None
        and point.ten_year_yield_percent is not None
    )
    erp_percentile = (
        _percentile(erp_history, current_erp)
        if current_erp is not None
        and len(erp_history) >= parameters.min_valuation_observations
        else None
    )
    low = (
        (
            price_percentile <= Decimal("0.20"),
            Decimal("0.20"),
            "four-year price percentile <= 0.20",
        ),
        (
            pe_percentile <= Decimal("0.10"),
            Decimal("0.30"),
            "four-year PE percentile <= 0.10",
        ),
        (
            drawdown <= Decimal("-0.20"),
            Decimal("0.15"),
            "252-window drawdown <= -20%",
        ),
        (
            erp_percentile is not None
            and erp_percentile >= Decimal("0.90"),
            Decimal("0.15"),
            "ERP percentile >= 0.90",
        ),
    )
    high = (
        (
            price_percentile >= Decimal("0.80"),
            Decimal("0.20"),
            "four-year price percentile >= 0.80",
        ),
        (
            pe_percentile >= Decimal("0.90"),
            Decimal("0.30"),
            "four-year PE percentile >= 0.90",
        ),
        (
            rise_from_low >= Decimal("0.30"),
            Decimal("0.15"),
            "252-window rise from low >= 30%",
        ),
        (
            erp_percentile is not None
            and erp_percentile <= Decimal("0.10"),
            Decimal("0.15"),
            "ERP percentile <= 0.10",
        ),
    )
    trend_state = (
        "close is above long-trend SMA"
        if trend_sma_ratio > ZERO
        else "close is at or below long-trend SMA"
    )
    missing = () if erp_percentile is not None else ("erp",)
    metrics = {
        "price_percentile": price_percentile,
        "pe_percentile": pe_percentile,
        "drawdown_from_high": drawdown,
        "rise_from_low": rise_from_low,
        "erp": current_erp,
        "erp_percentile": erp_percentile,
        "confirmation_sma_ratio": confirmation_sma_ratio,
        "trend_sma_ratio": trend_sma_ratio,
        "trend_above_sma": ONE if trend_sma_ratio > ZERO else ZERO,
        "reversal_return": reversal_return,
    }
    return _score_signal(
        "medium_long",
        low,
        high,
        confirmation_sma_ratio > ZERO and reversal_return > ZERO,
        confirmation_sma_ratio < ZERO and reversal_return < ZERO,
        Decimal("0.20"),
        Decimal("0.50"),
        Decimal("0.70"),
        missing,
        metrics,
        extra_evidence=(trend_state,),
    )


def _score_signal(
    horizon: Horizon,
    low_conditions: tuple[tuple[bool, Decimal, str], ...],
    high_conditions: tuple[tuple[bool, Decimal, str], ...],
    low_confirmed: bool,
    high_confirmed: bool,
    confirmation_weight: Decimal,
    watch_threshold: Decimal,
    confirmation_threshold: Decimal,
    missing_inputs: tuple[str, ...],
    metrics: dict[str, Decimal | None],
    extra_evidence: tuple[str, ...] = (),
) -> HorizonTimingSignal:
    low_position = sum(
        (weight for matched, weight, _ in low_conditions if matched), ZERO
    )
    high_position = sum(
        (weight for matched, weight, _ in high_conditions if matched), ZERO
    )
    metrics["low_position_score"] = low_position
    metrics["high_position_score"] = high_position
    evidence = tuple(
        description
        for matched, _, description in low_conditions + high_conditions
        if matched
    )
    evidence += extra_evidence

    if low_position == high_position:
        return HorizonTimingSignal(
            horizon,
            "neutral",
            "neutral",
            max(low_position, high_position),
            evidence,
            missing_inputs,
            metrics,
        )
    direction: Literal["low", "high"] = (
        "low" if low_position > high_position else "high"
    )
    position = low_position if direction == "low" else high_position
    confirmed = low_confirmed if direction == "low" else high_confirmed
    total = position + (confirmation_weight if confirmed else ZERO)
    metrics["confirmation_met"] = ONE if confirmed else ZERO
    if position < watch_threshold:
        return HorizonTimingSignal(
            horizon,
            "neutral",
            "neutral",
            total,
            evidence,
            missing_inputs,
            metrics,
        )
    if confirmed:
        evidence += (f"{direction} reversal confirmation met",)
    stage: Stage = (
        "confirmed"
        if confirmed and total >= confirmation_threshold
        else "watch"
    )
    return HorizonTimingSignal(
        horizon,
        direction,
        stage,
        total,
        evidence,
        missing_inputs,
        metrics,
    )


def _insufficient(horizon: Horizon, *missing: str) -> HorizonTimingSignal:
    return HorizonTimingSignal(
        horizon=horizon,
        direction="insufficient",
        stage="insufficient",
        confidence=ZERO,
        evidence=("required history or input is unavailable",),
        missing_inputs=tuple(missing),
        metrics={},
    )


def _confirmed_conflicts(
    signals: tuple[HorizonTimingSignal, ...],
) -> tuple[str, ...]:
    confirmed = tuple(signal for signal in signals if signal.stage == "confirmed")
    return tuple(
        f"{left.horizon}:{left.direction} conflicts with "
        f"{right.horizon}:{right.direction}"
        for index, left in enumerate(confirmed)
        for right in confirmed[index + 1 :]
        if left.direction != right.direction
    )


def _current_field(
    points: tuple[SSEMarketAuxPoint, ...],
    field: str,
    as_of: date,
    max_staleness_days: int,
) -> Decimal | None:
    for point in reversed(points):
        value = getattr(point, field)
        if value is None:
            continue
        point_date = date.fromisoformat(point.trade_date)
        if (as_of - point_date).days <= max_staleness_days:
            return value
        return None
    return None


def _current_erp(
    points: tuple[SSEMarketAuxPoint, ...],
    as_of: date,
    max_staleness_days: int,
) -> Decimal | None:
    for point in reversed(points):
        if point.market_pe is None or point.ten_year_yield_percent is None:
            continue
        point_date = date.fromisoformat(point.trade_date)
        if (as_of - point_date).days > max_staleness_days:
            return None
        return ONE / point.market_pe - point.ten_year_yield_percent / Decimal(
            "100"
        )
    return None


def _simple_return(closes: tuple[Decimal, ...], window: int) -> Decimal:
    return closes[-1] / closes[-window - 1] - ONE


def _rsi(closes: tuple[Decimal, ...], window: int) -> Decimal:
    changes = tuple(
        current - previous
        for previous, current in zip(
            closes[-window - 1 : -1], closes[-window:]
        )
    )
    gains = sum((max(change, ZERO) for change in changes), ZERO)
    losses = sum((max(-change, ZERO) for change in changes), ZERO)
    if losses == ZERO and gains == ZERO:
        return Decimal("50")
    if losses == ZERO:
        return Decimal("100")
    if gains == ZERO:
        return ZERO
    return Decimal("100") - Decimal("100") / (ONE + gains / losses)


def _percentile(values: Sequence[Decimal], current: Decimal) -> Decimal:
    if len(values) == 1:
        return Decimal("0.5")
    less = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    return (
        Decimal(less) + Decimal(equal - 1) / Decimal("2")
    ) / Decimal(
        len(values) - 1
    )


def _sma(closes: tuple[Decimal, ...], window: int) -> Decimal:
    return sum(closes[-window:], ZERO) / Decimal(window)


def _zscore(values: Sequence[Decimal], current: Decimal) -> Decimal | None:
    mean = sum(values, ZERO) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(
        len(values)
    )
    if variance == ZERO:
        return None
    return (current - mean) / variance.sqrt()


def _return_stddev(closes: tuple[Decimal, ...]) -> Decimal | None:
    if len(closes) < 2:
        return None
    returns = tuple(
        current / previous - ONE
        for previous, current in zip(closes, closes[1:])
    )
    mean = sum(returns, ZERO) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(
        len(returns)
    )
    return variance.sqrt()


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
        raise ValueError(f"{name} must be finite Decimals")


def _positive_decimal(value: Decimal, name: str) -> None:
    _finite_decimal(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")
