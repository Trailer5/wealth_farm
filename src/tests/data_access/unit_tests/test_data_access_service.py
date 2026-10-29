import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    DATASET_STOCK_TOTAL_RETURN_BARS,
    AssetInput,
    AssetResolutionRequiredError,
    DataAccessRequest,
    DataAccessService,
    DailyDataReader,
    SQLiteDataAccessRepository,
    TradingCalendarService,
)
from src.data_src.models import DailyBar
from src.data_store import SQLiteDataStore
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class FakeFetchService:
    def __init__(self, store):
        self.store = store
        self.calls = 0
        self.fail = False
        self.failed_sources = set()
        self.requests = []
        self.calendar_calls = 0
        self.first_bar_date = None
        self.closes = {}

    def fetch_and_store_a_share_daily_bars(
        self,
        symbol,
        start_date,
        end_date,
        source="baostock",
        timeout_seconds=None,
    ):
        self.calls += 1
        self.requests.append(("stock", source, start_date, end_date))
        if self.fail or source in self.failed_sources:
            raise RuntimeError("remote unavailable")
        bar = DailyBar(
            symbol=symbol,
            trade_date=self.first_bar_date or start_date,
            open=10.0,
            high=10.5,
            low=9.8,
            close=self.closes.get(source, 10.3),
            volume=1000.0,
            amount=10300.0,
            source=f"{source}:remote",
        )
        stored_count = self.store.upsert_stock_daily_bars([bar])
        return SimpleNamespace(fetched_count=1, stored_count=stored_count)

    def fetch_and_store_etf_daily_bars(
        self,
        symbol,
        start_date,
        end_date,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls += 1
        self.requests.append(("etf", source, start_date, end_date))
        if self.fail or source in self.failed_sources:
            raise RuntimeError("remote unavailable")
        bar = DailyBar(
            symbol=symbol,
            trade_date=start_date,
            open=5.0,
            high=5.2,
            low=4.9,
            close=5.1,
            volume=2000.0,
            amount=10200.0,
            source="fake:etf-remote",
        )
        stored_count = self.store.upsert_fund_exchange_daily_bars([bar])
        return SimpleNamespace(fetched_count=1, stored_count=stored_count)

    def fetch_and_store_total_return_bars(
        self,
        symbol,
        start_date,
        end_date,
        *,
        fund,
        source="baostock",
        timeout_seconds=None,
    ):
        self.calls += 1
        self.requests.append(("total_return", source, start_date, end_date))
        if self.fail or source in self.failed_sources:
            raise RuntimeError("remote unavailable")
        # 10-for-10 bonus shares on the second day: price halves, holders do not lose.
        bars = [
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1000.0,
                amount=None,
                source="fake:total-return",
                preclose=preclose,
            )
            for day, close, preclose in (
                (start_date, 20.0, 19.8),
                ("2026-07-02", 10.5, 10.0),
                ("2026-07-03", 10.2, 10.5),
            )
        ]
        table = (
            "fund_exchange_total_return_bars"
            if fund
            else "stock_total_return_bars"
        )
        stored_count = self.store.upsert_total_return_bars(table, bars)
        return SimpleNamespace(fetched_count=3, stored_count=stored_count)

    def fetch_trade_calendar(
        self,
        start_date,
        end_date,
        source="baostock",
        timeout_seconds=None,
    ):
        self.calendar_calls += 1
        current = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        rows = []
        while current <= end:
            rows.append(
                {
                    "calendar_date": current.isoformat(),
                    "is_trading_day": "1" if current.weekday() < 5 else "0",
                }
            )
            current += timedelta(days=1)
        return rows


class DataAccessServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "data-access.sqlite3"
        self.store = SQLiteDataStore(db_path)
        self.master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
        self.repository = SQLiteDataAccessRepository(self.store)
        self.fetch_service = FakeFetchService(self.store)
        self.service = DataAccessService(
            master_data=self.master_data,
            repository=self.repository,
            fetch_service=self.fetch_service,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_total_return_bars_are_forward_adjusted_by_exchange_preclose(self):
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519"),
            dataset=DATASET_STOCK_TOTAL_RETURN_BARS,
            start_date="2026-07-01",
            end_date="2026-07-03",
        )

        response = self.service.get(request)
        cached = self.service.get(request)

        closes = [record["close"] for record in response.records]
        self.assertEqual(self.fetch_service.requests[0][1], "baostock")
        self.assertEqual(response.metadata["price_basis"], "total_return")
        self.assertAlmostEqual(closes[-1], 10.2)
        self.assertAlmostEqual(closes[1] / closes[0], 10.5 / 10.0)
        self.assertAlmostEqual(response.records[0]["unadjusted_close"], 20.0)
        self.assertEqual(
            [record["close"] for record in cached.records],
            closes,
        )
        self.assertEqual(self.fetch_service.calls, 2)

    def test_concurrent_sources_merge_by_priority_and_report_conflicts(self):
        self.fetch_service.closes = {"tencent": 10.3, "sina": 10.5}
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519"),
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-01",
            remote_sources=("tencent", "sina", "mootdx"),
        )

        response = self.service.get(request)

        self.assertEqual(self.fetch_service.calls, 2)
        self.assertEqual(response.records[0]["source"], "tencent:remote")
        self.assertEqual(
            response.metadata["successful_sources"],
            ["tencent", "sina"],
        )
        self.assertEqual(response.metadata["source_conflict_count"], 1)
        self.assertEqual(
            response.metadata["source_conflicts"][0]["values"],
            {"tencent": 10.3, "sina": 10.5},
        )

    def test_later_sources_are_asked_only_when_the_first_two_fail(self):
        self.fetch_service.failed_sources = {"mootdx", "tencent"}
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519"),
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-01",
            remote_sources=("mootdx", "tencent", "sina", "ths"),
        )

        response = self.service.get(request)

        self.assertEqual(self.fetch_service.calls, 3)
        self.assertEqual(response.metadata["successful_sources"], ["sina"])
        self.assertEqual(response.metadata["attempted_sources"], ["mootdx", "tencent", "sina"])
        self.assertEqual(set(response.metadata["provider_errors"]), {"mootdx", "tencent"})

    def test_sequential_mode_stops_at_first_success(self):
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519"),
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-01",
            remote_sources=("mootdx", "tencent"),
            fetch_mode="sequential",
        )

        self.service.get(request)

        self.assertEqual(self.fetch_service.calls, 1)

    def test_missing_data_is_fetched_then_reused_from_fresh_coverage(self):
        request = _stock_request()

        first = self.service.get(request)
        self.fetch_service.fail = True
        second = self.service.get(request)

        self.assertTrue(first.complete)
        self.assertTrue(first.fetched_remote)
        self.assertEqual(first.asset.standard_symbol, "600519")
        self.assertEqual(first.records[0]["close"], 10.3)
        self.assertTrue(second.complete)
        self.assertFalse(second.fetched_remote)
        self.assertEqual(self.fetch_service.calls, 1)
        self.assertEqual(second.asset.asset_id, first.asset.asset_id)

    def test_local_rows_without_coverage_are_not_claimed_complete(self):
        asset = self.master_data.resolve_identifier(
            source="user:input",
            raw_symbol="600519",
            asset_type_hint="股票",
        ).asset
        self.store.upsert_stock_daily_bars(
            [
                DailyBar(
                    symbol="600519",
                    trade_date="2026-07-01",
                    open=10.0,
                    high=10.0,
                    low=10.0,
                    close=10.0,
                    volume=1.0,
                    amount=10.0,
                    source="local:test",
                )
            ]
        )

        response = self.service.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
                allow_remote=False,
            )
        )

        self.assertFalse(response.complete)
        self.assertTrue(response.stale)
        self.assertEqual(len(response.records), 1)
        self.assertEqual(self.fetch_service.calls, 0)

    def test_remote_failure_can_return_stale_local_rows(self):
        first = self.service.get(_stock_request())
        self.repository.mark_coverage(
            asset_id=first.asset.asset_id,
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-31",
            source="baostock",
            checked_at=datetime.now(timezone.utc) - timedelta(days=2),
        )
        self.fetch_service.fail = True

        response = self.service.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=first.asset.asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
                max_age_seconds=60,
                allow_stale_on_error=True,
            )
        )

        self.assertFalse(response.complete)
        self.assertTrue(response.stale)
        self.assertIn("All remote sources failed", response.error)
        self.assertIn(
            "remote unavailable",
            response.metadata["provider_errors"]["baostock"],
        )
        self.assertIn(
            "remote unavailable",
            response.metadata["provider_errors"]["mootdx"],
        )
        self.assertIn(
            "remote unavailable",
            response.metadata["provider_errors"]["tencent"],
        )
        self.assertIn(
            "remote unavailable",
            response.metadata["provider_errors"]["sina"],
        )
        self.assertIn(
            "remote unavailable",
            response.metadata["provider_errors"]["akshare"],
        )
        self.assertEqual(self.fetch_service.calls, 7)

    def test_unknown_asset_id_is_rejected(self):
        with self.assertRaises(AssetResolutionRequiredError):
            self.service.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id="asset:missing"),
                    dataset=DATASET_STOCK_DAILY_BARS,
                    start_date="2026-07-01",
                    end_date="2026-07-31",
                )
            )

    def test_exchange_fund_daily_bars_use_exchange_fund_identity(self):
        response = self.service.get(
            DataAccessRequest(
                asset=AssetInput(
                    raw_symbol="518880",
                    name="黄金ETF",
                ),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
                remote_sources=("akshare",),
            )
        )

        self.assertEqual(response.asset.asset_type, "exchange_fund")
        self.assertEqual(response.asset.standard_symbol, "518880")
        self.assertEqual(response.asset.exchange, "sh")
        self.assertTrue(response.complete)
        self.assertTrue(response.fetched_remote)
        self.assertEqual(response.records[0]["close"], 5.1)

    def test_exchange_fund_automatically_falls_back_to_second_provider(self):
        self.fetch_service.failed_sources.add("akshare")

        response = self.service.get(
            DataAccessRequest(
                asset=AssetInput(raw_symbol="518880", name="黄金ETF"),
                dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
                remote_sources=("akshare", "baostock"),
            )
        )

        self.assertTrue(response.complete)
        self.assertEqual(
            response.metadata["attempted_sources"],
            ["akshare", "baostock"],
        )
        self.assertIn("akshare", response.metadata["provider_errors"])
        self.assertEqual(self.fetch_service.calls, 2)

    def test_trading_calendar_uses_last_trading_day_and_avoids_refetch(self):
        calendar = TradingCalendarService(
            repository=self.repository,
            fetch_service=self.fetch_service,
        )
        service = DataAccessService(
            master_data=self.master_data,
            repository=self.repository,
            fetch_service=self.fetch_service,
            trading_calendar=calendar,
        )
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519", asset_type_hint="股票"),
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-05",
            max_age_seconds=0,
            remote_sources=("baostock",),
        )

        first = service.get(request)
        second = service.get(request)

        self.assertEqual(first.metadata["effective_end_date"], "2026-07-03")
        self.assertEqual(
            self.fetch_service.requests[0],
            ("stock", "baostock", "2026-07-01", "2026-07-03"),
        )
        self.assertTrue(first.fetched_remote)
        self.assertFalse(second.fetched_remote)
        self.assertEqual(self.fetch_service.calls, 1)
        self.assertEqual(self.fetch_service.calendar_calls, 2)

    def test_trading_calendar_falls_back_when_primary_source_fails(self):
        fetch = self.fetch_service
        original = fetch.fetch_trade_calendar
        sources = []

        def flaky(start_date, end_date, source="baostock", timeout_seconds=None):
            sources.append(source)
            if source == "baostock":
                raise RuntimeError("BaoStock login failed")
            return original(start_date, end_date, source, timeout_seconds)

        fetch.fetch_trade_calendar = flaky
        calendar = TradingCalendarService(repository=self.repository, fetch_service=fetch)

        self.assertEqual(calendar.first_trading_date("2026-07-04", "2026-07-10"), "2026-07-06")
        self.assertEqual(sources, ["baostock", "sina"])
        calendar.first_trading_date("2026-07-04", "2026-07-10")
        self.assertEqual(sources, ["baostock", "sina"])

    def test_trading_calendar_reports_every_failed_source(self):
        def broken(start_date, end_date, source="baostock", timeout_seconds=None):
            raise RuntimeError(f"{source} down")

        self.fetch_service.fetch_trade_calendar = broken
        calendar = TradingCalendarService(repository=self.repository, fetch_service=self.fetch_service)
        with self.assertRaisesRegex(RuntimeError, "baostock.*sina"):
            calendar.first_trading_date("2026-07-04", "2026-07-10")

    def test_truncated_fetch_does_not_cover_missing_leading_range(self):
        service = DataAccessService(
            master_data=self.master_data,
            repository=self.repository,
            fetch_service=self.fetch_service,
            trading_calendar=TradingCalendarService(
                repository=self.repository,
                fetch_service=self.fetch_service,
            ),
        )
        request = DataAccessRequest(
            asset=AssetInput(raw_symbol="600519", asset_type_hint="股票"),
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-10",
            remote_sources=("baostock",),
        )
        self.fetch_service.first_bar_date = "2026-07-08"

        first = service.get(request)
        service.get(request)

        self.assertEqual(
            first.metadata["leading_gap"],
            {
                "expected_first_date": "2026-07-01",
                "first_record_date": "2026-07-08",
            },
        )
        self.assertEqual(first.coverage_start, "2026-07-08")
        self.assertEqual(self.fetch_service.calls, 2)

    def test_fresh_daily_data_is_returned_without_remote_fetch(self):
        asset = Asset(
            asset_id="asset:shared-daily-data",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600519",
            display_name="贵州茅台",
            market="cn_a",
            exchange="sh",
        )
        daily_root = Path(self.temp_dir.name) / "daily_data"
        _write_daily_data(
            daily_root,
            asset,
            close=10.8,
            source="daily:baostock",
        )
        self.fetch_service.fail = True
        service = DataAccessService(
            master_data=self.master_data,
            repository=self.repository,
            fetch_service=self.fetch_service,
            daily_data_reader=DailyDataReader(daily_root),
        )

        response = service.get(
            DataAccessRequest(
                asset=AssetInput(raw_symbol="600519"),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
            )
        )

        self.assertTrue(response.complete)
        self.assertFalse(response.fetched_remote)
        self.assertEqual(response.asset.asset_id, "asset:shared-daily-data")
        self.assertEqual(response.records[0]["close"], 10.8)
        self.assertEqual(response.metadata["backends"], ["daily_data"])
        self.assertEqual(self.fetch_service.calls, 0)

    def test_fresh_sqlite_record_overrides_same_daily_data_date(self):
        asset = self.master_data.resolve_identifier(
            source="user:input",
            raw_symbol="600519",
            asset_type_hint="股票",
        ).asset
        daily_root = Path(self.temp_dir.name) / "daily_data"
        _write_daily_data(
            daily_root,
            asset,
            close=10.8,
            source="daily:baostock",
        )
        self.store.upsert_stock_daily_bars(
            [
                DailyBar(
                    symbol="600519",
                    trade_date="2026-07-01",
                    open=11.0,
                    high=11.0,
                    low=11.0,
                    close=11.0,
                    volume=1.0,
                    amount=11.0,
                    source="local:manual",
                )
            ]
        )
        self.repository.mark_coverage(
            asset_id=asset.asset_id,
            dataset=DATASET_STOCK_DAILY_BARS,
            start_date="2026-07-01",
            end_date="2026-07-31",
            source="manual",
        )
        service = DataAccessService(
            master_data=self.master_data,
            repository=self.repository,
            fetch_service=self.fetch_service,
            daily_data_reader=DailyDataReader(daily_root),
        )

        response = service.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=asset.asset_id),
                dataset=DATASET_STOCK_DAILY_BARS,
                start_date="2026-07-01",
                end_date="2026-07-31",
            )
        )

        self.assertEqual(len(response.records), 1)
        self.assertEqual(response.records[0]["close"], 11.0)
        self.assertEqual(response.metadata["backends"], ["daily_data", "sqlite"])


def _stock_request():
    return DataAccessRequest(
        asset=AssetInput(
            raw_symbol="600519",
            asset_type_hint="股票",
        ),
        dataset=DATASET_STOCK_DAILY_BARS,
        start_date="2026-07-01",
        end_date="2026-07-31",
        max_age_seconds=3600,
        remote_sources=("baostock",),
    )


def _write_daily_data(root, asset, close, source):
    asset_dir = (
        root
        / "assets"
        / asset.market
        / asset.exchange
        / asset.standard_symbol
    )
    dataset_dir = asset_dir / "daily_bars"
    month_dir = dataset_dir / "2026"
    month_dir.mkdir(parents=True)
    (asset_dir / "asset.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "asset_id": asset.asset_id,
                "asset_type": asset.asset_type,
                "standard_symbol": asset.standard_symbol,
                "display_name": asset.display_name,
                "market": asset.market,
                "exchange": asset.exchange,
                "status": asset.status,
            }
        ),
        encoding="utf-8",
    )
    (month_dir / "07.csv").write_text(
        "\n".join(
            [
                "symbol,trade_date,open,high,low,close,volume,amount,source",
                f"600519,2026-07-01,10.0,11.0,9.8,{close},1000,10800,{source}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": DATASET_STOCK_DAILY_BARS,
                "asset_id": asset.asset_id,
                "coverage": [
                    {
                        "range_start": "2026-07-01",
                        "range_end": "2026-07-31",
                        "source": source,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
