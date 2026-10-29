"""Target-weight signal generators for backtesting archived strategies (stage 9 slices 2 and 4).

A generator turns close-price histories (total-return levels, one list per asset) into
(signal date, target weights, reason) using only data visible on each signal date. The backtest
executes each signal at the next trading day's open. Generators are looked up by strategy id or
by the archive's template id (templates: src/strategy/templates.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from .etf_rotation import ETFDualMomentumTrendStrategy, ETFPriceSeries, ETFRotationParameters
from .templates import parse_weights

Series = Mapping[str, Sequence[tuple[str, float]]]
Signal = tuple[str, dict[str, float], str]


@dataclass(frozen=True)
class GeneratorContext:
    """roles: role -> asset_id; symbols: asset_id -> symbol; start: requested backtest start;
    valuation: (available_at, period_end, value) points sorted by available_at (valuation timing only)."""

    roles: Mapping[str, str] = field(default_factory=dict)
    symbols: Mapping[str, str] = field(default_factory=dict)
    start: str = ""
    valuation: Sequence[tuple[str, str, float]] = ()


@dataclass(frozen=True)
class SignalGenerator:
    generator_id: str
    description: str
    warmup_days: Callable[[Mapping[str, str]], int]
    build: Callable[[Series, Sequence[str], Mapping[str, str], GeneratorContext], list[Signal]]
    needs_valuation: bool = False


def _etf_parameters(parameters: Mapping[str, str], roles: Mapping[str, str]) -> ETFRotationParameters:
    defaults = ETFRotationParameters()
    return ETFRotationParameters(
        short_window=int(parameters.get("short_window", defaults.short_window)),
        long_window=int(parameters.get("long_window", defaults.long_window)),
        trend_window=int(parameters.get("trend_window", defaults.trend_window)),
        volatility_window=int(parameters.get("volatility_window", defaults.volatility_window)),
        short_weight=Decimal(str(parameters.get("short_weight", defaults.short_weight))),
        long_weight=Decimal(str(parameters.get("long_weight", defaults.long_weight))),
        top_n=int(parameters.get("top_n", defaults.top_n)),
        defensive_asset_id=roles.get("defensive"),
    )


def _dual_momentum(
    series: Series, calendar: Sequence[str], parameters: Mapping[str, str], context: GeneratorContext
) -> list[Signal]:
    """Month-end evaluation. An asset without the long window is simply not eligible; months where no
    risk asset has it yet are skipped."""

    params = _etf_parameters(parameters, context.roles)
    price_series = tuple(
        ETFPriceSeries(
            asset_id=asset_id,
            dates=tuple(day for day, _ in points),
            closes=tuple(Decimal(str(round(value, 8))) for _, value in points),
        )
        for asset_id, points in series.items()
        if points
    )
    index = {day: i for i, day in enumerate(calendar)}
    signals = []
    for day in _period_ends(calendar, "month"):
        if index[day] + 1 >= len(calendar):
            continue
        signal = ETFDualMomentumTrendStrategy.evaluate(price_series, day, calendar[index[day] + 1], params)
        risk = [m for m in signal.metrics if m.asset_id != params.defensive_asset_id]
        if not any(m.return_long is not None for m in risk):
            continue
        signals.append((day, {k: float(v) for k, v in signal.target_weights.items()}, signal.status))
    return signals


def _moving_average_timing(
    series: Series, calendar: Sequence[str], parameters: Mapping[str, str], context: GeneratorContext
) -> list[Signal]:
    """Hold the asset while its close is above its N-day average, else cash; a signal only when the state flips."""

    window = int(parameters.get("window", 200))
    (asset_id, points), = series.items()
    closes = [value for _, value in points]
    signals: list[Signal] = []
    state = None
    running = sum(closes[:window])
    for i in range(window - 1, len(points)):
        if i >= window:
            running += closes[i] - closes[i - window]
        above = closes[i] > running / window
        if above != state:
            state = above
            signals.append((points[i][0], {asset_id: 1.0} if above else {}, "站上均线" if above else "跌破均线"))
    return signals


def _valuation_percentile_timing(
    series: Series, calendar: Sequence[str], parameters: Mapping[str, str], context: GeneratorContext
) -> list[Signal]:
    """Evaluate on the first trading day each new valuation point is visible; hysteresis between the two thresholds."""

    (asset_id, _), = series.items()
    lookback, min_history = int(parameters["lookback_years"]), int(parameters["min_history_years"])
    buy_below, sell_above = float(parameters["buy_below"]), float(parameters["sell_above"])
    points = sorted(context.valuation)
    signals: list[Signal] = []
    visible: list[tuple[str, float]] = []
    state = None
    cursor = 0
    for day in calendar:
        fresh = False
        while cursor < len(points) and points[cursor][0] <= day:
            visible.append((points[cursor][1], points[cursor][2]))
            cursor += 1
            fresh = True
        if not fresh:
            continue
        visible.sort()
        latest_end, latest = visible[-1]
        if visible[0][0] > _years_before(latest_end, min_history):
            continue
        floor = _years_before(latest_end, lookback)
        window = [value for end, value in visible if end > floor]
        percentile = sum(1 for value in window if value <= latest) / len(window)
        if state is None:
            hold = percentile < (buy_below + sell_above) / 2
        elif percentile <= buy_below:
            hold = True
        elif percentile >= sell_above:
            hold = False
        else:
            continue
        if hold != state:
            state = hold
            reason = f"估值 {latest:g} 处于近 {lookback} 年 {percentile:.0%} 分位"
            signals.append((day, {asset_id: 1.0} if hold else {}, reason))
    return signals


def _fixed_weight_rebalance(
    series: Series, calendar: Sequence[str], parameters: Mapping[str, str], context: GeneratorContext
) -> list[Signal]:
    """Initial allocation just before the start, then back to target weights at each period end."""

    first_dates = {asset_id: points[0][0] for asset_id, points in series.items() if points}
    targets = _target_weights(parameters.get("weights", ""), first_dates, context.symbols)
    days = [_entry_day(calendar, context.start)] + [
        day for day in _period_ends(calendar, parameters.get("frequency", "quarter")) if day > _entry_day(calendar, context.start)
    ]
    signals = []
    for day in days:
        weights = _available(targets, first_dates, day)
        if weights:
            signals.append((day, weights, "再平衡到目标权重"))
    return signals


def _staged_entry(
    series: Series, calendar: Sequence[str], parameters: Mapping[str, str], context: GeneratorContext
) -> list[Signal]:
    """Invested share k/N at the k-th step: the first just before the start, the rest at period ends."""

    periods = int(parameters.get("periods", 12))
    first_dates = {asset_id: points[0][0] for asset_id, points in series.items() if points}
    targets = _target_weights(parameters.get("weights", ""), first_dates, context.symbols)
    entry = _entry_day(calendar, context.start)
    days = [entry] + [day for day in _period_ends(calendar, parameters.get("frequency", "month")) if day > entry]
    signals = []
    for step, day in enumerate(days[:periods], start=1):
        weights = _available(targets, first_dates, day)
        if weights:
            signals.append((day, {k: v * step / periods for k, v in weights.items()}, f"第 {step}/{periods} 期投入"))
    return signals


def _target_weights(text: str, first_dates: Mapping[str, str], symbols: Mapping[str, str]) -> dict[str, float]:
    by_symbol = parse_weights(text)
    if not by_symbol:
        return {asset_id: 1.0 for asset_id in first_dates}
    return {asset_id: by_symbol.get(symbols.get(asset_id, asset_id), 0.0) for asset_id in first_dates}


def _available(targets: Mapping[str, float], first_dates: Mapping[str, str], day: str) -> dict[str, float]:
    """Targets renormalised over the assets that have traded by day."""

    live = {asset_id: weight for asset_id, weight in targets.items() if weight > 0 and first_dates[asset_id] <= day}
    total = sum(live.values())
    return {asset_id: weight / total for asset_id, weight in live.items()} if total else {}


def _entry_day(calendar: Sequence[str], start: str) -> str:
    """Last trading day before the start (so the first order fills on the start), else the first day."""

    before = [day for day in calendar if day < start]
    return before[-1] if before else calendar[0]


def _period_ends(calendar: Sequence[str], frequency: str) -> list[str]:
    months = {"month": None, "quarter": ("03", "06", "09", "12"), "year": ("12",)}[frequency]
    ends = [day for day, following in zip(calendar, list(calendar[1:]) + [""]) if day[:7] != following[:7]]
    return ends if months is None else [day for day in ends if day[5:7] in months]


def _years_before(day: str, years: int) -> str:
    value = date.fromisoformat(day)
    try:
        return value.replace(year=value.year - years).isoformat()
    except ValueError:
        return value.replace(year=value.year - years, day=28).isoformat()


def _dual_momentum_warmup(parameters: Mapping[str, str]) -> int:
    return max(int(parameters.get("long_window", 252)), int(parameters.get("trend_window", 200))) + 1


_DUAL_MOMENTUM_TEXT = "双动量轮动：月末按相对与绝对动量选资产，下一交易日开盘换仓"

SIGNAL_GENERATORS: dict[str, SignalGenerator] = {
    "etf_dual_momentum_trend": SignalGenerator(
        "etf_dual_momentum_trend", _DUAL_MOMENTUM_TEXT, _dual_momentum_warmup, _dual_momentum
    ),
    "dual_momentum_rotation": SignalGenerator(
        "dual_momentum_rotation", _DUAL_MOMENTUM_TEXT, _dual_momentum_warmup, _dual_momentum
    ),
    "ma_timing": SignalGenerator(
        "ma_timing",
        "均线择时：收盘价在 N 日均线之上持有，否则空仓；状态变化的下一交易日开盘成交",
        lambda parameters: int(parameters.get("window", 200)),
        _moving_average_timing,
    ),
    "valuation_percentile_timing": SignalGenerator(
        "valuation_percentile_timing",
        "估值分位择时：估值新值可见时按历史分位与两道阈值决定持有或空仓，下一交易日开盘成交",
        lambda parameters: 0,
        _valuation_percentile_timing,
        needs_valuation=True,
    ),
    "fixed_weight_rebalance": SignalGenerator(
        "fixed_weight_rebalance",
        "定期再平衡：期初按目标权重买入，每期末调回目标权重",
        lambda parameters: 0,
        _fixed_weight_rebalance,
    ),
    "staged_entry": SignalGenerator(
        "staged_entry",
        "分批建仓：期初资金分 N 期投入，未投入部分按现金计息，投完后持有",
        lambda parameters: 0,
        _staged_entry,
    ),
}


def signal_generator_for(strategy_id: str, template_id: str | None) -> SignalGenerator | None:
    return SIGNAL_GENERATORS.get(strategy_id) or (SIGNAL_GENERATORS.get(template_id) if template_id else None)
