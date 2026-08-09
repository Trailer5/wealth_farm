import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import (
    InvestmentMandate,
    PortfolioCalculator,
    PositionBudgetCalculator,
    PositionBudgetInput,
    PortfolioRiskService,
    SQLitePortfolioRepository,
)
from src.research_state import (
    HoldingPosition,
    ResearchStateService,
    SQLiteResearchStateRepository,
)
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class PortfolioCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = PortfolioCalculator()

    def test_value_weights_and_concentration(self):
        valuation = self.calculator.calculate(
            user_id="user:test",
            as_of_date="2026-07-24",
            holdings=[
                _holding("asset:a", 10, 8),
                _holding("asset:b", 20, 6),
            ],
            prices={"asset:a": 10, "asset:b": 5},
        )

        self.assertEqual(valuation.snapshot.total_market_value, 200)
        self.assertEqual(valuation.snapshot.total_cost_value, 200)
        self.assertEqual(valuation.snapshot.unrealized_pnl, 0)
        self.assertEqual(valuation.snapshot.max_weight, 0.5)
        self.assertEqual(valuation.snapshot.concentration_hhi, 0.5)
        self.assertEqual(
            [position.weight for position in valuation.positions],
            [0.5, 0.5],
        )

    def test_missing_cost_keeps_total_cost_unknown(self):
        valuation = self.calculator.calculate(
            user_id="user:test",
            as_of_date="2026-07-24",
            holdings=[_holding("asset:a", 10, None)],
            prices={"asset:a": 10},
        )

        self.assertIsNone(valuation.snapshot.total_cost_value)
        self.assertIsNone(valuation.snapshot.unrealized_pnl)
        self.assertIsNone(valuation.positions[0].cost_value)

    def test_missing_price_and_negative_quantity_are_rejected(self):
        with self.assertRaises(KeyError):
            self.calculator.calculate(
                user_id="user:test",
                as_of_date="2026-07-24",
                holdings=[_holding("asset:a", 10, 8)],
                prices={},
            )
        with self.assertRaises(ValueError):
            self.calculator.calculate(
                user_id="user:test",
                as_of_date="2026-07-24",
                holdings=[_holding("asset:a", -1, 8)],
                prices={"asset:a": 10},
            )


class PositionBudgetCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = PositionBudgetCalculator()
        self.mandate = InvestmentMandate(
            mandate_id="mandate:absolute",
            objective="absolute_return",
            implementation_asset_id="asset:etf:risk",
            benchmark_asset_id="asset:index:benchmark",
            defensive_asset_id="asset:etf:bond",
            target_volatility=Decimal("0.10"),
            max_drawdown=Decimal("0.20"),
            max_tracking_error=None,
            max_risk_weight=Decimal("0.80"),
            min_cash_weight=Decimal("0.05"),
            drawdown_governor_enabled=True,
            timing_overlay_enabled=True,
        )

    def test_volatility_target_sets_total_risk_weight(self):
        result = self.calculator.calculate(
            self.mandate,
            _budget_input(
                volatility="0.20",
                drawdown="0",
                percentile="1",
            ),
        )

        self.assertEqual(result.risk_weight, Decimal("0.5"))
        self.assertEqual(
            result.defensive_weight,
            Decimal("0.45"),
        )
        self.assertEqual(result.cash_weight, Decimal("0.05"))
        self.assertEqual(
            result.estimated_portfolio_volatility,
            Decimal("0.100"),
        )

    def test_drawdown_and_timing_reduce_risk_monotonically(self):
        low = self.calculator.calculate(
            self.mandate,
            _budget_input("0.20", "-0.10", "0"),
        )
        middle = self.calculator.calculate(
            self.mandate,
            _budget_input("0.20", "-0.10", "0.5"),
        )
        high = self.calculator.calculate(
            self.mandate,
            _budget_input("0.20", "-0.10", "1"),
        )

        self.assertEqual(low.risk_weight, Decimal("0.050"))
        self.assertLess(low.risk_weight, middle.risk_weight)
        self.assertLess(middle.risk_weight, high.risk_weight)
        self.assertIn(
            "drawdown_governor",
            low.binding_constraints,
        )
        self.assertIn("timing_overlay", low.binding_constraints)

    def test_unvalidated_overlays_are_disabled_by_default(self):
        mandate = replace(
            self.mandate,
            drawdown_governor_enabled=False,
            timing_overlay_enabled=False,
        )
        result = self.calculator.calculate(
            mandate,
            _budget_input("0.20", "-0.10", "0"),
        )

        self.assertEqual(result.risk_weight, Decimal("0.5"))
        self.assertNotIn(
            "drawdown_governor",
            result.binding_constraints,
        )
        self.assertNotIn(
            "timing_overlay",
            result.binding_constraints,
        )

    def test_relative_mandate_reports_tracking_error_conflict(self):
        relative = InvestmentMandate(
            mandate_id="mandate:relative",
            objective="relative_return",
            implementation_asset_id="asset:etf:risk",
            benchmark_asset_id="asset:index:benchmark",
            defensive_asset_id=None,
            target_volatility=Decimal("0.10"),
            max_drawdown=Decimal("0.20"),
            max_tracking_error=Decimal("0.04"),
            max_risk_weight=Decimal("1"),
            min_cash_weight=Decimal("0"),
            drawdown_governor_enabled=True,
            timing_overlay_enabled=True,
        )
        result = self.calculator.calculate(
            relative,
            PositionBudgetInput(
                as_of_date="2026-08-07",
                realized_volatility=Decimal("0.20"),
                current_drawdown=Decimal("-0.10"),
                timing_score_percentile=Decimal("0"),
                benchmark_volatility=Decimal("0.20"),
            ),
        )

        self.assertEqual(
            result.estimated_tracking_error,
            Decimal("0.1900"),
        )
        self.assertEqual(
            result.warnings,
            ("tracking_error_budget_exceeded",),
        )
        self.assertEqual(
            result.cash_weight,
            ONE - result.risk_weight,
        )

    def test_mandate_rejects_inconsistent_constraints(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires max_tracking_error",
        ):
            InvestmentMandate(
                mandate_id="bad",
                objective="relative_return",
                implementation_asset_id="risk",
                benchmark_asset_id="benchmark",
                defensive_asset_id=None,
                target_volatility=Decimal("0.10"),
                max_drawdown=Decimal("0.20"),
                max_tracking_error=None,
                max_risk_weight=Decimal("0.8"),
                min_cash_weight=Decimal("0.05"),
            )


class PortfolioRiskServiceTest(unittest.TestCase):
    def test_snapshot_is_persisted_idempotently_and_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "portfolio.sqlite3"
            users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
            context = users.default_context()
            other = users.create_user("Other")
            other_context = users.context_for_user(other.user_id)
            master = MasterDataService(SQLiteMasterDataRepository(db_path))
            asset = master.resolve_identifier(
                source="unit-test",
                raw_symbol="518880",
                name="黄金ETF",
                asset_type_hint="ETF",
            ).asset
            research = ResearchStateService(
                SQLiteResearchStateRepository(db_path),
                users,
                master,
            )
            research.upsert_holding(
                context,
                asset_id=asset.asset_id,
                quantity=100,
                average_cost=8.1,
            )
            service = PortfolioRiskService(
                SQLitePortfolioRepository(db_path),
                research,
            )

            first = service.value_current_holdings(
                context,
                as_of_date="2026-07-24",
                prices={asset.asset_id: 8.385},
            )
            second = service.value_current_holdings(
                context,
                as_of_date="2026-07-24",
                prices={asset.asset_id: 8.4},
            )

            self.assertEqual(first.snapshot.snapshot_id, second.snapshot.snapshot_id)
            self.assertEqual(len(service.list_snapshots(context)), 1)
            self.assertEqual(
                service.get_snapshot(context, second.snapshot.snapshot_id),
                second,
            )
            self.assertEqual(service.list_snapshots(other_context), [])
            self.assertIsNone(
                service.get_snapshot(
                    other_context,
                    second.snapshot.snapshot_id,
                )
            )


def _holding(asset_id, quantity, average_cost):
    return HoldingPosition(
        user_id="user:test",
        asset_id=asset_id,
        quantity=quantity,
        average_cost=average_cost,
    )


ONE = Decimal("1")


def _budget_input(
    volatility,
    drawdown,
    percentile,
) -> PositionBudgetInput:
    return PositionBudgetInput(
        as_of_date="2026-08-07",
        realized_volatility=Decimal(volatility),
        current_drawdown=Decimal(drawdown),
        timing_score_percentile=(
            Decimal(percentile) if percentile is not None else None
        ),
    )


if __name__ == "__main__":
    unittest.main()
