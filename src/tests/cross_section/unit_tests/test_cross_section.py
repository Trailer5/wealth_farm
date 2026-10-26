import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.cross_section import CrossSectionService, CrossSectionUnavailable, SQLiteCrossSectionRepository
from src.cross_section.service import _quarter_ends_before


class FakeFetcher:
    def __init__(self):
        self.calls = []
        self.periods = {
            "2026-06-30": [
                {"symbol": "600519", "report_period": "2026-06-30", "announced_on": "2026-08-12", "roe_percent": 18.0},
                {"symbol": "000001", "report_period": "2026-06-30", "announced_on": "2026-08-30", "roe_percent": 5.0},
            ],
            "2026-03-31": [
                {"symbol": "600519", "report_period": "2026-03-31", "announced_on": "2026-04-28", "roe_percent": 9.0},
                {"symbol": "000001", "report_period": "2026-03-31", "announced_on": "2026-04-20", "roe_percent": 2.5},
            ],
        }

    def market_snapshot(self):
        self.calls.append("snapshot")
        return {
            "rows": [
                {"symbol": "600519", "close": 1349.98, "pe_ttm": 20.72, "pb": 6.72, "market_cap": 1.69e12, "quote_date": "2026-10-26"},
                {"symbol": "000001", "close": 11.0, "pe_ttm": None, "pb": 0.5, "market_cap": 2.1e11, "quote_date": "2026-10-26"},
            ],
            "errors": [],
        }

    def valuation_histories(self, symbols):
        self.calls.append(("valuation", tuple(symbols)))
        return {s: [{"trade_date": "2026-10-23", "pe_ttm": 20.5}] for s in symbols}

    def report_cross_section(self, report_period):
        self.calls.append(("period", report_period))
        return self.periods.get(report_period, [])

    def delisted(self):
        return [{"symbol": "600001", "name": "邯郸钢铁", "delisted_on": "2009-12-29"}]

    def index_members_on(self, index_symbol, day):
        raise TimeoutError("baostock login hung")


class CrossSectionServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
        self.fetcher = FakeFetcher()
        self.service = CrossSectionService(
            SQLiteCrossSectionRepository(Path(self.temp_dir.name) / "db.sqlite3"),
            fetcher=self.fetcher,
            clock=lambda: self.now,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_snapshot_is_reused_while_fresh_and_kept_as_a_record(self):
        first = self.service.market_snapshot()
        second = self.service.market_snapshot()
        self.now += timedelta(hours=7)
        third = self.service.market_snapshot()

        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertNotEqual(first["snapshot_id"], third["snapshot_id"])
        self.assertEqual(first["trade_date"], "2026-10-26")
        self.assertEqual(first["coverage"], {"rows": 2, "with_pe_ttm": 1, "with_pb": 2, "with_market_cap": 2})
        self.assertEqual(self.fetcher.calls.count("snapshot"), 2)

    def test_report_period_hides_rows_announced_after_the_cutoff(self):
        result = self.service.report_period("2026-06-30", as_of_date="2026-08-20")

        self.assertEqual([row["symbol"] for row in result["rows"]], ["600519"])

    def test_latest_financials_takes_newest_announced_period_per_stock(self):
        result = self.service.latest_financials("2026-08-20")

        by_symbol = {row["symbol"]: row["report_period"] for row in result["rows"]}
        self.assertEqual(by_symbol, {"600519": "2026-06-30", "000001": "2026-03-31"})
        self.assertEqual(result["periods_checked"][0], "2026-06-30")

    def test_valuation_history_is_fetched_once_per_day(self):
        self.service.valuation_history(["600519"], start_date="2026-01-01", end_date="2026-10-26")
        again = self.service.valuation_history(["600519"], start_date="2026-01-01", end_date="2026-10-26")

        self.assertEqual(again["series"]["600519"][0]["pe_ttm"], 20.5)
        self.assertEqual([c for c in self.fetcher.calls if c[0] == "valuation"], [("valuation", ("600519",))])

    def test_unavailable_index_members_raise_a_clear_error(self):
        with self.assertRaises(CrossSectionUnavailable):
            self.service.index_members_on("000300", "2015-06-30")
        self.assertEqual(self.service.delisted()[0]["symbol"], "600001")

    def test_quarter_ends_are_strictly_before_the_date(self):
        self.assertEqual(
            _quarter_ends_before("2026-10-26", 5),
            ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"],
        )
        self.assertEqual(_quarter_ends_before("2026-03-31", 1), ["2025-12-31"])


if __name__ == "__main__":
    unittest.main()
