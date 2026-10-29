"""Replay strategies on past month-ends inside a scope and measure forward excess returns (decision 9)."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, median
from typing import Any

from .features import Financials, MemberFeatures, ValuationSeries, build_features, exclusion
from .strategies import ScopeContext, Strategy

HORIZON_MONTHS = (6, 12)
# Below this many non-overlapping months a rule's history is reported but not usable as evidence.
MIN_INDEPENDENT_SAMPLES = 8
REPLAY_START = "2019-01-01"
RETURN_BASIS = (
    "总市值变化（东方财富个股估值日频），不受送转影响，不含分红，增发会抬高；"
    "超额 = 入选组合等权收益 − 范围内全部有数据成员等权收益"
)


def scope_context(features: list[MemberFeatures]) -> ScopeContext:
    pes = [item.pe_ttm for item in features if item.pe_ttm is not None and item.pe_ttm > 0]
    percentiles = [item.pe_percentile for item in features if item.pe_percentile is not None]
    return ScopeContext(
        median_pe=median(pes) if pes else None,
        median_pe_percentile=median(percentiles) if percentiles else None,
    )


def replay(
    *,
    strategies: tuple[Strategy, ...],
    members: dict[str, tuple[str, str | None]],
    valuations: dict[str, ValuationSeries],
    financials: Financials,
    as_of_date: str,
) -> dict[str, dict[str, Any]]:
    """Per strategy and horizon: excess-return statistics on non-overlapping month-ends."""

    month_ends = _month_ends(valuations, as_of_date)
    observations: dict[tuple[str, int], list[tuple[str, int, float]]] = {
        (item.strategy_id, horizon): [] for item in strategies for horizon in HORIZON_MONTHS
    }
    for day in month_ends:
        features = []
        for symbol, (name, industry) in members.items():
            series = valuations.get(symbol)
            if series is None:
                continue
            item = build_features(
                symbol=symbol,
                name=name,
                industry_l1=industry,
                financials=financials,
                on=day,
                valuation=series,
            )
            if exclusion(item) is None:
                features.append(item)
        if not features:
            continue
        context = scope_context(features)
        passers = {
            item.strategy_id: [f.symbol for f in features if item.evaluate(f, context).passed]
            for item in strategies
        }
        for horizon in HORIZON_MONTHS:
            end = _add_months(day, horizon)
            if end > as_of_date:
                continue
            returns = {}
            for symbol in members:
                series = valuations.get(symbol)
                start_cap = series.cap_on(day) if series else None
                end_cap = series.cap_on(end) if series else None
                if start_cap and end_cap:
                    returns[symbol] = end_cap / start_cap - 1
            if len(returns) < 5:
                continue
            baseline = mean(returns.values())
            for strategy_id, symbols in passers.items():
                picked = [returns[s] for s in symbols if s in returns]
                if picked:
                    observations[(strategy_id, horizon)].append(
                        (day, len(picked), mean(picked) - baseline)
                    )
    return {
        item.strategy_id: {
            f"{horizon}m": _summary(observations[(item.strategy_id, horizon)], horizon)
            for horizon in HORIZON_MONTHS
        }
        for item in strategies
    }


def _summary(rows: list[tuple[str, int, float]], horizon: int) -> dict[str, Any]:
    if not rows:
        return {"samples": 0, "note": "没有入选月份或历史不足"}
    rows = sorted(rows, reverse=True)
    independent, last = [], None
    for row in rows:
        if last is None or row[0] <= _add_months(last, -horizon):
            independent.append(row)
            last = row[0]
    excess = [row[2] for row in independent]
    return {
        "samples": len(independent),
        "months_with_candidates": len(rows),
        "first_month": rows[-1][0],
        "last_month": rows[0][0],
        "mean_excess": round(mean(excess), 4),
        "median_excess": round(median(excess), 4),
        "hit_rate": round(sum(1 for value in excess if value > 0) / len(excess), 4),
        "worst_excess": round(min(excess), 4),
        "best_excess": round(max(excess), 4),
        "average_candidates": round(mean(row[1] for row in independent), 1),
        "insufficient_samples": len(independent) < MIN_INDEPENDENT_SAMPLES,
    }


def _month_ends(valuations: dict[str, ValuationSeries], as_of_date: str) -> list[str]:
    by_month: dict[str, str] = {}
    for series in valuations.values():
        for day in series.month_days:
            if REPLAY_START <= day <= as_of_date and day > by_month.get(day[:7], ""):
                by_month[day[:7]] = day
    return sorted(by_month.values())


def _add_months(day: str, months: int) -> str:
    """Calendar month-end `months` after the month of `day`."""

    value = date.fromisoformat(day)
    year, month = divmod(value.year * 12 + value.month + months, 12)
    return (date(year, month + 1, 1) - timedelta(days=1)).isoformat()
