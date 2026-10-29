import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import (
    PortfolioRiskService,
    PreferenceHorizon,
    RiskProfile,
    SQLitePortfolioRepository,
    TradeRecord,
    TradeSide,
    calibration_checks,
    fixed_weight_path,
    initial_preference,
    max_drawdown,
    realized_path,
    trade_behaviours,
)
from src.research_state import ResearchStateService, SQLiteResearchStateRepository
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService

LEVELS = {"asset:a": {"2026-01-05": 100.0, "2026-01-06": 110.0, "2026-01-07": 88.0, "2026-01-08": 99.0}}


def _conservative(horizon=PreferenceHorizon.OVER_3Y):
    return initial_preference(
        user_id="user:a",
        recorded_on="2026-01-01",
        risk_profile=RiskProfile.CONSERVATIVE,
        horizon=horizon,
        user_statement="最多只能亏一成",
    )


def _trade(side, day, quantity, asset_id="asset:a"):
    return TradeRecord(
        user_id="user:a",
        asset_id=asset_id,
        side=side,
        trade_date=day,
        quantity=Decimal(quantity),
    )


class RealizedPathTest(unittest.TestCase):
    def test_rebuilds_holdings_backwards_and_measures_time_weighted_drawdown(self):
        trades = [_trade(TradeSide.BUY, "2026-01-05", "50"), _trade(TradeSide.BUY, "2026-01-07", "50")]

        result = realized_path({"asset:a": 100.0}, trades, LEVELS, as_of_date="2026-01-08")

        self.assertEqual(result["gaps"], [])
        growth = dict(result["path"])
        self.assertAlmostEqual(growth["2026-01-06"], 1.1)
        self.assertAlmostEqual(growth["2026-01-07"], 0.88)
        # The second buy does not count as a gain: 100 units from 88 to 99.
        self.assertAlmostEqual(growth["2026-01-08"], 0.99)
        self.assertAlmostEqual(max_drawdown(result["path"])["drawdown"], 0.2)

    def test_log_selling_more_than_held_is_reported_not_rebuilt(self):
        result = realized_path(
            {"asset:a": 10.0}, [_trade(TradeSide.BUY, "2026-01-05", "50")], LEVELS, as_of_date="2026-01-08"
        )

        self.assertEqual(result["path"], [])
        self.assertIn("数量为 -40", result["gaps"][0])

    def test_no_trades_means_no_realized_path(self):
        self.assertIsNone(realized_path({"asset:a": 10.0}, [], LEVELS, as_of_date="2026-01-08"))


class CalibrationTest(unittest.TestCase):
    def test_stated_ten_percent_against_realized_twenty_five_percent(self):
        path = [("2025-01-02", 1.0), ("2025-03-03", 1.2), ("2025-06-02", 0.9), ("2025-12-31", 1.0)]

        result = calibration_checks(
            _conservative(),
            realized={"path": path},
            replay={"start": "2023-10-28", "end": "2026-10-28", "max_drawdown": {"drawdown": 0.18, "peak": "x", "trough": "y"}, "volatility": 0.05},
            worst_stress=None,
            equity_share=0.6,
            behaviours=[],
        )

        drawdown = result["checks"][0]
        self.assertEqual(drawdown["status"], "exceeds")
        self.assertEqual(drawdown["worst_source"], "realized")
        self.assertIn("25.0%", drawdown["message"])
        self.assertIn("10.0%", drawdown["message"])
        self.assertIn("约 24%", drawdown["message"])
        self.assertFalse(result["consistent"])
        self.assertTrue(result["findings"])

    def test_measured_risk_far_below_statement_is_flagged_as_unused(self):
        result = calibration_checks(
            _conservative(),
            realized=None,
            replay={"start": "a", "end": "b", "max_drawdown": {"drawdown": 0.03, "peak": "x", "trough": "y"}, "volatility": 0.06},
            worst_stress=None,
            equity_share=0.1,
            behaviours=[],
        )

        self.assertEqual(result["checks"][0]["status"], "under_used")
        self.assertEqual(result["checks"][1]["status"], "ok")
        self.assertTrue(result["consistent"])
        self.assertFalse(result["has_trade_log"])

    def test_fixed_weight_path_uses_benchmark_beta_for_gaps(self):
        benchmark = {"2026-01-05": 100.0, "2026-01-06": 90.0, "2026-01-07": 99.0}

        path = fixed_weight_path(
            {"asset:a": 0.5, "cash": 0.5},
            {"asset:a": {"2026-01-05": 10.0, "2026-01-06": 8.0, "2026-01-07": 8.0}},
            as_of_date="2026-01-07",
            benchmark_levels=benchmark,
            zero_return=frozenset({"cash"}),
        )

        self.assertAlmostEqual(path[1][1], 0.9)
        self.assertAlmostEqual(max_drawdown(path)["drawdown"], 0.1)


class BehaviourTest(unittest.TestCase):
    def test_averaging_down_selling_into_a_fall_and_short_holding(self):
        trades = [
            _trade(TradeSide.BUY, "2026-01-05", "50"),
            _trade(TradeSide.BUY, "2026-01-07", "50"),
            _trade(TradeSide.SELL, "2026-01-07", "30", asset_id="asset:a"),
        ]
        realized = [("2026-01-05", 1.0), ("2026-01-06", 1.1), ("2026-01-07", 1.05)]

        hints = trade_behaviours(
            trades,
            LEVELS,
            preference=_conservative(),
            first_bought={},
            realized=realized,
            labels={"asset:a": "甲"},
        )

        by_id = {item["hint_id"]: item for item in hints}
        self.assertEqual(by_id["averaging_down"]["severity"], "caution")
        self.assertIn("12%", by_id["averaging_down"]["evidence"])
        # Portfolio was only ~5% below its peak, under the stated 10%: selling early is a caution.
        self.assertEqual(by_id["sell_in_drawdown"]["severity"], "caution")
        self.assertIn("20%", by_id["sell_in_drawdown"]["evidence"])
        self.assertIn("3 年以上", by_id["short_holding"]["evidence"])

    def test_short_horizon_user_gets_no_holding_period_hint(self):
        hints = trade_behaviours(
            [_trade(TradeSide.BUY, "2026-01-05", "50"), _trade(TradeSide.SELL, "2026-01-06", "50")],
            LEVELS,
            preference=_conservative(PreferenceHorizon.UNDER_1Y),
            first_bought={},
            realized=None,
            labels={},
        )

        self.assertEqual(hints, [])


class TradePersistenceTest(unittest.TestCase):
    def test_trades_are_idempotent_and_user_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "portfolio.sqlite3"
            users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
            context = users.default_context()
            master = MasterDataService(SQLiteMasterDataRepository(db_path))
            research = ResearchStateService(SQLiteResearchStateRepository(db_path), users, master)
            service = PortfolioRiskService(SQLitePortfolioRepository(db_path), research)
            trade = TradeRecord(
                user_id=context.user_id,
                asset_id="asset:a",
                side=TradeSide.SELL,
                trade_date="2026-03-02",
                quantity=Decimal("100"),
                price=Decimal("12.5"),
            )

            self.assertTrue(service.record_trade(context, trade))
            self.assertFalse(service.record_trade(context, trade))
            self.assertEqual(service.list_trades(context), [trade])
            other = users.context_for_user(users.create_user("Other").user_id)
            self.assertEqual(service.list_trades(other), [])
            with self.assertRaises(ValueError):
                service.record_trade(other, trade)
            with self.assertRaises(ValueError):
                TradeRecord(user_id="u", asset_id="a", side=TradeSide.BUY, trade_date="2026-01-01", quantity=Decimal("0"))


if __name__ == "__main__":
    unittest.main()
