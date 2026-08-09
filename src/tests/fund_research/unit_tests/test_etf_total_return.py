import unittest
from dataclasses import replace
from decimal import Decimal

from src.fund_research import (
    ETFDistributionEvent,
    ETFMarketPricePoint,
    ETFNavPoint,
    ETFReturnIndexPoint,
    ETFSplitEvent,
    ETFTotalReturnCalculator,
    ETFTrackingQualityCalculator,
)


ASSET_ID = "asset:etf:510300"


def nav(value_date, value, *, available_at=None):
    return ETFNavPoint(
        asset_id=ASSET_ID,
        date=value_date,
        unit_nav=Decimal(value),
        accumulated_nav=None,
        available_at=available_at or value_date,
        source="unit-test:official-nav",
    )


def price(value_date, value, *, available_at=None):
    return ETFMarketPricePoint(
        asset_id=ASSET_ID,
        date=value_date,
        close=Decimal(value),
        available_at=available_at or value_date,
        source="unit-test:unadjusted-close",
    )


def index_points(*values):
    dates = ("2026-08-05", "2026-08-06", "2026-08-07")
    return tuple(
        ETFReturnIndexPoint(value_date, Decimal(value))
        for value_date, value in zip(dates, values)
    )


class ETFTotalReturnCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = ETFTotalReturnCalculator()

    def test_reinvests_cash_distribution_without_using_accumulated_nav(self):
        result = self.calculator.calculate(
            asset_id=ASSET_ID,
            as_of_date="2026-08-07",
            nav_points=(
                replace(nav("2026-08-05", "10"), accumulated_nav=Decimal("90")),
                replace(nav("2026-08-06", "9"), accumulated_nav=Decimal("99")),
                replace(nav("2026-08-07", "9.9"), accumulated_nav=Decimal("999")),
            ),
            market_prices=(
                price("2026-08-05", "10"),
                price("2026-08-06", "9.5"),
                price("2026-08-07", "10.45"),
            ),
            distributions=(
                ETFDistributionEvent(
                    ex_date="2026-08-06",
                    cash_per_share=Decimal("1"),
                    available_at="2026-08-06",
                    source="unit-test:distribution",
                ),
            ),
            distribution_history_complete=True,
            split_history_complete=True,
        )

        self.assertEqual(
            [point.value for point in result.total_return],
            [Decimal("100"), Decimal("100"), Decimal("110.0")],
        )
        self.assertEqual(
            result.nav_return[-1].value,
            Decimal("99.00"),
        )
        self.assertEqual(
            result.market_price_return[-1].value,
            Decimal("104.500"),
        )
        self.assertEqual(
            result.points[1].premium_discount,
            Decimal("9.5") / Decimal("9") - Decimal("1"),
        )
        self.assertTrue(result.coverage.complete)

    def test_adjusts_five_for_one_split_without_creating_return(self):
        result = self.calculator.calculate(
            asset_id=ASSET_ID,
            as_of_date="2026-08-06",
            nav_points=(
                nav("2026-08-05", "10"),
                nav("2026-08-06", "2"),
            ),
            market_prices=(
                price("2026-08-05", "10.2"),
                price("2026-08-06", "2.04"),
            ),
            splits=(
                ETFSplitEvent(
                    effective_date="2026-08-06",
                    ratio=Decimal("5"),
                    available_at="2026-08-06",
                    source="unit-test:split",
                ),
            ),
            distribution_history_complete=True,
            split_history_complete=True,
        )

        self.assertEqual(result.points[-1].adjusted_nav, Decimal("10"))
        self.assertEqual(
            result.total_return[-1].value,
            Decimal("100"),
        )
        self.assertEqual(
            result.nav_return[-1].value,
            Decimal("100"),
        )
        self.assertEqual(
            result.market_price_return[-1].value,
            Decimal("100"),
        )
        self.assertEqual(
            result.points[-1].premium_discount,
            Decimal("0.02"),
        )

    def test_rejects_inputs_not_available_at_point_in_time(self):
        with self.assertRaisesRegex(ValueError, "available_at"):
            self.calculator.calculate(
                asset_id=ASSET_ID,
                as_of_date="2026-08-06",
                nav_points=(
                    nav("2026-08-05", "10"),
                    nav(
                        "2026-08-06",
                        "10.1",
                        available_at="2026-08-07",
                    ),
                ),
                market_prices=(
                    price("2026-08-05", "10"),
                    price("2026-08-06", "10.1"),
                ),
                distribution_history_complete=True,
                split_history_complete=True,
            )

    def test_missing_action_histories_are_explicit_limitations(self):
        result = self.calculator.calculate(
            asset_id=ASSET_ID,
            as_of_date="2026-08-06",
            nav_points=(
                nav("2026-08-05", "10"),
                nav("2026-08-06", "10.1"),
            ),
            market_prices=(
                price("2026-08-05", "10"),
                price("2026-08-06", "10.1"),
            ),
        )

        self.assertFalse(result.coverage.complete)
        self.assertFalse(
            result.coverage.distribution_history_complete
        )
        self.assertFalse(result.coverage.split_history_complete)
        self.assertIn(
            "distribution_events_absent_but_not_confirmed_none",
            result.coverage.limitations,
        )
        self.assertIn(
            "split_events_absent_but_not_confirmed_none",
            result.coverage.limitations,
        )

    def test_price_and_nav_tracking_use_separate_benchmarks(self):
        result = ETFTrackingQualityCalculator().calculate(
            asset_id=ASSET_ID,
            price_benchmark_asset_id="index:price",
            total_return_benchmark_asset_id="index:total-return",
            market_price_series=index_points("100", "110", "100"),
            price_benchmark_series=index_points("100", "100", "100"),
            nav_total_return_series=index_points("100", "102", "104"),
            total_return_benchmark_series=index_points(
                "100",
                "102",
                "104",
            ),
        )

        self.assertGreater(result.price_tracking_error, 0)
        self.assertEqual(result.nav_tracking_error, 0)
        self.assertEqual(
            result.market_price.benchmark_asset_id,
            "index:price",
        )
        self.assertEqual(
            result.nav_total_return.benchmark_asset_id,
            "index:total-return",
        )
        self.assertIn(
            "etf_market_price_return_index",
            result.market_price.metrics[0].source_dataset,
        )
        self.assertIn(
            "etf_nav_total_return_index",
            result.nav_total_return.metrics[0].source_dataset,
        )


if __name__ == "__main__":
    unittest.main()
