import json
import tempfile
import unittest
from pathlib import Path

from src.research_ledger import LedgerEntryType, ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.strategy_archive import StrategyArchiveError, StrategyArchiveService
from src.strategy import LifecycleState, SQLiteStrategyArchiveRepository

AS_OF = "2026-10-29"


class StrategyArchiveServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "docs/evidence/strategy_sources").mkdir(parents=True)
        for name in ("etf_dual_momentum.md", "sse_market_timing.md", "ma.md"):
            (root / "docs/evidence/strategy_sources" / name).write_text("x", encoding="utf-8")
        db = root / "db.sqlite3"
        self.ledger = ResearchLedgerService(SQLiteResearchLedgerRepository(db))
        self.service = StrategyArchiveService(
            repository=SQLiteStrategyArchiveRepository(db), ledger=self.ledger, project_root=root
        )

    def tearDown(self):
        self.temp.cleanup()

    def _ledger_states(self, strategy_id):
        return sorted(
            entry.artifact.dimensions[0].value
            for entry in self.ledger.list_entries(
                user_id=None, entry_type=LedgerEntryType.ASSESSMENT, subject_id=f"strategy:{strategy_id}"
            )
        )

    def test_built_in_rules_are_archived_once_and_sourced_ones_validate(self):
        first = {row["strategy_id"]: row for row in self.service.list_archives(AS_OF)["strategies"]}
        again = self.service.list_archives(AS_OF)["strategies"]
        self.assertEqual(len(first), 6)
        self.assertEqual(len(again), 6)
        self.assertEqual(first["etf_dual_momentum_trend"]["state"], "validating")
        self.assertEqual(first["quality_value"]["state"], "research")
        self.assertIn("没有来源登记", first["quality_value"]["next_blockers"][0])
        self.assertEqual(first["etf_dual_momentum_trend"]["next_blockers"], ["没有完整验证记录（strategy-evaluate）"])
        self.assertEqual(self._ledger_states("etf_dual_momentum_trend"), ["research", "validating"])

    def test_refused_transition_is_logged_without_moving(self):
        self.service.list_archives(AS_OF)
        result = self.service.transition(
            "etf_dual_momentum_trend", LifecycleState.FORWARD_TRACKING, as_of_date=AS_OF, reason="试试"
        )
        self.assertEqual(result["status"], "rejected")
        shown = self.service.show("etf_dual_momentum_trend", AS_OF)
        self.assertEqual(shown["state"], "validating")
        self.assertEqual(shown["events"][-1]["event_type"], "rejected")
        self.assertEqual(self._ledger_states("etf_dual_momentum_trend"), ["research", "validating"])

    def test_register_new_strategy_and_versions(self):
        payload = {
            "strategy_id": "ma_timing",
            "version": "v1",
            "name": "均线择时",
            "kind": "timing",
            "mechanism": "收盘价在 200 日均线之上持有",
            "universe": "沪深300",
            "horizons": ["medium_short"],
            "parameters": {"window": "200"},
            "source_refs": ["docs/evidence/strategy_sources/ma.md"],
            "failure_conditions": ["样本外跑输买入持有"],
            "limitations": [],
        }
        self.assertEqual(self.service.register(payload, AS_OF)["status"], "registered")
        self.assertEqual(self.service.register(payload, AS_OF)["status"], "unchanged")
        with self.assertRaises(StrategyArchiveError):
            self.service.register({**payload, "mechanism": "改了"}, AS_OF)
        moved = self.service.transition("ma_timing", LifecycleState.VALIDATING, as_of_date=AS_OF, reason="来源已登记")
        self.assertEqual(moved["status"], "moved")
        self.service.register({**json.loads(json.dumps(payload)), "version": "v2"}, AS_OF)
        shown = self.service.show("ma_timing", AS_OF)
        self.assertEqual(shown["archive"]["version"], "v2")
        self.assertEqual(shown["state"], "research")
        self.assertEqual([item["version"] for item in shown["versions"]], ["v1", "v2"])

    def test_retire_needs_a_reason(self):
        self.service.list_archives(AS_OF)
        refused = self.service.transition("trend_timing", LifecycleState.RETIRED, as_of_date=AS_OF, reason="")
        self.assertEqual(refused["status"], "rejected")
        retired = self.service.transition(
            "trend_timing", LifecycleState.RETIRED, as_of_date=AS_OF, reason="失效条件触发"
        )
        self.assertEqual(retired["state"], "retired")
        self.assertIsNone(self.service.show("trend_timing", AS_OF)["next_state"])

    def test_unknown_strategy(self):
        with self.assertRaises(StrategyArchiveError):
            self.service.show("nope", AS_OF)


if __name__ == "__main__":
    unittest.main()
