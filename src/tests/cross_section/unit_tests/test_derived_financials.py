import unittest

from src.cross_section import derived_fields, shift_quarter


def _rows(values: dict[str, dict]) -> dict[str, dict]:
    return {period: {"report_period": period, **fields} for period, fields in values.items()}


ROWS = _rows(
    {
        "2025-03-31": {"roe_percent": 8.0, "net_profit": 80.0, "revenue": 400.0, "operating_cash_flow": -50.0},
        "2025-06-30": {"roe_percent": 12.0, "net_profit": 120.0, "revenue": 700.0, "operating_cash_flow": 30.0},
        "2025-12-31": {"roe_percent": 20.0, "net_profit": 200.0, "revenue": 1300.0, "operating_cash_flow": 150.0},
        "2026-03-31": {"roe_percent": 6.0, "net_profit": 60.0, "revenue": 420.0, "operating_cash_flow": -40.0},
        "2026-06-30": {"roe_percent": 11.0, "net_profit": 110.0, "revenue": 760.0, "operating_cash_flow": 20.0},
    }
)


class DerivedFinancialsTest(unittest.TestCase):
    def test_ttm_removes_seasonality_and_single_quarter_uses_the_same_year(self):
        derived = derived_fields(ROWS["2026-06-30"], ROWS.get)

        self.assertAlmostEqual(derived["roe_ttm_percent"], 19.0)
        self.assertAlmostEqual(derived["net_profit_ttm"], 190.0)
        self.assertAlmostEqual(derived["operating_cash_flow_ttm"], 140.0)
        self.assertAlmostEqual(derived["net_profit_quarter"], 50.0)
        self.assertAlmostEqual(derived["net_profit_quarter_yoy_percent"], 25.0)
        self.assertAlmostEqual(derived["revenue_quarter_yoy_percent"], (340 / 300 - 1) * 100)

    def test_first_quarter_and_missing_inputs(self):
        q1 = derived_fields(ROWS["2026-03-31"], ROWS.get)
        self.assertAlmostEqual(q1["net_profit_quarter"], 60.0)
        self.assertAlmostEqual(q1["net_profit_quarter_yoy_percent"], -25.0)
        annual = derived_fields(ROWS["2025-12-31"], ROWS.get)
        self.assertEqual(annual["roe_ttm_percent"], 20.0)
        # The 2025-09-30 report is missing, so the fourth quarter cannot be isolated.
        self.assertIsNone(annual["net_profit_quarter"])
        early = derived_fields(ROWS["2025-06-30"], ROWS.get)
        self.assertIsNone(early["roe_ttm_percent"])

    def test_loss_base_has_no_growth_rate(self):
        rows = _rows(
            {
                "2025-03-31": {"net_profit": -10.0},
                "2026-03-31": {"net_profit": 30.0},
            }
        )
        self.assertIsNone(derived_fields(rows["2026-03-31"], rows.get)["net_profit_quarter_yoy_percent"])
        self.assertEqual(shift_quarter("2026-03-31", -5), "2024-12-31")


if __name__ == "__main__":
    unittest.main()
