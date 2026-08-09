import unittest
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

from src.backtest import (
    BacktestBar,
    BacktestSample,
    BacktestSpec,
    FractionalExposureBacktestEngine,
    FractionalExposureSignal,
    TradeSide,
)


class FractionalExposureBacktestEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = FractionalExposureBacktestEngine()
        self.spec = BacktestSpec(
            run_id="fractional:test",
            strategy_id="fractional_exposure",
            strategy_version="v1",
            asset_id="asset:etf",
            start_date="2026-01-01",
            end_date="2026-01-05",
            initial_cash=Decimal("1000"),
            fee_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            sample=BacktestSample.FULL,
            benchmark_asset_id="asset:benchmark",
            annualization_days=5,
            price_adjustment="none",
        )
        self.bars = _bars(
            ("2026-01-01", "10", "10"),
            ("2026-01-02", "10", "10"),
            ("2026-01-03", "20", "20"),
            ("2026-01-04", "10", "10"),
            ("2026-01-05", "12", "12"),
        )

    def test_half_exposure_executes_at_next_open(self):
        run = self.engine.run(
            self.spec,
            self.bars,
            [_signal("2026-01-01", "2026-01-02", "0.5")],
        )

        self.assertEqual(len(run.trades), 1)
        trade = run.trades[0]
        self.assertEqual(trade.signal_date, "2026-01-01")
        self.assertEqual(trade.trade_date, "2026-01-02")
        self.assertEqual(trade.side, TradeSide.BUY)
        self.assertEqual(trade.quantity, Decimal("50.0"))
        self.assertEqual(run.equity_curve[1].cash, Decimal("500.0"))
        self.assertEqual(
            run.equity_curve[1].quantity,
            Decimal("50.0"),
        )
        self.assertEqual(run.metrics["target_change_count"], 1)
        self.assertGreater(run.metrics["average_exposure"], 0)

    def test_increase_and_decrease_use_directional_trades(self):
        run = self.engine.run(
            self.spec,
            self.bars,
            [
                _signal("2026-01-01", "2026-01-02", "0.5"),
                _signal("2026-01-02", "2026-01-03", "1"),
                _signal("2026-01-03", "2026-01-04", "0"),
                _signal("2026-01-04", "2026-01-05", "1"),
            ],
        )

        self.assertEqual(
            [
                (trade.trade_date, trade.side)
                for trade in run.trades
            ],
            [
                ("2026-01-02", TradeSide.BUY),
                ("2026-01-03", TradeSide.BUY),
                ("2026-01-04", TradeSide.SELL),
                ("2026-01-05", TradeSide.BUY),
            ],
        )
        sell_index = next(
            index
            for index, trade in enumerate(run.trades)
            if trade.side is TradeSide.SELL
        )
        later_buy_index = max(
            index
            for index, trade in enumerate(run.trades)
            if trade.side is TradeSide.BUY
        )
        self.assertLess(sell_index, later_buy_index)
        self.assertEqual(run.metrics["target_change_count"], 4)

    def test_fees_slippage_and_full_exposure_buy_scaling(self):
        costly_spec = replace(
            self.spec,
            fee_rate=Decimal("0.01"),
            slippage_rate=Decimal("0.01"),
        )
        costly = self.engine.run(
            costly_spec,
            self.bars,
            [_signal("2026-01-01", "2026-01-02", "1")],
        )
        free = self.engine.run(
            self.spec,
            self.bars,
            [_signal("2026-01-01", "2026-01-02", "1")],
        )

        trade = costly.trades[0]
        self.assertEqual(trade.execution_price, Decimal("10.10"))
        self.assertEqual(
            trade.fee,
            trade.notional * Decimal("0.01"),
        )
        self.assertEqual(
            trade.slippage_cost,
            trade.quantity * Decimal("0.10"),
        )
        self.assertGreaterEqual(costly.equity_curve[1].cash, 0)
        self.assertLess(
            costly.metrics["final_equity"],
            free.metrics["final_equity"],
        )
        self.assertGreater(costly.metrics["total_fees"], 0)
        self.assertGreater(costly.metrics["slippage_cost"], 0)
        self.assertGreater(costly.metrics["turnover"], 0)

    def test_pending_uses_later_open_and_limit_down_blocks_sell(self):
        delayed_bars = list(self.bars)
        delayed_bars[1] = replace(delayed_bars[1], tradable=False)
        delayed = self.engine.run(
            self.spec,
            delayed_bars,
            [_signal("2026-01-01", "2026-01-02", "0.5")],
        )

        self.assertEqual(delayed.trades[0].trade_date, "2026-01-03")
        self.assertEqual(delayed.trades[0].market_open, Decimal("20"))

        sell_bars = list(self.bars)
        sell_bars[2] = replace(sell_bars[2], limit_down=True)
        sell_bars[3] = replace(sell_bars[3], open=Decimal("11"))
        sold = self.engine.run(
            self.spec,
            sell_bars,
            [
                _signal("2026-01-01", "2026-01-02", "1"),
                _signal("2026-01-02", "2026-01-03", "0"),
            ],
        )
        self.assertEqual(sold.trades[1].trade_date, "2026-01-04")
        self.assertEqual(sold.trades[1].market_open, Decimal("11"))

    def test_new_signal_replaces_blocked_pending_order(self):
        bars = list(self.bars)
        bars[1] = replace(bars[1], limit_up=True)
        run = self.engine.run(
            self.spec,
            bars,
            [
                _signal("2026-01-01", "2026-01-02", "1", "risk-on"),
                _signal("2026-01-02", "2026-01-03", "0", "risk-off"),
            ],
        )

        self.assertEqual(run.trades, ())
        self.assertEqual(len(run.skipped_orders), 1)
        self.assertEqual(
            run.skipped_orders[0].reason,
            "superseded_by_newer_signal",
        )
        self.assertEqual(
            run.skipped_orders[0].attempted_date,
            "2026-01-03",
        )

    def test_zero_and_one_are_valid_frozen_boundaries(self):
        zero = _signal("2026-01-01", "2026-01-02", "0")
        one = _signal("2026-01-02", "2026-01-03", "1")
        run = self.engine.run(self.spec, self.bars, [zero, one])

        self.assertEqual(len(run.trades), 1)
        self.assertEqual(run.trades[0].side, TradeSide.BUY)
        with self.assertRaises(FrozenInstanceError):
            zero.target_exposure = Decimal("0.5")
        for invalid in (Decimal("-0.01"), Decimal("1.01")):
            with self.assertRaisesRegex(ValueError, r"\[0, 1\]"):
                _signal(
                    "2026-01-01",
                    "2026-01-02",
                    str(invalid),
                )

    def test_timing_duplicates_and_out_of_range_are_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "effective_date must be the",
        ):
            self.engine.run(
                self.spec,
                self.bars,
                [_signal("2026-01-01", "2026-01-03", "0.5")],
            )
        with self.assertRaisesRegex(ValueError, "extends beyond"):
            self.engine.run(
                self.spec,
                self.bars,
                [_signal("2026-01-05", "2026-01-06", "0.5")],
            )
        with self.assertRaisesRegex(ValueError, "duplicate signal"):
            self.engine.run(
                self.spec,
                self.bars,
                [
                    _signal("2026-01-01", "2026-01-02", "0.5"),
                    _signal("2026-01-01", "2026-01-02", "1"),
                ],
            )
        with self.assertRaisesRegex(ValueError, "duplicate trade date"):
            self.engine.run(
                self.spec,
                [*self.bars, self.bars[0]],
                [],
            )

    def test_multi_session_delay_uses_declared_effective_date(self):
        spec = replace(self.spec, signal_delay_sessions=2)
        run = self.engine.run(
            spec,
            self.bars,
            [_signal("2026-01-01", "2026-01-03", "0.5")],
        )

        self.assertEqual(run.trades[0].trade_date, "2026-01-03")

    def test_benchmark_and_risk_metrics_are_preserved(self):
        benchmark = _bars(
            ("2026-01-01", "100", "100"),
            ("2026-01-02", "100", "100"),
            ("2026-01-04", "110", "110"),
            ("2026-01-05", "120", "120"),
        )
        run = self.engine.run(
            self.spec,
            self.bars,
            [],
            benchmark,
        )

        self.assertIsNone(run.equity_curve[2].benchmark_value)
        self.assertEqual(
            run.metrics["benchmark_total_return"],
            Decimal("0.2"),
        )
        self.assertEqual(
            run.metrics["excess_total_return"],
            Decimal("-0.2"),
        )
        for metric in (
            "total_return",
            "annualized_return",
            "annualized_volatility",
            "max_drawdown",
            "sharpe_ratio",
            "turnover",
            "total_fees",
            "slippage_cost",
            "average_exposure",
            "target_change_count",
        ):
            self.assertIn(metric, run.metrics)


def _bars(*values):
    return [
        BacktestBar(
            trade_date=trade_date,
            open=Decimal(open_price),
            close=Decimal(close_price),
        )
        for trade_date, open_price, close_price in values
    ]


def _signal(
    as_of,
    effective,
    exposure,
    reason="target allocation",
):
    return FractionalExposureSignal(
        as_of_date=as_of,
        effective_date=effective,
        target_exposure=Decimal(exposure),
        reason=reason,
    )


if __name__ == "__main__":
    unittest.main()
