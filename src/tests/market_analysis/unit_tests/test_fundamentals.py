import unittest
from decimal import Decimal

from src.market_analysis import (
    IndexFundamentalCalculator,
    IndexFundamentalTrendCalculator,
    IndexValuationSnapshot,
    IndexValuationMethodComparisonCalculator,
    IndustryFundamentalSnapshot,
    MacroObservation,
    MarketIndustryAssessmentCalculator,
)


class IndexFundamentalCalculatorTest(unittest.TestCase):
    def test_hand_calculated_yields_and_provenance(self):
        result = IndexFundamentalCalculator().assess(
            _valuation(),
            ten_year_yield=_macro(
                "china_10y_government_yield",
                "2026-08-07",
                "2.5",
                "percent",
                "bond-source",
            ),
            cpi=_macro(
                "china_cpi_yoy",
                "2026-07",
                "0.8",
                "percent",
                "cpi-source",
            ),
        )

        self.assertEqual(result.earnings_yield, Decimal("0.05"))
        self.assertEqual(result.dividend_yield, Decimal("0.03"))
        self.assertEqual(result.pb, Decimal("1.5"))
        self.assertEqual(
            result.shareholder_yield_proxy,
            Decimal("0.08"),
        )
        self.assertEqual(
            result.real_yield_cpi_proxy,
            Decimal("0.017"),
        )
        self.assertEqual(
            result.equity_risk_premium,
            Decimal("0.025"),
        )
        self.assertEqual(
            result.sources,
            ("valuation-source", "bond-source", "cpi-source"),
        )
        self.assertEqual(result.methodology, "csindex_official_1")
        self.assertIn(
            ("earnings_yield", "1 / PE; ratio"),
            result.formulae,
        )
        self.assertEqual(result.macro_observations[1].name, "china_cpi_yoy")

    def test_macro_percent_and_ratio_units_are_equivalent(self):
        calculator = IndexFundamentalCalculator()
        percent = calculator.assess(
            _valuation(),
            ten_year_yield=_macro(
                "ten_year",
                "2026-08-07",
                "2.5",
                "percent",
                "bond-source",
            ),
            cpi=_macro(
                "cpi",
                "2026-07",
                "0.8",
                "percent",
                "cpi-source",
            ),
        )
        ratio = calculator.assess(
            _valuation(),
            ten_year_yield=_macro(
                "ten_year",
                "2026-08-07",
                "0.025",
                "ratio",
                "bond-source",
            ),
            cpi=_macro(
                "cpi",
                "2026-07",
                "0.008",
                "ratio",
                "cpi-source",
            ),
        )

        self.assertEqual(
            percent.real_yield_cpi_proxy,
            ratio.real_yield_cpi_proxy,
        )
        self.assertEqual(
            percent.equity_risk_premium,
            ratio.equity_risk_premium,
        )

    def test_current_provider_macro_records_are_normalized(self):
        cpi = MacroObservation.from_current_cpi_record(
            {
                "reference_period": "2026-07",
                "available_at": "2026-08-09",
                "cpi_yoy_percent": 0.5,
                "source": "nbs:data_release_page",
                "historical_backtest_eligible": False,
            }
        )
        ten_year = MacroObservation.from_ten_year_yield_record(
            {
                "trade_date": "2026-08-07",
                "ten_year_yield_percent": 1.75,
                "source": "akshare:bond_zh_us_rate",
            }
        )

        self.assertEqual(cpi.ratio_value, Decimal("0.005"))
        self.assertEqual(cpi.available_at, "2026-08-09")
        self.assertEqual(ten_year.ratio_value, Decimal("0.0175"))
        weekend = IndexFundamentalCalculator().assess(
            _valuation(),
            as_of="2026-08-09",
            ten_year_yield=ten_year,
            cpi=cpi,
        )
        self.assertEqual(weekend.as_of, "2026-08-09")
        self.assertEqual(
            weekend.real_yield_cpi_proxy,
            Decimal("0.0125"),
        )

        with self.assertRaisesRegex(ValueError, "ineligible"):
            MacroObservation.from_current_cpi_record(
                {
                    "reference_period": "2026-07",
                    "available_at": "2026-08-09",
                    "cpi_yoy_percent": 0.5,
                    "source": "test",
                    "historical_backtest_eligible": True,
                }
            )

    def test_future_cpi_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "future macro observation rejected",
        ):
            IndexFundamentalCalculator().assess(
                _valuation(),
                cpi=_macro(
                    "china_cpi_yoy",
                    "2026-08",
                    "1.0",
                    "percent",
                    "cpi-source",
                    available_at="2026-08-08",
                ),
            )

    def test_missing_values_produce_no_unsupported_proxies(self):
        valuation = IndexValuationSnapshot(
            asset_id="index:cn_a:sh:000300",
            as_of="2026-08-07",
            pe=None,
            pb=None,
            dividend_yield=Decimal("0.03"),
            available_at="2026-08-07",
            methodology="csindex_official_1",
            sources=("valuation-source",),
        )

        result = IndexFundamentalCalculator().assess(valuation)

        self.assertIsNone(result.earnings_yield)
        self.assertIsNone(result.pb)
        self.assertIsNone(result.shareholder_yield_proxy)
        self.assertIsNone(result.real_yield_cpi_proxy)
        self.assertIsNone(result.equity_risk_premium)

    def test_trend_keeps_one_method_and_calculates_changes(self):
        earlier = IndexValuationSnapshot(
            asset_id="index:cn_a:sh:000300",
            as_of="2026-08-06",
            pe=Decimal("25"),
            pb=Decimal("1.4"),
            dividend_yield=Decimal("0.02"),
            available_at="2026-08-06",
            methodology="csindex_official_1",
            sources=("earlier",),
        )

        result = IndexFundamentalTrendCalculator().calculate(
            (earlier, _valuation()),
            as_of="2026-08-07",
        )

        self.assertEqual(
            result.earnings_yield_change,
            Decimal("0.01"),
        )
        self.assertEqual(result.pb_change, Decimal("0.1"))
        self.assertEqual(
            result.dividend_yield_change,
            Decimal("0.01"),
        )
        self.assertEqual(result.sources, ("earlier", "valuation-source"))

    def test_aligned_valuation_methods_expose_ranges(self):
        alternative = IndexValuationSnapshot(
            asset_id="index:cn_a:sh:000300",
            as_of="2026-08-07",
            pe=Decimal("25"),
            pb=Decimal("1.8"),
            dividend_yield=Decimal("0.02"),
            available_at="2026-08-07",
            methodology="equal_weight_ttm",
            sources=("alternative-source",),
        )

        result = IndexValuationMethodComparisonCalculator().compare(
            (_valuation(), alternative)
        )

        self.assertEqual(result.pe_range, Decimal("5"))
        self.assertEqual(
            result.earnings_yield_range,
            Decimal("0.01"),
        )
        self.assertEqual(result.pb_range, Decimal("0.3"))
        self.assertEqual(
            result.dividend_yield_range,
            Decimal("0.01"),
        )
        self.assertIn("no method is designated", result.limitation)

    def test_method_comparison_rejects_unaligned_dates(self):
        alternative = IndexValuationSnapshot(
            asset_id="index:cn_a:sh:000300",
            as_of="2026-08-06",
            pe=Decimal("25"),
            pb=Decimal("1.8"),
            dividend_yield=Decimal("0.02"),
            available_at="2026-08-06",
            methodology="equal_weight_ttm",
            sources=("alternative-source",),
        )
        with self.assertRaisesRegex(ValueError, "observation date"):
            IndexValuationMethodComparisonCalculator().compare(
                (_valuation(), alternative)
            )


class MarketIndustryAssessmentCalculatorTest(unittest.TestCase):
    def test_contribution_sum_and_current_cross_section_rankings(self):
        prior = (
            _industry("A", "行业A", "2026-08-06", "0", "60", "20", "2"),
            _industry("B", "行业B", "2026-08-06", "0", "40", "10", "1"),
        )
        current = (
            _industry(
                "A",
                "行业A",
                "2026-08-07",
                "0.10",
                "70",
                "20",
                "2",
                dividend="0.01",
                amount_share="0.40",
            ),
            _industry(
                "B",
                "行业B",
                "2026-08-07",
                "-0.05",
                "30",
                "10",
                "1",
                dividend="0.03",
                amount_share="0.60",
            ),
        )

        result = MarketIndustryAssessmentCalculator().assess(
            prior,
            current,
        )

        self.assertEqual(
            sum(
                (item.contribution for item in result.contributions),
                Decimal("0"),
            ),
            Decimal("0.040"),
        )
        self.assertEqual(
            result.ranking("contribution").industry_codes,
            ("A", "B"),
        )
        self.assertEqual(
            result.ranking("pe").industry_codes,
            ("B", "A"),
        )
        self.assertEqual(
            result.ranking("pb").industry_codes,
            ("B", "A"),
        )
        self.assertEqual(
            result.ranking("dividend_yield").industry_codes,
            ("B", "A"),
        )
        self.assertEqual(
            result.ranking("amount_share").industry_codes,
            ("B", "A"),
        )
        self.assertIn("not historical attribution", result.limitation)

    def test_sw_record_converts_provider_percent_fields_to_ratios(self):
        snapshot = IndustryFundamentalSnapshot.from_sw_record(
            {
                "trade_date": "2026-08-07",
                "index_code": "801001",
                "index_name": "申万行业",
                "return_percent": 1.2,
                "float_market_cap": 100,
                "pe": 20,
                "pb": 2,
                "dividend_yield_percent": 1.5,
                "amount_share_percent": 12.5,
                "source": "akshare:index_analysis_daily_sw",
            }
        )

        self.assertEqual(snapshot.return_ratio, Decimal("0.012"))
        self.assertEqual(snapshot.dividend_yield, Decimal("0.015"))
        self.assertEqual(snapshot.amount_share, Decimal("0.125"))


def _valuation():
    return IndexValuationSnapshot(
        asset_id="index:cn_a:sh:000300",
        as_of="2026-08-07",
        pe=Decimal("20"),
        pb=Decimal("1.5"),
        dividend_yield=Decimal("0.03"),
        available_at="2026-08-07",
        methodology="csindex_official_1",
        sources=("valuation-source",),
    )


def _macro(
    name,
    reference_period,
    value,
    unit,
    source,
    *,
    available_at="2026-08-07",
):
    return MacroObservation(
        name=name,
        reference_period=reference_period,
        available_at=available_at,
        value=Decimal(value),
        unit=unit,
        source=source,
    )


def _industry(
    code,
    name,
    trade_date,
    return_ratio,
    market_cap,
    pe,
    pb,
    *,
    dividend=None,
    amount_share=None,
):
    return IndustryFundamentalSnapshot(
        industry_code=code,
        industry_name=name,
        trade_date=trade_date,
        return_ratio=Decimal(return_ratio),
        float_market_cap=Decimal(market_cap),
        pe=Decimal(pe) if pe is not None else None,
        pb=Decimal(pb) if pb is not None else None,
        dividend_yield=(
            Decimal(dividend) if dividend is not None else None
        ),
        amount_share=(
            Decimal(amount_share) if amount_share is not None else None
        ),
        methodology="shenwan_market_representation_current_cross_section",
        sources=("sw-source",),
    )


if __name__ == "__main__":
    unittest.main()
