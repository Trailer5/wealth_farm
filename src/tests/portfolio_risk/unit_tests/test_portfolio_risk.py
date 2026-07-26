import tempfile
import unittest
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import (
    PortfolioCalculator,
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


if __name__ == "__main__":
    unittest.main()
