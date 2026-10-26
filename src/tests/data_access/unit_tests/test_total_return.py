import unittest

from src.data_access import adjusted_nav_levels, forward_adjusted_bars
from src.data_src.models import DailyBar
from src.data_src.total_return_bars import bars_with_factor_preclose


class ForwardAdjustedBarsTest(unittest.TestCase):
    def test_missing_preclose_falls_back_to_previous_close(self):
        records = [
            {"trade_date": "2026-07-01", "close": 2.0, "preclose": 1.9},
            {"trade_date": "2026-07-02", "close": 1.0, "preclose": 0.98},
            {"trade_date": "2026-07-03", "close": 1.1},
        ]

        bars = forward_adjusted_bars(records)

        self.assertAlmostEqual(bars[-1]["close"], 1.1)
        self.assertAlmostEqual(bars[1]["close"], 1.0)
        self.assertAlmostEqual(bars[0]["close"], 0.98)
        self.assertEqual(bars[2]["return_reference"], "previous_close")
        self.assertEqual(bars[1]["return_reference"], "exchange_preclose")


class FactorPrecloseTest(unittest.TestCase):
    def test_ex_date_preclose_uses_factor_change(self):
        bars = [
            DailyBar("600519", day, None, None, None, close, None, None, "sina")
            for day, close in (
                ("2025-06-24", 1437.2),
                ("2025-06-25", 1435.86),
                ("2025-06-26", 1420.0),
            )
        ]
        factors = [("2001-08-27", 1.0), ("2025-06-26", 1.0196)]

        output = bars_with_factor_preclose(bars, factors, "2025-06-25")

        self.assertEqual([bar.trade_date for bar in output], ["2025-06-25", "2025-06-26"])
        self.assertAlmostEqual(output[0].preclose, 1437.2)
        self.assertAlmostEqual(output[1].preclose, 1435.86 / 1.0196)


class AdjustedNavLevelsTest(unittest.TestCase):
    def test_distribution_day_uses_published_growth_rate(self):
        records = [
            {"nav_date": "2026-07-01", "unit_nav": 1.5, "daily_growth_rate": 0.0},
            # 0.3 per unit paid out; the fund itself rose 1%.
            {"nav_date": "2026-07-02", "unit_nav": 1.212, "daily_growth_rate": 1.0},
            {"nav_date": "2026-07-03", "unit_nav": 1.2241, "daily_growth_rate": 1.0},
            {"nav_date": "2026-07-04", "unit_nav": 9.9, "estimated": True},
        ]

        levels = adjusted_nav_levels(records)

        self.assertEqual(sorted(levels), ["2026-07-01", "2026-07-02", "2026-07-03"])
        self.assertAlmostEqual(levels["2026-07-03"], 1.2241)
        self.assertAlmostEqual(
            levels["2026-07-03"] / levels["2026-07-01"],
            1.01 * 1.2241 / 1.212,
        )


if __name__ == "__main__":
    unittest.main()
