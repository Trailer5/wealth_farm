import unittest
from datetime import date, timedelta

from src.backtest.market_regimes import (
    MIN_STATE_DAYS,
    VOLATILITY_HISTORY,
    classify,
    state_performance,
)
from src.backtest.regime_evidence import assess_evidence
from src.backtest.validation_evidence import assess_evidence as assess_validation_evidence

DAYS = [(date(2010, 1, 1) + timedelta(days=i)).isoformat() for i in range(1400)]


def _closes():
    """Rising for 600 days with tiny moves, then falling with large daily swings."""

    level, closes = 100.0, []
    for i, day in enumerate(DAYS):
        if i < 1000:
            level *= 1.001 + (0.001 if i % 2 else -0.001)
        else:
            level *= 0.998 + (0.03 if i % 2 else -0.03)
        closes.append((day, level))
    return closes


class MarketRegimeTest(unittest.TestCase):
    def test_methods_pass_the_source_gate(self):
        self.assertTrue(assess_evidence().implementation_ready, assess_evidence())
        self.assertTrue(assess_validation_evidence().implementation_ready, assess_validation_evidence())

    def test_trend_volatility_and_valuation_states_use_visible_data(self):
        valuation = [(f"{year}-{month:02d}-28", f"{year}-{month:02d}-28", float(v))
                     for v, (year, month) in enumerate(((y, m) for y in range(2007, 2014) for m in range(1, 13)))]
        states = classify(_closes(), valuation)
        self.assertIsNone(states[DAYS[100]]["trend"])
        self.assertEqual(states[DAYS[900]]["trend"], "up")
        self.assertEqual(states[DAYS[1300]]["trend"], "down")
        first_vol = next(day for day in DAYS if states[day]["volatility"] is not None)
        self.assertEqual(DAYS.index(first_vol), 60 + VOLATILITY_HISTORY - 1)
        self.assertEqual(states[DAYS[1300]]["volatility"], "high")
        # A steadily rising P/E is always at the top of its own history once visible.
        self.assertEqual(states["2012-03-01"]["valuation"], "expensive")
        self.assertIsNone(states["2010-01-01"]["valuation"])

    def test_state_performance_attributes_next_day_returns_and_flags_thin_states(self):
        dates = DAYS[:400]
        states = {day: {"trend": "up" if (i // 50) % 2 == 0 else "down", "volatility": None, "valuation": None}
                  for i, day in enumerate(dates)}
        strategy = [0.002 if states[dates[i]]["trend"] == "up" else -0.001 for i in range(399)]
        strategy = [r + (0.001 if i % 2 else -0.001) for i, r in enumerate(strategy)]
        benchmark = [0.0005 + (0.002 if i % 2 else -0.002) for i in range(399)]
        result = state_performance(dates, strategy, benchmark, states)
        rows = {row["state"]: row for row in result["dimensions"]["trend"]}
        self.assertEqual(rows["up"]["days"] + rows["down"]["days"], 399)
        self.assertEqual(rows["up"]["segments"], 4)
        self.assertTrue(rows["up"]["readable"])
        self.assertGreater(rows["up"]["z_vs_benchmark"], 1.645)
        self.assertEqual(result["dimensions"]["volatility"], [])
        self.assertEqual(result["dimensions"]["combined"], [])
        self.assertEqual(result["current"], {"date": dates[-1], "trend": "均线之下", "volatility": None, "valuation": None})
        self.assertEqual(result["exposure_driven"], [])
        exposure = [1.0 if states[dates[i]]["trend"] == "up" else 0.0 for i in range(399)]
        driven = state_performance(dates, strategy, benchmark, states, exposure)
        self.assertEqual(driven["exposure_driven"], ["trend"])
        short = state_performance(dates[:100], strategy[:99], benchmark[:99], states)
        thin = {row["state"]: row for row in short["dimensions"]["trend"]}
        self.assertLess(thin["up"]["days"], MIN_STATE_DAYS)
        self.assertFalse(thin["up"]["readable"])


if __name__ == "__main__":
    unittest.main()
