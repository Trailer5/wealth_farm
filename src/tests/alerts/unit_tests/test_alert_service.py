import tempfile
import unittest
from pathlib import Path

from src.alerts import (
    ALERT_ACKNOWLEDGED,
    ALERT_PENDING,
    RULE_PRICE_ABOVE,
    AlertService,
    SQLiteAlertRepository,
)
from src.event_knowledge import EVENT_FINANCIAL_REPORT, ResearchEvent
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import PortfolioSnapshot
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


class AlertServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "alerts.sqlite3"
        self.users = UserSpaceService(SQLiteUserSpaceRepository(self.db_path))
        self.context = self.users.default_context()
        self.master = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master.resolve_identifier(
            source="unit-test",
            raw_symbol="518880",
            name="黄金ETF",
            asset_type_hint="ETF",
        ).asset
        self.service = AlertService(
            SQLiteAlertRepository(self.db_path),
            self.users,
            self.master,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_price_rule_triggers_once_and_can_be_acknowledged(self):
        self.service.create_price_rule(
            self.context,
            asset_id=self.asset.asset_id,
            rule_type=RULE_PRICE_ABOVE,
            threshold=8.0,
        )

        first = self.service.evaluate_price(
            self.context,
            asset_id=self.asset.asset_id,
            price=8.385,
            observed_at="2026-07-24T15:00:00+08:00",
        )
        second = self.service.evaluate_price(
            self.context,
            asset_id=self.asset.asset_id,
            price=8.385,
            observed_at="2026-07-24T15:00:00+08:00",
        )
        acknowledged = self.service.acknowledge(
            self.context,
            first[0].alert_id,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(first[0].status, ALERT_PENDING)
        self.assertEqual(acknowledged.status, ALERT_ACKNOWLEDGED)
        self.assertEqual(
            len(
                self.service.list_alerts(
                    self.context,
                    status=ALERT_ACKNOWLEDGED,
                )
            ),
            1,
        )

    def test_event_type_rule_uses_event_id_for_deduplication(self):
        self.service.create_event_rule(
            self.context,
            event_type=EVENT_FINANCIAL_REPORT,
            asset_id=self.asset.asset_id,
        )
        event = ResearchEvent(
            event_id="event:annual",
            asset_id=self.asset.asset_id,
            event_type=EVENT_FINANCIAL_REPORT,
            event_date="2026-04-17",
            title="黄金ETF年度报告",
            summary="",
            source="cninfo",
            source_record_id="1",
            source_url="https://example.com/report.pdf",
            confidence=0.95,
            extraction_method="title_rule",
            rule_version="v1",
        )

        first = self.service.evaluate_event(self.context, event)
        second = self.service.evaluate_event(self.context, event)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(first[0].payload["event_id"], event.event_id)

    def test_portfolio_weight_rule_triggers(self):
        self.service.create_max_weight_rule(
            self.context,
            threshold=0.6,
        )
        snapshot = PortfolioSnapshot(
            snapshot_id="portfolio:test",
            user_id=self.context.user_id,
            as_of_date="2026-07-24",
            total_market_value=838.5,
            total_cost_value=810.0,
            unrealized_pnl=28.5,
            max_weight=1.0,
            concentration_hhi=1.0,
        )

        alerts = self.service.evaluate_portfolio(
            self.context,
            snapshot,
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].payload["max_weight"], 1.0)

    def test_users_only_query_their_own_alerts(self):
        other = self.users.create_user("Other")
        other_context = self.users.context_for_user(other.user_id)
        self.service.create_price_rule(
            self.context,
            asset_id=self.asset.asset_id,
            rule_type=RULE_PRICE_ABOVE,
            threshold=8.0,
        )
        self.service.evaluate_price(
            self.context,
            asset_id=self.asset.asset_id,
            price=8.385,
            observed_at="2026-07-24",
        )

        self.assertEqual(self.service.list_alerts(other_context), [])
        self.assertEqual(self.service.list_rules(other_context), [])


if __name__ == "__main__":
    unittest.main()
