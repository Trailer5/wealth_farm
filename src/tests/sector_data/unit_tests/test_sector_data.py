import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.master_data import SW_INDEX_EXCHANGE, MasterDataService, SQLiteMasterDataRepository
from src.sector_data import SectorDataService, SQLiteSectorDataRepository

CATALOG = {
    1: [{"code": "801120", "name": "食品饮料", "level": 1, "parent_name": None, "constituent_count": 3}],
    2: [{"code": "801125", "name": "白酒Ⅱ", "level": 2, "parent_name": "食品饮料", "constituent_count": 3}],
    3: [
        {"code": "851251", "name": "白酒Ⅲ", "level": 3, "parent_name": "白酒Ⅱ", "constituent_count": 2},
        {"code": "851252", "name": "黄酒", "level": 3, "parent_name": "白酒Ⅱ", "constituent_count": 1},
    ],
}
MEMBERS = {
    "801120": [
        {"symbol": "600519", "name": "贵州茅台", "included_on": "2021-12-13"},
        {"symbol": "000858", "name": "五粮液", "included_on": "2021-12-13"},
        {"symbol": "600059", "name": "古越龙山", "included_on": "2021-12-13"},
    ],
    "851251": [
        {"symbol": "600519", "name": "贵州茅台", "included_on": "2021-12-13"},
        {"symbol": "000858", "name": "五粮液", "included_on": "2021-12-13"},
    ],
    "851252": [{"symbol": "600059", "name": "古越龙山", "included_on": "2021-12-13"}],
}


class FakeFetcher:
    def __init__(self):
        self.calls = []

    def catalog(self, level):
        self.calls.append(("catalog", level))
        return CATALOG[level]

    def constituents(self, codes):
        self.calls.append(("constituents", tuple(codes)))
        return {code: MEMBERS[code] for code in codes}

    def level_metrics(self, category, trade_dates):
        self.calls.append(("metrics", category, tuple(trade_dates)))
        return {
            day: [{"index_code": "801120", "trade_date": day, "pe": 20.0 + i, "pb": 5.0, "source": "fake"}]
            for i, day in enumerate(trade_dates)
        }


class FakeDataAccess:
    def get(self, request):
        days = ["2026-08-28", "2026-08-31", "2026-09-29", "2026-09-30", "2026-10-23"]
        return SimpleNamespace(
            records=[{"trade_date": day} for day in days if request.start_date <= day <= request.end_date],
            error=None,
        )


class SectorDataServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        self.fetcher = FakeFetcher()
        self.master_data = MasterDataService(SQLiteMasterDataRepository(path))
        self.service = SectorDataService(
            SQLiteSectorDataRepository(path),
            master_data=self.master_data,
            data_access=FakeDataAccess(),
            fetcher=self.fetcher,
            clock=lambda: datetime(2026, 10, 26, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_classify_links_levels_and_caches_reference_data(self):
        path = self.service.classify("600519")
        again = self.service.classify("600519")

        self.assertEqual((path.l1.code, path.l2.code, path.l3.code), ("801120", "801125", "851251"))
        self.assertEqual(again, path)
        self.assertEqual(
            [call for call in self.fetcher.calls if call[0] == "constituents"],
            [("constituents", ("801120",)), ("constituents", ("851251", "851252"))],
        )
        self.assertIsNone(self.service.classify("000001"))

    def test_peers_fall_back_to_level_two_when_level_three_is_small(self):
        few = self.service.peers("600519", min_peers=1)
        wider = self.service.peers("600519", min_peers=2)

        self.assertEqual(few["peer_industry"]["code"], "851251")
        self.assertEqual([p["symbol"] for p in few["peers"]], ["000858"])
        self.assertEqual(wider["peer_industry"]["code"], "801125")
        self.assertEqual({p["symbol"] for p in wider["peers"]}, {"000858", "600059"})

    def test_index_asset_is_registered_once_on_the_sw_exchange(self):
        first = self.service.index_asset("801120")
        second = self.service.index_asset("801120")

        self.assertEqual(first.asset_id, second.asset_id)
        self.assertEqual(first.exchange, SW_INDEX_EXCHANGE)
        self.assertEqual(first.asset_type, "index")

    def test_valuation_history_samples_month_ends_and_fetches_each_date_once(self):
        first = self.service.valuation_history("801120", start_date="2026-08-01", end_date="2026-10-26")
        second = self.service.valuation_history("801120", start_date="2026-08-01", end_date="2026-10-26")
        level3 = self.service.valuation_history("851251", start_date="2026-08-01", end_date="2026-10-26")

        self.assertEqual(
            [p["trade_date"] for p in first["points"]],
            ["2026-08-31", "2026-09-30", "2026-10-23"],
        )
        self.assertEqual(second["points"], first["points"])
        self.assertEqual(len([c for c in self.fetcher.calls if c[0] == "metrics"]), 1)
        self.assertEqual(level3["points"], [])
        self.assertIn("三级", level3["note"])


if __name__ == "__main__":
    unittest.main()
