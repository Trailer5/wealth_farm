"""Unified target-weight backtest (stage 9 slice 2).

Every strategy is reduced to target weights decided at a signal date's close and executed at the
next trading day's open. Sells run before buys; buys are scaled to the cash on hand. Costs follow
the asset type (commission with a per-trade minimum, stamp duty on stock sales by date, slippage),
optional board lots use the unadjusted price, and idle cash earns a short rate. Prices are
total-return levels (forward adjusted), so dividends are reinvested in the asset itself.

Design notes: docs/modules/3-分析工具/低频回测模块.md「统一回测入口」.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence

# (effective from, buy rate, sell rate): 2005-01-24 0.1% both sides; 2007-05-30 0.3% both;
# 2008-04-24 0.1% both; 2008-09-19 0.1% sell only; 2023-08-28 0.05% sell only.
STOCK_STAMP_DUTY = (
    ("1900-01-01", 0.001, 0.001),
    ("2007-05-30", 0.003, 0.003),
    ("2008-04-24", 0.001, 0.001),
    ("2008-09-19", 0.0, 0.001),
    ("2023-08-28", 0.0, 0.0005),
)
TRADING_DAYS = 252
# Drift below this share of equity is not traded on a rebalance.
REBALANCE_TOLERANCE = 0.001
CAPACITY_PARTICIPATION = 0.05


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    slippage_rate: float = 0.0005
    stamp_duty: tuple[tuple[str, float, float], ...] = ()
    lot_size: int = 0

    def stamp_rate(self, side: str, on: str) -> float:
        rate = 0.0
        for since, buy, sell in self.stamp_duty:
            if since <= on:
                rate = buy if side == "buy" else sell
        return rate


def cost_model_for(asset_type: str, *, lots: bool = False) -> CostModel:
    """Stocks pay stamp duty on sales; funds and index proxies only commission. Lots of 100 when asked."""

    lot = 100 if lots else 0
    if asset_type == "stock":
        return CostModel(stamp_duty=STOCK_STAMP_DUTY, lot_size=lot)
    return CostModel(lot_size=lot)


@dataclass(frozen=True)
class PriceBar:
    asset_id: str
    trade_date: str
    open: float
    close: float
    raw_open: float | None = None
    amount: float | None = None
    can_buy: bool = True
    can_sell: bool = True


@dataclass(frozen=True)
class TargetSignal:
    as_of_date: str
    weights: Mapping[str, float]
    reason: str = ""

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if any(value < 0 for value in self.weights.values()):
            raise ValueError("target weights must not be negative")
        if sum(self.weights.values()) > 1 + 1e-9:
            raise ValueError("target weights must not sum above 1")


@dataclass(frozen=True)
class Trade:
    signal_date: str
    trade_date: str
    asset_id: str
    side: str
    notional: float
    commission: float
    stamp_duty: float
    slippage: float
    participation: float | None


@dataclass(frozen=True)
class SkippedOrder:
    signal_date: str
    trade_date: str
    asset_id: str
    reason: str


@dataclass(frozen=True)
class BacktestResult:
    dates: tuple[str, ...]
    equity: tuple[float, ...]
    exposure: tuple[float, ...]
    trades: tuple[Trade, ...]
    skipped: tuple[SkippedOrder, ...]
    initial_cash: float
    cash_returns: tuple[float, ...] = field(default=())

    def metrics(self) -> dict[str, float | int | None]:
        return performance_metrics(self.dates, self.equity, self.cash_returns) | {
            "trade_count": len(self.trades),
            "skipped_orders": len(self.skipped),
            "cost_total": round(sum(t.commission + t.stamp_duty + t.slippage for t in self.trades), 2),
            "cost_drag_annual": _cost_drag(self),
            "turnover_annual": _turnover(self),
            "average_exposure": round(sum(self.exposure) / len(self.exposure), 4) if self.exposure else None,
        }

    def excess_returns(self) -> list[float]:
        """Daily returns over cash, the series behind the reported Sharpe."""

        cash = list(self.cash_returns)[1:] if self.cash_returns else [0.0] * (len(self.equity) - 1)
        return [self.equity[i] / self.equity[i - 1] - 1 - cash[i - 1] for i in range(1, len(self.equity))]

    def capacity(self) -> dict[str, float | int | None]:
        """Capital at which the 95th-percentile trade would take CAPACITY_PARTICIPATION of the day's turnover."""

        shares = sorted(t.participation for t in self.trades if t.participation)
        if not shares:
            return {"trades_with_turnover": 0, "capacity": None, "p95_participation": None, "max_participation": None}
        p95 = shares[min(len(shares) - 1, int(math.ceil(0.95 * len(shares))) - 1)]
        return {
            "trades_with_turnover": len(shares),
            "p95_participation": round(p95, 6),
            "max_participation": round(shares[-1], 6),
            "participation_limit": CAPACITY_PARTICIPATION,
            "capacity": round(self.initial_cash * CAPACITY_PARTICIPATION / p95, -4) if p95 > 0 else None,
        }


def run_target_weights(
    bars: Sequence[PriceBar],
    signals: Sequence[TargetSignal],
    *,
    start_date: str,
    end_date: str,
    initial_cash: float,
    costs: Mapping[str, CostModel],
    cash_rates: Sequence[tuple[str, float]] = (),
) -> BacktestResult:
    """cash_rates: (date, annual rate as a decimal) points; the latest point on or before a day applies."""

    by_date: dict[str, dict[str, PriceBar]] = {}
    for bar in bars:
        if start_date <= bar.trade_date <= end_date:
            by_date.setdefault(bar.trade_date, {})[bar.asset_id] = bar
    calendar = sorted(by_date)
    if not calendar:
        raise ValueError("no price bars inside the backtest range")
    pending = _schedule(signals, calendar)
    rate_dates = [day for day, _ in cash_rates]
    cash = float(initial_cash)
    units: dict[str, float] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    skipped: list[SkippedOrder] = []
    equity_curve, exposure_curve, cash_returns = [], [], []
    previous_day: str | None = None
    for day in calendar:
        day_bars = by_date[day]
        daily_cash_return = 0.0
        if previous_day is not None:
            index = bisect.bisect_right(rate_dates, previous_day) - 1
            rate = cash_rates[index][1] if index >= 0 else 0.0
            daily_cash_return = rate * (date.fromisoformat(day) - date.fromisoformat(previous_day)).days / 365
            cash *= 1 + daily_cash_return
        signal = pending.get(day)
        if signal is not None:
            cash = _rebalance(signal, day, day_bars, last_close, units, cash, costs, trades, skipped)
        for asset_id, bar in day_bars.items():
            last_close[asset_id] = bar.close
        held = sum(quantity * last_close[asset_id] for asset_id, quantity in units.items())
        equity = cash + held
        equity_curve.append(equity)
        exposure_curve.append(held / equity if equity > 0 else 0.0)
        cash_returns.append(daily_cash_return)
        previous_day = day
    return BacktestResult(
        dates=tuple(calendar),
        equity=tuple(equity_curve),
        exposure=tuple(exposure_curve),
        trades=tuple(trades),
        skipped=tuple(skipped),
        initial_cash=float(initial_cash),
        cash_returns=tuple(cash_returns),
    )


def _schedule(signals: Sequence[TargetSignal], calendar: list[str]) -> dict[str, TargetSignal]:
    """Each signal executes on the first trading day after its date; a later signal for the same day wins."""

    pending: dict[str, TargetSignal] = {}
    for signal in sorted(signals, key=lambda item: item.as_of_date):
        index = bisect.bisect_right(calendar, signal.as_of_date)
        if index < len(calendar):
            pending[calendar[index]] = signal
    return pending


def _rebalance(
    signal: TargetSignal,
    day: str,
    day_bars: dict[str, PriceBar],
    last_close: dict[str, float],
    units: dict[str, float],
    cash: float,
    costs: Mapping[str, CostModel],
    trades: list[Trade],
    skipped: list[SkippedOrder],
) -> float:
    prices = {
        asset_id: (day_bars[asset_id].open if asset_id in day_bars else last_close.get(asset_id))
        for asset_id in set(units) | set(signal.weights)
    }
    equity = cash + sum(quantity * (prices[asset_id] or 0.0) for asset_id, quantity in units.items())
    deltas = {}
    for asset_id in sorted(prices):
        current = units.get(asset_id, 0.0) * (prices[asset_id] or 0.0)
        delta = equity * signal.weights.get(asset_id, 0.0) - current
        if abs(delta) < equity * REBALANCE_TOLERANCE and signal.weights.get(asset_id, 0.0) > 0:
            continue
        if delta == 0:
            continue
        bar = day_bars.get(asset_id)
        if bar is None:
            skipped.append(SkippedOrder(signal.as_of_date, day, asset_id, "停牌或无行情"))
            continue
        if (delta < 0 and not bar.can_sell) or (delta > 0 and not bar.can_buy):
            skipped.append(SkippedOrder(signal.as_of_date, day, asset_id, "涨跌停不可成交"))
            continue
        deltas[asset_id] = delta
    for asset_id, delta in deltas.items():
        if delta < 0:
            cash += _trade(signal, day, day_bars[asset_id], "sell", -delta, units, costs[asset_id], trades)
    buys = {asset_id: delta for asset_id, delta in deltas.items() if delta > 0}
    needed = sum(delta * (1 + costs[asset_id].slippage_rate + costs[asset_id].commission_rate) for asset_id, delta in buys.items())
    scale = min(1.0, cash / needed) if needed > 0 else 0.0
    for asset_id, delta in buys.items():
        cash -= _trade(signal, day, day_bars[asset_id], "buy", delta * scale, units, costs[asset_id], trades, cash)
    return cash


def _trade(
    signal: TargetSignal,
    day: str,
    bar: PriceBar,
    side: str,
    value: float,
    units: dict[str, float],
    cost: CostModel,
    trades: list[Trade],
    cash: float | None = None,
) -> float:
    """Sell: cash received; buy: cash spent. Values are in total-return price terms."""

    held = units.get(bar.asset_id, 0.0)
    quantity = value / bar.open
    if side == "sell":
        quantity = min(quantity, held)
        full_exit = quantity >= held * (1 - 1e-9)
    if cost.lot_size and bar.raw_open:
        scale = bar.open / bar.raw_open
        shares = quantity * scale
        lots = math.floor(shares / cost.lot_size)
        if side == "sell" and full_exit:
            lots_shares = shares
        else:
            lots_shares = lots * cost.lot_size
        quantity = lots_shares / scale
    if quantity <= 0:
        return 0.0
    notional = quantity * bar.open
    slippage = notional * cost.slippage_rate
    commission = max(cost.min_commission, notional * cost.commission_rate)
    stamp = notional * cost.stamp_rate(side, day)
    if side == "buy":
        total = notional + slippage + commission + stamp
        if cash is not None and total > cash:
            factor = max(0.0, (cash - commission) / (notional * (1 + cost.slippage_rate + cost.stamp_rate(side, day))))
            quantity *= factor
            if cost.lot_size and bar.raw_open:
                scale = bar.open / bar.raw_open
                quantity = math.floor(quantity * scale / cost.lot_size) * cost.lot_size / scale
            if quantity <= 0:
                return 0.0
            notional = quantity * bar.open
            slippage = notional * cost.slippage_rate
            stamp = notional * cost.stamp_rate(side, day)
            total = notional + slippage + commission + stamp
        units[bar.asset_id] = held + quantity
        cash_flow = total
    else:
        units[bar.asset_id] = held - quantity
        if units[bar.asset_id] <= held * 1e-9:
            del units[bar.asset_id]
        cash_flow = notional - slippage - commission - stamp
    raw_notional = notional * (bar.raw_open / bar.open) if bar.raw_open else notional
    trades.append(
        Trade(
            signal_date=signal.as_of_date,
            trade_date=day,
            asset_id=bar.asset_id,
            side=side,
            notional=round(notional, 2),
            commission=round(commission, 4),
            stamp_duty=round(stamp, 4),
            slippage=round(slippage, 4),
            participation=(raw_notional / bar.amount) if bar.amount else None,
        )
    )
    return cash_flow


def performance_metrics(
    dates: Sequence[str], equity: Sequence[float], cash_returns: Sequence[float] = ()
) -> dict[str, float | int | None]:
    if len(equity) < 2:
        return {"days": len(equity)}
    returns = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    years = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days / 365.25
    total = equity[-1] / equity[0] - 1
    mean = sum(returns) / len(returns)
    volatility = math.sqrt(sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)) * math.sqrt(TRADING_DAYS)
    excess = [r - c for r, c in zip(returns, list(cash_returns)[1:])] if cash_returns else returns
    excess_mean = sum(excess) / len(excess)
    excess_sd = math.sqrt(sum((r - excess_mean) ** 2 for r in excess) / max(1, len(excess) - 1))
    peak, drawdown = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "start": dates[0],
        "end": dates[-1],
        "days": len(equity),
        "total_return": round(total, 4),
        "annual_return": round((1 + total) ** (1 / years) - 1, 4) if years > 0 and total > -1 else None,
        "annual_volatility": round(volatility, 4),
        "sharpe": round(excess_mean / excess_sd * math.sqrt(TRADING_DAYS), 3) if excess_sd > 0 else None,
        "max_drawdown": round(drawdown, 4),
    }


def _cost_drag(result: BacktestResult) -> float | None:
    years = (date.fromisoformat(result.dates[-1]) - date.fromisoformat(result.dates[0])).days / 365.25
    if years <= 0:
        return None
    average = sum(result.equity) / len(result.equity)
    costs = sum(t.commission + t.stamp_duty + t.slippage for t in result.trades)
    return round(costs / average / years, 5)


def _turnover(result: BacktestResult) -> float | None:
    years = (date.fromisoformat(result.dates[-1]) - date.fromisoformat(result.dates[0])).days / 365.25
    if years <= 0:
        return None
    average = sum(result.equity) / len(result.equity)
    return round(sum(t.notional for t in result.trades) / 2 / average / years, 3)


def month_end_dates(calendar: Sequence[str]) -> list[str]:
    """Last trading day of each month in a sorted calendar."""

    return [day for day, following in zip(calendar, list(calendar[1:]) + [""]) if day[:7] != following[:7]]


def buy_and_hold_signals(assets: Sequence[str], before: str) -> list[TargetSignal]:
    """Equal weights bought on the first trading day after `before` and never rebalanced."""

    return [TargetSignal(before, {asset: 1 / len(assets) for asset in assets}, "买入持有")] if assets else []


def equal_weight_signals(
    available: Mapping[str, Sequence[str]], rebalance_dates: Sequence[str]
) -> list[TargetSignal]:
    """Monthly equal weights over the assets that have traded by each rebalance date."""

    signals = []
    for day in rebalance_dates:
        assets = [asset for asset, dates in available.items() if dates and dates[0] <= day]
        if assets:
            signals.append(TargetSignal(day, {asset: 1 / len(assets) for asset in assets}, "等权再平衡"))
    return signals
