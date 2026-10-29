import unittest

from src.portfolio_risk import (
    AdjustmentLine,
    PreferenceHorizon,
    RiskProfile,
    initial_preference,
    migration_steps,
    round_to_lots,
    target_weights,
)


def _preference():
    return initial_preference(
        user_id="user:a",
        recorded_on="2026-10-28",
        risk_profile=RiskProfile.GROWTH,
        horizon=PreferenceHorizon.OVER_3Y,
        user_statement="成长",
    )


LINES = [
    AdjustmentLine("stock", "茅台", "stock", 0.34, 1.0, {"食品饮料": 1.0}),
    AdjustmentLine("etf", "沪深300ETF", "exchange_fund", 0.20, 0.97, {"食品饮料": 0.02, "电子": 0.1}),
    AdjustmentLine("gold", "黄金ETF", "exchange_fund", 0.16, 0.0),
    AdjustmentLine("money", "余额宝", "open_fund", 0.30, 0.0, cash_like=True),
    AdjustmentLine("new", "中证500ETF", "exchange_fund", 0.0, 0.98, {"电子": 0.12}, candidate=True),
]


class TargetWeightTest(unittest.TestCase):
    def test_caps_concentration_and_refills_equity_from_receivers(self):
        targets = target_weights(
            LINES,
            preference=_preference(),
            equity_low=0.55,
            equity_high=0.65,
            receivers=["etf", "new"],
        )
        summary = targets.pop("_summary")

        self.assertEqual(targets["stock"]["target"], 0.2)
        self.assertIn("单只个股上限 20%", targets["stock"]["reasons"])
        equity = sum(targets[line.asset_id]["target"] * line.risk_fraction for line in LINES)
        self.assertAlmostEqual(equity, summary["target_equity"], places=3)
        self.assertGreater(targets["new"]["target"], 0)
        self.assertAlmostEqual(sum(item["target"] for item in targets.values()), 1.0, places=3)
        self.assertEqual(targets["gold"]["target"], 0.16)

    def test_lowering_equity_releases_money_to_defensive_lines(self):
        targets = target_weights(
            LINES[:4],
            preference=_preference(),
            equity_low=0.30,
            equity_high=0.35,
            receivers=["etf"],
        )
        targets.pop("_summary")

        self.assertLess(targets["etf"]["target"], 0.20)
        self.assertGreater(targets["money"]["target"] + targets["gold"]["target"], 0.46)


class MigrationTest(unittest.TestCase):
    def test_sells_fund_buys_and_fee_wait_moves_a_sale(self):
        result = migration_steps(
            [
                {"asset_id": "stock", "label": "茅台", "amount": -60000, "cost_rate": 0.00075, "risk": True},
                {
                    "asset_id": "fund",
                    "label": "基金",
                    "amount": -9000,
                    "cost_rate": 0.0,
                    "risk": True,
                    "earliest": "2026-11-25",
                },
                {"asset_id": "new", "label": "中证500ETF", "amount": 60000, "cost_rate": 0.00025, "risk": True},
            ],
            as_of_date="2026-10-28",
            starting_cash=0.0,
            buy_condition="估值条件",
        )

        steps = result["steps"]
        self.assertEqual(steps[0]["sell_amount"], 30000)
        self.assertEqual(steps[0]["buy_amount"], 20000)
        fund_legs = [(step["step"], leg["window"][0]) for step in steps for leg in step["legs"] if leg["asset_id"] == "fund"]
        self.assertEqual(fund_legs, [(2, "2026-11-25"), (3, "2026-11-26")])
        self.assertTrue(all(leg["condition"] == "估值条件" for step in steps for leg in step["legs"] if leg["action"] == "buy"))
        self.assertEqual(result["cash_shortfall"], 0)
        self.assertAlmostEqual(result["total_cost"], 60000 * 0.00075 + 60000 * 0.00025, places=1)

    def test_buys_wait_for_released_cash(self):
        result = migration_steps(
            [
                {"asset_id": "s", "label": "S", "amount": -10000, "cost_rate": 0.0, "risk": True, "earliest": "2026-11-20"},
                {"asset_id": "b", "label": "B", "amount": 30000, "cost_rate": 0.0, "risk": False},
            ],
            as_of_date="2026-10-28",
            starting_cash=0.0,
            buy_condition="x",
        )

        self.assertEqual(result["steps"][0]["buy_amount"], 0)
        self.assertEqual(result["cash_shortfall"], 20000)
        self.assertTrue(all(leg["condition"] is None for step in result["steps"] for leg in step["legs"]))

    def test_exchange_traded_legs_are_whole_lots_with_minimum_commission(self):
        result = migration_steps(
            [
                {"asset_id": "s", "label": "S", "amount": -3000, "cost_rate": 0.00075, "risk": True,
                 "price": 10.0, "lot": 100, "commission_rate": 0.00025, "min_commission": 5.0},
                {"asset_id": "e", "label": "E", "amount": 4000, "cost_rate": 0.00025, "risk": True,
                 "price": 10.0, "lot": 100, "commission_rate": 0.00025, "min_commission": 5.0},
            ],
            as_of_date="2026-10-28",
            starting_cash=1000.0,
            buy_condition="x",
        )

        legs = [(step["step"], leg["action"], leg["quantity"]) for step in result["steps"] for leg in step["legs"]]
        self.assertEqual(legs, [(1, "sell", 200), (1, "buy", 200), (2, "sell", 100), (2, "buy", 100), (3, "buy", 100)])
        first_sell = result["steps"][0]["legs"][0]
        self.assertAlmostEqual(first_sell["cost"], 2000 * 0.00075 + 5.0 - 2000 * 0.00025, places=2)
        self.assertEqual(result["cash_shortfall"], 0)


class ReviewFixTest(unittest.TestCase):
    def _lines(self):
        return [
            AdjustmentLine("etf", "沪深300ETF", "exchange_fund", 0.50, 1.0, {"银行": 0.1}, main_class="equity"),
            AdjustmentLine("fund", "主动基金", "open_fund", 0.20, 1.0, {"电子": 0.2}, main_class="equity"),
            AdjustmentLine("gold", "黄金ETF", "exchange_fund", 0.10, 0.0, main_class="commodity"),
            AdjustmentLine("bond", "国债ETF", "exchange_fund", 0.05, 0.0, main_class="bond"),
            AdjustmentLine("money", "余额宝", "open_fund", 0.15, 0.0, cash_like=True, main_class="cash"),
        ]

    def test_released_money_goes_to_bonds_and_cash_not_gold(self):
        targets = target_weights(
            self._lines(), preference=_preference(), equity_low=0.4, equity_high=0.5, receivers=["etf"]
        )
        targets.pop("_summary")

        self.assertEqual(targets["gold"]["target"], 0.10)
        self.assertGreater(targets["bond"]["target"], 0.05)
        self.assertGreater(targets["money"]["target"], 0.15)

    def test_named_bond_fund_takes_the_released_money(self):
        lines = [*self._lines(), AdjustmentLine("newbond", "国债", "exchange_fund", 0.0, 0.0, candidate=True, main_class="bond")]
        targets = target_weights(lines, preference=_preference(), equity_low=0.4, equity_high=0.5, receivers=[])
        targets.pop("_summary")

        self.assertAlmostEqual(targets["newbond"]["target"], 0.2, places=3)
        self.assertEqual(targets["money"]["target"], 0.15)

    def test_named_candidate_gets_an_equal_slot_when_equity_is_in_range(self):
        lines = [*self._lines(), AdjustmentLine("power", "电力", "stock", 0.0, 1.0, {"公用事业": 1.0}, candidate=True)]
        targets = target_weights(
            lines, preference=_preference(), equity_low=0.6, equity_high=0.8, receivers=["power"], substitute=True
        )
        summary = targets.pop("_summary")

        # One equal slot is 70% / 3, capped at the 20% single-stock limit.
        self.assertAlmostEqual(targets["power"]["target"], 0.2, places=3)
        self.assertLess(targets["etf"]["target"], 0.5)
        self.assertIn("为点名候选腾出权益额度", targets["fund"]["reasons"])
        self.assertAlmostEqual(summary["target_equity"], 0.7, places=3)

    def test_cash_floor_for_near_term_spending_sells_defensive_first(self):
        targets = target_weights(
            self._lines(), preference=_preference(), equity_low=0.6, equity_high=0.8, receivers=["etf"], cash_floor=0.25
        )
        targets.pop("_summary")

        self.assertAlmostEqual(targets["money"]["target"], 0.25, places=3)
        self.assertLess(targets["gold"]["target"], 0.10)
        self.assertEqual(targets["etf"]["target"], 0.5)

    def test_round_to_lots_keeps_whole_lots_and_moves_the_rest_to_cash(self):
        targets = {
            "stock": {"label": "茅台", "current": 0.33, "target": 0.143, "low": 0.123, "high": 0.163, "change": -0.187, "reasons": []},
            "etf": {"label": "ETF", "current": 0.2, "target": 0.2004, "low": 0.1804, "high": 0.2204, "change": 0.0004, "reasons": []},
            "cash": {"label": "现金", "current": 0.05, "target": 0.236, "low": 0.216, "high": 0.256, "change": 0.186, "reasons": []},
        }

        notes = round_to_lots(
            targets,
            total=400000.0,
            quantities={"stock": 100.0, "etf": 20000.0},
            prices={"stock": 1320.0, "etf": 4.0},
            lots={"stock": 100, "etf": 100},
            sink="cash",
        )

        self.assertEqual(targets["stock"]["quantity_change"], -100)
        self.assertEqual(targets["stock"]["target"], 0.0)
        self.assertIn("按每手 100 份取整", targets["stock"]["reasons"][0])
        self.assertEqual(targets["etf"]["quantity_change"], 0)
        self.assertIn("不足一手", targets["etf"]["reasons"][0])
        self.assertAlmostEqual(targets["cash"]["target"], 0.236 + 0.143 + 0.0004, places=3)
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
