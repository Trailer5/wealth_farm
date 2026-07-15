import tempfile
import unittest
from pathlib import Path

from src.data_src.models import (
    DailyBar,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
    OCRQueueItem,
    SecurityInfo,
)
from src.data_src.service import DataFetchService, compare_daily_bars
from src.data_store import SQLiteDataStore


class FakeDailySource:
    def __init__(self, source, close=10.0, amount=1000.0):
        self.source = source
        self.close = close
        self.amount = amount

    def get_a_share_daily_bars(self, symbol, start_date, end_date):
        return [
            DailyBar(
                symbol="000001" if self.source == "akshare" else "sz.000001",
                trade_date="2026-01-05",
                open=9.8,
                high=10.2,
                low=9.7,
                close=self.close,
                volume=100.0,
                amount=self.amount,
                source=f"{self.source}:test",
                raw={"provider": self.source},
            )
        ]

    def get_etf_daily_bars(self, symbol, start_date, end_date):
        return [
            DailyBar(
                symbol="510300",
                trade_date="2026-01-05",
                open=4.0,
                high=4.1,
                low=3.9,
                close=4.05,
                volume=1000.0,
                amount=4050.0,
                source=f"{self.source}:etf-test",
                raw={"provider": self.source},
            )
        ]

    def get_open_fund_nav_history_records(self, symbol):
        return [
            FundNav(
                symbol="000001",
                nav_date="2026-01-05",
                unit_nav=1.0,
                accumulated_nav=2.0,
                daily_growth_rate=0.5,
                source=f"{self.source}:fund-nav-test",
                estimated=False,
                raw={"provider": self.source},
            )
        ]

    def get_stock_basic_records(self, symbol=None):
        return [
            SecurityInfo(
                symbol="sz.000001",
                name="平安银行",
                security_type="1",
                exchange="sz",
                source=f"{self.source}:security-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_names(self):
        return [
            SecurityInfo(
                symbol="000001",
                name="测试基金",
                security_type="混合型",
                exchange=None,
                source=f"{self.source}:fund-name-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_value_estimates(self):
        return [
            FundEstimate(
                symbol="000001",
                estimate_date="2026-01-05",
                estimate_time="15:00",
                estimated_nav=1.1,
                estimated_growth_rate=0.5,
                source=f"{self.source}:fund-estimate-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_purchase_status_records(self):
        return [
            FundPurchaseStatus(
                symbol="000001",
                name="测试基金",
                fund_type="混合型",
                purchase_status="开放申购",
                redemption_status="开放赎回",
                next_open_date="2026-01-06",
                min_purchase_amount=10.0,
                daily_limit_amount=10000.0,
                fee_rate=0.15,
                source=f"{self.source}:purchase-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_scale_records(self, fund_type):
        return [
            FundReportRecord(
                symbol="000001",
                report_type="fund_scale_open_sina",
                report_date="2026-01-05",
                item_name="测试基金",
                item_value=1000.0,
                ratio=None,
                source=f"{self.source}:scale-test",
                raw={"provider": self.source},
            )
        ]

    def get_fund_stock_holdings(self, symbol, year):
        return [
            FundReportRecord(
                symbol="000001",
                report_type="fund_portfolio_hold_em",
                report_date="2026-06-30",
                item_name="平安银行",
                item_value=100.0,
                ratio=1.2,
                source=f"{self.source}:stock-holding-test",
                raw={"provider": self.source},
            )
        ]


class SQLiteDataStoreTest(unittest.TestCase):
    def test_default_database_path_uses_data_database(self):
        self.assertEqual(SQLiteDataStore().db_path.as_posix(), "data/database/wealth_farm.sqlite3")

    def test_initialize_records_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()

            versions = store.get_schema_versions()

            self.assertEqual(versions[0]["version"], SQLiteDataStore.SCHEMA_VERSION)
            self.assertIn("initial", versions[0]["description"])

    def test_upsert_and_read_stock_daily_bars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            bar = DailyBar(
                symbol="000001",
                trade_date="2026-01-05",
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100.0,
                amount=150.0,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_stock_daily_bars([bar]), 1)
            self.assertEqual(store.upsert_stock_daily_bars([bar]), 1)

            rows = store.get_stock_daily_bars("000001")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["close"], 1.5)
            self.assertEqual(rows[0]["source"], "unit-test")

    def test_upsert_and_read_fund_nav_daily(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            nav = FundNav(
                symbol="000001",
                nav_date="2026-01-05",
                unit_nav=1.0,
                accumulated_nav=2.0,
                daily_growth_rate=0.5,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_fund_nav_daily([nav]), 1)
            self.assertEqual(store.upsert_fund_nav_daily([nav]), 1)

            rows = store.get_fund_nav_daily("000001")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["unit_nav"], 1.0)
            self.assertEqual(rows[0]["estimated"], 0)

    def test_upsert_and_read_security_and_fund_estimate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            security = SecurityInfo(
                symbol="000001",
                name="测试基金",
                security_type="混合型",
                exchange=None,
                source="unit-test",
                raw={"x": 1},
            )
            estimate = FundEstimate(
                symbol="000001",
                estimate_date="2026-01-05",
                estimate_time="15:00",
                estimated_nav=1.1,
                estimated_growth_rate=0.5,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_securities([security]), 1)
            self.assertEqual(store.upsert_fund_estimates([estimate]), 1)

            self.assertEqual(store.get_security("000001")["name"], "测试基金")
            self.assertEqual(store.get_fund_estimates("000001")[0]["estimated_nav"], 1.1)

    def test_upsert_and_read_fund_purchase_status_and_report_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            status = FundPurchaseStatus(
                symbol="000001",
                name="测试基金",
                fund_type="混合型",
                purchase_status="开放申购",
                redemption_status="开放赎回",
                next_open_date="2026-01-06",
                min_purchase_amount=10.0,
                daily_limit_amount=10000.0,
                fee_rate=0.15,
                source="unit-test",
                raw={"x": 1},
            )
            record = FundReportRecord(
                symbol="000001",
                report_type="fund_scale_open_sina",
                report_date="2026-01-05",
                item_name="测试基金",
                item_value=1000.0,
                ratio=None,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_fund_purchase_status([status]), 1)
            self.assertEqual(store.upsert_fund_report_records([record]), 1)

            self.assertEqual(store.get_fund_purchase_status("000001")[0]["purchase_status"], "开放申购")
            self.assertEqual(store.get_fund_report_records("000001")[0]["item_value"], 1000.0)

    def test_upsert_and_read_ocr_queue_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            item = OCRQueueItem(
                document_id="doc-1",
                file_path="data/disclosures/doc-1.pdf",
                reason="empty",
                status="queued",
                attempts=0,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_ocr_queue_items([item]), 1)
            self.assertEqual(store.upsert_ocr_queue_items([item]), 1)

            rows = store.get_ocr_queue_items("queued")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["document_id"], "doc-1")
            self.assertEqual(rows[0]["reason"], "empty")


class DataFetchServiceTest(unittest.TestCase):
    def test_fetch_and_store_a_share_daily_bars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_a_share_daily_bars(
                "000001", "20260101", "20260131", source="baostock"
            )

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_stock_daily_bars("sz.000001")[0]["close"], 10.0)

    def test_compare_a_share_daily_bars_persists_checks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare", close=10.0, amount=1000.0),
                baostock_source=FakeDailySource("baostock", close=10.05, amount=1005.0),
            )

            checks = service.compare_a_share_daily_bars("000001", "20260101", "20260131")

            self.assertTrue(checks)
            self.assertTrue(all("field_name" in check for check in checks))
            self.assertTrue(any(check["field_name"] == "close" for check in checks))

    def test_compare_daily_bars_marks_large_difference_failed(self):
        left = [FakeDailySource("left", close=12.0).get_a_share_daily_bars("000001", "", "")[0]]
        right = [FakeDailySource("right", close=10.0).get_a_share_daily_bars("000001", "", "")[0]]

        checks = compare_daily_bars("000001", "left", "right", left, right, tolerance_pct=1.0)
        close_check = next(check for check in checks if check["field_name"] == "close")
        self.assertEqual(close_check["passed"], 0)

    def test_fetch_and_store_etf_daily_bars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_etf_daily_bars("510300", "20260101", "20260131")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_fund_exchange_daily_bars("510300")[0]["close"], 4.05)

    def test_fetch_and_store_open_fund_nav_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            result = service.fetch_and_store_open_fund_nav_history("000001")

            self.assertEqual(result.fetched_count, 1)
            self.assertEqual(result.stored_count, 1)
            self.assertEqual(store.get_fund_nav_daily("000001")[0]["accumulated_nav"], 2.0)

    def test_fetch_and_store_basic_info_and_fund_estimates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            stock_result = service.fetch_and_store_stock_basic_info("000001")
            fund_result = service.fetch_and_store_fund_basic_info()
            estimate_result = service.fetch_and_store_fund_value_estimates()

            self.assertEqual(stock_result.stored_count, 1)
            self.assertEqual(fund_result.stored_count, 1)
            self.assertEqual(estimate_result.stored_count, 1)
            self.assertEqual(store.get_security("sz.000001")["name"], "平安银行")
            self.assertEqual(store.get_security("000001")["name"], "测试基金")
            self.assertEqual(store.get_fund_estimates("000001")[0]["estimated_nav"], 1.1)

    def test_fetch_and_store_fund_status_scale_and_portfolio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            service = DataFetchService(
                store=store,
                akshare_source=FakeDailySource("akshare"),
                baostock_source=FakeDailySource("baostock"),
            )

            status_result = service.fetch_and_store_fund_purchase_status()
            scale_result = service.fetch_and_store_fund_scale_records()
            holding_result = service.fetch_and_store_fund_portfolio_records("000001", "2026", "stock")

            self.assertEqual(status_result.stored_count, 1)
            self.assertEqual(scale_result.stored_count, 1)
            self.assertEqual(holding_result.stored_count, 1)
            self.assertEqual(store.get_fund_purchase_status("000001")[0]["redemption_status"], "开放赎回")
            self.assertEqual(
                store.get_fund_report_records("000001", report_type="fund_portfolio_hold_em")[0]["item_name"],
                "平安银行",
            )


if __name__ == "__main__":
    unittest.main()
