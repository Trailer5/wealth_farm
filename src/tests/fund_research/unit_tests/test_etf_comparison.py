import unittest
from dataclasses import replace
from decimal import Decimal

from src.fund_research.comparison import (
    ETFComparisonSeries,
    ETFComparisonService,
    ReturnIndexPoint,
)
from src.fund_research.total_return import (
    ETFDistributionEvent,
    ETFMarketPricePoint,
    ETFNavPoint,
    ETFTotalReturnCalculator,
)


class ETFComparisonServiceTest(unittest.TestCase):
    def test_compares_actual_return_fee_drawdown_and_tracking(self):
        panel = ETFComparisonService().compare(
            as_of_date="2026-08-07",
            series=(
                _series(
                    symbol="510300",
                    index_id="index:000300",
                    market=("100", "105", "102"),
                    nav=("100", "104", "103"),
                    benchmark=("100", "103", "104"),
                    fee="0.0020",
                ),
                _series(
                    symbol="510310",
                    index_id="index:000300",
                    market=("100", "103", "104"),
                    nav=("100", "103.1", "103.8"),
                    benchmark=("100", "103", "104"),
                    fee="0.0015",
                ),
                _series(
                    symbol="510500",
                    index_id="index:000905",
                    market=("100", "98", "106"),
                    nav=("100", "97", "105"),
                    benchmark=("100", "96", "106"),
                    fee="0.0020",
                ),
            ),
        )

        by_symbol = {row.standard_symbol: row for row in panel.rows}
        first = by_symbol["510300"]
        self.assertEqual(first.market_price_return, Decimal("0.02"))
        self.assertEqual(first.nav_total_return, Decimal("0.03"))
        self.assertEqual(first.benchmark_total_return, Decimal("0.04"))
        self.assertEqual(first.nav_tracking_difference, Decimal("-0.01"))
        self.assertEqual(first.nav_max_drawdown, Decimal("103") / Decimal("104") - 1)
        self.assertNotEqual(
            first.price_tracking_error,
            first.nav_tracking_error,
        )
        self.assertEqual(by_symbol["510310"].same_index_rank, 1)
        self.assertEqual(by_symbol["510500"].same_index_rank, 1)
        self.assertIn(
            "different_index_returns_are_descriptive_not_ranked",
            panel.methodology,
        )

    def test_rejects_future_availability_and_misaligned_short_history(self):
        future = replace(
            _series(
                symbol="510300",
                index_id="index:000300",
                market=("100", "101", "102"),
                nav=("100", "101", "102"),
                benchmark=("100", "101", "102"),
                fee="0.0020",
            ),
            as_of_date="2026-08-08",
            available_at="2026-08-08",
        )
        with self.assertRaisesRegex(ValueError, "unavailable"):
            ETFComparisonService().compare(
                as_of_date="2026-08-07",
                series=(future,),
            )

        no_common_interval = replace(
            _series(
                symbol="510300",
                index_id="index:000300",
                market=("100", "101", "102"),
                nav=("100", "101", "102"),
                benchmark=("100", "101", "102"),
                fee="0.0020",
            ),
            nav_total_return=(
                ReturnIndexPoint("2026-08-02", Decimal("100")),
                ReturnIndexPoint("2026-08-03", Decimal("101")),
            ),
        )
        with self.assertRaisesRegex(ValueError, "two common"):
            ETFComparisonService().compare(
                as_of_date="2026-08-07",
                series=(no_common_interval,),
            )

    def test_missing_benchmarks_keep_returns_and_expose_limitations(self):
        without_benchmarks = replace(
            _series(
                symbol="510300",
                index_id="index:000300",
                market=("100", "101", "102"),
                nav=("100", "102", "104"),
                benchmark=("100", "102", "104"),
                fee="0.0020",
            ),
            price_benchmark_return=(),
            benchmark_total_return=(),
        )

        row = ETFComparisonService().compare(
            as_of_date="2026-08-07",
            series=(without_benchmarks,),
        ).rows[0]

        self.assertEqual(row.nav_total_return, Decimal("0.04"))
        self.assertIsNone(row.price_tracking_error)
        self.assertIsNone(row.nav_tracking_error)
        self.assertIsNone(row.benchmark_total_return)
        self.assertEqual(
            row.limitations,
            (
                "price_benchmark_unavailable",
                "total_return_benchmark_unavailable",
            ),
        )

    def test_adapts_nav_total_return_without_calling_price_return_total(self):
        result = ETFTotalReturnCalculator().calculate(
            asset_id="asset:etf:510300",
            as_of_date="2026-08-07",
            nav_points=(
                ETFNavPoint(
                    "asset:etf:510300",
                    "2026-08-06",
                    Decimal("10"),
                    None,
                    "2026-08-06",
                    "nav",
                ),
                ETFNavPoint(
                    "asset:etf:510300",
                    "2026-08-07",
                    Decimal("9"),
                    None,
                    "2026-08-07",
                    "nav",
                ),
            ),
            market_prices=(
                ETFMarketPricePoint(
                    "asset:etf:510300",
                    "2026-08-06",
                    Decimal("10"),
                    "2026-08-06",
                    "market",
                ),
                ETFMarketPricePoint(
                    "asset:etf:510300",
                    "2026-08-07",
                    Decimal("9"),
                    "2026-08-07",
                    "market",
                ),
            ),
            distributions=(
                ETFDistributionEvent(
                    "2026-08-07",
                    Decimal("1"),
                    "2026-08-07",
                    "distribution",
                ),
            ),
            distribution_history_complete=True,
            split_history_complete=True,
        )
        comparison = ETFComparisonSeries.from_total_return_result(
            result=result,
            standard_symbol="510300",
            underlying_index_asset_id="index:000300",
            available_at="2026-08-07",
            annual_expense_rate=Decimal("0.002"),
            price_benchmark_return=(
                ReturnIndexPoint("2026-08-06", Decimal("100")),
                ReturnIndexPoint("2026-08-07", Decimal("90")),
            ),
            benchmark_total_return=(
                ReturnIndexPoint("2026-08-06", Decimal("100")),
                ReturnIndexPoint("2026-08-07", Decimal("100")),
            ),
            source_refs=("nav", "market", "distribution"),
        )

        self.assertEqual(
            comparison.nav_total_return[-1].value,
            Decimal("100"),
        )
        self.assertEqual(
            comparison.market_price_return[-1].value,
            Decimal("90"),
        )


def _series(
    *,
    symbol: str,
    index_id: str,
    market: tuple[str, str, str],
    nav: tuple[str, str, str],
    benchmark: tuple[str, str, str],
    fee: str,
) -> ETFComparisonSeries:
    dates = ("2026-08-05", "2026-08-06", "2026-08-07")
    return ETFComparisonSeries(
        asset_id=f"asset:etf:{symbol}",
        standard_symbol=symbol,
        underlying_index_asset_id=index_id,
        as_of_date="2026-08-07",
        available_at="2026-08-07",
        annual_expense_rate=Decimal(fee),
        market_price_return=tuple(
            ReturnIndexPoint(value_date, Decimal(value))
            for value_date, value in zip(dates, market)
        ),
        nav_total_return=tuple(
            ReturnIndexPoint(value_date, Decimal(value))
            for value_date, value in zip(dates, nav)
        ),
        price_benchmark_return=tuple(
            ReturnIndexPoint(value_date, Decimal(value))
            for value_date, value in zip(dates, benchmark)
        ),
        benchmark_total_return=tuple(
            ReturnIndexPoint(value_date, Decimal(value))
            for value_date, value in zip(dates, benchmark)
        ),
        source_refs=("unit-test",),
    )


if __name__ == "__main__":
    unittest.main()
