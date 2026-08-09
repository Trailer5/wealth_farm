"""Deterministic multi-asset backtest for the ETF rotation research rule."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Sequence

from src.strategy.etf_rotation import (
    ETF_DUAL_MOMENTUM_STRATEGY_ID,
    ETF_DUAL_MOMENTUM_VERSION,
    ETFRotationSignal,
)


ETF_ROTATION_BACKTEST_VERSION = "etf_rotation_backtest_v1"
TradeSide = Literal["buy", "sell"]
MetricValue = Decimal | int | str | None


@dataclass(frozen=True)
class PortfolioTargetSignal:
    """Generic point-in-time target weights with residual cash."""

    as_of_date: str
    effective_date: str
    target_weights: dict[str, Decimal]
    reason: str

    def __post_init__(self) -> None:
        as_of = _strict_iso_date(self.as_of_date, "as_of_date")
        effective = _strict_iso_date(
            self.effective_date,
            "effective_date",
        )
        if effective <= as_of:
            raise ValueError(
                "effective_date must be later than as_of_date"
            )
        if not self.reason.strip():
            raise ValueError("reason must not be blank")
        weights = dict(self.target_weights)
        object.__setattr__(self, "target_weights", weights)
        for asset_id, weight in weights.items():
            if not asset_id.strip():
                raise ValueError("target asset_id must not be blank")
            if (
                not isinstance(weight, Decimal)
                or not weight.is_finite()
                or weight < 0
            ):
                raise ValueError(
                    "target weights must be non-negative finite Decimals"
                )
        if sum(weights.values(), Decimal("0")) > Decimal("1"):
            raise ValueError("target weights must not sum above 1")


@dataclass(frozen=True)
class ETFRotationBacktestBar:
    asset_id: str
    trade_date: str
    open: Decimal
    close: Decimal
    tradable: bool = True

    def __post_init__(self) -> None:
        _non_blank(self.asset_id, "asset_id")
        _strict_iso_date(self.trade_date, "trade_date")
        _positive_decimal(self.open, "open")
        _positive_decimal(self.close, "close")
        if not isinstance(self.tradable, bool):
            raise TypeError("tradable must be bool")


@dataclass(frozen=True)
class ETFRotationBacktestSpec:
    run_id: str
    start_date: str
    end_date: str
    initial_cash: Decimal
    fee_rate: Decimal
    slippage_rate: Decimal
    benchmark_asset_id: str | None = None
    version: str = ETF_ROTATION_BACKTEST_VERSION

    def __post_init__(self) -> None:
        _non_blank(self.run_id, "run_id")
        start = _strict_iso_date(self.start_date, "start_date")
        end = _strict_iso_date(self.end_date, "end_date")
        if start > end:
            raise ValueError("start_date must not exceed end_date")
        _positive_decimal(self.initial_cash, "initial_cash")
        _rate(self.fee_rate, "fee_rate")
        _rate(self.slippage_rate, "slippage_rate")
        if self.benchmark_asset_id is not None:
            _non_blank(self.benchmark_asset_id, "benchmark_asset_id")
        if self.version != ETF_ROTATION_BACKTEST_VERSION:
            raise ValueError(
                "version must be etf_rotation_backtest_v1"
            )


@dataclass(frozen=True)
class ETFRotationTrade:
    signal_date: str
    trade_date: str
    asset_id: str
    side: TradeSide
    quantity: Decimal
    market_open: Decimal
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal

    def __post_init__(self) -> None:
        signal_date = _strict_iso_date(self.signal_date, "signal_date")
        trade_date = _strict_iso_date(self.trade_date, "trade_date")
        if trade_date <= signal_date:
            raise ValueError("trade_date must be later than signal_date")
        _non_blank(self.asset_id, "asset_id")
        if self.side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        for value, name in (
            (self.quantity, "quantity"),
            (self.market_open, "market_open"),
            (self.execution_price, "execution_price"),
            (self.notional, "notional"),
        ):
            _positive_decimal(value, name)
        for value, name in (
            (self.fee, "fee"),
            (self.slippage_cost, "slippage_cost"),
        ):
            _non_negative_decimal(value, name)


@dataclass(frozen=True)
class ETFRotationEquityPoint:
    trade_date: str
    cash: Decimal
    positions_value: Decimal
    equity: Decimal
    benchmark_value: Decimal | None

    def __post_init__(self) -> None:
        _strict_iso_date(self.trade_date, "trade_date")
        _non_negative_decimal(self.cash, "cash")
        _non_negative_decimal(self.positions_value, "positions_value")
        _non_negative_decimal(self.equity, "equity")
        if self.equity != self.cash + self.positions_value:
            raise ValueError("equity must equal cash plus positions_value")
        if self.benchmark_value is not None:
            _positive_decimal(self.benchmark_value, "benchmark_value")


@dataclass(frozen=True)
class ETFRotationSkippedOrder:
    signal_date: str
    trade_date: str
    asset_id: str
    reason: str

    def __post_init__(self) -> None:
        _strict_iso_date(self.signal_date, "signal_date")
        _strict_iso_date(self.trade_date, "trade_date")
        _non_blank(self.asset_id, "asset_id")
        _non_blank(self.reason, "reason")


@dataclass(frozen=True)
class ETFRotationBacktestRun:
    spec: ETFRotationBacktestSpec
    trades: tuple[ETFRotationTrade, ...]
    equity_curve: tuple[ETFRotationEquityPoint, ...]
    skipped_orders: tuple[ETFRotationSkippedOrder, ...]
    metrics: dict[str, MetricValue]
    status: str = "completed"

    def __post_init__(self) -> None:
        if not isinstance(self.spec, ETFRotationBacktestSpec):
            raise TypeError("spec must be ETFRotationBacktestSpec")
        object.__setattr__(self, "trades", tuple(self.trades))
        object.__setattr__(self, "equity_curve", tuple(self.equity_curve))
        object.__setattr__(
            self,
            "skipped_orders",
            tuple(self.skipped_orders),
        )
        object.__setattr__(self, "metrics", dict(self.metrics))
        if self.status != "completed":
            raise ValueError("status must be completed")


class ETFRotationBacktestEngine:
    """Run the v1 ETF rotation signal without position optimization."""

    def run(
        self,
        spec: ETFRotationBacktestSpec,
        bars: Sequence[ETFRotationBacktestBar],
        signals: Sequence[
            ETFRotationSignal | PortfolioTargetSignal
        ],
        benchmark_bars: Sequence[ETFRotationBacktestBar] | None = None,
    ) -> ETFRotationBacktestRun:
        if not isinstance(spec, ETFRotationBacktestSpec):
            raise TypeError("spec must be ETFRotationBacktestSpec")

        all_bars = tuple(bars)
        _validate_bars(all_bars, "bars")
        ranged_bars = tuple(
            bar
            for bar in all_bars
            if spec.start_date <= bar.trade_date <= spec.end_date
        )
        if not ranged_bars:
            raise ValueError("backtest has no bars in the requested range")

        bars_by_date: dict[
            str, dict[str, ETFRotationBacktestBar]
        ] = {}
        for bar in ranged_bars:
            bars_by_date.setdefault(bar.trade_date, {})[bar.asset_id] = bar
        trade_dates = tuple(sorted(bars_by_date))

        signals_by_date = self._validate_signals(signals, trade_dates)
        benchmark_by_date = _prepare_benchmark(
            spec,
            benchmark_bars,
        )
        benchmark_base = next(
            (
                benchmark_by_date[trade_date].close
                for trade_date in trade_dates
                if trade_date in benchmark_by_date
            ),
            None,
        )

        cash = spec.initial_cash
        positions: dict[str, Decimal] = {}
        latest_closes: dict[str, Decimal] = {}
        trades: list[ETFRotationTrade] = []
        skipped: list[ETFRotationSkippedOrder] = []
        equity_curve: list[ETFRotationEquityPoint] = []
        stale_valuation_count = 0

        for trade_date in trade_dates:
            day_bars = bars_by_date[trade_date]
            signal = signals_by_date.get(trade_date)
            if signal is not None:
                cash = self._rebalance(
                    spec=spec,
                    signal=signal,
                    trade_date=trade_date,
                    day_bars=day_bars,
                    latest_closes=latest_closes,
                    positions=positions,
                    cash=cash,
                    trades=trades,
                    skipped=skipped,
                )

            positions_value = Decimal("0")
            for asset_id, quantity in positions.items():
                if quantity == 0:
                    continue
                bar = day_bars.get(asset_id)
                if bar is not None:
                    close = bar.close
                else:
                    close = latest_closes.get(asset_id)
                    if close is None:
                        raise ValueError(
                            "held asset has no known close for valuation: "
                            f"{asset_id} on {trade_date}"
                        )
                    stale_valuation_count += 1
                positions_value += quantity * close

            benchmark_bar = benchmark_by_date.get(trade_date)
            benchmark_value = (
                spec.initial_cash
                * benchmark_bar.close
                / benchmark_base
                if benchmark_bar is not None
                and benchmark_base is not None
                else None
            )
            equity_curve.append(
                ETFRotationEquityPoint(
                    trade_date=trade_date,
                    cash=cash,
                    positions_value=positions_value,
                    equity=cash + positions_value,
                    benchmark_value=benchmark_value,
                )
            )
            latest_closes.update(
                {
                    asset_id: bar.close
                    for asset_id, bar in day_bars.items()
                }
            )

        metrics = _calculate_metrics(
            spec=spec,
            trades=trades,
            equity_curve=equity_curve,
            rebalance_count=len(signals_by_date),
            stale_valuation_count=stale_valuation_count,
        )
        return ETFRotationBacktestRun(
            spec=spec,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            skipped_orders=tuple(skipped),
            metrics=metrics,
        )

    @staticmethod
    def _validate_signals(
        signals: Sequence[
            ETFRotationSignal | PortfolioTargetSignal
        ],
        trade_dates: tuple[str, ...],
    ) -> dict[
        str,
        ETFRotationSignal | PortfolioTargetSignal,
    ]:
        available_dates = set(trade_dates)
        by_effective_date: dict[
            str,
            ETFRotationSignal | PortfolioTargetSignal,
        ] = {}
        for signal in signals:
            if not isinstance(
                signal,
                (ETFRotationSignal, PortfolioTargetSignal),
            ):
                raise TypeError(
                    "signals must contain supported target signals"
                )
            if isinstance(signal, ETFRotationSignal):
                if signal.strategy_id != ETF_DUAL_MOMENTUM_STRATEGY_ID:
                    raise ValueError(
                        "signal strategy_id must use ETF dual momentum"
                    )
                if signal.version != ETF_DUAL_MOMENTUM_VERSION:
                    raise ValueError("signal version must be v1")
            if signal.effective_date <= signal.as_of_date:
                raise ValueError(
                    "signal effective_date must be later than as_of_date"
                )
            if signal.effective_date not in available_dates:
                raise ValueError(
                    "signal effective_date has no backtest trading date: "
                    f"{signal.effective_date}"
                )
            if signal.effective_date in by_effective_date:
                raise ValueError(
                    "multiple signals have the same effective_date: "
                    f"{signal.effective_date}"
                )
            by_effective_date[signal.effective_date] = signal
        return by_effective_date

    @staticmethod
    def _rebalance(
        *,
        spec: ETFRotationBacktestSpec,
        signal: ETFRotationSignal | PortfolioTargetSignal,
        trade_date: str,
        day_bars: dict[str, ETFRotationBacktestBar],
        latest_closes: dict[str, Decimal],
        positions: dict[str, Decimal],
        cash: Decimal,
        trades: list[ETFRotationTrade],
        skipped: list[ETFRotationSkippedOrder],
    ) -> Decimal:
        open_values: dict[str, Decimal] = {}
        for asset_id, quantity in positions.items():
            bar = day_bars.get(asset_id)
            valuation_price = (
                bar.open if bar is not None else latest_closes.get(asset_id)
            )
            if valuation_price is None:
                raise ValueError(
                    "held asset has no known price before rebalance: "
                    f"{asset_id} on {trade_date}"
                )
            open_values[asset_id] = quantity * valuation_price

        portfolio_value = cash + sum(
            open_values.values(),
            Decimal("0"),
        )
        target_values = {
            asset_id: portfolio_value * weight
            for asset_id, weight in signal.target_weights.items()
        }
        assets = sorted(set(positions) | set(target_values))
        sell_plans: list[tuple[str, Decimal]] = []
        buy_plans: list[tuple[str, Decimal]] = []

        for asset_id in assets:
            current_quantity = positions.get(asset_id, Decimal("0"))
            bar = day_bars.get(asset_id)
            current_value = open_values.get(asset_id, Decimal("0"))
            target_value = target_values.get(asset_id, Decimal("0"))
            difference = target_value - current_value
            if difference == 0:
                continue
            if bar is None:
                skipped.append(
                    ETFRotationSkippedOrder(
                        signal_date=signal.as_of_date,
                        trade_date=trade_date,
                        asset_id=asset_id,
                        reason="missing_bar",
                    )
                )
                continue
            if not bar.tradable:
                skipped.append(
                    ETFRotationSkippedOrder(
                        signal_date=signal.as_of_date,
                        trade_date=trade_date,
                        asset_id=asset_id,
                        reason="not_tradable",
                    )
                )
                continue
            quantity_difference = difference / bar.open
            if quantity_difference < 0 and current_quantity > 0:
                sell_plans.append(
                    (
                        asset_id,
                        min(current_quantity, -quantity_difference),
                    )
                )
            elif quantity_difference > 0:
                buy_plans.append((asset_id, quantity_difference))

        for asset_id, quantity in sell_plans:
            bar = day_bars[asset_id]
            execution_price = bar.open * (
                Decimal("1") - spec.slippage_rate
            )
            notional = quantity * execution_price
            fee = notional * spec.fee_rate
            cash += notional - fee
            positions[asset_id] -= quantity
            if positions[asset_id] == 0:
                del positions[asset_id]
            trades.append(
                ETFRotationTrade(
                    signal_date=signal.as_of_date,
                    trade_date=trade_date,
                    asset_id=asset_id,
                    side="sell",
                    quantity=quantity,
                    market_open=bar.open,
                    execution_price=execution_price,
                    notional=notional,
                    fee=fee,
                    slippage_cost=quantity
                    * (bar.open - execution_price),
                )
            )

        planned_cost = sum(
            (
                quantity
                * day_bars[asset_id].open
                * (Decimal("1") + spec.slippage_rate)
                * (Decimal("1") + spec.fee_rate)
                for asset_id, quantity in buy_plans
            ),
            Decimal("0"),
        )
        scale = (
            min(Decimal("1"), cash / planned_cost)
            if planned_cost > 0
            else Decimal("0")
        )
        for asset_id, planned_quantity in buy_plans:
            quantity = planned_quantity * scale
            if quantity == 0 or cash == 0:
                continue
            bar = day_bars[asset_id]
            execution_price = bar.open * (
                Decimal("1") + spec.slippage_rate
            )
            unit_cost = execution_price * (
                Decimal("1") + spec.fee_rate
            )
            quantity = min(quantity, cash / unit_cost)
            notional = quantity * execution_price
            fee = notional * spec.fee_rate
            while notional + fee > cash:
                quantity = quantity.next_minus()
                notional = quantity * execution_price
                fee = notional * spec.fee_rate
            cash -= notional + fee
            positions[asset_id] = (
                positions.get(asset_id, Decimal("0")) + quantity
            )
            trades.append(
                ETFRotationTrade(
                    signal_date=signal.as_of_date,
                    trade_date=trade_date,
                    asset_id=asset_id,
                    side="buy",
                    quantity=quantity,
                    market_open=bar.open,
                    execution_price=execution_price,
                    notional=notional,
                    fee=fee,
                    slippage_cost=quantity
                    * (execution_price - bar.open),
                )
            )
        if cash < 0:
            raise ArithmeticError("buy scaling produced negative cash")
        return cash


def _prepare_benchmark(
    spec: ETFRotationBacktestSpec,
    benchmark_bars: Sequence[ETFRotationBacktestBar] | None,
) -> dict[str, ETFRotationBacktestBar]:
    values = tuple(benchmark_bars or ())
    _validate_bars(values, "benchmark_bars")
    if values and spec.benchmark_asset_id is None:
        raise ValueError(
            "benchmark_asset_id is required when benchmark_bars are supplied"
        )
    for bar in values:
        if bar.asset_id != spec.benchmark_asset_id:
            raise ValueError("benchmark bar asset_id does not match spec")
    return {
        bar.trade_date: bar
        for bar in values
        if spec.start_date <= bar.trade_date <= spec.end_date
    }


def _validate_bars(
    bars: tuple[ETFRotationBacktestBar, ...],
    name: str,
) -> None:
    seen: set[tuple[str, str]] = set()
    for bar in bars:
        if not isinstance(bar, ETFRotationBacktestBar):
            raise TypeError(
                f"{name} must contain ETFRotationBacktestBar values"
            )
        key = (bar.asset_id, bar.trade_date)
        if key in seen:
            raise ValueError(
                f"{name} contain duplicate asset/date bar: "
                f"{bar.asset_id} {bar.trade_date}"
            )
        seen.add(key)


def _calculate_metrics(
    *,
    spec: ETFRotationBacktestSpec,
    trades: list[ETFRotationTrade],
    equity_curve: list[ETFRotationEquityPoint],
    rebalance_count: int,
    stale_valuation_count: int,
) -> dict[str, MetricValue]:
    final_equity = equity_curve[-1].equity
    total_return = final_equity / spec.initial_cash - Decimal("1")
    peak = equity_curve[0].equity
    max_drawdown = Decimal("0")
    for point in equity_curve:
        peak = max(peak, point.equity)
        drawdown = point.equity / peak - Decimal("1")
        max_drawdown = min(max_drawdown, drawdown)

    benchmark_values = [
        point.benchmark_value
        for point in equity_curve
        if point.benchmark_value is not None
    ]
    benchmark_total_return = (
        benchmark_values[-1] / benchmark_values[0] - Decimal("1")
        if benchmark_values
        else None
    )
    turnover_notional = sum(
        (trade.notional for trade in trades),
        Decimal("0"),
    )
    return {
        "version": ETF_ROTATION_BACKTEST_VERSION,
        "initial_cash": spec.initial_cash,
        "final_equity": final_equity,
        "total_return": total_return,
        "benchmark_total_return": benchmark_total_return,
        "max_drawdown": max_drawdown,
        "turnover_notional": turnover_notional,
        "turnover_ratio": turnover_notional / spec.initial_cash,
        "trade_count": len(trades),
        "rebalance_count": rebalance_count,
        "stale_valuation_count": stale_valuation_count,
    }


def _non_blank(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


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


def _positive_decimal(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite Decimal")


def _non_negative_decimal(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
    ):
        raise ValueError(
            f"{name} must be a non-negative finite Decimal"
        )


def _rate(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or not Decimal("0") <= value < Decimal("1")
    ):
        raise ValueError(f"{name} must be a finite Decimal in [0, 1)")
