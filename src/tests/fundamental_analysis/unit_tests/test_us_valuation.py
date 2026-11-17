import unittest

from src.fundamental_analysis.us_facts import summarize, ttm
from src.research_orchestration.us_valuation import value_us_company


def _flow(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


FACTS = {
    "us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            _flow("2024-12-29", "2025-12-27", 400, "2026-02-04", "10-K"),
            _flow("2023-12-31", "2024-12-28", 160, "2025-02-04", "10-K"),
            _flow("2024-12-29", "2025-09-27", 300, "2025-11-04"),
            _flow("2025-06-29", "2025-09-27", 120, "2025-11-04"),
            _flow("2025-12-28", "2026-09-26", 600, "2026-11-04"),
            _flow("2026-06-28", "2026-09-26", 250, "2026-11-04"),
        ]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _flow("2024-12-29", "2025-12-27", 500, "2026-02-04", "10-K"),
        ]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"end": "2026-09-26", "val": 50, "filed": "2026-11-04", "form": "10-Q"},
        ]}},
    },
    "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2026-10-28", "val": 10, "filed": "2026-11-04", "form": "10-Q"},
    ]}}},
}


class USFactsTest(unittest.TestCase):
    def test_ttm_is_fiscal_year_plus_ytd_minus_prior_ytd_and_respects_filing_dates(self):
        now = ttm(FACTS, ("NetIncomeLoss",), "2026-11-17")
        before = ttm(FACTS, ("NetIncomeLoss",), "2026-11-01")

        self.assertEqual(now, {"value": 700.0, "period_end": "2026-09-26", "basis": "ttm"})
        self.assertEqual(before["value"], 400.0)
        self.assertEqual(before["basis"], "fiscal_year")
        summary = summarize(FACTS, "2026-11-17")
        self.assertEqual(summary["shares"]["value"], 10.0)
        self.assertEqual([row["value"] for row in summary["annual"]["net_income"]], [160.0, 400.0])

    def test_value_table_and_implied_growth(self):
        result = value_us_company(
            {"company": "X", "facts": FACTS},
            price=700.0,
            price_date="2026-11-16",
            as_of_date="2026-11-17",
            risk_free={"percent": 5.0, "date": "2026-11-16", "source": "test"},
            equity_risk_premium=0.05,
            terminal_growth=0.025,
        )

        (earnings, cash_flow) = result["bases"]
        self.assertEqual(earnings["multiple"], 10.0)
        self.assertIsNotNone(earnings["implied_growth"])
        values = earnings["value_per_share_by_growth"]
        self.assertLess(values["10%"], values["20%"])
        self.assertEqual(cash_flow["basis"], "free_cash_flow")
        self.assertEqual(result["market"]["enterprise_value"], 7000.0 - 50.0)


if __name__ == "__main__":
    unittest.main()
