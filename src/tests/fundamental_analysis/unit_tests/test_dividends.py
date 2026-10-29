import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.fundamental_analysis import DividendHistoryService, dividends_by_year, trailing_dividends

RECORDS = [
    {"announce_date": "2026-05-29", "ex_date": "2026-06-05", "cash_per_share": "0.9"},
    {"announce_date": "2025-12-09", "ex_date": "2025-12-17", "cash_per_share": "0.48"},
    {"announce_date": "2025-05-30", "ex_date": "2025-06-06", "cash_per_share": "1.22"},
]


class DividendHistoryTest(unittest.TestCase):
    def test_trailing_and_annual_cash_per_share(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            calls = []

            def fetcher(symbol, timeout):
                calls.append(symbol)
                if len(calls) > 1:
                    raise OSError("offline")
                return RECORDS

            today = [date(2026, 10, 28)]
            service = DividendHistoryService(Path(temp_dir) / "db.sqlite3", fetcher=fetcher, today=lambda: today[0])
            events, error = service.events("600887")
            self.assertIsNone(error)
            ttm, paid = trailing_dividends(events, "2026-10-28")
            self.assertEqual(ttm, Decimal("1.38"))
            self.assertEqual(len(paid), 2)
            # Announced but not yet ex-dividend on the cutoff, so 2026-06-05 is excluded.
            self.assertEqual(trailing_dividends(events, "2026-06-01")[0], Decimal("1.70"))
            self.assertEqual(dividends_by_year(events, "2026-10-28"), {"2025": Decimal("1.70"), "2026": Decimal("0.9")})

            service.events("600887")
            self.assertEqual(len(calls), 1)
            today[0] = date(2026, 11, 20)
            events, error = service.events("600887")
            self.assertEqual(len(calls), 2)
            self.assertIn("offline", error)
            self.assertEqual(len(events), 3)


if __name__ == "__main__":
    unittest.main()
