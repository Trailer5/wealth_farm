"""Point-in-time event mapping and market-adjusted reaction measures."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from .models import (
    EventTimingQuality,
    ExternalEventAnchor,
    MarketReactionSnapshot,
)


ONE = Decimal("1")
REACTION_CALCULATION_VERSION = "event_name_reaction_v1"


@dataclass(frozen=True)
class ReactionBar:
    trade_date: str
    close: Decimal
    volume: Decimal
    source: str
    limit_up: Decimal | None = None
    limit_down: Decimal | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.trade_date)
        for value, name, positive in (
            (self.close, "close", True),
            (self.volume, "volume", False),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
            if positive and value <= 0:
                raise ValueError("close must be positive")
            if not positive and value < 0:
                raise ValueError("volume must be non-negative")
        for value, name in (
            (self.limit_up, "limit_up"),
            (self.limit_down, "limit_down"),
        ):
            if value is not None and (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value <= 0
            ):
                raise ValueError(
                    f"{name} must be a positive finite Decimal"
                )
        if not self.source.strip():
            raise ValueError("source must not be blank")


@dataclass(frozen=True)
class EventTradeDate:
    trade_date: str
    timing_quality: EventTimingQuality


class EventMarketReactionCalculator:
    """Calculate daily market-adjusted reaction without future filling."""

    def __init__(
        self,
        *,
        lookback_volume_days: int = 20,
        reversal_days: int = 5,
    ) -> None:
        if lookback_volume_days < 5:
            raise ValueError(
                "lookback_volume_days must be at least 5"
            )
        if reversal_days < 1:
            raise ValueError("reversal_days must be positive")
        self.lookback_volume_days = lookback_volume_days
        self.reversal_days = reversal_days

    def calculate(
        self,
        *,
        asset_id: str,
        event: ExternalEventAnchor,
        asset_bars: tuple[ReactionBar, ...],
        benchmark_bars: tuple[ReactionBar, ...],
        trading_calendar: tuple[str, ...],
        calendar_id: str,
        as_of_date: str,
    ) -> MarketReactionSnapshot:
        cutoff = date.fromisoformat(as_of_date)
        asset = _unique_bars(asset_bars, "asset")
        benchmark = _unique_bars(benchmark_bars, "benchmark")
        if not calendar_id.strip():
            raise ValueError("calendar_id must not be blank")
        calendar_dates = tuple(
            value_date
            for value_date in trading_calendar
            if date.fromisoformat(value_date) <= cutoff
        )
        if len(calendar_dates) < self.lookback_volume_days + 2:
            raise ValueError(
                "reaction calculation has insufficient calendar history"
            )
        mapping = map_event_to_trade_date(
            event.first_public_at,
            calendar_dates,
        )
        event_index = calendar_dates.index(mapping.trade_date)
        if event_index < self.lookback_volume_days:
            raise ValueError(
                "insufficient pre-event volume estimation window"
            )
        required_dates = calendar_dates[
            event_index - self.lookback_volume_days : event_index + 1
        ]
        missing_asset = [
            value_date
            for value_date in required_dates
            if value_date not in asset
        ]
        missing_benchmark = [
            value_date
            for value_date in required_dates
            if value_date not in benchmark
        ]
        if missing_asset:
            raise ValueError(
                "asset is suspended or missing on required dates: "
                f"{missing_asset}"
            )
        if missing_benchmark:
            raise ValueError(
                "benchmark is missing on required dates: "
                f"{missing_benchmark}"
            )
        previous_date = calendar_dates[event_index - 1]
        event_asset_return = (
            asset[mapping.trade_date].close
            / asset[previous_date].close
            - ONE
        )
        event_benchmark_return = (
            benchmark[mapping.trade_date].close
            / benchmark[previous_date].close
            - ONE
        )
        lookback_dates = calendar_dates[
            event_index - self.lookback_volume_days : event_index
        ]
        volume_zscore = _zscore(
            asset[mapping.trade_date].volume,
            [asset[value_date].volume for value_date in lookback_dates],
        )

        reversal_index = event_index + self.reversal_days
        reversal_abnormal_return = None
        observed_through = mapping.trade_date
        if reversal_index < len(calendar_dates):
            reversal_date = calendar_dates[reversal_index]
            if (
                reversal_date not in asset
                or reversal_date not in benchmark
            ):
                reversal_date = None
        else:
            reversal_date = None
        if reversal_date is not None:
            reversal_abnormal_return = (
                asset[reversal_date].close
                / asset[mapping.trade_date].close
                - ONE
                - (
                    benchmark[reversal_date].close
                    / benchmark[mapping.trade_date].close
                    - ONE
                )
            )
            observed_through = reversal_date
        event_bar = asset[mapping.trade_date]
        return MarketReactionSnapshot(
            asset_id=asset_id,
            event_id=event.event_id,
            event_first_public_at=event.first_public_at,
            event_trade_date=mapping.trade_date,
            observed_through=observed_through,
            abnormal_return=(
                event_asset_return - event_benchmark_return
            ),
            volume_zscore=volume_zscore,
            reversal_abnormal_return=reversal_abnormal_return,
            timing_quality=mapping.timing_quality,
            limit_state=_limit_state(event_bar),
            calendar_id=calendar_id,
            calculation_version=REACTION_CALCULATION_VERSION,
            source_refs=tuple(
                dict.fromkeys(
                    (
                        event.source_url,
                        *(bar.source for bar in asset_bars),
                        *(bar.source for bar in benchmark_bars),
                    )
                )
            ),
        )


def map_event_to_trade_date(
    first_public_at: str,
    trading_dates: tuple[str, ...],
) -> EventTradeDate:
    if not trading_dates:
        raise ValueError("trading_dates must not be empty")
    parsed_dates = [date.fromisoformat(value) for value in trading_dates]
    if parsed_dates != sorted(parsed_dates):
        raise ValueError("trading_dates must be increasing")
    if len(set(parsed_dates)) != len(parsed_dates):
        raise ValueError("trading_dates must be unique")
    published = datetime.fromisoformat(
        first_public_at.replace("Z", "+00:00")
    )
    if published.tzinfo is None or published.utcoffset() is None:
        raise ValueError("first_public_at must include a timezone")
    local = published.astimezone(ZoneInfo("Asia/Shanghai"))
    local_date = local.date()
    trading_set = set(parsed_dates)
    if local_date not in trading_set:
        return EventTradeDate(
            _next_trade_date(local_date, parsed_dates).isoformat(),
            EventTimingQuality.NON_TRADING_DAY,
        )
    local_time = local.time().replace(tzinfo=None)
    if local_time < time(9, 30):
        return EventTradeDate(
            local_date.isoformat(),
            EventTimingQuality.PREOPEN,
        )
    if local_time < time(15, 0):
        return EventTradeDate(
            local_date.isoformat(),
            EventTimingQuality.INTRADAY_AMBIGUOUS,
        )
    return EventTradeDate(
        _next_trade_date(local_date, parsed_dates).isoformat(),
        EventTimingQuality.POSTCLOSE,
    )


def _next_trade_date(
    value_date: date,
    trading_dates: list[date],
) -> date:
    for trade_date in trading_dates:
        if trade_date > value_date:
            return trade_date
    raise ValueError("no trading date is available after the event")


def _unique_bars(
    bars: tuple[ReactionBar, ...],
    label: str,
) -> dict[str, ReactionBar]:
    result = {}
    for bar in bars:
        if bar.trade_date in result:
            raise ValueError(
                f"duplicate {label} bar date: {bar.trade_date}"
            )
        result[bar.trade_date] = bar
    return result


def _zscore(
    value: Decimal,
    history: list[Decimal],
) -> Decimal | None:
    if not history:
        raise ValueError("zscore history must not be empty")
    mean = sum(history, Decimal("0")) / Decimal(len(history))
    variance = sum(
        ((item - mean) ** 2 for item in history),
        Decimal("0"),
    ) / Decimal(len(history))
    if variance == 0:
        return Decimal("0") if value == mean else None
    return (value - mean) / Decimal(str(math.sqrt(float(variance))))


def _limit_state(bar: ReactionBar) -> str:
    if bar.limit_up is not None and bar.close >= bar.limit_up:
        return "limit_up"
    if bar.limit_down is not None and bar.close <= bar.limit_down:
        return "limit_down"
    return "tradable_close"
