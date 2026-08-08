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
)
from src.data_store import SQLiteDataStore


class SQLiteDataStoreTest(unittest.TestCase):
    def test_default_database_path_uses_data_database(self):
        self.assertEqual(
            SQLiteDataStore().db_path.as_posix(),
            "data/database/wealth_farm.sqlite3",
        )

    def test_initialize_records_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()

            versions = store.get_schema_versions()

            self.assertEqual(
                versions[0]["version"],
                SQLiteDataStore.SCHEMA_VERSION,
            )
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

    def test_upsert_and_read_fund_estimate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SQLiteDataStore(Path(temp_dir) / "test.sqlite3")
            store.initialize()
            estimate = FundEstimate(
                symbol="000001",
                estimate_date="2026-01-05",
                estimate_time="15:00",
                estimated_nav=1.1,
                estimated_growth_rate=0.5,
                source="unit-test",
                raw={"x": 1},
            )

            self.assertEqual(store.upsert_fund_estimates([estimate]), 1)
            rows = store.get_fund_estimates("000001")
            self.assertEqual(rows[0]["estimated_nav"], 1.1)

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

            self.assertEqual(
                store.upsert_fund_purchase_status([status]),
                1,
            )
            self.assertEqual(
                store.upsert_fund_report_records([record]),
                1,
            )

            statuses = store.get_fund_purchase_status("000001")
            records = store.get_fund_report_records("000001")
            self.assertEqual(
                statuses[0]["purchase_status"],
                "开放申购",
            )
            self.assertEqual(records[0]["item_value"], 1000.0)

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


if __name__ == "__main__":
    unittest.main()
