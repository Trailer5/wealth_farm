import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.research_ledger import LedgerEntryType, ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_ledger.decision_review import summarize_decisions
from src.research_orchestration.strategy_archive import StrategyArchiveError, StrategyArchiveService
from src.research_orchestration.strategy_backtest import StrategyBacktestService
from src.research_orchestration.strategy_track import StrategyTrackService
from src.strategy import LifecycleState, SQLiteStrategyArchiveRepository
from src.strategy.signals import SIGNAL_GENERATORS, SignalGenerator

DAYS = [(date(2021, 1, 1) + timedelta(days=i)).isoformat() for i in range(500)]


class _DataAccess:
    def get(self, request):
        rows = [
            {"trade_date": day, "open": 1 + i / 1000, "close": 1 + i / 1000, "preclose": 1, "amount": 1e8, "volume": 1}
            for i, day in enumerate(DAYS)
            if request.start_date <= day <= request.end_date
        ]
        return SimpleNamespace(records=rows, error=None)


class _Master:
    def find_assets(self, *, standard_symbol, asset_type):
        return [SimpleNamespace(asset_id=f"exchange_fund:{standard_symbol}", display_name=standard_symbol)]


class _Fetch:
    store = SimpleNamespace(get_exchange_listings=lambda code: [])


class _Macro:
    def get(self, series_id, **kwargs):
        return SimpleNamespace(to_dict=lambda limit=None: {"points": [{"period": "2020-01-01", "value": 2.0}]}, points=())


def _weekly(series, calendar, parameters, context):
    """Switches between the two assets every week: many signal days to miss between runs."""

    (a, _), (b, _) = sorted(series.items())
    return [(day, {a: 1.0} if (i // 7) % 2 == 0 else {b: 1.0}, "周度切换") for i, day in enumerate(calendar) if i % 7 == 0]


ARCHIVE = {
    "strategy_id": "weekly",
    "version": "v1",
    "name": "周度切换",
    "kind": "rotation",
    "mechanism": "每周在两只 ETF 之间切换",
    "universe": "510300、510500",
    "horizons": ["short"],
    "parameters": {},
    "source_refs": ["docs/evidence/strategy_sources/weekly.md"],
    "failure_conditions": ["跑输等权"],
    "limitations": [],
    "template_id": "weekly_test",
    "universe_assets": ["510300:exchange_fund", "510500:exchange_fund"],
}


class StrategyTrackServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "docs/evidence/strategy_sources").mkdir(parents=True)
        (root / "docs/evidence/strategy_sources/weekly.md").write_text("x", encoding="utf-8")
        db = root / "db.sqlite3"
        self.repository = SQLiteStrategyArchiveRepository(db)
        self.ledger = ResearchLedgerService(SQLiteResearchLedgerRepository(db))
        self.archives = StrategyArchiveService(repository=self.repository, ledger=self.ledger, project_root=root)
        backtest = StrategyBacktestService(
            archives=self.archives, repository=self.repository, data_access=_DataAccess(), data_fetch=_Fetch(),
            master_data=_Master(), macro_data=_Macro(),
        )
        self.service = StrategyTrackService(backtest=backtest, archives=self.archives, ledger=self.ledger)
        patch = mock.patch.dict(SIGNAL_GENERATORS, {"weekly_test": SignalGenerator("weekly_test", "周度", lambda p: 0, _weekly)})
        patch.start()
        self.addCleanup(patch.stop)
        self.archives.register(ARCHIVE, "2021-06-01")
        self.archives.transition("weekly", LifecycleState.VALIDATING, as_of_date="2021-06-01", reason="来源已登记")

    def tearDown(self):
        self.temp.cleanup()

    def _enter_forward_tracking(self, on):
        archive, _, _ = self.repository.get("weekly")
        flags = dict.fromkeys(
            ("rolling_out_of_sample", "parameter_neighborhood", "deflated_sharpe", "out_of_sample_not_worse_than_benchmark"),
            True,
        )
        self.repository.record_evaluation(archive, as_of_date=on, flags=flags, payload={})
        moved = self.archives.transition("weekly", LifecycleState.FORWARD_TRACKING, as_of_date=on, reason="验证完整")
        self.assertEqual(moved["status"], "moved")

    def test_only_tracked_states_get_signals(self):
        with self.assertRaisesRegex(StrategyArchiveError, "前向跟踪"):
            self.service.track("weekly", as_of_date="2021-07-01", allow_remote=False)

    def test_signals_are_recorded_once_gaps_are_logged_and_enabling_is_refused(self):
        self._enter_forward_tracking("2021-07-01")
        first = self.service.track("weekly", as_of_date="2021-07-02", allow_remote=False)
        self.assertEqual(first["status"], "recorded")
        self.assertEqual(len(first["ledger_entries"]), 2)
        self.assertEqual(sum(first["target_weights"].values()), 1.0)
        again = self.service.track("weekly", as_of_date="2021-07-02", allow_remote=False)
        self.assertEqual(again["status"], "already_recorded")
        self.assertEqual(again["signals_recorded"], 1)
        later = self.service.track("weekly", as_of_date="2021-08-15", allow_remote=False)
        self.assertEqual(later["status"], "recorded")
        self.assertGreaterEqual(len(later["missed_signal_dates"]), 5)
        gaps = [event for event in self.repository.events("weekly") if event["event_type"] == "forward_gap"]
        self.assertEqual(gaps[-1]["detail"]["missed_signal_dates"], later["missed_signal_dates"])
        self.assertIsNotNone(later["forward"]["excess_return"])
        entries = self.ledger.list_entries(user_id=None, entry_type=LedgerEntryType.ACTION)
        self.assertEqual(len(entries), 4)
        self.assertTrue(all(entry.source_ref.startswith("strategy:weekly:v1:") for entry in entries))
        refused = self.archives.transition("weekly", LifecycleState.ENABLED, as_of_date="2021-08-15", reason="试一下")
        self.assertEqual(refused["status"], "rejected")
        self.assertTrue(any("不足 6 个月" in item for item in refused["blockers"]))
        self.assertTrue(any("已结算信号 0 个" in item for item in refused["blockers"]))
        self.assertIn("暂无已结算信号，命中率尚无法判断", refused["blockers"])
        self.assertNotIn("命中率区间劣于基准", refused["blockers"])
        self.assertIsNone(later["forward"]["hit_rate_not_worse"])
        self.assertLessEqual(later["rule_signal_date"], later["signal_date"])
        self.assertTrue(later["position"])
        backdated = self.archives.transition("weekly", LifecycleState.RETIRED, as_of_date="2021-06-01", reason="失效")
        self.assertEqual(backdated["status"], "rejected")
        self.assertIn("时间线会倒序", backdated["blockers"][-1])

    def test_decision_review_groups_actions_by_strategy(self):
        def entry(source_ref, result):
            return {
                "entry_id": source_ref, "entry_type": "action", "source_ref": source_ref, "subject_id": "a",
                "as_of_date": "2021-01-01", "effective_until": "2021-01-01", "superseded": False,
                "artifact": {"action_type": "buy", "horizon": "short", "trigger": {"kind": "scheduled"}},
                "settlements": [{"status": "settled", "checkpoint_date": "2021-02-01", "claim_result": result,
                                 "asset_return": "0.01"}],
            }

        summary = summarize_decisions(
            [entry("strategy:x:v1:2021-01-01", "met"), entry("strategy:y:v1:2021-01-01", "missed"),
             entry("exit_plan:1", "met")],
            as_of_date="2021-03-01",
        )
        groups = {row["group"]: row for row in summary["actions"]["by_strategy"]}
        self.assertEqual(set(groups), {"x", "y"})
        self.assertIn("策略前向信号", {row["group"] for row in summary["actions"]["by_source"]})


if __name__ == "__main__":
    unittest.main()
