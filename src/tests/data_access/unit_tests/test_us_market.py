import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    AssetInput,
    DataAccessFetchError,
    DataAccessRequest,
    DataAccessService,
    SQLiteDataAccessRepository,
)
from src.data_access.trading_calendar import us_latest_completed_date
from src.data_src.models import DailyBar, SecurityInfo
from src.data_store import SQLiteDataStore
from src.master_data import MARKET_US, MasterDataService, SQLiteMasterDataRepository


class FakeUsFetch:
    def __init__(self, store):
        self.store = store
        self.requests = []

    def fetch_and_store_us_total_return_bars(self, symbol, start_date, end_date, *, fund, source, timeout_seconds=None):
        self.requests.append((source, start_date, end_date))
        bars = [
            DailyBar(symbol, day, close, close, close, close, 1.0, None, f"{source}:fake", preclose=preclose)
            for day, close, preclose in (("2024-06-07", 1208.88, 1209.98), ("2024-06-10", 121.79, 120.888))
            if start_date <= day <= end_date
        ]
        stored = self.store.upsert_total_return_bars("stock_total_return_bars", bars)
        return SimpleNamespace(fetched_count=len(bars), stored_count=stored)

    def fetch_and_store_total_return_bars(self, *args, **kwargs):
        raise AssertionError("US assets must not use A-share sources")


class UsMarketAccessTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "db.sqlite3"
        store = SQLiteDataStore(path)
        self.master_data = MasterDataService(SQLiteMasterDataRepository(path))
        self.fetch = FakeUsFetch(store)
        self.service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=self.fetch,
            trading_calendar=SimpleNamespace(),  # an SSE calendar call would fail on this stub
            # Sunday morning in Shanghai: Friday's US session has closed.
            clock=lambda: datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc),
        )
        self.asset = self.master_data.register_us_listing(
            SecurityInfo(symbol="NVDA", name="NVIDIA", security_type="stock", exchange="nasdaq", source="nasdaq_us")
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_us_bars_use_us_sources_and_chain_total_return_across_the_split(self):
        response = self.service.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=self.asset.asset_id),
                dataset=DATASET_STOCK_TOTAL_RETURN_BARS,
                start_date="2024-06-07",
                end_date="2024-06-10",
            )
        )

        self.assertEqual({item[0] for item in self.fetch.requests}, {"sina_us", "nasdaq_us"})
        self.assertEqual(response.metadata["trading_calendar"], "us_session_close")
        closes = [item["close"] for item in response.records]
        self.assertAlmostEqual(closes[1] / closes[0], 121.79 / 120.888)
        self.assertAlmostEqual(response.records[-1]["unadjusted_close"], 121.79)

    def test_registering_twice_returns_the_same_asset(self):
        again = self.master_data.register_us_listing(
            SecurityInfo(symbol="NVDA", name="NVIDIA", security_type="stock", exchange="nasdaq", source="nasdaq_us")
        )

        self.assertEqual(again.asset_id, self.asset.asset_id)
        self.assertEqual(again.market, MARKET_US)

    def test_us_assets_only_have_total_return_bars(self):
        with self.assertRaises(DataAccessFetchError):
            self.service.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=self.asset.asset_id),
                    dataset=DATASET_STOCK_DAILY_BARS,
                    start_date="2024-06-07",
                    end_date="2024-06-10",
                )
            )

    def test_latest_completed_us_session(self):
        cases = {
            # Friday 15:00 New York: the session is still open, so Thursday.
            datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc): "2026-11-12",
            # Friday 17:00 New York.
            datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc): "2026-11-13",
            # Sunday: back to Friday.
            datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc): "2026-11-13",
        }
        for now, expected in cases.items():
            with self.subTest(now=now):
                self.assertEqual(us_latest_completed_date(now), expected)


if __name__ == "__main__":
    unittest.main()
