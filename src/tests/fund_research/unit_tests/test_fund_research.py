import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from src.data_access import (
    DATASET_FUND_STATUS,
    DATASET_OPEN_FUND_NAV,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
    SQLiteDataAccessRepository,
)
from src.data_src.models import (
    DailyBar,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
)
from src.data_store import SQLiteDataStore
from src.factor_engineering import SQLiteFactorRepository
from src.fund_research import (
    ETFExpenseSchedule,
    ETFMarketSnapshot,
    ETF_METRIC_VERSION,
    ExchangeFundMetricService,
    FundRelationType,
    FundResearchService,
    SQLiteFundResearchRepository,
    percent_to_ratio,
    official_etf_expense_schedule,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_workflow import default_profile_registry


class FakeFundFetchService:
    def __init__(self):
        self.calls = []

    def fetch_open_fund_nav_history(
        self,
        symbol,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(("nav", symbol))
        return [
            FundNav(
                symbol=symbol,
                nav_date="2026-06-30",
                unit_nav=1.1,
                accumulated_nav=2.2,
                daily_growth_rate=0.5,
                source="akshare:nav",
                estimated=False,
                raw={"provider_date": date(2026, 6, 30)},
            )
        ]

    def fetch_fund_value_estimates(
        self,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(("estimate", source))
        return [
            FundEstimate(
                symbol="000001",
                estimate_date="2026-06-30",
                estimate_time="14:30",
                estimated_nav=1.11,
                estimated_growth_rate=0.6,
                source="akshare:estimate",
            )
        ]

    def fetch_fund_purchase_status(
        self,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(("status", source))
        return [
            FundPurchaseStatus(
                symbol="000001",
                name="测试基金",
                fund_type="股票型基金",
                purchase_status="开放申购",
                redemption_status="开放赎回",
                next_open_date=None,
                min_purchase_amount=10,
                daily_limit_amount=10000,
                fee_rate=0.15,
                source="akshare:status",
            )
        ]

    def fetch_fund_scale_records(
        self,
        fund_type="股票型基金",
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(("scale", fund_type))
        return [
            FundReportRecord(
                symbol="000001",
                report_type="fund_scale_open_sina",
                report_date="2026-06-30",
                item_name="测试基金",
                item_value=1000,
                ratio=None,
                source="akshare:scale",
            )
        ]

    def fetch_fund_allocation_records(
        self,
        symbol,
        year,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls.append(("allocation", year))
        return [
            FundReportRecord(
                symbol=symbol,
                report_type=(
                    "fund_portfolio_industry_allocation_em"
                ),
                report_date=f"{year}-06-30",
                item_name="制造业",
                item_value=300,
                ratio=30,
                source="akshare:allocation",
            )
        ]


class FundResearchTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "fund.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.open_fund = self._register(
            "asset:open",
            ASSET_TYPE_OPEN_FUND,
            "000001",
            "测试场外基金",
            "cn_fund",
            None,
        )
        self.stock_same_code = self._register(
            "asset:stock",
            ASSET_TYPE_STOCK,
            "000001",
            "平安银行",
            "cn_a",
            "sz",
        )
        self.gold_etf = self._register(
            "asset:gold-etf",
            ASSET_TYPE_EXCHANGE_FUND,
            "518880",
            "黄金ETF",
            "cn_a",
            "sh",
        )
        self.csi300_etf = self._register(
            "asset:csi300-etf",
            ASSET_TYPE_EXCHANGE_FUND,
            "510300",
            "沪深300ETF",
            "cn_a",
            "sh",
        )
        self.gold_index = self._register(
            "asset:gold-index",
            ASSET_TYPE_INDEX,
            "AU9999",
            "黄金现货基准",
            "cn_commodity",
            None,
        )
        self.store = SQLiteDataStore(self.db_path)
        self.data_repository = SQLiteDataAccessRepository(
            self.store
        )
        self.fetch_service = FakeFundFetchService()
        self.data_access = DataAccessService(
            master_data=self.master_data,
            repository=self.data_repository,
            fetch_service=self.fetch_service,
        )
        self.relation_repository = SQLiteFundResearchRepository(
            self.db_path
        )
        self.service = FundResearchService(
            master_data=self.master_data,
            data_access=self.data_access,
            repository=self.relation_repository,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _register(
        self,
        asset_id,
        asset_type,
        symbol,
        name,
        market,
        exchange,
    ):
        return self.master_data.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=name,
                market=market,
                exchange=exchange,
            ),
            source=f"unit-test:{asset_type}",
            raw_symbol=symbol,
        )

    def test_open_fund_bundle_keeps_official_and_estimated_nav_separate(
        self,
    ):
        self.service.add_manager_relation(
            fund_asset_id=self.open_fund.asset_id,
            manager_name="测试基金公司",
            source="unit-test",
            effective_date="2026-01-01",
        )
        bundle = self.service.get_open_fund_bundle(
            asset_id=self.open_fund.asset_id,
            start_date="2026-01-01",
            end_date="2026-12-31",
            as_of_date="2026-12-31",
            allocation_year="2026",
        )

        self.assertTrue(bundle.complete)
        self.assertEqual(len(bundle.nav_records), 1)
        self.assertEqual(len(bundle.estimate_records), 1)
        self.assertFalse(bundle.nav_records[0]["estimated"])
        self.assertIn("estimate_date", bundle.estimate_records[0])
        self.assertEqual(len(bundle.status_records), 1)
        self.assertEqual(len(bundle.scale_records), 1)
        self.assertEqual(len(bundle.allocation_records), 1)
        self.assertEqual(
            bundle.relations[0].relation_type,
            FundRelationType.MANAGER,
        )

    def test_fund_data_uses_cache_after_first_fetch(self):
        request = DataAccessRequest(
            asset=AssetInput(asset_id=self.open_fund.asset_id),
            dataset=DATASET_OPEN_FUND_NAV,
            start_date="2026-01-01",
            end_date="2026-12-31",
            as_of_date="2026-12-31",
            allow_remote=True,
        )
        first = self.data_access.get(request)
        second = self.data_access.get(request)

        self.assertTrue(first.fetched_remote)
        self.assertFalse(second.fetched_remote)
        self.assertEqual(
            self.fetch_service.calls.count(("nav", "000001")),
            1,
        )

    def test_status_has_observation_date_and_source(self):
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=self.open_fund.asset_id),
                dataset=DATASET_FUND_STATUS,
                start_date="2026-01-01",
                end_date="2026-12-31",
                as_of_date="2026-12-31",
            )
        )

        self.assertIn("observed_at", response.records[0])
        self.assertEqual(
            response.records[0]["source"],
            "akshare:status",
        )

    def test_etf_bundle_contains_daily_bars_and_underlying_index(self):
        self.store.upsert_fund_exchange_daily_bars(
            [
                DailyBar(
                    symbol="518880",
                    trade_date="2026-06-30",
                    open=8.0,
                    high=8.2,
                    low=7.9,
                    close=8.1,
                    volume=1000,
                    amount=8100,
                    source="unit-test",
                )
            ]
        )
        self.data_repository.mark_coverage(
            asset_id=self.gold_etf.asset_id,
            dataset="fund_exchange_daily_bars",
            start_date="2026-01-01",
            end_date="2026-12-31",
            source="unit-test",
        )
        self.service.add_underlying_index_relation(
            fund_asset_id=self.gold_etf.asset_id,
            index_asset_id=self.gold_index.asset_id,
            source="unit-test",
            effective_date="2026-01-01",
        )
        bundle = self.service.get_etf_bundle(
            asset_id=self.gold_etf.asset_id,
            start_date="2026-01-01",
            end_date="2026-12-31",
            as_of_date="2026-12-31",
            allow_remote=False,
        )

        self.assertTrue(bundle.complete)
        self.assertEqual(len(bundle.daily_bar_records), 1)
        self.assertEqual(
            bundle.relations[0].related_asset_id,
            self.gold_index.asset_id,
        )

    def test_etf_metrics_preserve_market_and_fee_sources(self):
        repository = SQLiteFactorRepository(self.db_path)
        metrics = ExchangeFundMetricService(
            master_data=self.master_data,
            factor_repository=repository,
        ).calculate_and_store(
            asset_id=self.csi300_etf.asset_id,
            as_of_date="2026-08-03",
            market=ETFMarketSnapshot(
                asset_id=self.csi300_etf.asset_id,
                as_of_date="2026-08-03",
                market_price=Decimal("4.619"),
                iopv=Decimal("4.6232"),
                source="akshare:fund_etf_spot_em",
            ),
            expenses=ETFExpenseSchedule(
                asset_id=self.csi300_etf.asset_id,
                effective_date="2024-11-22",
                management_fee_rate=Decimal("0.0015"),
                custody_fee_rate=Decimal("0.0005"),
                comprehensive_expense_ratio=Decimal("0.0020"),
                source="sse:fund_fee_amendment",
                source_url="https://example.test/official.pdf",
            ),
        )

        by_name = {
            value.factor_name: value for value in metrics.values
        }
        expected = (
            Decimal("4.619") - Decimal("4.6232")
        ) / Decimal("4.6232")
        self.assertAlmostEqual(
            by_name["premium_discount"].value,
            float(expected),
        )
        self.assertEqual(
            by_name["expense_ratio"].value,
            0.002,
        )
        self.assertEqual(
            by_name["expense_ratio"].calculation_version,
            ETF_METRIC_VERSION,
        )
        stored = repository.list_values(
            self.csi300_etf.asset_id
        )
        self.assertEqual(len(stored), 2)
        self.assertEqual(
            by_name["premium_discount"].parameters["unit"],
            "ratio",
        )
        self.assertIn(
            "source_url",
            by_name["expense_ratio"].parameters,
        )

    def test_etf_metric_units_and_boundaries_are_explicit(self):
        self.assertEqual(
            percent_to_ratio("0.09"),
            Decimal("0.0009"),
        )
        with self.assertRaisesRegex(ValueError, "iopv"):
            ETFMarketSnapshot(
                asset_id=self.gold_etf.asset_id,
                as_of_date="2026-08-03",
                market_price=Decimal("8.4"),
                iopv=Decimal("0"),
                source="unit-test",
            )
        with self.assertRaisesRegex(
            ValueError,
            "Comprehensive expense ratio",
        ):
            ETFExpenseSchedule(
                asset_id=self.gold_etf.asset_id,
                effective_date="2026-01-01",
                management_fee_rate=Decimal("0.005"),
                custody_fee_rate=Decimal("0.001"),
                comprehensive_expense_ratio=Decimal("0.005"),
                source="unit-test",
                source_url="https://example.test/official.pdf",
            )
        schedule = official_etf_expense_schedule(
            asset_id=self.gold_etf.asset_id,
            standard_symbol="518880",
        )
        self.assertEqual(
            schedule.comprehensive_expense_ratio,
            Decimal("0.0067"),
        )

    def test_relation_validation_rejects_non_funds_and_non_indices(self):
        with self.assertRaisesRegex(
            ValueError,
            "not a fund product",
        ):
            self.service.add_manager_relation(
                fund_asset_id=self.stock_same_code.asset_id,
                manager_name="错误管理人",
                source="unit-test",
                effective_date="2026-01-01",
            )
        with self.assertRaisesRegex(
            ValueError,
            "requires an index asset",
        ):
            self.service.add_underlying_index_relation(
                fund_asset_id=self.gold_etf.asset_id,
                index_asset_id=self.csi300_etf.asset_id,
                source="unit-test",
                effective_date="2026-01-01",
            )

    def test_stock_and_open_fund_with_same_code_stay_isolated(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires asset_type open_fund",
        ):
            self.data_access.get(
                DataAccessRequest(
                    asset=AssetInput(
                        asset_id=self.stock_same_code.asset_id
                    ),
                    dataset=DATASET_OPEN_FUND_NAV,
                    start_date="2026-01-01",
                    end_date="2026-12-31",
                )
            )

    def test_profiles_use_current_fund_dataset_names(self):
        registry = default_profile_registry()
        open_profile = registry.default_for_asset(self.open_fund)

        self.assertIn(
            DATASET_OPEN_FUND_NAV,
            open_profile.required_datasets,
        )
        self.assertNotIn(
            "fund_nav_daily",
            open_profile.required_datasets,
        )


if __name__ == "__main__":
    unittest.main()
