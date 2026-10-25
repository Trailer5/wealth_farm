import unittest
from decimal import Decimal

from src.fund_research import (
    DisclosureExtractionStatus,
    OpenFundHistoricalSummaryCalculator,
    OpenFundHolding,
    OpenFundIndustryAllocation,
    OpenFundPeriodicSnapshot,
    OpenFundProfileSnapshot,
    OpenFundShareFlow,
)


class OpenFundHistoricalSummaryCalculatorTest(unittest.TestCase):
    def test_derives_flow_concentration_exposure_fee_and_scale(self):
        values = OpenFundHistoricalSummaryCalculator().calculate(
            asset_id="fund:test",
            as_of_date="2026-08-22",
            share_class_code="000001",
            profile_snapshot=_profile(),
            periodic_snapshot=_periodic(),
            scale_records=(
                _scale("2025-12-31", 100),
                _scale("2026-06-30", 120),
            ),
        )

        by_name = {value.factor_name: value for value in values}
        self.assertEqual(
            by_name["open_fund_net_share_flow"].value,
            -10.0,
        )
        self.assertAlmostEqual(
            by_name["open_fund_net_share_flow_rate"].value,
            -0.1,
        )
        self.assertAlmostEqual(
            by_name["open_fund_top10_concentration"].value,
            0.3,
        )
        self.assertAlmostEqual(
            by_name["open_fund_hong_kong_exposure"].value,
            0.25,
        )
        self.assertAlmostEqual(
            by_name[
                "open_fund_top3_industry_concentration"
            ].value,
            0.45,
        )
        self.assertAlmostEqual(
            by_name["open_fund_ongoing_fee_rate"].value,
            0.012,
        )
        self.assertEqual(
            by_name["open_fund_scale_change"].value,
            20.0,
        )
        self.assertAlmostEqual(
            by_name["open_fund_scale_change_rate"].value,
            0.2,
        )


def _profile():
    return OpenFundProfileSnapshot(
        fund_asset_id="fund:test",
        effective_date="2026-06-01",
        announcement_date="2026-06-02",
        fund_name="测试基金",
        share_class_name="测试基金A",
        share_class_code="000001",
        manager_name="测试公司",
        custodian_name="测试银行",
        fund_managers=(),
        benchmark_text="测试指数收益率×100%",
        management_fee_rate=Decimal("0.01"),
        custody_fee_rate=Decimal("0.002"),
        sales_service_fee_rate=Decimal("0"),
        source_document_id="profile-doc",
        source_title="测试产品资料概要",
        source_url="https://example.test/profile.pdf",
        source="official-test",
        parser_version="unit-test",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
    )


def _periodic():
    return OpenFundPeriodicSnapshot(
        fund_asset_id="fund:test",
        report_period_start="2026-04-01",
        report_period_end="2026-06-30",
        announcement_date="2026-07-21",
        share_classes=(),
        share_flows=(
            OpenFundShareFlow(
                share_class_name="测试基金A",
                share_class_code="000001",
                opening_shares=Decimal("100"),
                subscriptions=Decimal("20"),
                redemptions=Decimal("30"),
                ending_shares=Decimal("90"),
                page_number=10,
            ),
        ),
        asset_allocations=(),
        industry_allocations=(
            _industry("医疗保健", "0.15", "hong_kong"),
            _industry("制造业", "0.20", "domestic"),
            _industry("信息技术", "0.10", "hong_kong"),
        ),
        top_holdings=(
            _holding(1, "0.10"),
            _holding(2, "0.20"),
        ),
        source_document_id="periodic-doc",
        source_title="测试季度报告",
        source_url="https://example.test/periodic.pdf",
        source="official-test",
        parser_version="unit-test",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
    )


def _industry(name, ratio, market_scope):
    return OpenFundIndustryAllocation(
        category=name,
        fair_value=Decimal("100"),
        net_asset_ratio=Decimal(ratio),
        market_scope=market_scope,
        portfolio_scope="all",
        page_number=8,
    )


def _holding(rank, ratio):
    return OpenFundHolding(
        rank=rank,
        security_code=f"00000{rank}",
        security_name=f"股票{rank}",
        quantity=Decimal("100"),
        fair_value=Decimal("100"),
        net_asset_ratio=Decimal(ratio),
        page_number=9,
    )


def _scale(value_date, value):
    return {
        "report_date": value_date,
        "item_name": "测试基金A",
        "item_value": value,
        "source": "unit-test:scale",
    }


if __name__ == "__main__":
    unittest.main()
