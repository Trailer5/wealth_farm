"""Market states from CSI 300 and strategy performance within each state (stage 9 slice 7).

Three fixed dimensions, each judged with data visible at a day's close; the return of the next day is attributed
to that state. Sources: docs/evidence/strategy_sources/market_regimes.md. Design notes:
docs/modules/3-分析工具/低频回测模块.md「分市场状态表现」.
"""

from __future__ import annotations

import bisect
import math
from datetime import date
from typing import Mapping, Sequence

from .strategy_validation import WORSE_Z, annual_sharpe, sharpe_difference_z

REGIME_VERSION = "csi300-v1"
TREND_WINDOW = 200
VOLATILITY_WINDOW = 60
VOLATILITY_HISTORY = 756  # three years of daily volatility readings
VALUATION_YEARS = 10
VALUATION_MIN_YEARS = 5
# A state is read only with at least half a year of days spread over three separate episodes.
MIN_STATE_DAYS = 126
MIN_STATE_SEGMENTS = 3
EXPOSURE_DRIVEN_GAP = 0.7
DEFINITIONS = {
    "trend": f"沪深300 收盘价相对 {TREND_WINDOW} 日均线：之上为 up，之下为 down",
    "volatility": f"沪深300 {VOLATILITY_WINDOW} 日年化波动相对近 3 年（{VOLATILITY_HISTORY} 个交易日）中位数：之上为 high，否则 low",
    "valuation": f"沪深300 滚动市盈率在近 {VALUATION_YEARS} 年中的分位：低于 1/3 为 cheap，高于 2/3 为 expensive，其余 fair；至少 {VALUATION_MIN_YEARS} 年历史，按发布日可见",
}
LABELS = {
    "trend": {"up": "均线之上", "down": "均线之下"},
    "volatility": {"high": "高波动", "low": "低波动"},
    "valuation": {"cheap": "估值低位", "fair": "估值中位", "expensive": "估值高位"},
}


def classify(
    closes: Sequence[tuple[str, float]], valuation: Sequence[tuple[str, str, float]] = ()
) -> dict[str, dict[str, str | None]]:
    """day -> {trend, volatility, valuation}; a dimension is None until its history is long enough.

    closes: (day, close) sorted; valuation: (available_at, period_end, value)."""

    days = [day for day, _ in closes]
    values = [value for _, value in closes]
    states: dict[str, dict[str, str | None]] = {day: {"trend": None, "volatility": None, "valuation": None} for day in days}
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= TREND_WINDOW:
            running -= values[i - TREND_WINDOW]
        if i >= TREND_WINDOW - 1:
            states[days[i]]["trend"] = "up" if value > running / TREND_WINDOW else "down"
    logs = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]
    window: list[float] = []
    history: list[float] = []
    for i in range(VOLATILITY_WINDOW, len(values)):
        sample = logs[i - VOLATILITY_WINDOW:i]
        mean = sum(sample) / len(sample)
        vol = math.sqrt(sum((x - mean) ** 2 for x in sample) / (len(sample) - 1) * 252)
        history.append(vol)
        bisect.insort(window, vol)
        if len(history) > VOLATILITY_HISTORY:
            window.pop(bisect.bisect_left(window, history[-VOLATILITY_HISTORY - 1]))
        if len(history) >= VOLATILITY_HISTORY:
            median = (window[(len(window) - 1) // 2] + window[len(window) // 2]) / 2
            states[days[i]]["volatility"] = "high" if vol > median else "low"
    points = sorted(valuation)
    visible: list[tuple[str, float]] = []
    cursor, label = 0, None
    for day in days:
        fresh = False
        while cursor < len(points) and points[cursor][0] <= day:
            visible.append((points[cursor][1], points[cursor][2]))
            cursor += 1
            fresh = True
        if fresh:
            visible.sort()
            latest_end, latest = visible[-1]
            label = None
            if visible[0][0] <= _years_before(latest_end, VALUATION_MIN_YEARS):
                floor = _years_before(latest_end, VALUATION_YEARS)
                window_values = [value for end, value in visible if end > floor]
                rank = sum(1 for value in window_values if value <= latest) / len(window_values)
                label = "cheap" if rank < 1 / 3 else "expensive" if rank > 2 / 3 else "fair"
        states[day]["valuation"] = label
    return states


def state_performance(
    dates: Sequence[str],
    strategy_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    states: Mapping[str, Mapping[str, str | None]],
    exposure: Sequence[float] = (),
) -> dict[str, object]:
    """Per dimension and state: days, separate episodes, annualised excess return and Sharpe of the strategy and the
    benchmark, the Jobson–Korkie statistic of their difference and the strategy's average exposure. Returns on
    dates[i] belong to the state (and exposure) of dates[i - 1]."""

    output: dict[str, list[dict[str, object]]] = {}
    unclassified = 0
    for dimension in DEFINITIONS:
        groups: dict[str, list[int]] = {}
        for i in range(1, len(dates)):
            label = (states.get(dates[i - 1]) or {}).get(dimension)
            if label is None:
                if dimension == "trend":
                    unclassified += 1
                continue
            groups.setdefault(label, []).append(i - 1)
        output[dimension] = [
            _row(label, LABELS[dimension][label], groups[label], strategy_returns, benchmark_returns, exposure)
            for label in LABELS[dimension]
            if label in groups
        ]
    combined: dict[str, list[int]] = {}
    for i in range(1, len(dates)):
        state = states.get(dates[i - 1]) or {}
        if all(state.get(dimension) for dimension in DEFINITIONS):
            combined.setdefault("/".join(state[d] for d in DEFINITIONS), []).append(i - 1)
    output["combined"] = sorted(
        (
            _row(key, "、".join(LABELS[d][part] for d, part in zip(DEFINITIONS, key.split("/"))), indexes,
                 strategy_returns, benchmark_returns, exposure)
            for key, indexes in combined.items()
        ),
        key=lambda row: -row["days"],
    )
    known = [day for day in states if dates and day <= dates[-1]]
    last = states[max(known)] if known else {}
    # The strategy's exposure is (almost) decided by this dimension, e.g. a 200-day timing rule against the trend
    # dimension: the comparison restates the rule rather than describing an environment.
    exposure_driven = []
    for dimension in DEFINITIONS:
        exposures = [row["average_exposure"] for row in output[dimension] if row["average_exposure"] is not None]
        if len(exposures) >= 2 and max(exposures) - min(exposures) >= EXPOSURE_DRIVEN_GAP:
            exposure_driven.append(dimension)
    return {
        "version": REGIME_VERSION,
        "definitions": DEFINITIONS,
        "dimensions": output,
        "days_without_trend_state": unclassified,
        "current": {
            "date": max(known) if known else None,
            **{d: LABELS[d][last[d]] if last.get(d) else None for d in DEFINITIONS},
        },
        "exposure_driven": exposure_driven,
    }


def _row(
    state: str,
    label: str,
    indexes: list[int],
    strategy_returns: Sequence[float],
    benchmark_returns: Sequence[float],
    exposure: Sequence[float],
) -> dict[str, object]:
    strategy = [strategy_returns[i] for i in indexes]
    benchmark = [benchmark_returns[i] for i in indexes]
    segments = 1 + sum(1 for a, b in zip(indexes, indexes[1:]) if b != a + 1)
    readable = len(indexes) >= MIN_STATE_DAYS and segments >= MIN_STATE_SEGMENTS
    z = sharpe_difference_z(strategy, benchmark)
    return {
        "state": state,
        "label": label,
        "days": len(indexes),
        "segments": segments,
        "average_exposure": round(sum(exposure[i] for i in indexes) / len(indexes), 3) if exposure else None,
        "annual_excess_return": round(sum(strategy) / len(strategy) * 252, 4),
        "sharpe": annual_sharpe(strategy),
        "benchmark_annual_excess_return": round(sum(benchmark) / len(benchmark) * 252, 4),
        "benchmark_sharpe": annual_sharpe(benchmark),
        "z_vs_benchmark": z,
        "readable": readable,
        "reading": _reading(readable, z, len(indexes), segments),
    }


def _reading(readable: bool, z: float | None, days: int, segments: int) -> str:
    if not readable:
        return f"样本不足（{days} 天、{segments} 段；至少 {MIN_STATE_DAYS} 天且 {MIN_STATE_SEGMENTS} 段才解读），只列不读"
    if z is None:
        return "无法比较（某一方没有波动）"
    if z >= WORSE_Z:
        return f"该状态下夏普显著高于基准（z={z}）"
    if z <= -WORSE_Z:
        return f"该状态下夏普显著低于基准（z={z}）"
    return f"该状态下与基准没有显著差别（z={z}）"


def _years_before(day: str, years: int) -> str:
    value = date.fromisoformat(day)
    try:
        return value.replace(year=value.year - years).isoformat()
    except ValueError:
        return value.replace(year=value.year - years, day=28).isoformat()
