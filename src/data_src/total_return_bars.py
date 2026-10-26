"""Bars carrying the ex-rights reference close, composed from several providers."""

from __future__ import annotations

import bisect
from dataclasses import replace
from datetime import date, timedelta

from .akshare import AkShareDataSource
from .models import DailyBar
from .mootdx import MootdxDataSource
from .sina import SinaExchangeFundDataSource
from .ths import ThsDataSource


def sina_stock_total_return_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    sina: SinaExchangeFundDataSource | None = None,
    akshare: AkShareDataSource | None = None,
) -> list[DailyBar]:
    """Sina unadjusted bars with preclose implied by Sina hfq factors."""

    lookback_start = (
        date.fromisoformat(start_date[:10]) - timedelta(days=20)
    ).isoformat()
    bars = (sina or SinaExchangeFundDataSource()).get_a_share_daily_bars(
        symbol,
        lookback_start,
        end_date,
    )
    factors = (akshare or AkShareDataSource()).get_a_share_hfq_factors(symbol)
    return bars_with_factor_preclose(bars, factors, start_date)


def ths_tdx_total_return_bars(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    ths: ThsDataSource | None = None,
    mootdx: MootdxDataSource | None = None,
) -> list[DailyBar]:
    """10jqka unadjusted bars with preclose from TDX ex-rights events."""

    lookback_start = (
        date.fromisoformat(start_date[:10]) - timedelta(days=20)
    ).isoformat()
    bars = (ths or ThsDataSource()).get_a_share_daily_bars(
        symbol,
        lookback_start,
        end_date,
    )
    events = (mootdx or MootdxDataSource()).get_xdxr_events(symbol)
    return bars_with_event_preclose(bars, events, start_date)


def bars_with_event_preclose(
    bars: list[DailyBar],
    events: list[dict[str, float | str]],
    start_date: str,
) -> list[DailyBar]:
    """Apply every event with an ex-date after the previous bar to its close."""

    ordered_events = sorted(events, key=lambda item: str(item["ex_date"]))
    output = []
    previous: DailyBar | None = None
    for bar in sorted(bars, key=lambda item: item.trade_date):
        day = bar.trade_date[:10]
        preclose = None
        if previous is not None and previous.close:
            preclose = previous.close
            for event in ordered_events:
                if previous.trade_date[:10] < str(event["ex_date"]) <= day:
                    preclose = ex_rights_price(preclose, event)
        previous = bar
        if day < start_date[:10]:
            continue
        output.append(
            replace(
                bar,
                trade_date=day,
                source=f"{bar.source}:xdxr_preclose",
                preclose=preclose,
            )
        )
    return output


def ex_rights_price(price: float, event: dict[str, float | str]) -> float:
    """Exchange reference price after one event; amounts are per 10 shares."""

    if int(event["category"]) == 11:
        ratio = float(event["split_ratio"])
        return price / ratio if ratio else price
    cash = float(event["cash_per_10"]) / 10
    bonus = float(event["bonus_per_10"]) / 10
    rights = float(event["rights_per_10"]) / 10
    rights_price = float(event["rights_price"])
    return (price - cash + rights_price * rights) / (1 + bonus + rights)


def bars_with_factor_preclose(
    bars: list[DailyBar],
    factors: list[tuple[str, float]],
    start_date: str,
) -> list[DailyBar]:
    """preclose_t = close_{t-1} * f_{t-1} / f_t, with f effective from its date."""

    factor_dates = [day for day, _ in factors]

    def factor_on(day: str) -> float | None:
        index = bisect.bisect_right(factor_dates, day) - 1
        return factors[index][1] if index >= 0 else None

    output = []
    previous: DailyBar | None = None
    for bar in sorted(bars, key=lambda item: item.trade_date):
        day = bar.trade_date[:10]
        preclose = None
        if previous is not None and previous.close and bar.close:
            current_factor = factor_on(day)
            previous_factor = factor_on(previous.trade_date[:10])
            if current_factor and previous_factor:
                preclose = previous.close * previous_factor / current_factor
        previous = bar
        if day < start_date[:10]:
            continue
        output.append(
            replace(
                bar,
                trade_date=day,
                source=f"{bar.source}:hfq_factor_preclose",
                preclose=preclose,
            )
        )
    return output
