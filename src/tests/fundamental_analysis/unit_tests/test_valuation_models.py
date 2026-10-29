import unittest
from decimal import Decimal
from types import SimpleNamespace

from src.financial_data import DATASET_STOCK_INCOME_STATEMENTS
from src.fundamental_analysis import (
    FundamentalMetricResult,
    FundamentalSnapshot,
    MetricStatus,
    ValuationModelService,
    ValuationModelUnavailable,
    valuation_model_lines,
)
from src.fundamental_analysis.valuation_models import (
    cagr,
    implied_discount_rate,
    implied_growth,
    implied_revenue_growth,
    implied_roe,
    implied_years,
    present_value,
    revenue_value,
)
from src.master_data import ASSET_TYPE_STOCK, Asset
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_ORDINARY_COMPANY,
)
from src.research_workflow.valuation_context import collect_bank_valuation_model

ASSET = Asset(
    asset_id="asset:company",
    asset_type=ASSET_TYPE_STOCK,
    standard_symbol="600000",
    display_name="测试公司",
    market="cn_a",
    exchange="sh",
)


class ValuationMathTest(unittest.TestCase):
    def test_implied_growth_recovers_the_growth_used_to_price(self):
        value = present_value(100, 0.08, discount_rate=0.07, terminal_growth=0.02, years=10)
        growth = implied_growth(value, 100, discount_rate=0.07, terminal_growth=0.02, years=10)
        self.assertAlmostEqual(growth, 0.08, places=6)

    def test_zero_growth_with_zero_terminal_is_a_perpetuity(self):
        value = present_value(70, 0.0, discount_rate=0.07, terminal_growth=0.0, years=10)
        self.assertAlmostEqual(value, 1000, places=6)

    def test_out_of_range_or_negative_bases_have_no_implied_growth(self):
        self.assertIsNone(implied_growth(1e12, 1, discount_rate=0.07, terminal_growth=0.02, years=10))
        self.assertIsNone(implied_growth(1000, -5, discount_rate=0.07, terminal_growth=0.02, years=10))
        with self.assertRaises(ValueError):
            present_value(1, 0, discount_rate=0.02, terminal_growth=0.02, years=10)

    def test_implied_years_is_the_first_year_reaching_the_price(self):
        target = present_value(100, 0.15, discount_rate=0.07, terminal_growth=0.02, years=6)
        self.assertEqual(
            implied_years(target, 100, 0.15, discount_rate=0.07, terminal_growth=0.02), 6
        )
        self.assertIsNone(implied_years(target, 100, 0.01, discount_rate=0.07, terminal_growth=0.02))

    def test_revenue_reverse_dcf_recovers_growth(self):
        value = revenue_value(
            1000, 0.3, start_margin=-0.2, terminal_margin=0.1, discount_rate=0.07, terminal_growth=0.02, years=10
        )
        growth = implied_revenue_growth(
            value, 1000, start_margin=-0.2, terminal_margin=0.1, discount_rate=0.07, terminal_growth=0.02, years=10
        )
        self.assertAlmostEqual(growth, 0.3, places=6)

    def test_implied_return_recovers_the_discount_rate(self):
        value = present_value(100, 0.05, discount_rate=0.09, terminal_growth=0.02, years=10)
        rate = implied_discount_rate(value, 100, 0.05, terminal_growth=0.02, years=10)
        self.assertAlmostEqual(rate, 0.09, places=6)

    def test_pb_roe_and_cagr(self):
        self.assertAlmostEqual(implied_roe(1.0, discount_rate=0.07, terminal_growth=0.02), 0.07)
        self.assertAlmostEqual(implied_roe(0.5, discount_rate=0.07, terminal_growth=0.02), 0.045)
        self.assertAlmostEqual(cagr(100, 121, 2), 0.1)
        self.assertIsNone(cagr(-1, 121, 2))


class _Macro:
    def __init__(self, points):
        self.points = points

    def get(self, series_id, **kwargs):
        assert series_id == "cgb_10y"
        return SimpleNamespace(points=self.points)


class _DataAccess:
    def __init__(self, records):
        self.records = records
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return SimpleNamespace(records=tuple(self.records), sources=("unit-test",), complete=True)


def _income(years):
    rows = []
    for index, year in enumerate(years):
        factor = 1.1**index
        for name, base in (("revenue", 1000), ("earnings", 100), ("deducted_parent_net_profit", 90)):
            rows.append(
                {
                    "asset_id": ASSET.asset_id,
                    "dataset": DATASET_STOCK_INCOME_STATEMENTS,
                    "report_period": f"{year}-12-31",
                    "published_at": f"{year + 1}-03-31",
                    "period_kind": "annual",
                    "value_scope": "cumulative",
                    "field_name": name,
                    "field_label": name,
                    "normalized_value": str(round(base * factor, 6)),
                    "raw_value": str(round(base * factor, 6)),
                    "currency": "CNY",
                    "unit": "CNY",
                    "source": "unit-test",
                    "revision": 1,
                    "fetched_at": "2026-04-01T00:00:00+00:00",
                    "raw": {},
                }
            )
    return rows


def _snapshot(**fields):
    values = {
        "revenue": "1464.1",
        "earnings": "146.41",
        "recurring_earnings": "131.769",
        "free_cash_flow": "120",
        "cash_and_equivalents": "500",
        "total_debt": "200",
    }
    values.update(fields)
    return FundamentalSnapshot(
        asset_id=ASSET.asset_id,
        as_of_date="2026-04-30",
        report_period="2025-12-31",
        currency="CNY",
        market_price=Decimal("30"),
        shares_outstanding=Decimal("100"),
        fields={name: Decimal(value) for name, value in values.items() if value is not None},
        prior_fields={},
    )


class ValuationModelServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = ValuationModelService(
            data_access=_DataAccess(_income(range(2021, 2026))),
            macro_data=_Macro((SimpleNamespace(period_end="2026-04-29", value=2.0),)),
        )

    def test_ordinary_company_reverse_dcf_scenarios_and_sensitivity(self):
        model = self.service.evaluate_company(
            snapshot=_snapshot(), asset=ASSET, profile_id=PROFILE_ORDINARY_COMPANY, as_of_date="2026-04-30"
        )
        self.assertEqual(model["method"], "reverse_dcf")
        self.assertAlmostEqual(model["assumptions"]["discount_rate"], 0.07)
        self.assertEqual(model["market"]["market_cap"], 3000)
        self.assertEqual(model["market"]["net_debt"], -300)
        self.assertEqual(model["primary_basis"], "free_cash_flow")
        fcf, recurring = model["implied"]
        self.assertEqual(fcf["target"], "enterprise_value")
        self.assertEqual(fcf["target_value"], 2700)
        self.assertEqual(recurring["basis"], "recurring_earnings")
        recomputed = present_value(
            131.769, recurring["implied_growth"], discount_rate=0.07, terminal_growth=0.02, years=10
        )
        self.assertAlmostEqual(recomputed, 3000, delta=0.1)
        self.assertEqual(model["historical_growth"]["span_years"], 3)
        self.assertAlmostEqual(model["historical_growth"]["recurring_earnings_cagr"], 0.1, places=6)
        scenarios = model["scenarios"]
        self.assertAlmostEqual(scenarios["base"]["growth"], 0.1, places=6)
        self.assertLess(scenarios["pessimistic"]["per_share"], scenarios["base"]["per_share"])
        self.assertLess(scenarios["base"]["per_share"], scenarios["optimistic"]["per_share"])
        self.assertEqual(scenarios["anchor"]["basis"], "recurring_earnings")
        expected = present_value(131.769, 0.1, discount_rate=0.07, terminal_growth=0.02, years=10) / 100
        self.assertAlmostEqual(scenarios["base"]["per_share"], expected, places=3)
        grid = model["sensitivity"]["per_share"]
        self.assertEqual((len(grid), len(grid[0])), (3, 3))
        self.assertAlmostEqual(grid[1][1], scenarios["base"]["per_share"], places=3)
        self.assertGreater(grid[0][2], grid[2][0])
        self.assertIsNotNone(model["implied_high_growth_years"])
        implied_return = model["scenarios"]["anchor"]["implied_return"]
        self.assertAlmostEqual(
            present_value(131.769, 0.1, discount_rate=implied_return, terminal_growth=0.02, years=10), 3000, delta=0.1
        )
        names = [name for name, _ in valuation_model_lines(model)]
        self.assertIn("情景区间", names)
        self.assertIn("假设", names)
        self.assertTrue(any("不是目标价" in note for note in model["notes"]))

    def test_growth_anchor_is_clamped_and_overrides_are_recorded(self):
        service = ValuationModelService(
            data_access=_DataAccess(_fast_income()),
            macro_data=self.service.macro_data,
        )
        model = service.evaluate_company(
            snapshot=_snapshot(),
            asset=ASSET,
            profile_id=PROFILE_ORDINARY_COMPANY,
            as_of_date="2026-04-30",
            equity_risk_premium=0.06,
        )
        self.assertAlmostEqual(model["scenarios"]["anchor"]["growth_used"], 0.2)
        self.assertAlmostEqual(model["assumptions"]["discount_rate"], 0.08)
        self.assertEqual(model["assumptions"]["overrides"], ["equity_risk_premium"])

    def test_missing_debt_lines_count_as_zero_and_are_flagged(self):
        model = self.service.evaluate_company(
            snapshot=_snapshot(total_debt=None),
            asset=ASSET,
            profile_id=PROFILE_ORDINARY_COMPANY,
            as_of_date="2026-04-30",
        )
        self.assertEqual(model["market"]["net_debt"], -500)
        self.assertTrue(any("有息负债" in gap for gap in model["gaps"]))

    def test_loss_making_company_has_no_scenarios(self):
        model = self.service.evaluate_company(
            snapshot=_snapshot(free_cash_flow="-50", recurring_earnings="-10"),
            asset=ASSET,
            profile_id=PROFILE_ORDINARY_COMPANY,
            as_of_date="2026-04-30",
        )
        self.assertIsNone(model["primary_basis"])
        self.assertIsNone(model["scenarios"])
        self.assertTrue(any("反向 DCF" in gap for gap in model["gaps"]))

    def test_cyclical_company_uses_normalized_earnings(self):
        model = self.service.evaluate_company(
            snapshot=_snapshot(revenue="2000"),
            asset=ASSET,
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            as_of_date="2026-04-30",
        )
        self.assertEqual(model["method"], "normalized_reverse_dcf")
        normalized = model["implied"][0]
        self.assertEqual(normalized["basis"], "normalized_earnings")
        self.assertAlmostEqual(normalized["base_value"], 200, places=3)
        self.assertEqual(model["primary_basis"], "normalized_earnings")
        self.assertEqual(model["historical_growth"]["long_run_span_years"], 4)
        self.assertAlmostEqual(model["scenarios"]["anchor"]["growth_used"], 0.1, places=6)
        self.assertIn("周期股", model["scenarios"]["anchor"]["note"])
        self.assertIsNone(model["implied_high_growth_years"])

    def test_high_growth_unprofitable_uses_revenue(self):
        model = self.service.evaluate_company(
            snapshot=_snapshot(earnings="-100"),
            asset=ASSET,
            profile_id=PROFILE_HIGH_GROWTH_UNPROFITABLE,
            as_of_date="2026-04-30",
        )
        self.assertEqual(model["method"], "revenue_reverse_dcf")
        implied = model["implied_revenue_growth_by_terminal_margin"]
        self.assertEqual(list(implied), ["5%", "10%", "15%"])
        self.assertGreater(implied["5%"], implied["15%"])
        self.assertEqual(valuation_model_lines(model)[0][0], "隐含营收增速")

    def test_missing_risk_free_rate_is_reported(self):
        service = ValuationModelService(data_access=self.service.data_access, macro_data=_Macro(()))
        with self.assertRaises(ValuationModelUnavailable):
            service.evaluate_company(
                snapshot=_snapshot(), asset=ASSET, profile_id=PROFILE_ORDINARY_COMPANY, as_of_date="2026-04-30"
            )

    def test_bank_pb_roe_annualizes_interim_roe(self):
        pb = FundamentalMetricResult(
            asset_id=ASSET.asset_id,
            metric_name="pb",
            as_of_date="2026-08-31",
            report_period="2026-06-30",
            status=MetricStatus.AVAILABLE,
            value=Decimal("0.6"),
            unit="multiple",
            formula="market_price/book_value_per_share",
            numerator=Decimal("6"),
            denominator=Decimal("10"),
            reason=None,
            calculation_version="test",
            parameters={},
            inputs=(),
            computed_at="2026-08-31T00:00:00+00:00",
        )
        records = [
            {"field_name": "roe", "report_period": p, "published_at": pub, "normalized_value": v}
            for p, pub, v in (
                ("2024-12-31", "2025-03-30", "0.11"),
                ("2025-12-31", "2026-03-30", "0.10"),
                ("2026-06-30", "2026-08-20", "0.05"),
                ("2026-06-30", "2026-08-01", "0.04"),
            )
        ]
        model, gaps = collect_bank_valuation_model(
            self.service,
            as_of_date="2026-08-31",
            metrics=(pb,),
            indicator_records=records,
            allow_remote=False,
        )
        self.assertEqual(gaps, ())
        self.assertAlmostEqual(model["implied_roe"], 0.02 + 0.6 * 0.05)
        self.assertAlmostEqual(model["roe_latest_annualized"], 0.10)
        self.assertEqual([item["period"] for item in model["roe_history"]], ["2024-12-31", "2025-12-31"])
        self.assertAlmostEqual(model["roe_recent_median"], 0.105)
        self.assertTrue(any("年化" in note for note in model["notes"]))
        self.assertEqual(valuation_model_lines(model)[0][0], "隐含 ROE")


def _fast_income():
    rows = _income(range(2022, 2026))
    for row in rows:
        year = int(row["report_period"][:4])
        if row["field_name"] == "deducted_parent_net_profit":
            row["normalized_value"] = str(90 * 1.5 ** (year - 2022))
    return rows


if __name__ == "__main__":
    unittest.main()
