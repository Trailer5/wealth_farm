import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import (
    NOTIONAL_TOTAL,
    HoldingWeights,
    PortfolioRiskService,
    SQLitePortfolioRepository,
    WeightItem,
)
from src.research_orchestration.portfolio_diagnosis import CASH_ASSET_ID, PortfolioDiagnosisService
from src.research_state import ResearchStateService, SQLiteResearchStateRepository
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


def _snapshot(user_id: str, as_of_date: str = "2026-11-17", cash: str = "0.60") -> HoldingWeights:
    return HoldingWeights(
        user_id=user_id,
        as_of_date=as_of_date,
        items=(
            WeightItem("现金及活期", Decimal(cash), asset_class="cash"),
            WeightItem("其他债券", Decimal("0.30"), asset_class="bond"),
            WeightItem("其他高风险基金", Decimal("0.10"), asset_class="equity"),
        ),
        user_statement="只给比例",
    )


class HoldingWeightsTest(unittest.TestCase):
    def test_lines_must_add_up_and_name_an_asset_or_a_class(self):
        with self.assertRaises(ValueError):
            _snapshot("user:a", cash="0.40")
        with self.assertRaises(ValueError):
            WeightItem("x", Decimal("0.1"))
        with self.assertRaises(ValueError):
            WeightItem("x", Decimal("0.1"), asset_class="crypto")
        snapshot = _snapshot("user:a")
        self.assertTrue(snapshot.snapshot_id.startswith("weights:"))
        self.assertEqual(HoldingWeights.from_dict(snapshot.to_dict()), snapshot)

    def test_latest_snapshot_on_or_before_the_date_is_user_scoped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "portfolio.sqlite3"
            users = UserSpaceService(SQLiteUserSpaceRepository(db_path))
            context = users.default_context()
            master = MasterDataService(SQLiteMasterDataRepository(db_path))
            research = ResearchStateService(SQLiteResearchStateRepository(db_path), users, master)
            service = PortfolioRiskService(SQLitePortfolioRepository(db_path), research)

            early = _snapshot(context.user_id, "2026-11-10")
            service.record_weights(context, early)
            corrected = _snapshot(context.user_id, "2026-11-17", cash="0.61")
            service.record_weights(context, _snapshot(context.user_id, "2026-11-17"))
            service.record_weights(context, corrected)

            self.assertEqual(service.latest_weights(context, on_or_before="2026-11-12"), early)
            self.assertEqual(service.latest_weights(context, on_or_before="2026-11-30"), corrected)
            other = users.context_for_user(users.create_user("Other").user_id)
            self.assertIsNone(service.latest_weights(other, on_or_before="2026-11-30"))


class _Master:
    def find_assets(self, **_):
        return []

    def get_asset(self, _):
        return None


class WeightedPositionsTest(unittest.TestCase):
    def test_buckets_become_notional_positions_and_cash_one_line(self):
        service = object.__new__(PortfolioDiagnosisService)
        service.master_data = _Master()
        levels: dict = {}
        gaps: list = []
        benchmark = {"2026-11-16": 100.0, "2026-11-17": 101.0}

        positions = service._weighted_positions(
            _snapshot("user:a"), {}, levels, benchmark, None,
            as_of_date="2026-11-17", industry_map={}, allow_remote=False, gaps=gaps,
        )

        values = {item.asset_id: item.market_value for item in positions}
        self.assertAlmostEqual(values[CASH_ASSET_ID], 0.60 * NOTIONAL_TOTAL)
        self.assertAlmostEqual(values["bucket:equity:其他高风险基金"], 0.10 * NOTIONAL_TOTAL)
        self.assertEqual(levels["bucket:equity:其他高风险基金"], benchmark)
        self.assertEqual(levels["bucket:bond:其他债券"], {})
        self.assertTrue(any("名义总资产" in item for item in gaps))
        self.assertTrue(any("其他债券 没有代表品种" in item for item in gaps))


if __name__ == "__main__":
    unittest.main()
