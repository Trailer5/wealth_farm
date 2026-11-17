import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.financial_data import SQLiteFinancialDataRepository
from src.financial_data.models import FinancialDataPoint, FinancialPeriodKind, FinancialValueScope
from src.financial_data.normalizer import canonical_field_name
from src.research_orchestration.financial_fields import field_series

KINDS = {"03-31": FinancialPeriodKind.Q1, "06-30": FinancialPeriodKind.HALF_YEAR, "09-30": FinancialPeriodKind.Q3, "12-31": FinancialPeriodKind.ANNUAL}


def _point(dataset, scope, label, period, value, published):
    return FinancialDataPoint(
        asset_id="asset:x",
        dataset=dataset,
        report_period=period,
        published_at=published,
        period_kind=KINDS[period[5:]],
        value_scope=scope,
        field_name=canonical_field_name(label),
        field_label=label,
        normalized_value=Decimal(value),
        raw_value=value,
        currency="CNY",
        unit="CNY",
        source="test",
        fetched_at="2026-11-17",
    )


class FinancialFieldsTest(unittest.TestCase):
    def test_cumulative_items_become_quarters_and_balances_get_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = SQLiteFinancialDataRepository(Path(temp_dir) / "f.sqlite3")
            repository.initialize()
            income = "stock_income_statements"
            balance = "stock_balance_sheets"
            cumulative, point = FinancialValueScope.CUMULATIVE, FinancialValueScope.POINT_IN_TIME
            repository.save_points([
                _point(income, cumulative, "OTHER_INCOME", "2025-09-30", "30", "2025-10-30"),
                _point(income, cumulative, "OTHER_INCOME", "2026-03-31", "10", "2026-04-30"),
                _point(income, cumulative, "OTHER_INCOME", "2026-06-30", "25", "2026-08-26"),
                _point(income, cumulative, "OTHER_INCOME", "2026-09-30", "60", "2026-10-30"),
                _point(income, cumulative, "OTHER_INCOME", "2025-06-30", "20", "2025-08-26"),
                _point(balance, point, "CONTRACT_LIAB", "2026-06-30", "53", "2026-08-26"),
                _point(balance, point, "CONTRACT_LIAB", "2026-09-30", "53", "2026-10-30"),
                _point(balance, point, "CONTRACT_LIAB", "2025-09-30", "47", "2025-10-30"),
            ])

            result = field_series(
                repository, asset_id="asset:x", fields=("OTHER_INCOME", "CONTRACT_LIAB", "INVENTORY"), as_of_date="2026-11-17"
            )
            before = field_series(repository, asset_id="asset:x", fields=("CONTRACT_LIAB",), as_of_date="2026-10-01")

        other = {row["period"]: row for row in result["fields"]["OTHER_INCOME"]["rows"]}
        self.assertEqual(result["fields"]["OTHER_INCOME"]["basis"], "single_quarter")
        self.assertEqual(other["2026-09-30"]["value"], 35.0)
        self.assertEqual(other["2026-06-30"]["value"], 15.0)
        self.assertAlmostEqual(other["2026-09-30"]["yoy"], 2.5)
        liab = {row["period"]: row for row in result["fields"]["CONTRACT_LIAB"]["rows"]}
        self.assertEqual(liab["2026-09-30"]["qoq_change"], 0.0)
        self.assertAlmostEqual(liab["2026-09-30"]["yoy"], 0.1277, places=4)
        self.assertTrue(result["fields"]["INVENTORY"]["missing"])
        self.assertNotIn("2026-09-30", [row["period"] for row in before["fields"]["CONTRACT_LIAB"]["rows"]])


if __name__ == "__main__":
    unittest.main()
