import tempfile
import unittest
from pathlib import Path

from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.research_state import ResearchStateService, SQLiteResearchStateRepository
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class ResearchStateServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "research-state.sqlite3"
        self.user_space = UserSpaceService(
            SQLiteUserSpaceRepository(self.db_path)
        )
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="518880",
            name="黄金ETF",
            asset_type_hint="ETF",
        ).asset
        self.service = ResearchStateService(
            SQLiteResearchStateRepository(self.db_path),
            self.user_space,
            self.master_data,
        )
        self.context = self.user_space.default_context()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_watchlist_item_can_be_added_updated_and_removed(self):
        first = self.service.add_watchlist_item(
            self.context,
            asset_id=self.asset.asset_id,
            reason="观察黄金走势",
            tags=("黄金", "ETF", "黄金"),
        )
        updated = self.service.add_watchlist_item(
            self.context,
            asset_id=self.asset.asset_id,
            reason="等待回调",
            tags=("ETF",),
        )

        items = self.service.list_watchlist_items(self.context)
        self.assertEqual(first.watchlist_id, updated.watchlist_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].reason, "等待回调")
        self.assertEqual(items[0].tags, ("ETF",))
        self.assertTrue(
            self.service.remove_watchlist_item(
                self.context,
                asset_id=self.asset.asset_id,
            )
        )
        self.assertEqual(self.service.list_watchlist_items(self.context), [])

    def test_current_holding_is_upserted(self):
        self.service.upsert_holding(
            self.context,
            asset_id=self.asset.asset_id,
            quantity=100,
            average_cost=8.1,
            note="初始仓位",
        )
        self.service.upsert_holding(
            self.context,
            asset_id=self.asset.asset_id,
            quantity=150,
            average_cost=8.2,
            note="加仓后",
        )

        holdings = self.service.list_holdings(self.context)
        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].quantity, 150)
        self.assertEqual(holdings[0].average_cost, 8.2)
        self.assertEqual(holdings[0].note, "加仓后")

    def test_reports_are_saved_as_history(self):
        first = self.service.save_report(
            self.context,
            asset_id=self.asset.asset_id,
            title="黄金ETF初步观察",
            summary="趋势稳定",
            content_markdown="# 结论\n继续观察。",
        )
        second = self.service.save_report(
            self.context,
            asset_id=self.asset.asset_id,
            title="黄金ETF复查",
            summary="维持判断",
            content_markdown="# 复查\n暂无变化。",
            status="final",
        )

        reports = self.service.list_reports(
            self.context,
            asset_id=self.asset.asset_id,
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(
            self.service.get_report(self.context, first.report_id),
            first,
        )
        self.assertEqual(second.status, "final")

    def test_two_users_are_separated_by_normal_queries(self):
        other_user = self.user_space.create_user("Other")
        other_context = self.user_space.context_for_user(other_user.user_id)
        self.service.add_watchlist_item(
            self.context,
            asset_id=self.asset.asset_id,
            reason="默认用户",
        )
        report = self.service.save_report(
            self.context,
            asset_id=self.asset.asset_id,
            title="默认用户报告",
            summary="",
            content_markdown="仅用于普通查询隔离测试。",
        )

        self.assertEqual(
            self.service.list_watchlist_items(other_context),
            [],
        )
        self.assertEqual(self.service.list_reports(other_context), [])
        self.assertIsNone(
            self.service.get_report(other_context, report.report_id)
        )


if __name__ == "__main__":
    unittest.main()
