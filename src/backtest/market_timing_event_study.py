"""Event-study validation for SSE multi-horizon market-timing signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Sequence

from src.strategy.sse_market_timing import (
    HorizonTimingSignal,
    SSEIndexSeries,
    SSEMarketTimingResult,
)


MARKET_TIMING_EVENT_STUDY_VERSION = "market_timing_event_study_v1"
Horizon = Literal["short", "medium_short", "medium_long"]
EventDirection = Literal["low", "high"]
MetricValue = Decimal | int | str
GroupedMetricValue = MetricValue | dict[str, MetricValue]

ZERO = Decimal("0")
ONE = Decimal("1")
HORIZONS = ("short", "medium_short", "medium_long")
DIRECTIONS = ("low", "high")


@dataclass(frozen=True)
class MarketTimingEventStudyParameters:
    """Fixed forward windows and cluster cooldowns for the v1 study."""

    short_forward_sessions: int = 10
    medium_short_forward_sessions: int = 60
    medium_long_forward_sessions: int = 252
    short_cooldown_sessions: int = 5
    medium_short_cooldown_sessions: int = 20
    medium_long_cooldown_sessions: int = 60
    min_confidence: Decimal = Decimal("0.60")

    def __post_init__(self) -> None:
        for value, name in (
            (self.short_forward_sessions, "short_forward_sessions"),
            (
                self.medium_short_forward_sessions,
                "medium_short_forward_sessions",
            ),
            (
                self.medium_long_forward_sessions,
                "medium_long_forward_sessions",
            ),
            (self.short_cooldown_sessions, "short_cooldown_sessions"),
            (
                self.medium_short_cooldown_sessions,
                "medium_short_cooldown_sessions",
            ),
            (
                self.medium_long_cooldown_sessions,
                "medium_long_cooldown_sessions",
            ),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        _confidence(self.min_confidence, "min_confidence")

    def forward_sessions(self, horizon: Horizon) -> int:
        return {
            "short": self.short_forward_sessions,
            "medium_short": self.medium_short_forward_sessions,
            "medium_long": self.medium_long_forward_sessions,
        }[horizon]

    def cooldown_sessions(self, horizon: Horizon) -> int:
        return {
            "short": self.short_cooldown_sessions,
            "medium_short": self.medium_short_cooldown_sessions,
            "medium_long": self.medium_long_cooldown_sessions,
        }[horizon]


@dataclass(frozen=True)
class MarketTimingEvent:
    """One signal and its subsequently observed directional outcome."""

    signal_date: str
    effective_date: str
    evaluation_date: str
    horizon: Horizon
    direction: EventDirection
    confidence: Decimal
    entry_close: Decimal
    exit_close: Decimal
    forward_return: Decimal
    signed_forward_return: Decimal
    max_favorable_excursion: Decimal
    max_adverse_excursion: Decimal

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
        _horizon(self.horizon)
        _event_direction(self.direction)
        _confidence(self.confidence, "confidence")
        _positive_decimal(self.entry_close, "entry_close")
        _positive_decimal(self.exit_close, "exit_close")
        for value, name in (
            (self.forward_return, "forward_return"),
            (self.signed_forward_return, "signed_forward_return"),
            (self.max_favorable_excursion, "max_favorable_excursion"),
            (self.max_adverse_excursion, "max_adverse_excursion"),
        ):
            _finite_decimal(value, name)
        if self.max_favorable_excursion < ZERO:
            raise ValueError("max_favorable_excursion must be non-negative")
        if self.max_adverse_excursion > ZERO:
            raise ValueError("max_adverse_excursion must be non-positive")


@dataclass(frozen=True)
class MarketTimingSkippedEvent:
    """A qualifying signal omitted from event metrics with an explicit reason."""

    signal_date: str
    effective_date: str
    horizon: Horizon
    direction: EventDirection
    reason: str

    def __post_init__(self) -> None:
        signal_date = _strict_iso_date(self.signal_date, "signal_date")
        effective_date = _strict_iso_date(
            self.effective_date,
            "effective_date",
        )
        if effective_date <= signal_date:
            raise ValueError("effective_date must be later than signal_date")
        _horizon(self.horizon)
        _event_direction(self.direction)
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must not be blank")


@dataclass(frozen=True)
class MarketTimingEventStudyRun:
    asset_id: str
    version: str
    events: tuple[MarketTimingEvent, ...]
    skipped: tuple[MarketTimingSkippedEvent, ...]
    metrics: dict[str, GroupedMetricValue]
    status: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("asset_id must not be blank")
        if self.version != MARKET_TIMING_EVENT_STUDY_VERSION:
            raise ValueError(
                "version must be market_timing_event_study_v1"
            )
        events = tuple(self.events)
        skipped = tuple(self.skipped)
        metrics = {
            key: dict(value) if isinstance(value, dict) else value
            for key, value in self.metrics.items()
        }
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "skipped", skipped)
        object.__setattr__(self, "metrics", metrics)
        if any(not isinstance(event, MarketTimingEvent) for event in events):
            raise TypeError("events must contain MarketTimingEvent values")
        if any(
            not isinstance(item, MarketTimingSkippedEvent) for item in skipped
        ):
            raise TypeError(
                "skipped must contain MarketTimingSkippedEvent values"
            )
        _validate_metrics(metrics)
        if self.status != "completed":
            raise ValueError("status must be completed")


class MarketTimingEventStudyEngine:
    """Validate confirmed SSE timing signals without changing signal selection."""

    @staticmethod
    def run(
        series: SSEIndexSeries,
        results: Sequence[SSEMarketTimingResult],
        parameters: MarketTimingEventStudyParameters,
    ) -> MarketTimingEventStudyRun:
        if not isinstance(series, SSEIndexSeries):
            raise TypeError("series must be SSEIndexSeries")
        if not isinstance(parameters, MarketTimingEventStudyParameters):
            raise TypeError(
                "parameters must be MarketTimingEventStudyParameters"
            )

        bars = series.bars
        index_by_date = {
            bar.trade_date: index for index, bar in enumerate(bars)
        }
        candidates = _validated_candidates(
            results,
            index_by_date,
            parameters.min_confidence,
            series.asset_id,
        )

        events: list[MarketTimingEvent] = []
        skipped: list[MarketTimingSkippedEvent] = []
        last_selected_index: dict[tuple[Horizon, EventDirection], int] = {}

        for effective_index, result, signal in candidates:
            key = (signal.horizon, signal.direction)
            previous_index = last_selected_index.get(key)
            cooldown = parameters.cooldown_sessions(signal.horizon)
            if (
                previous_index is not None
                and effective_index - previous_index <= cooldown
            ):
                skipped.append(
                    _skipped(result, signal, "cooldown")
                )
                continue

            # Cluster selection is fixed before any forward outcome is read.
            last_selected_index[key] = effective_index
            forward_sessions = parameters.forward_sessions(signal.horizon)
            evaluation_index = effective_index + forward_sessions
            if evaluation_index >= len(bars):
                skipped.append(
                    _skipped(
                        result,
                        signal,
                        "insufficient_forward_sessions",
                    )
                )
                continue

            entry_close = bars[effective_index].close
            exit_close = bars[evaluation_index].close
            forward_return = exit_close / entry_close - ONE
            raw_path = tuple(
                bar.close / entry_close - ONE
                for bar in bars[effective_index : evaluation_index + 1]
            )
            if signal.direction == "low":
                signed_return = forward_return
                favorable = max(raw_path)
                adverse = min(raw_path)
            else:
                signed_return = -forward_return
                favorable = -min(raw_path)
                adverse = -max(raw_path)

            events.append(
                MarketTimingEvent(
                    signal_date=result.as_of_date,
                    effective_date=result.effective_date,
                    evaluation_date=bars[evaluation_index].trade_date,
                    horizon=signal.horizon,
                    direction=signal.direction,
                    confidence=signal.confidence,
                    entry_close=entry_close,
                    exit_close=exit_close,
                    forward_return=forward_return,
                    signed_forward_return=signed_return,
                    max_favorable_excursion=favorable,
                    max_adverse_excursion=adverse,
                )
            )

        metrics = _calculate_metrics(events, skipped)
        return MarketTimingEventStudyRun(
            asset_id=series.asset_id,
            version=MARKET_TIMING_EVENT_STUDY_VERSION,
            events=tuple(events),
            skipped=tuple(skipped),
            metrics=metrics,
        )


def _validated_candidates(
    results: Sequence[SSEMarketTimingResult],
    index_by_date: dict[str, int],
    min_confidence: Decimal,
    series_asset_id: str,
) -> list[tuple[int, SSEMarketTimingResult, HorizonTimingSignal]]:
    candidates: list[
        tuple[int, SSEMarketTimingResult, HorizonTimingSignal]
    ] = []
    seen: set[tuple[str, str]] = set()
    for result in results:
        if not isinstance(result, SSEMarketTimingResult):
            raise TypeError(
                "results must contain SSEMarketTimingResult values"
            )
        if result.asset_id != series_asset_id:
            raise ValueError(
                "result asset_id does not match event-study series: "
                f"{result.asset_id}"
            )
        effective_index = index_by_date.get(result.effective_date)
        if effective_index is None:
            raise ValueError(
                "result effective_date has no index bar: "
                f"{result.effective_date}"
            )
        for signal in result.signals:
            key = (result.effective_date, signal.horizon)
            if key in seen:
                raise ValueError(
                    "duplicate effective_date/horizon: "
                    f"{result.effective_date}/{signal.horizon}"
                )
            seen.add(key)
            if (
                signal.stage == "confirmed"
                and signal.direction in DIRECTIONS
                and signal.confidence >= min_confidence
            ):
                candidates.append((effective_index, result, signal))
    horizon_order = {value: index for index, value in enumerate(HORIZONS)}
    direction_order = {value: index for index, value in enumerate(DIRECTIONS)}
    candidates.sort(
        key=lambda item: (
            item[0],
            horizon_order[item[2].horizon],
            direction_order[item[2].direction],
        )
    )
    return candidates


def _skipped(
    result: SSEMarketTimingResult,
    signal: HorizonTimingSignal,
    reason: str,
) -> MarketTimingSkippedEvent:
    return MarketTimingSkippedEvent(
        signal_date=result.as_of_date,
        effective_date=result.effective_date,
        horizon=signal.horizon,
        direction=signal.direction,
        reason=reason,
    )


def _calculate_metrics(
    events: list[MarketTimingEvent],
    skipped: list[MarketTimingSkippedEvent],
) -> dict[str, GroupedMetricValue]:
    metrics: dict[str, GroupedMetricValue] = {}
    groups: dict[str, list[MarketTimingEvent]] = {}
    for event in events:
        groups.setdefault(
            f"{event.horizon}:{event.direction}",
            [],
        ).append(event)

    for group_name in sorted(groups):
        group = groups[group_name]
        signed_returns = sorted(
            event.signed_forward_return for event in group
        )
        adverse_excursions = [
            event.max_adverse_excursion for event in group
        ]
        count = len(group)
        hit_count = sum(value > ZERO for value in signed_returns)
        hit_rate = Decimal(hit_count) / Decimal(count)
        mean_signed_return = (
            sum(signed_returns, ZERO) / Decimal(count)
        )
        metrics[group_name] = {
            "count": count,
            "hit_rate": hit_rate,
            "mean_signed_forward_return": mean_signed_return,
            "median_signed_forward_return": _median(signed_returns),
            "mean_max_adverse_excursion": (
                sum(adverse_excursions, ZERO) / Decimal(count)
            ),
        }

    metrics["event_count"] = len(events)
    metrics["skipped_count"] = len(skipped)
    metrics["version"] = MARKET_TIMING_EVENT_STUDY_VERSION
    return metrics


def _median(values: list[Decimal]) -> Decimal:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")


def _validate_metrics(metrics: dict[str, GroupedMetricValue]) -> None:
    for key, value in metrics.items():
        if not isinstance(key, str) or not key:
            raise ValueError("metric keys must be non-blank strings")
        values = value.values() if isinstance(value, dict) else (value,)
        if any(
            isinstance(item, bool)
            or not isinstance(item, (Decimal, int, str))
            for item in values
        ):
            raise TypeError("metric values must be Decimal, int, or str")


def _horizon(value: str) -> None:
    if value not in HORIZONS:
        raise ValueError("horizon must be short, medium_short, or medium_long")


def _event_direction(value: str) -> None:
    if value not in DIRECTIONS:
        raise ValueError("direction must be low or high")


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


def _confidence(value: Decimal, name: str) -> None:
    _finite_decimal(value, name)
    if not ZERO <= value <= ONE:
        raise ValueError(f"{name} must be between 0 and 1")
