import unittest

from src.data_access import adjusted_nav_levels, forward_adjusted_bars
from src.data_src.models import DailyBar
from src.data_src.total_return_bars import (
    bars_with_factor_preclose,
    sina_stock_total_return_bars,
    sina_tdx_fund_total_return_bars,
)


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

    def test_long_ranges_use_the_full_sina_history(self):
        class _ShortSina:
            def get_a_share_daily_bars(self, symbol, start, end):
                raise ValueError("Sina baseline provider supports up to 1023 daily bars")

        class _History:
            def get_a_share_daily_bars_sina_history(self, symbol, start, end):
                return [
                    DailyBar("600519", day, None, None, None, close, None, None, "sina")
                    for day, close in (("2014-01-02", 10.0), ("2014-01-03", 11.0))
                ]

            def get_a_share_hfq_factors(self, symbol):
                return [("2001-01-01", 1.0)]

        output = sina_stock_total_return_bars(
            "600519", "2014-01-03", "2026-10-28", sina=_ShortSina(), akshare=_History()
        )

        self.assertEqual([(bar.trade_date, bar.preclose) for bar in output], [("2014-01-03", 10.0)])

    def test_fund_share_consolidation_raises_the_reference_close(self):
        class _History:
            def get_etf_daily_bars_sina_history(self, symbol, start, end):
                return [
                    DailyBar("512100", day, None, None, None, close, None, None, "sina")
                    for day, close in (("2022-09-01", 0.982), ("2022-09-05", 2.713))
                ]

        class _Tdx:
            def get_xdxr_events(self, symbol):
                return [{"ex_date": "2022-09-05", "category": 11, "cash_per_10": 0.0, "bonus_per_10": 0.0,
                         "rights_per_10": 0.0, "rights_price": 0.0, "split_ratio": 0.36555}]

        output = sina_tdx_fund_total_return_bars(
            "512100", "2022-09-05", "2022-09-05", akshare=_History(), mootdx=_Tdx()
        )

        self.assertAlmostEqual(output[0].preclose, 0.982 / 0.36555)


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
