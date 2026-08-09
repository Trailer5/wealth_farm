import unittest
from dataclasses import replace
from decimal import Decimal

from src.backtest.etf_rotation import (
    ETF_ROTATION_BACKTEST_VERSION,
    ETFRotationBacktestBar,
    ETFRotationBacktestEngine,
    ETFRotationBacktestSpec,
    PortfolioTargetSignal,
)
from src.strategy.etf_rotation import (
    ETF_DUAL_MOMENTUM_LIMITATIONS,
    ETFRotationSignal,
)


class ETFRotationBacktestEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = ETFRotationBacktestEngine()
        self.spec = ETFRotationBacktestSpec(
            run_id="etf-rotation:test",
            start_date="2026-01-01",
            end_date="2026-01-04",
            initial_cash=Decimal("1000"),
            fee_rate=Decimal("0.01"),
            slippage_rate=Decimal("0.01"),
        )

    def test_effective_open_and_switch_sell_before_buy_with_costs(self):
        bars = _bars(
            {
                "2026-01-01": {"A": ("9", "9"), "B": ("19", "19")},
                "2026-01-02": {"A": ("10", "11"), "B": ("20", "20")},
                "2026-01-03": {"A": ("12", "12"), "B": ("20", "21")},
            }
        )
        run = self.engine.run(
            replace(self.spec, end_date="2026-01-03"),
            bars,
            [
                _signal("2026-01-01", "2026-01-02", "risk_on", {"A": "1"}),
                _signal("2026-01-02", "2026-01-03", "risk_on", {"B": "1"}),
            ],
        )

        self.assertEqual(
            [(trade.trade_date, trade.asset_id, trade.side) for trade in run.trades],
            [
                ("2026-01-02", "A", "buy"),
                ("2026-01-03", "A", "sell"),
                ("2026-01-03", "B", "buy"),
            ],
        )
        first_buy, switch_sell, switch_buy = run.trades
        self.assertEqual(first_buy.signal_date, "2026-01-01")
        self.assertEqual(first_buy.execution_price, Decimal("10.10"))
        self.assertEqual(switch_sell.execution_price, Decimal("11.88"))
        self.assertEqual(switch_buy.execution_price, Decimal("20.20"))
        for trade in run.trades:
            self.assertEqual(trade.fee, trade.notional * Decimal("0.01"))
            self.assertEqual(
                trade.slippage_cost,
                trade.quantity * trade.market_open * Decimal("0.01"),
            )
        self.assertTrue(all(point.cash >= 0 for point in run.equity_curve))
        self.assertEqual(run.metrics["trade_count"], 3)
        self.assertEqual(run.metrics["rebalance_count"], 2)

    def test_defensive_then_cash_liquidates_at_effective_opens(self):
        bars = _bars(
            {
                "2026-01-01": {"A": ("10", "10"), "D": ("5", "5")},
                "2026-01-02": {"A": ("10", "10"), "D": ("5", "5")},
                "2026-01-03": {"A": ("10", "10"), "D": ("5", "5")},
                "2026-01-04": {"A": ("10", "10"), "D": ("5", "5")},
            }
        )
        run = self.engine.run(
            replace(
                self.spec,
                fee_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
            ),
            bars,
            [
                _signal("2026-01-01", "2026-01-02", "risk_on", {"A": "1"}),
                _signal("2026-01-02", "2026-01-03", "defensive", {"D": "1"}),
                _signal("2026-01-03", "2026-01-04", "cash", {}),
            ],
        )

        self.assertEqual(
            [(trade.asset_id, trade.side) for trade in run.trades],
            [
                ("A", "buy"),
                ("A", "sell"),
                ("D", "buy"),
                ("D", "sell"),
            ],
        )
        self.assertEqual(run.equity_curve[-1].cash, Decimal("1000"))
        self.assertEqual(run.equity_curve[-1].positions_value, Decimal("0"))

    def test_generic_target_weights_preserve_residual_cash(self):
        bars = _bars(
            {
                "2026-01-01": {"A": ("10", "10"), "D": ("5", "5")},
                "2026-01-02": {"A": ("10", "10"), "D": ("5", "5")},
            }
        )
        run = self.engine.run(
            replace(
                self.spec,
                end_date="2026-01-02",
                fee_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
            ),
            bars,
            [
                PortfolioTargetSignal(
                    as_of_date="2026-01-01",
                    effective_date="2026-01-02",
                    target_weights={
                        "A": Decimal("0.5"),
                        "D": Decimal("0.4"),
                    },
                    reason="risk budget",
                )
            ],
        )

        self.assertEqual(run.equity_curve[-1].cash, Decimal("100.0"))
        self.assertEqual(
            run.equity_curve[-1].positions_value,
            Decimal("900.0"),
        )

    def test_missing_and_untradable_assets_skip_only_their_adjustments(self):
        bars = _bars(
            {
                "2026-01-01": {"A": ("10", "10"), "B": ("20", "20")},
                "2026-01-02": {"A": ("10", "10"), "B": ("20", "20")},
                "2026-01-03": {"B": ("20", "20", False)},
            }
        )
        run = self.engine.run(
            replace(
                self.spec,
                end_date="2026-01-03",
                fee_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
            ),
            bars,
            [
                _signal("2026-01-01", "2026-01-02", "risk_on", {"A": "1"}),
                _signal("2026-01-02", "2026-01-03", "risk_on", {"B": "1"}),
            ],
        )

        self.assertEqual(len(run.trades), 1)
        self.assertEqual(
            {(order.asset_id, order.reason) for order in run.skipped_orders},
            {("A", "missing_bar"), ("B", "not_tradable")},
        )
        self.assertEqual(run.equity_curve[-1].positions_value, Decimal("1000"))
        self.assertEqual(run.metrics["stale_valuation_count"], 1)

    def test_benchmark_normalization_and_equity_max_drawdown(self):
        bars = _bars(
            {
                "2026-01-01": {"A": ("10", "10")},
                "2026-01-02": {"A": ("10", "8")},
                "2026-01-03": {"A": ("8", "12")},
            }
        )
        benchmark = [
            ETFRotationBacktestBar("BM", "2026-01-02", Decimal("100"), Decimal("100")),
            ETFRotationBacktestBar("BM", "2026-01-03", Decimal("110"), Decimal("110")),
        ]
        run = self.engine.run(
            replace(
                self.spec,
                end_date="2026-01-03",
                fee_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
                benchmark_asset_id="BM",
            ),
            bars,
            [_signal("2025-12-31", "2026-01-01", "risk_on", {"A": "1"})],
            benchmark,
        )

        self.assertIsNone(run.equity_curve[0].benchmark_value)
        self.assertEqual(run.equity_curve[1].benchmark_value, Decimal("1000"))
        self.assertEqual(run.equity_curve[2].benchmark_value, Decimal("1100"))
        self.assertEqual(run.metrics["benchmark_total_return"], Decimal("0.1"))
        self.assertEqual(run.metrics["max_drawdown"], Decimal("-0.2"))
        self.assertEqual(run.metrics["total_return"], Decimal("0.2"))

    def test_duplicate_bars_and_effective_date_signals_are_rejected(self):
        bar = ETFRotationBacktestBar(
            "A",
            "2026-01-02",
            Decimal("10"),
            Decimal("10"),
        )
        spec = replace(self.spec, start_date="2026-01-02", end_date="2026-01-02")
        with self.assertRaisesRegex(ValueError, "duplicate asset/date"):
            self.engine.run(spec, [bar, bar], [])

        signals = [
            _signal("2025-12-31", "2026-01-02", "risk_on", {"A": "1"}),
            _signal("2026-01-01", "2026-01-02", "cash", {}),
        ]
        with self.assertRaisesRegex(ValueError, "same effective_date"):
            self.engine.run(spec, [bar], signals)

    def test_models_require_v1_and_decimal_prices(self):
        self.assertEqual(self.spec.version, ETF_ROTATION_BACKTEST_VERSION)
        with self.assertRaisesRegex(ValueError, "version must be"):
            replace(self.spec, version="etf_rotation_backtest_v2")
        with self.assertRaisesRegex(ValueError, "positive finite Decimal"):
            ETFRotationBacktestBar("A", "2026-01-01", 10, Decimal("10"))


def _bars(values):
    bars = []
    for trade_date, assets in values.items():
        for asset_id, prices in assets.items():
            open_price, close_price, *tradable = prices
            bars.append(
                ETFRotationBacktestBar(
                    asset_id=asset_id,
                    trade_date=trade_date,
                    open=Decimal(open_price),
                    close=Decimal(close_price),
                    tradable=tradable[0] if tradable else True,
                )
            )
    return bars


def _signal(as_of, effective, status, target_weights):
    return ETFRotationSignal(
        as_of_date=as_of,
        effective_date=effective,
        status=status,
        target_weights={
            asset_id: Decimal(weight)
            for asset_id, weight in target_weights.items()
        },
        ranked_assets=(),
        metrics=(),
        limitations=ETF_DUAL_MOMENTUM_LIMITATIONS,
    )


if __name__ == "__main__":
    unittest.main()
