import unittest
from decimal import Decimal

from src.financial_data import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FinancialResearchBundle,
)
from src.fundamental_analysis import format_metric_value, metric_history, metric_label
from src.master_data import ASSET_TYPE_STOCK, Asset

ASSET = Asset(
    asset_id="asset:company",
    asset_type=ASSET_TYPE_STOCK,
    standard_symbol="600000",
    display_name="测试公司",
    market="cn_a",
    exchange="sh",
)


def _record(dataset, period, field_name, value, published=None, revision=1):
    return {
        "asset_id": ASSET.asset_id,
        "dataset": dataset,
        "report_period": period,
        "published_at": published or f"{int(period[:4]) + 1}-03-31",
        "period_kind": "annual",
        "value_scope": "cumulative",
        "field_name": field_name,
        "field_label": field_name,
        "normalized_value": value,
        "raw_value": value,
        "currency": "CNY",
        "unit": "ratio" if dataset == DATASET_STOCK_FINANCIAL_INDICATORS else "CNY",
        "source": "unit-test",
        "revision": revision,
        "fetched_at": "2026-04-01T00:00:00+00:00",
        "raw": {},
    }


def _bundle(as_of="2026-04-30"):
    income = [
        _record(DATASET_STOCK_INCOME_STATEMENTS, f"{year}-12-31", "revenue", value)
        for year, value in ((2023, "100"), (2024, "120"), (2025, "150"))
    ]
    income.append(
        _record(DATASET_STOCK_INCOME_STATEMENTS, "2024-12-31", "revenue", "130", published="2025-08-01", revision=2)
    )
    indicators = [
        _record(DATASET_STOCK_FINANCIAL_INDICATORS, "2025-12-31", "roe", "0.11"),
        _record(DATASET_STOCK_FINANCIAL_INDICATORS, "2025-12-31", "roe", "0.12", published="2026-04-20", revision=2),
    ]
    return FinancialResearchBundle(
        asset=ASSET,
        as_of_date=as_of,
        start_date="2023-01-01",
        end_date=as_of,
        records_by_dataset={
            DATASET_STOCK_INCOME_STATEMENTS: tuple(income),
            DATASET_STOCK_BALANCE_SHEETS: (),
            DATASET_STOCK_CASH_FLOW_STATEMENTS: (),
            DATASET_STOCK_FINANCIAL_INDICATORS: tuple(indicators),
        },
        sources=("unit-test",),
        complete=True,
    )


class MetricHistoryTest(unittest.TestCase):
    def test_values_are_as_first_published(self):
        history = metric_history(_bundle(), "revenue")
        self.assertEqual(
            [(item.period_end, item.available_at, item.value) for item in history],
            [
                ("2023-12-31", "2024-03-31", Decimal("100")),
                ("2024-12-31", "2025-03-31", Decimal("120")),
                ("2025-12-31", "2026-03-31", Decimal("150")),
            ],
        )

    def test_computed_growth_needs_the_prior_period(self):
        history = metric_history(_bundle(), "revenue_growth")
        self.assertEqual([item.period_end for item in history], ["2024-12-31", "2025-12-31"])
        self.assertEqual(history[0].value, Decimal("0.2"))
        # The 2025 value uses the restated 2024 revenue published before 2026-03-31.
        self.assertAlmostEqual(float(history[1].value), 150 / 130 - 1)

    def test_indicator_history_keeps_the_first_disclosure(self):
        (item,) = metric_history(_bundle(), "indicator.roe")
        self.assertEqual((item.available_at, item.value), ("2026-03-31", Decimal("0.11")))

    def test_publication_after_as_of_is_excluded_and_unknown_metric_rejected(self):
        history = metric_history(_bundle(as_of="2026-01-31"), "revenue")
        self.assertEqual(history[-1].period_end, "2024-12-31")
        with self.assertRaises(ValueError):
            metric_history(_bundle(), "pe_ttm")
        self.assertIsNone(metric_label("indicator."))

    def test_format(self):
        self.assertEqual(format_metric_value("revenue", Decimal("250000000")), "2.50 亿元")
        self.assertEqual(format_metric_value("gross_margin", Decimal("0.912")), "91.20%")


if __name__ == "__main__":
    unittest.main()
