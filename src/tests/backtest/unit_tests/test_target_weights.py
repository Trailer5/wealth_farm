import unittest

from src.backtest.target_weights import (
    CostModel,
    PriceBar,
    TargetSignal,
    buy_and_hold_signals,
    cost_model_for,
    month_end_dates,
    run_target_weights,
)

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
FREE = CostModel(commission_rate=0.0, min_commission=0.0, slippage_rate=0.0)


def _bars(asset_id, opens, closes, **extra):
    return [PriceBar(asset_id, day, o, c, **extra) for day, o, c in zip(DAYS, opens, closes)]


class TargetWeightEngineTest(unittest.TestCase):
    def test_signal_executes_at_next_open_and_tracks_closes(self):
        bars = _bars("a", [10, 10, 11, 12], [10, 11, 12, 12])
        result = run_target_weights(
            bars, [TargetSignal("2026-01-05", {"a": 1.0})], start_date=DAYS[0], end_date=DAYS[-1],
            initial_cash=1000, costs={"a": FREE},
        )
        # Bought at 2026-01-06 open (10): 100 units, then closes 11, 12, 12.
        self.assertEqual([round(v, 6) for v in result.equity], [1000, 1100, 1200, 1200])
        self.assertEqual(len(result.trades), 1)

    def test_minimum_commission_and_stamp_duty_by_date(self):
        cost = cost_model_for("stock")
        self.assertEqual(cost.stamp_rate("sell", "2023-08-27"), 0.001)
        self.assertEqual(cost.stamp_rate("sell", "2023-08-28"), 0.0005)
        self.assertEqual(cost.stamp_rate("buy", "2007-06-01"), 0.003)
        self.assertEqual(cost.stamp_rate("buy", "2010-01-01"), 0.0)
        bars = _bars("a", [10, 10, 10, 10], [10, 10, 10, 10])
        result = run_target_weights(
            bars,
            [TargetSignal("2026-01-05", {"a": 1.0}), TargetSignal("2026-01-06", {})],
            start_date=DAYS[0], end_date=DAYS[-1], initial_cash=10_000, costs={"a": cost},
        )
        buy, sell = result.trades
        self.assertEqual(buy.commission, 5.0)
        self.assertAlmostEqual(sell.stamp_duty, sell.notional * 0.0005, places=3)
        self.assertLess(result.equity[-1], 10_000)

    def test_limit_up_open_cannot_be_bought(self):
        bars = _bars("a", [10, 11, 11, 11], [10, 11, 11, 11])
        bars[1] = PriceBar("a", DAYS[1], 11, 11, can_buy=False)
        result = run_target_weights(
            bars, [TargetSignal("2026-01-05", {"a": 1.0})], start_date=DAYS[0], end_date=DAYS[-1],
            initial_cash=1000, costs={"a": FREE},
        )
        self.assertEqual(result.trades, ())
        self.assertEqual(result.skipped[0].reason, "涨跌停不可成交")

    def test_lots_use_the_unadjusted_price(self):
        bars = _bars("a", [5, 5, 5, 5], [5, 5, 5, 5], raw_open=10.0)
        result = run_target_weights(
            bars, [TargetSignal("2026-01-05", {"a": 1.0})], start_date=DAYS[0], end_date=DAYS[-1],
            initial_cash=2_550, costs={"a": CostModel(0.0, 0.0, 0.0, lot_size=100)},
        )
        # 2,550 buys 255 real shares at 10; only two whole lots (2,000 yuan) are traded.
        self.assertAlmostEqual(result.trades[0].notional, 2_000, places=2)
        self.assertAlmostEqual(result.equity[-1], 2_550, places=6)

    def test_idle_cash_earns_the_short_rate(self):
        bars = _bars("a", [10] * 4, [10] * 4)
        result = run_target_weights(
            bars, [], start_date=DAYS[0], end_date=DAYS[-1], initial_cash=1000,
            costs={"a": FREE}, cash_rates=[("2025-01-01", 0.0365)],
        )
        self.assertAlmostEqual(result.equity[-1], 1000 * 1.0001 ** 3, places=6)

    def test_capacity_scales_with_participation(self):
        bars = _bars("a", [10] * 4, [10] * 4, amount=1_000_000.0)
        result = run_target_weights(
            bars, [TargetSignal("2026-01-05", {"a": 1.0})], start_date=DAYS[0], end_date=DAYS[-1],
            initial_cash=10_000, costs={"a": FREE},
        )
        capacity = result.capacity()
        self.assertAlmostEqual(capacity["p95_participation"], 0.01)
        self.assertEqual(capacity["capacity"], 50_000)

    def test_helpers(self):
        self.assertEqual(month_end_dates(["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]), ["2026-01-30", "2026-02-27", "2026-03-02"])
        (signal,) = buy_and_hold_signals(["a", "b"], "2026-01-04")
        self.assertEqual(signal.weights, {"a": 0.5, "b": 0.5})
        with self.assertRaises(ValueError):
            TargetSignal("2026-01-05", {"a": 0.7, "b": 0.6})


if __name__ == "__main__":
    unittest.main()
