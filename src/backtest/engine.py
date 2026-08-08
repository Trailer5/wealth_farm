"""Daily long-only strategy-result backtest engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.factor_engineering import (
    RiskMetricCalculator,
    RiskMetricParameters,
)
from src.strategy import StrategyResult, StrategyRunStatus

from .models import (
    BacktestBar,
    BacktestRun,
    BacktestSpec,
    BacktestTrade,
    EquityPoint,
    SkippedOrder,
    TradeSide,
)


@dataclass
class _PendingOrder:
    result: StrategyResult
    target_exposure: Decimal


class BacktestEngine:
    def __init__(
        self,
        risk_calculator: RiskMetricCalculator | None = None,
    ) -> None:
        self.risk_calculator = (
            risk_calculator or RiskMetricCalculator()
        )

    def run(
        self,
        spec: BacktestSpec,
        *,
        bars: list[BacktestBar],
        strategy_results: list[StrategyResult],
        benchmark_bars: list[BacktestBar] | None = None,
    ) -> BacktestRun:
        ordered_bars = sorted(
            [
                bar
                for bar in bars
                if spec.start_date
                <= bar.trade_date
                <= spec.end_date
            ],
            key=lambda bar: bar.trade_date,
        )
        if not ordered_bars:
            raise ValueError("Backtest has no market bars")
        if len({bar.trade_date for bar in ordered_bars}) != len(
            ordered_bars
        ):
            raise ValueError(
                "Backtest bars contain duplicate trade dates"
            )
        dates = [bar.trade_date for bar in ordered_bars]
        date_to_index = {
            trade_date: index
            for index, trade_date in enumerate(dates)
        }
        due_results: dict[int, StrategyResult] = {}
        skipped: list[SkippedOrder] = []
        seen_signal_dates: set[str] = set()
        for result in strategy_results:
            self._validate_result(spec, result)
            if result.as_of_date in seen_signal_dates:
                raise ValueError(
                    f"Duplicate strategy result date: "
                    f"{result.as_of_date}"
                )
            seen_signal_dates.add(result.as_of_date)
            if result.as_of_date not in date_to_index:
                raise ValueError(
                    f"Strategy result date has no market bar: "
                    f"{result.as_of_date}"
                )
            target = _target_exposure(result.status)
            if target is None:
                skipped.append(
                    SkippedOrder(
                        signal_date=result.as_of_date,
                        attempted_date=None,
                        reason="insufficient_strategy_data",
                    )
                )
                continue
            due_index = (
                date_to_index[result.as_of_date]
                + spec.signal_delay_sessions
            )
            if due_index >= len(ordered_bars):
                skipped.append(
                    SkippedOrder(
                        signal_date=result.as_of_date,
                        attempted_date=None,
                        reason="no_future_session_for_execution",
                    )
                )
                continue
            due_results[due_index] = result

        benchmark_by_date = {
            bar.trade_date: bar.close
            for bar in benchmark_bars or []
            if spec.start_date
            <= bar.trade_date
            <= spec.end_date
        }
        benchmark_base = next(
            (
                benchmark_by_date[trade_date]
                for trade_date in dates
                if trade_date in benchmark_by_date
            ),
            None,
        )
        cash = spec.initial_cash
        quantity = Decimal("0")
        pending: _PendingOrder | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[EquityPoint] = []

        for index, bar in enumerate(ordered_bars):
            due_result = due_results.get(index)
            if due_result is not None:
                if pending is not None:
                    skipped.append(
                        SkippedOrder(
                            signal_date=(
                                pending.result.as_of_date
                            ),
                            attempted_date=bar.trade_date,
                            reason="superseded_by_newer_signal",
                        )
                    )
                pending = _PendingOrder(
                    result=due_result,
                    target_exposure=_target_exposure(
                        due_result.status
                    ),
                )
            if pending is not None:
                if bar.trade_date > pending.result.valid_until:
                    skipped.append(
                        SkippedOrder(
                            signal_date=pending.result.as_of_date,
                            attempted_date=bar.trade_date,
                            reason="strategy_result_expired",
                        )
                    )
                    pending = None
                else:
                    side = _required_side(
                        pending.target_exposure,
                        quantity,
                    )
                    if side is None:
                        pending = None
                    elif _can_trade(bar, side):
                        trade, cash, quantity = _execute_trade(
                            spec,
                            pending.result,
                            bar,
                            side,
                            cash,
                            quantity,
                        )
                        trades.append(trade)
                        pending = None
            benchmark_value = (
                spec.initial_cash
                * benchmark_by_date[bar.trade_date]
                / benchmark_base
                if benchmark_base is not None
                and bar.trade_date in benchmark_by_date
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
                    signal_date=pending.result.as_of_date,
                    attempted_date=ordered_bars[-1].trade_date,
                    reason="no_tradable_session_before_end",
                )
            )
        metrics = self._metrics(
            spec,
            equity_curve,
            trades,
        )
        return BacktestRun(
            spec=spec,
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
            skipped_orders=tuple(skipped),
            metrics=metrics,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def _validate_result(
        self,
        spec: BacktestSpec,
        result: StrategyResult,
    ) -> None:
        if result.asset_id != spec.asset_id:
            raise ValueError("Strategy result asset mismatch")
        if result.strategy_id != spec.strategy_id:
            raise ValueError("Strategy result strategy_id mismatch")
        if result.strategy_version != spec.strategy_version:
            raise ValueError(
                "Strategy result version mismatch"
            )
        if not (
            spec.start_date
            <= result.as_of_date
            <= spec.end_date
        ):
            raise ValueError(
                "Strategy result is outside backtest range"
            )
        if (
            result.status is not StrategyRunStatus.INSUFFICIENT_DATA
            and not result.input_effective_dates
        ):
            raise ValueError(
                "Tradable strategy result requires input provenance"
            )
        future_inputs = {
            name: effective_date
            for name, effective_date
            in result.input_effective_dates.items()
            if effective_date > result.as_of_date
        }
        if future_inputs:
            raise ValueError(
                "Strategy result contains future input dates: "
                f"{future_inputs}"
            )

    def _metrics(
        self,
        spec: BacktestSpec,
        equity_curve: list[EquityPoint],
        trades: list[BacktestTrade],
    ) -> dict[str, Decimal | int | str | None]:
        final_equity = equity_curve[-1].equity
        total_return = (
            final_equity / spec.initial_cash - Decimal("1")
        )
        risk = self.risk_calculator.calculate(
            asset_id=spec.asset_id,
            records=[
                {
                    "trade_date": point.trade_date,
                    "close": float(point.equity),
                }
                for point in equity_curve
            ],
            source_dataset="backtest_equity_curve",
            parameters=RiskMetricParameters(
                annualization_days=spec.annualization_days,
                risk_free_rate=spec.risk_free_rate,
                rolling_windows=(),
                price_adjustment=spec.price_adjustment,
            ),
        )
        risk_metrics = {
            value.factor_name: Decimal(str(value.value))
            for value in risk.metrics
        }
        benchmark_points = [
            point.benchmark_value
            for point in equity_curve
            if point.benchmark_value is not None
        ]
        benchmark_return = (
            benchmark_points[-1] / benchmark_points[0]
            - Decimal("1")
            if len(benchmark_points) >= 2
            else None
        )
        total_notional = sum(
            (trade.notional for trade in trades),
            Decimal("0"),
        )
        total_fees = sum(
            (trade.fee for trade in trades),
            Decimal("0"),
        )
        slippage_cost = sum(
            (trade.slippage_cost for trade in trades),
            Decimal("0"),
        )
        return {
            "sample": spec.sample.value,
            "initial_cash": spec.initial_cash,
            "final_equity": final_equity,
            "total_return": total_return,
            "annualized_return": risk_metrics.get(
                "annualized_return"
            ),
            "annualized_volatility": risk_metrics.get(
                "annualized_volatility"
            ),
            "max_drawdown": risk_metrics.get("max_drawdown"),
            "sharpe_ratio": risk_metrics.get("sharpe_ratio"),
            "benchmark_total_return": benchmark_return,
            "excess_total_return": (
                total_return - benchmark_return
                if benchmark_return is not None
                else None
            ),
            "turnover": total_notional / spec.initial_cash,
            "trade_count": len(trades),
            "total_fees": total_fees,
            "slippage_cost": slippage_cost,
        }


def bars_from_records(
    records: list[dict[str, Any]],
) -> list[BacktestBar]:
    bars = []
    for record in records:
        bars.append(
            BacktestBar(
                trade_date=str(record["trade_date"]),
                open=Decimal(str(record["open"])),
                close=Decimal(str(record["close"])),
                tradable=bool(record.get("tradable", True)),
                limit_up=bool(record.get("limit_up", False)),
                limit_down=bool(record.get("limit_down", False)),
            )
        )
    return bars


def _target_exposure(
    status: StrategyRunStatus,
) -> Decimal | None:
    if status is StrategyRunStatus.PASS:
        return Decimal("1")
    if status in {
        StrategyRunStatus.FAIL,
        StrategyRunStatus.VETOED,
    }:
        return Decimal("0")
    return None


def _required_side(
    target_exposure: Decimal,
    quantity: Decimal,
) -> TradeSide | None:
    if target_exposure == 1 and quantity == 0:
        return TradeSide.BUY
    if target_exposure == 0 and quantity > 0:
        return TradeSide.SELL
    return None


def _can_trade(bar: BacktestBar, side: TradeSide) -> bool:
    if not bar.tradable:
        return False
    if side is TradeSide.BUY and bar.limit_up:
        return False
    if side is TradeSide.SELL and bar.limit_down:
        return False
    return True


def _execute_trade(
    spec: BacktestSpec,
    result: StrategyResult,
    bar: BacktestBar,
    side: TradeSide,
    cash: Decimal,
    quantity: Decimal,
) -> tuple[BacktestTrade, Decimal, Decimal]:
    if side is TradeSide.BUY:
        execution_price = bar.open * (
            Decimal("1") + spec.slippage_rate
        )
        bought_quantity = cash / (
            execution_price * (
                Decimal("1") + spec.fee_rate
            )
        )
        notional = bought_quantity * execution_price
        fee = notional * spec.fee_rate
        new_cash = cash - notional - fee
        slippage_cost = bought_quantity * (
            execution_price - bar.open
        )
        trade = BacktestTrade(
            signal_date=result.as_of_date,
            trade_date=bar.trade_date,
            side=side,
            quantity=bought_quantity,
            market_open=bar.open,
            execution_price=execution_price,
            notional=notional,
            fee=fee,
            slippage_cost=slippage_cost,
            reason=result.signal,
        )
        return trade, new_cash, bought_quantity
    execution_price = bar.open * (
        Decimal("1") - spec.slippage_rate
    )
    notional = quantity * execution_price
    fee = notional * spec.fee_rate
    new_cash = cash + notional - fee
    slippage_cost = quantity * (
        bar.open - execution_price
    )
    trade = BacktestTrade(
        signal_date=result.as_of_date,
        trade_date=bar.trade_date,
        side=side,
        quantity=quantity,
        market_open=bar.open,
        execution_price=execution_price,
        notional=notional,
        fee=fee,
        slippage_cost=slippage_cost,
        reason=result.signal,
    )
    return trade, new_cash, Decimal("0")
