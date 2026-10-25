import math
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.fundamental_analysis import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    MetricStatus,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.peer_analysis import (
    IndustryAssignment,
    PeerAnalysisService,
    PeerComparisonStatus,
    PeerSet,
    PeerSetMember,
    SQLitePeerAnalysisRepository,
)


class PeerAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "peers.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.target = self._asset(
            "asset:maotai",
            "600519",
            "贵州茅台",
        )
        self.peer1 = self._asset(
            "asset:wuliangye",
            "000858",
            "五粮液",
        )
        self.peer2 = self._asset(
            "asset:luzhou",
            "000568",
            "泸州老窖",
        )
        self.peer3 = self._asset(
            "asset:fenjiu",
            "600809",
            "山西汾酒",
        )
        self.excluded = self._asset(
            "asset:beer",
            "600600",
            "青岛啤酒",
        )
        self.etf = self.master_data.register_asset(
            Asset(
                asset_id="asset:etf",
                asset_type=ASSET_TYPE_EXCHANGE_FUND,
                standard_symbol="510300",
                display_name="沪深300ETF",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test:etf",
            raw_symbol="510300",
        )
        self.metric_repository = (
            SQLiteFundamentalMetricRepository(self.db_path)
        )
        self.peer_repository = SQLitePeerAnalysisRepository(
            self.db_path
        )
        self.service = PeerAnalysisService(
            master_data=self.master_data,
            metric_repository=self.metric_repository,
            peer_repository=self.peer_repository,
        )
        self.service.assign_industry(
            IndustryAssignment(
                asset_id=self.target.asset_id,
                industry_code="baijiu",
                industry_name="白酒",
                classification_source="manual_review",
                classification_version="v1",
                effective_date="2026-01-01",
            )
        )
        self.peer_set = PeerSet(
            peer_set_id="peer-set:baijiu:v1",
            target_asset_id=self.target.asset_id,
            name="贵州茅台白酒可比组",
            source="manual_review",
            version="v1",
            effective_date="2026-01-01",
            members=(
                PeerSetMember(
                    self.peer1.asset_id,
                    True,
                    "全国高端白酒龙头",
                ),
                PeerSetMember(
                    self.peer2.asset_id,
                    True,
                    "全国性浓香白酒",
                ),
                PeerSetMember(
                    self.peer3.asset_id,
                    True,
                    "次高端白酒与全国化扩张",
                ),
                PeerSetMember(
                    self.excluded.asset_id,
                    False,
                    "啤酒商业模式与利润结构不可比",
                ),
            ),
        )
        self.service.save_peer_set(self.peer_set)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _asset(self, asset_id, symbol, name):
        return self.master_data.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol=symbol,
                display_name=name,
                market="cn_a",
                exchange=(
                    "sh" if symbol.startswith("6") else "sz"
                ),
            ),
            source=f"unit-test:{asset_id}",
            raw_symbol=symbol,
        )

    def test_public_service_returns_effective_industry(self):
        industry = self.service.get_industry(
            asset_id=self.target.asset_id,
            as_of_date="2026-08-23",
        )

        self.assertIsNotNone(industry)
        assert industry is not None
        self.assertEqual(industry.industry_name, "白酒")

    def test_peer_statistics_and_reasons_are_preserved(self):
        self.metric_repository.upsert_results(
            [
                _metric(self.target.asset_id, "pe_ttm", "20"),
                _metric(self.peer1.asset_id, "pe_ttm", "10"),
                _metric(self.peer2.asset_id, "pe_ttm", "20"),
                _metric(self.peer3.asset_id, "pe_ttm", "30"),
            ]
        )

        bundle = self.service.compare(
            peer_set_id=self.peer_set.peer_set_id,
            as_of_date="2026-04-01",
            metric_names=("pe_ttm",),
        )
        comparison = bundle.comparisons[0]

        self.assertEqual(
            comparison.status,
            PeerComparisonStatus.AVAILABLE,
        )
        self.assertEqual(comparison.sample_count, 3)
        self.assertEqual(comparison.median, Decimal("20"))
        self.assertEqual(comparison.mean, Decimal("20"))
        self.assertAlmostEqual(
            float(comparison.standard_deviation),
            math.sqrt(Decimal("200") / Decimal("3")),
        )
        self.assertEqual(comparison.minimum, Decimal("10"))
        self.assertEqual(comparison.maximum, Decimal("30"))
        self.assertEqual(
            comparison.target_percentile,
            Decimal("2") / Decimal("3"),
        )
        self.assertEqual(
            comparison.target_vs_median,
            Decimal("0"),
        )
        self.assertIn(
            self.excluded.asset_id,
            comparison.excluded_assets,
        )
        self.assertEqual(bundle.industry.industry_name, "白酒")
        self.assertEqual(
            bundle.chart_data["pe_ttm"]["labels"][0],
            "贵州茅台",
        )

    def test_incompatible_units_and_periods_are_excluded(self):
        self.metric_repository.upsert_results(
            [
                _metric(self.target.asset_id, "roe", "0.3", unit="ratio"),
                _metric(self.peer1.asset_id, "roe", "0.2", unit="ratio"),
                _metric(
                    self.peer2.asset_id,
                    "roe",
                    "20",
                    unit="percent",
                ),
                _metric(
                    self.peer3.asset_id,
                    "roe",
                    "0.25",
                    unit="ratio",
                    report_period="2025-09-30",
                ),
            ]
        )

        comparison = self.service.compare(
            peer_set_id=self.peer_set.peer_set_id,
            as_of_date="2026-04-01",
            metric_names=("roe",),
        ).comparisons[0]

        self.assertEqual(
            comparison.status,
            PeerComparisonStatus.INSUFFICIENT_SAMPLE,
        )
        self.assertEqual(comparison.sample_count, 1)
        self.assertIsNone(comparison.target_percentile)
        self.assertIn(
            "unit mismatch",
            comparison.excluded_assets[self.peer2.asset_id],
        )
        self.assertIn(
            "report_period mismatch",
            comparison.excluded_assets[self.peer3.asset_id],
        )

    def test_missing_target_metric_is_incompatible(self):
        comparison = self.service.compare(
            peer_set_id=self.peer_set.peer_set_id,
            as_of_date="2026-04-01",
            metric_names=("pe_ttm",),
        ).comparisons[0]

        self.assertEqual(
            comparison.status,
            PeerComparisonStatus.INCOMPATIBLE,
        )
        self.assertIsNone(comparison.target)

    def test_latest_metric_on_or_before_cutoff_is_used(self):
        self.metric_repository.upsert_results(
            [
                _metric(
                    self.target.asset_id,
                    "pe_ttm",
                    "25",
                    as_of_date="2025-12-31",
                ),
                _metric(
                    self.target.asset_id,
                    "pe_ttm",
                    "20",
                    as_of_date="2026-03-31",
                ),
                _metric(self.peer1.asset_id, "pe_ttm", "10"),
                _metric(self.peer2.asset_id, "pe_ttm", "20"),
            ]
        )
        comparison = self.service.compare(
            peer_set_id=self.peer_set.peer_set_id,
            as_of_date="2026-04-01",
            metric_names=("pe_ttm",),
        ).comparisons[0]

        self.assertEqual(comparison.target.value, Decimal("20"))

    def test_non_stock_member_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires stock assets",
        ):
            self.service.save_peer_set(
                PeerSet(
                    peer_set_id="peer-set:broken",
                    target_asset_id=self.target.asset_id,
                    name="错误集合",
                    source="unit-test",
                    version="v1",
                    effective_date="2026-01-01",
                    members=(
                        PeerSetMember(
                            self.etf.asset_id,
                            True,
                            "错误类型",
                        ),
                    ),
                )
            )


def _metric(
    asset_id,
    metric_name,
    value,
    *,
    unit="multiple",
    report_period="2025-12-31",
    as_of_date="2026-03-31",
):
    decimal_value = Decimal(value)
    return FundamentalMetricResult(
        asset_id=asset_id,
        metric_name=metric_name,
        as_of_date=as_of_date,
        report_period=report_period,
        status=MetricStatus.AVAILABLE,
        value=decimal_value,
        unit=unit,
        formula="unit-test",
        numerator=decimal_value,
        denominator=Decimal("1"),
        reason=None,
        calculation_version=FUNDAMENTAL_CALCULATION_VERSION,
        parameters={"currency": "CNY"},
        inputs=(),
        computed_at="2026-03-31T00:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
