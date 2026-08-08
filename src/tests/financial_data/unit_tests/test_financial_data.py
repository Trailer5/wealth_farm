import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.data_access import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
    SQLiteDataAccessRepository,
)
from src.data_store import SQLiteDataStore
from src.financial_data import (
    FinancialNormalizationError,
    SQLiteFinancialDataRepository,
    normalize_financial_records,
    ttm_input_periods,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class FakeFinancialFetchService:
    def __init__(self):
        self.calls = []
        self.fail = False

    def fetch_stock_financial_records(
        self,
        symbol,
        dataset,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(
            (symbol, dataset, source, timeout_seconds)
        )
        if self.fail:
            raise RuntimeError("financial provider unavailable")
        field_by_dataset = {
            DATASET_STOCK_INCOME_STATEMENTS: (
                "营业总收入",
                "1000000",
            ),
            DATASET_STOCK_BALANCE_SHEETS: (
                "资产总计",
                "2000000",
            ),
            DATASET_STOCK_CASH_FLOW_STATEMENTS: (
                "经营活动产生的现金流量净额",
                "300000",
            ),
            DATASET_STOCK_FINANCIAL_INDICATORS: (
                "净资产收益率",
                "10",
            ),
        }
        field_name, value = field_by_dataset[dataset]
        return [
            {
                "股票代码": symbol,
                "报告期": "2024-12-31",
                "公告日期": "2025-03-31",
                field_name: value,
            },
            {
                "股票代码": symbol,
                "报告期": "2025-03-31",
                "公告日期": "2025-04-30",
                field_name: value,
            },
        ]


class FinancialDataTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "financial.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.stock = self.master_data.register_asset(
            Asset(
                asset_id="asset:stock",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="600519",
        )
        self.etf = self.master_data.register_asset(
            Asset(
                asset_id="asset:etf",
                asset_type=ASSET_TYPE_EXCHANGE_FUND,
                standard_symbol="518880",
                display_name="黄金ETF",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="518880",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normalizer_preserves_raw_and_normalized_values(self):
        points = normalize_financial_records(
            asset_id=self.stock.asset_id,
            dataset=DATASET_STOCK_FINANCIAL_INDICATORS,
            records=[
                {
                    "股票代码": "600519",
                    "REPORT_DATE": "2025-12-31 00:00:00",
                    "NOTICE_DATE": "2026-03-31",
                    "净资产收益率": "10",
                    "每股收益": "2.50",
                }
            ],
            source="akshare",
            fetched_at="2026-04-01T00:00:00+00:00",
        )
        by_name = {point.field_name: point for point in points}

        self.assertEqual(
            by_name["roe"].normalized_value,
            Decimal("0.1"),
        )
        self.assertEqual(by_name["roe"].raw_value, "10")
        per_share = next(
            point
            for point in points
            if point.field_label == "每股收益"
        )
        self.assertEqual(per_share.unit, "CNY_per_share")
        self.assertEqual(per_share.currency, "CNY")

    def test_bank_fields_are_normalized_as_ratios(self):
        points = normalize_financial_records(
            asset_id=self.stock.asset_id,
            dataset=DATASET_STOCK_FINANCIAL_INDICATORS,
            records=[
                {
                    "REPORT_DATE": "2026-03-31",
                    "NOTICE_DATE": "2026-04-25",
                    "BPS": "20.00",
                    "NEWCAPITALADER": "13.46",
                    "NET_INTEREST_MARGIN": "1.79",
                    "NONPERLOAN": "1.05",
                }
            ],
            source="akshare",
        )
        by_name = {point.field_name: point for point in points}

        self.assertEqual(
            by_name["book_value_per_share"].unit,
            "CNY_per_share",
        )
        self.assertEqual(
            by_name["capital_adequacy"].normalized_value,
            Decimal("0.1346"),
        )
        self.assertEqual(
            by_name["net_interest_margin"].normalized_value,
            Decimal("0.0179"),
        )
        self.assertEqual(
            by_name[
                "non_performing_loan_ratio"
            ].normalized_value,
            Decimal("0.0105"),
        )

    def test_normalizer_requires_publication_date(self):
        with self.assertRaisesRegex(
            FinancialNormalizationError,
            "requires published date",
        ):
            normalize_financial_records(
                asset_id=self.stock.asset_id,
                dataset=DATASET_STOCK_INCOME_STATEMENTS,
                records=[
                    {
                        "报告期": "2025-12-31",
                        "营业收入": "100",
                    }
                ],
                source="akshare",
            )

    def test_ttm_period_components_are_explicit(self):
        components = ttm_input_periods("2026-03-31")

        self.assertEqual(
            components.previous_annual_period,
            "2025-12-31",
        )
        self.assertEqual(
            components.prior_comparable_period,
            "2025-03-31",
        )
        annual = ttm_input_periods("2025-12-31")
        self.assertIsNone(annual.prior_comparable_period)

    def test_repository_preserves_revisions_and_as_of_cutoff(self):
        repository = SQLiteFinancialDataRepository(self.db_path)
        repository.initialize()
        point = normalize_financial_records(
            asset_id=self.stock.asset_id,
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            records=[
                {
                    "报告期": "2024-12-31",
                    "公告日期": "2025-03-31",
                    "营业总收入": "100",
                }
            ],
            source="akshare",
            fetched_at="2025-04-01T00:00:00+00:00",
        )[0]
        first = repository.save_points([point])
        unchanged = repository.save_points([point])
        revised = repository.save_points(
            [
                replace(
                    point,
                    normalized_value=Decimal("110"),
                    raw_value="110",
                    published_at="2025-04-15",
                )
            ]
        )

        self.assertEqual(first[0].revision, 1)
        self.assertEqual(unchanged[0].revision, 1)
        self.assertEqual(revised[0].revision, 2)
        before_revision = repository.list_points(
            asset_id=self.stock.asset_id,
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            start_date="2024-01-01",
            end_date="2024-12-31",
            as_of_date="2025-04-01",
        )
        after_revision = repository.list_points(
            asset_id=self.stock.asset_id,
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            start_date="2024-01-01",
            end_date="2024-12-31",
            as_of_date="2025-04-30",
        )
        self.assertEqual(
            before_revision[0].normalized_value,
            Decimal("100"),
        )
        self.assertEqual(
            after_revision[0].normalized_value,
            Decimal("110"),
        )

    def test_unified_access_fetches_caches_and_filters_as_of(self):
        store = SQLiteDataStore(self.db_path)
        fetch_service = FakeFinancialFetchService()
        service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch_service,
            financial_repository=SQLiteFinancialDataRepository(
                self.db_path
            ),
        )
        request = DataAccessRequest(
            asset=AssetInput(asset_id=self.stock.asset_id),
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            start_date="2024-01-01",
            end_date="2025-12-31",
            as_of_date="2025-04-01",
            allow_remote=True,
            provider_timeout_seconds=10,
        )
        fetched = service.get(request)
        cached = service.get(request)

        self.assertTrue(fetched.fetched_remote)
        self.assertTrue(fetched.complete)
        self.assertEqual(len(fetched.records), 1)
        self.assertFalse(cached.fetched_remote)
        self.assertEqual(len(fetch_service.calls), 1)
        self.assertEqual(
            fetched.records[0]["report_period"],
            "2024-12-31",
        )

    def test_stale_financial_data_survives_remote_failure(self):
        store = SQLiteDataStore(self.db_path)
        fetch_service = FakeFinancialFetchService()
        service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch_service,
        )
        initial_request = DataAccessRequest(
            asset=AssetInput(asset_id=self.stock.asset_id),
            dataset=DATASET_STOCK_INCOME_STATEMENTS,
            start_date="2024-01-01",
            end_date="2025-12-31",
            as_of_date="2025-05-01",
            allow_remote=True,
        )
        service.get(initial_request)
        fetch_service.fail = True
        stale = service.get(
            replace(
                initial_request,
                max_age_seconds=0,
                allow_stale_on_error=True,
            )
        )

        self.assertTrue(stale.stale)
        self.assertFalse(stale.complete)
        self.assertEqual(len(stale.records), 2)
        self.assertIn(
            "akshare",
            stale.metadata["provider_errors"],
        )

    def test_financial_dataset_rejects_etf_asset(self):
        service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(
                SQLiteDataStore(self.db_path)
            ),
            fetch_service=FakeFinancialFetchService(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires asset_type stock",
        ):
            service.get(
                DataAccessRequest(
                    asset=AssetInput(asset_id=self.etf.asset_id),
                    dataset=DATASET_STOCK_INCOME_STATEMENTS,
                    start_date="2024-01-01",
                    end_date="2025-12-31",
                    as_of_date="2026-01-01",
                )
            )

    def test_financial_request_requires_as_of_date(self):
        service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(
                SQLiteDataStore(self.db_path)
            ),
            fetch_service=FakeFinancialFetchService(),
        )
        with self.assertRaisesRegex(
            ValueError,
            "require as_of_date",
        ):
            service.get(
                DataAccessRequest(
                    asset=AssetInput(
                        asset_id=self.stock.asset_id
                    ),
                    dataset=DATASET_STOCK_INCOME_STATEMENTS,
                    start_date="2024-01-01",
                    end_date="2025-12-31",
                )
            )

    def test_financial_bundle_contains_four_datasets(self):
        service = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(
                SQLiteDataStore(self.db_path)
            ),
            fetch_service=FakeFinancialFetchService(),
        )
        bundle = service.get_financial_bundle(
            asset_id=self.stock.asset_id,
            start_date="2024-01-01",
            end_date="2025-12-31",
            as_of_date="2025-05-01",
        )

        self.assertTrue(bundle.complete)
        self.assertEqual(len(bundle.records_by_dataset), 4)
        self.assertEqual(bundle.sources, ("akshare",))


if __name__ == "__main__":
    unittest.main()
