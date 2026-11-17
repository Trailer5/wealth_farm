import unittest
from decimal import Decimal

from src.portfolio_risk import (
    PreferenceHorizon,
    RiskProfile,
    equity_range,
    erc_weights,
    initial_preference,
    within_risk_assets,
)

COV = {
    "equity": {"equity": 0.04, "bond": 0.0, "cash": 0.0},
    "bond": {"equity": 0.0, "bond": 0.0016, "cash": 0.0},
    "cash": {"equity": 0.0, "bond": 0.0, "cash": 0.0},
}
RISK = {
    "classes": ["equity", "bond", "cash"],
    "cov": COV,
    "scenario_returns": {"crash": {"equity": -0.45, "bond": 0.02, "cash": 0.0}},
    "scenario_labels": {"crash": "股灾"},
}


def _preference(profile: RiskProfile, **overrides):
    return initial_preference(
        user_id="user:a",
        recorded_on="2026-10-28",
        risk_profile=profile,
        horizon=PreferenceHorizon.OVER_3Y,
        user_statement="测试",
        overrides=overrides,
    )


class EquityRangeTest(unittest.TestCase):
    def test_volatility_budget_binds_for_balanced(self):
        result = equity_range(
            _preference(RiskProfile.BALANCED, max_drawdown="0.30"), RISK, defensive_mix={"bond": 0.5, "cash": 0.5}
        )

        self.assertIn("volatility", result["binding"])
        self.assertLessEqual(result["at_high"]["volatility"], 0.10)
        self.assertAlmostEqual(result["high"], 0.495, places=3)
        self.assertFalse(result["conflict"])

    def test_drawdown_budget_conflicts_with_preferred_floor(self):
        result = equity_range(
            _preference(RiskProfile.GROWTH, max_drawdown="0.20"),
            RISK,
            defensive_mix={"cash": 1.0},
        )

        self.assertEqual(result["binding"], ["drawdown"])
        self.assertTrue(result["conflict"])
        self.assertLess(result["high"], 0.60)
        shares = {item["class"]: item["risk_share"] for item in result["risk_budget"]}
        self.assertEqual(shares["equity"], 1.0)

    def test_exit_line_judges_drawdown_at_the_first_close_past_it(self):
        # Equity falls 10 points a day from 1.0 to 0.5: with a 15% exit line it is sold at 0.8 (-20%).
        path = [(f"2015-06-{day:02d}", round(1.0 - 0.1 * index, 2)) for index, day in enumerate((8, 9, 10, 11, 12, 15))]
        risk = {**RISK, "scenario_paths": {"crash": {"equity": path}}}
        risk["scenario_returns"] = {"crash": {"equity": -0.5, "bond": 0.02, "cash": 0.0}}
        held = equity_range(_preference(RiskProfile.GROWTH, max_drawdown="0.20"), risk, defensive_mix={"cash": 1.0})
        managed = equity_range(
            _preference(RiskProfile.GROWTH, max_drawdown="0.20", exit_drawdown="0.15"), risk, defensive_mix={"cash": 1.0}
        )

        self.assertEqual(held["drawdown_basis"], "buy_and_hold")
        self.assertEqual(managed["drawdown_basis"], "exit_line")
        self.assertAlmostEqual(held["limits"]["drawdown"], 0.40, places=3)
        self.assertAlmostEqual(managed["limits"]["drawdown"], 1.0, places=3)
        self.assertNotIn("drawdown", managed["binding"])
        self.assertAlmostEqual(managed["at_high"]["worst_loss"], managed["high"] * 0.20, places=3)


class RiskBalanceTest(unittest.TestCase):
    def test_erc_equalizes_risk_contributions(self):
        cov = {
            "a": {"a": 0.09, "b": 0.012, "c": 0.0},
            "b": {"a": 0.012, "b": 0.04, "c": 0.006},
            "c": {"a": 0.0, "b": 0.006, "c": 0.01},
        }
        weights = erc_weights(["a", "b", "c"], cov)

        contributions = [weights[x] * sum(cov[x][y] * weights[y] for y in weights) for x in weights]
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertAlmostEqual(max(contributions), min(contributions), places=6)
        self.assertGreater(weights["c"], weights["a"])

    def test_single_stock_cap_is_flagged_at_target(self):
        cov = {"s": {"s": 0.06, "f": 0.02}, "f": {"s": 0.02, "f": 0.03}}
        rows = within_risk_assets(
            [
                {"asset_id": "s", "label": "S", "single_stock": True, "risk_weight": 0.4},
                {"asset_id": "f", "label": "F", "single_stock": False, "risk_weight": 0.2},
            ],
            cov,
            target_share=0.7,
            preference=_preference(RiskProfile.GROWTH),
        )

        stock = rows[0]
        self.assertAlmostEqual(stock["current_share"], 0.6667, places=4)
        self.assertTrue(stock["over_single_cap"])
        self.assertEqual(_preference(RiskProfile.GROWTH).max_single_weight, Decimal("0.20"))


if __name__ == "__main__":
    unittest.main()
