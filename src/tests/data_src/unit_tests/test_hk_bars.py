import unittest
from datetime import datetime, timezone

from src.data_access.trading_calendar import hk_latest_completed_date
from src.data_src.common import DataSourceError
from src.data_src.sina.hk_stock import bars_with_factor_preclose, normalize_hk_symbol


class HongKongBarsTest(unittest.TestCase):
    def test_reference_close_follows_the_forward_adjust_factor(self):
        rows = [
            {"d": "2026-05-13", "c": 500.0},
            {"d": "2026-05-14", "c": 505.0},
            {"d": "2026-05-15", "c": 498.0},
            {"d": "2026-05-18", "c": 499.0},
        ]
        factors = [("1900-01-01", 0.98), ("2026-05-15", 1.0)]

        bars = bars_with_factor_preclose("00700", rows, factors, "2026-05-14", "2026-05-18")

        by_day = {bar.trade_date: bar for bar in bars}
        self.assertEqual(by_day["2026-05-14"].preclose, 500.0)
        self.assertAlmostEqual(by_day["2026-05-15"].preclose, 505.0 * 0.98)
        self.assertEqual(by_day["2026-05-18"].preclose, 498.0)
        self.assertNotIn("2026-05-13", by_day)

    def test_codes_and_session_dates(self):
        self.assertEqual(normalize_hk_symbol("700"), "00700")
        self.assertEqual(normalize_hk_symbol("HK03865"), "03865")
        with self.assertRaises(DataSourceError):
            normalize_hk_symbol("AAPL")
        # 2026-11-17 08:00 UTC is 16:00 in Hong Kong: that day's session is not final yet.
        self.assertEqual(hk_latest_completed_date(datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)), "2026-11-16")
        self.assertEqual(hk_latest_completed_date(datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)), "2026-11-17")
        self.assertEqual(hk_latest_completed_date(datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)), "2026-11-13")


if __name__ == "__main__":
    unittest.main()
