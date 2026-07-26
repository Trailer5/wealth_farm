import unittest

from src.data_access import DataAccessResponse
from src.data_quality import (
    QUALITY_FAIL,
    QUALITY_WARN,
    DataQualityService,
    SQLiteDataQualityRepository,
)
from src.master_data import ASSET_TYPE_EXCHANGE_FUND, Asset


class DataQualityServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = DataQualityService(SQLiteDataQualityRepository(":memory:"))

    def test_valid_daily_bars_can_be_published(self):
        summary = self.service.evaluate_daily_bars(
            task_name="valid-bars",
            records=[_bar()],
            expected_start="2026-07-01",
            expected_end="2026-07-01",
            coverage_start="2026-07-01",
            coverage_end="2026-07-01",
            asset_id="asset:test",
            source="unit-test",
        )

        self.assertEqual(summary.pass_count, 6)
        self.assertEqual(summary.warn_count, 0)
        self.assertEqual(summary.fail_count, 0)
        self.assertTrue(summary.can_publish)
        self.assertTrue(self.service.can_publish(summary.run.run_id))

    def test_coverage_gap_is_warning_and_does_not_block_publish(self):
        summary = self.service.evaluate_daily_bars(
            task_name="coverage-warning",
            records=[_bar()],
            expected_start="2026-07-01",
            expected_end="2026-07-31",
            coverage_start="2026-07-01",
            coverage_end="2026-07-24",
            asset_id="asset:test",
        )

        warning = next(check for check in summary.checks if check.check_name == "coverage")
        self.assertEqual(warning.level, QUALITY_WARN)
        self.assertEqual(summary.warn_count, 1)
        self.assertTrue(summary.can_publish)

    def test_empty_result_blocks_publish(self):
        summary = self.service.evaluate_daily_bars(
            task_name="empty-bars",
            records=[],
            expected_start="2026-07-01",
            expected_end="2026-07-01",
            coverage_start=None,
            coverage_end=None,
            asset_id="asset:test",
        )

        self.assertGreater(summary.fail_count, 0)
        self.assertFalse(summary.can_publish)

    def test_duplicate_and_invalid_ohlc_block_publish(self):
        invalid = _bar(high=9.0, low=10.0, close=10.5)
        summary = self.service.evaluate_daily_bars(
            task_name="invalid-bars",
            records=[invalid, dict(invalid)],
            expected_start="2026-07-01",
            expected_end="2026-07-01",
            coverage_start="2026-07-01",
            coverage_end="2026-07-01",
            asset_id="asset:test",
        )

        failed_names = {
            check.check_name
            for check in summary.checks
            if check.level == QUALITY_FAIL
        }
        self.assertIn("duplicate_keys", failed_names)
        self.assertIn("ohlc", failed_names)
        self.assertFalse(summary.can_publish)

    def test_unresolved_asset_blocks_publish(self):
        summary = self.service.evaluate_daily_bars(
            task_name="unresolved-asset",
            records=[_bar()],
            expected_start="2026-07-01",
            expected_end="2026-07-01",
            coverage_start="2026-07-01",
            coverage_end="2026-07-01",
            asset_id=None,
            asset_needs_review=True,
        )

        failed = next(
            check for check in summary.checks if check.check_name == "asset_resolved"
        )
        self.assertEqual(failed.level, QUALITY_FAIL)
        self.assertFalse(summary.can_publish)

    def test_data_access_response_can_be_evaluated(self):
        asset = Asset(
            asset_id="asset:gold",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            standard_symbol="518880",
            display_name="黄金ETF",
            market="cn_a",
            exchange="sh",
        )
        response = DataAccessResponse(
            asset=asset,
            dataset="fund_exchange_daily_bars",
            records=(_bar(symbol="518880", source="baostock:test"),),
            sources=("baostock:test",),
            coverage_start="2026-07-01",
            coverage_end="2026-07-01",
            complete=True,
            fetched_remote=True,
        )

        summary = self.service.evaluate_data_access_response(
            task_name="gold-etf",
            response=response,
            expected_start="2026-07-01",
            expected_end="2026-07-01",
        )

        self.assertEqual(summary.run.asset_id, "asset:gold")
        self.assertEqual(summary.run.record_count, 1)
        self.assertTrue(summary.can_publish)

    def test_failed_task_cannot_be_published(self):
        run = self.service.start_task(
            "failed-fetch",
            dataset="daily_bars",
            source="akshare",
            asset_id="asset:test",
        )

        failed = self.service.fail_task(run.run_id, "remote disconnected")
        summary = self.service.summary(run.run_id)

        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error, "remote disconnected")
        self.assertFalse(summary.can_publish)

    def test_recent_tasks_are_listed_newest_first(self):
        first = self.service.start_task("first")
        self.service.complete_task(first.run_id, 1)
        second = self.service.start_task("second")
        self.service.fail_task(second.run_id, "failed")

        tasks = self.service.list_tasks(limit=1)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].run_id, second.run_id)


def _bar(
    *,
    symbol="600519",
    trade_date="2026-07-01",
    open=10.0,
    high=11.0,
    low=9.5,
    close=10.5,
    source="unit-test",
):
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
        "amount": 10500.0,
        "source": source,
    }


if __name__ == "__main__":
    unittest.main()
