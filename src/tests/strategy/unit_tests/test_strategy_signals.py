import unittest
from datetime import date, timedelta

from src.strategy.signals import SIGNAL_GENERATORS, GeneratorContext, signal_generator_for


def _calendar(count):
    start = date(2020, 1, 1)
    return [(start + timedelta(days=i)).isoformat() for i in range(count)]


class SignalGeneratorTest(unittest.TestCase):
    def test_moving_average_timing_signals_only_on_state_changes(self):
        days = _calendar(8)
        closes = [10, 10, 10, 12, 13, 9, 8, 12]
        signals = SIGNAL_GENERATORS["ma_timing"].build(
            {"a": list(zip(days, closes))}, days, {"window": "3"}, GeneratorContext()
        )
        self.assertEqual(
            [(day, weights) for day, weights, _ in signals],
            [(days[2], {}), (days[3], {"a": 1.0}), (days[5], {}), (days[7], {"a": 1.0})],
        )

    def test_etf_dual_momentum_uses_month_ends_and_waits_for_history(self):
        days = _calendar(120)
        rising = [(day, 100 + i) for i, day in enumerate(days)]
        falling = [(day, 300 - i) for i, day in enumerate(days)]
        signals = SIGNAL_GENERATORS["etf_dual_momentum_trend"].build(
            {"up": rising, "down": falling, "bond": [(day, 100.0) for day in days]},
            days,
            {"short_window": "10", "long_window": "40", "trend_window": "20", "volatility_window": "10", "top_n": "1"},
            GeneratorContext(roles={"defensive": "bond"}),
        )
        self.assertTrue(signals)
        self.assertGreater(signals[0][0], days[40])
        self.assertTrue(all(weights == {"up": 1.0} for _, weights, _ in signals))
        self.assertTrue(all(days.index(day) + 1 < len(days) for day, _, _ in signals))

    def test_lookup_by_strategy_or_template(self):
        self.assertIs(signal_generator_for("x", "ma_timing"), SIGNAL_GENERATORS["ma_timing"])
        self.assertIsNone(signal_generator_for("quality_value", None))

    def test_valuation_timing_uses_visible_points_and_hysteresis(self):
        days = [f"{year}-{month:02d}-15" for year in range(2010, 2016) for month in range(1, 13)]
        # Monthly PE rising for three years, then expensive (cash), a slump (hold), and a rally (cash).
        values = [float(v) for v in range(1, 37)] + [40.0, 2.0] + [50.0] * 34
        points = [(day, day, value) for day, value in zip(days, values)]
        params = {"lookback_years": "10", "min_history_years": "3", "buy_below": "0.3", "sell_above": "0.7"}
        signals = SIGNAL_GENERATORS["valuation_percentile_timing"].build(
            {"a": [(day, 1.0) for day in days]}, days, params, GeneratorContext(valuation=points)
        )
        held = [(day, bool(weights)) for day, weights, _ in signals]
        self.assertEqual(held[0][0], "2013-01-15")
        self.assertEqual([state for _, state in held], [False, True, False])
        self.assertLess(held[1][0], held[2][0])
        self.assertTrue(all(day >= "2013-01-15" for day, _ in held))

    def test_fixed_weight_rebalance_starts_before_start_and_skips_unlisted_assets(self):
        days = _calendar(200)
        series = {"x": [(day, 1.0) for day in days], "y": [(day, 1.0) for day in days[100:]]}
        context = GeneratorContext(symbols={"x": "510300", "y": "511010"}, start=days[10])
        signals = SIGNAL_GENERATORS["fixed_weight_rebalance"].build(
            series, days, {"weights": "510300=0.6,511010=0.4", "frequency": "month"}, context
        )
        self.assertEqual(signals[0][0], days[9])
        self.assertEqual(signals[0][1], {"x": 1.0})
        self.assertEqual(signals[-1][1], {"x": 0.6, "y": 0.4})

    def test_staged_entry_reaches_full_weight_after_n_periods_then_holds(self):
        days = _calendar(400)
        signals = SIGNAL_GENERATORS["staged_entry"].build(
            {"x": [(day, 1.0) for day in days]}, days, {"periods": "4", "frequency": "month", "weights": ""},
            GeneratorContext(symbols={"x": "510300"}, start=days[5]),
        )
        self.assertEqual([weights["x"] for _, weights, _ in signals], [0.25, 0.5, 0.75, 1.0])
        self.assertEqual(signals[0][0], days[4])


if __name__ == "__main__":
    unittest.main()
