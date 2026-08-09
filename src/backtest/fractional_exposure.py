"""Single-asset fractional-exposure backtesting with execution costs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Sequence

from .engine import BacktestEngine
from .models import (
    BACKTEST_VERSION,
    BacktestBar,
    BacktestRun,
    BacktestSpec,
    BacktestTrade,
    EquityPoint,
    SkippedOrder,
    TradeSide,
)


@dataclass(frozen=True)
class FractionalExposureSignal:
    """A target exposure known at one close and effective at a later open."""

    as_of_date: str
    effective_date: str
    target_exposure: Decimal
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
        if (
            not isinstance(self.target_exposure, Decimal)
            or not self.target_exposure.is_finite()
            or not Decimal("0")
            <= self.target_exposure
            <= Decimal("1")
        ):
            raise ValueError(
                "target_exposure must be a finite Decimal in [0, 1]"
            )
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must not be blank")


@dataclass(frozen=True)
class _PendingRebalance:
    signal: FractionalExposureSignal


class FractionalExposureBacktestEngine(BacktestEngine):
    """Backtest continuous long-only exposure in one asset."""

    def run(
        self,
        spec: BacktestSpec,
        bars: Sequence[BacktestBar],
        signals: Sequence[FractionalExposureSignal],
        benchmark_bars: Sequence[BacktestBar] | None = None,
    ) -> BacktestRun:
        if not isinstance(spec, BacktestSpec):
            raise TypeError("spec must be BacktestSpec")
        if spec.version != BACKTEST_VERSION:
            raise ValueError("spec version must use daily_long_only_v1")

        ordered_bars = _prepare_bars(spec, bars, "bars")
        trade_dates = tuple(bar.trade_date for bar in ordered_bars)
        signals_by_effective = _prepare_signals(
            spec,
            signals,
            trade_dates,
        )
        benchmark_by_date = _prepare_benchmark(spec, benchmark_bars)
        benchmark_base = next(
            (
                benchmark_by_date[trade_date].close
                for trade_date in trade_dates
                if trade_date in benchmark_by_date
            ),
            None,
        )

        cash = spec.initial_cash
        quantity = Decimal("0")
        pending: _PendingRebalance | None = None
        trades: list[BacktestTrade] = []
        skipped: list[SkippedOrder] = []
        equity_curve: list[EquityPoint] = []

        for bar in ordered_bars:
            due_signal = signals_by_effective.get(bar.trade_date)
            if due_signal is not None:
                if pending is not None:
                    skipped.append(
                        SkippedOrder(
                            signal_date=pending.signal.as_of_date,
                            attempted_date=bar.trade_date,
                            reason="superseded_by_newer_signal",
                        )
                    )
                pending = _PendingRebalance(signal=due_signal)

            if pending is not None:
                target = pending.signal.target_exposure
                portfolio_value = cash + quantity * bar.open
                target_value = portfolio_value * target
                current_value = quantity * bar.open
                difference = target_value - current_value
                side = _side_for_difference(difference)
                if side is None:
                    pending = None
                elif _can_trade(bar, side):
                    trade, cash, quantity = _execute_rebalance(
                        spec=spec,
                        signal=pending.signal,
                        bar=bar,
                        side=side,
                        difference=difference,
                        cash=cash,
                        quantity=quantity,
                    )
                    if trade is not None:
                        trades.append(trade)
                    pending = None

            benchmark_bar = benchmark_by_date.get(bar.trade_date)
            benchmark_value = (
                spec.initial_cash
                * benchmark_bar.close
                / benchmark_base
                if benchmark_bar is not None
                and benchmark_base is not None
                else None
            )
            equity_curve.append(
                EquityPoint(
                    trade_date=bar.trade_date,
                    cash=cash,
                    quantity=quantity,
                    close=bar.close,
                    equity=cash + quantity * bar.close,
                    benchmark_value=benchmark_value,
                )
            )

        if pending is not None:
            skipped.append(
                SkippedOrder(
                    signal_date=pending.signal.as_of_date,
                    attempted_date=ordered_bars[-1].trade_date,
                    reason="no_tradable_session_before_end",
                )
            )

        metrics = super()._metrics(spec, equity_curve, trades)
        metrics.update(
            {
                "average_exposure": _average_exposure(equity_curve),
                "target_change_count": _target_change_count(
                    signals_by_effective,
                    trade_dates,
                ),
            }
        )
        return BacktestRun(
            spec=spec,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            skipped_orders=tuple(skipped),
            metrics=metrics,
            created_at=datetime.now(timezone.utc).isoformat(),
        )


def _prepare_bars(
    spec: BacktestSpec,
    bars: Sequence[BacktestBar],
    name: str,
) -> tuple[BacktestBar, ...]:
    values = tuple(bars)
    seen_dates: set[str] = set()
    for bar in values:
        if not isinstance(bar, BacktestBar):
            raise TypeError(f"{name} must contain BacktestBar values")
        if bar.trade_date in seen_dates:
            raise ValueError(
                f"{name} contain duplicate trade date: {bar.trade_date}"
            )
        seen_dates.add(bar.trade_date)
    ranged = tuple(
        sorted(
            (
                bar
                for bar in values
                if spec.start_date
                <= bar.trade_date
                <= spec.end_date
            ),
            key=lambda bar: bar.trade_date,
        )
    )
    if not ranged:
        raise ValueError("backtest has no bars in the requested range")
    return ranged


def _prepare_signals(
    spec: BacktestSpec,
    signals: Sequence[FractionalExposureSignal],
    trade_dates: tuple[str, ...],
) -> dict[str, FractionalExposureSignal]:
    date_to_index = {
        trade_date: index
        for index, trade_date in enumerate(trade_dates)
    }
    seen_as_of: set[str] = set()
    by_effective: dict[str, FractionalExposureSignal] = {}
    for signal in signals:
        if not isinstance(signal, FractionalExposureSignal):
            raise TypeError(
                "signals must contain FractionalExposureSignal values"
            )
        if signal.as_of_date in seen_as_of:
            raise ValueError(
                f"duplicate signal as_of_date: {signal.as_of_date}"
            )
        seen_as_of.add(signal.as_of_date)
        if signal.effective_date in by_effective:
            raise ValueError(
                "duplicate signal effective_date: "
                f"{signal.effective_date}"
            )
        if signal.as_of_date not in date_to_index:
            raise ValueError(
                "signal as_of_date is outside the backtest trading dates: "
                f"{signal.as_of_date}"
            )
        as_of_index = date_to_index[signal.as_of_date]
        expected_index = as_of_index + spec.signal_delay_sessions
        if expected_index >= len(trade_dates):
            raise ValueError(
                "signal delay extends beyond the backtest trading dates: "
                f"{signal.as_of_date}"
            )
        expected_effective = trade_dates[expected_index]
        if signal.effective_date != expected_effective:
            raise ValueError(
                "signal effective_date must be the "
                f"{spec.signal_delay_sessions}th trading session after "
                f"as_of_date; expected {expected_effective}"
            )
        by_effective[signal.effective_date] = signal
    return by_effective


def _prepare_benchmark(
    spec: BacktestSpec,
    benchmark_bars: Sequence[BacktestBar] | None,
) -> dict[str, BacktestBar]:
    if benchmark_bars is None:
        return {}
    values = tuple(benchmark_bars)
    seen_dates: set[str] = set()
    for bar in values:
        if not isinstance(bar, BacktestBar):
            raise TypeError(
                "benchmark_bars must contain BacktestBar values"
            )
        if bar.trade_date in seen_dates:
            raise ValueError(
                "benchmark_bars contain duplicate trade date: "
                f"{bar.trade_date}"
            )
        seen_dates.add(bar.trade_date)
    return {
        bar.trade_date: bar
        for bar in values
        if spec.start_date <= bar.trade_date <= spec.end_date
    }


def _side_for_difference(
    difference: Decimal,
) -> TradeSide | None:
    if difference > 0:
        return TradeSide.BUY
    if difference < 0:
        return TradeSide.SELL
    return None


def _can_trade(bar: BacktestBar, side: TradeSide) -> bool:
    if not bar.tradable:
        return False
    if side is TradeSide.BUY:
        return not bar.limit_up
    return not bar.limit_down


def _execute_rebalance(
    *,
    spec: BacktestSpec,
    signal: FractionalExposureSignal,
    bar: BacktestBar,
    side: TradeSide,
    difference: Decimal,
    cash: Decimal,
    quantity: Decimal,
) -> tuple[BacktestTrade | None, Decimal, Decimal]:
    if side is TradeSide.SELL:
        sold_quantity = min(quantity, -difference / bar.open)
        if sold_quantity <= 0:
            return None, cash, quantity
        execution_price = bar.open * (
            Decimal("1") - spec.slippage_rate
        )
        notional = sold_quantity * execution_price
        fee = notional * spec.fee_rate
        new_cash = cash + notional - fee
        new_quantity = quantity - sold_quantity
        trade = BacktestTrade(
            signal_date=signal.as_of_date,
            trade_date=bar.trade_date,
            side=side,
            quantity=sold_quantity,
            market_open=bar.open,
            execution_price=execution_price,
            notional=notional,
            fee=fee,
            slippage_cost=sold_quantity
            * (bar.open - execution_price),
            reason=signal.reason,
        )
        return trade, new_cash, new_quantity

    planned_quantity = difference / bar.open
    execution_price = bar.open * (
        Decimal("1") + spec.slippage_rate
    )
    unit_cost = execution_price * (
        Decimal("1") + spec.fee_rate
    )
    bought_quantity = min(planned_quantity, cash / unit_cost)
    if bought_quantity <= 0:
        return None, cash, quantity
    notional = bought_quantity * execution_price
    fee = notional * spec.fee_rate
    total_cost = notional + fee
    while total_cost > cash:
        bought_quantity = bought_quantity.next_minus()
        notional = bought_quantity * execution_price
        fee = notional * spec.fee_rate
        total_cost = notional + fee
    new_cash = cash - total_cost
    if new_cash < 0:
        raise ArithmeticError("buy scaling produced negative cash")
    trade = BacktestTrade(
        signal_date=signal.as_of_date,
        trade_date=bar.trade_date,
        side=side,
        quantity=bought_quantity,
        market_open=bar.open,
        execution_price=execution_price,
        notional=notional,
        fee=fee,
        slippage_cost=bought_quantity
        * (execution_price - bar.open),
        reason=signal.reason,
    )
    return trade, new_cash, quantity + bought_quantity


def _average_exposure(
    equity_curve: Sequence[EquityPoint],
) -> Decimal:
    exposures = [
        point.quantity * point.close / point.equity
        for point in equity_curve
    ]
    return sum(exposures, Decimal("0")) / Decimal(
        len(exposures)
    )


def _target_change_count(
    signals_by_effective: dict[str, FractionalExposureSignal],
    trade_dates: tuple[str, ...],
) -> int:
    previous_target = Decimal("0")
    changes = 0
    for trade_date in trade_dates:
        signal = signals_by_effective.get(trade_date)
        if signal is None:
            continue
        if signal.target_exposure != previous_target:
            changes += 1
        previous_target = signal.target_exposure
    return changes


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
