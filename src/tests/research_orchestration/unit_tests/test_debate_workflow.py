import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.research_debate import DebateService, SQLiteDebateRepository
from src.research_ledger import ClaimResult, SettlementStatus
from src.research_orchestration.debate import DebateWorkflow, debate_next_actions


@dataclass(frozen=True)
class _Observation:
    period_end: str
    value: Decimal
    available_at: str
    recorded_at: str


@dataclass(frozen=True)
class _Settlement:
    checkpoint_date: str
    status: SettlementStatus
    claim_result: ClaimResult
    computed_at: str


class _Intel:
    def __init__(self, observations):
        self._observations = observations

    def observations(self, observable_id):
        return self._observations.get(observable_id, [])


class _Ledger:
    def __init__(self, settlements):
        self._settlements = settlements

    def list_settlements(self, entry_id, *, user_id):
        if entry_id not in self._settlements:
            raise KeyError(entry_id)
        return self._settlements[entry_id]


class DebateWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = DebateService(
            SQLiteDebateRepository(Path(self.temp_dir.name) / "debate.sqlite3"),
            clock=lambda: datetime(2026, 11, 15, 12, tzinfo=timezone.utc),
        )
        debate = self.service.start(
            user_id="user:default", topic="辩题", trigger="起因", as_of_date="2026-11-15"
        )
        self.debate_id = debate.debate_id
        for party, ledger_ref in (("user", "ledger-user"), ("persona:li_lu", "ledger-li")):
            position = self.service.add_position(
                self.debate_id,
                party=party,
                statement=f"{party} 的命题",
                mechanism="机制",
                horizon="medium_long",
                falsifiers=("证伪",),
            )
            rebuttal = self.service.rebut(
                self.debate_id,
                position_id=position.position_id,
                party="agent",
                argument="反方",
                kind="fact",
            )
            self.service.answer(self.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=False, response="依据")
            self.service.resolve(
                self.debate_id, position_id=position.position_id, status="stands", note="立住", ledger_refs=(ledger_ref,)
            )
        self.service.add_signpost(
            self.debate_id,
            observation="长鑫是否被列入实体清单",
            trigger="值变为 1",
            action="回避长鑫",
            refs=("obs:us-cxmt-entity-list",),
        )
        self.service.add_signpost(
            self.debate_id, observation="AMD 复评", trigger="复评日", action="更新推荐比例", due_on="2027-02-15"
        )
        self.service.add_signpost(
            self.debate_id, observation="云厂商资本开支", trigger="下调", action="维持回避", refs=("obs:quiet",)
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _workflow(self):
        intel = _Intel(
            {
                "obs:us-cxmt-entity-list": [
                    _Observation("2026-11-15", Decimal("0"), "2026-11-15", "2026-11-15T09:00:00+00:00"),
                    _Observation("2027-01-20", Decimal("1"), "2027-01-20", "2027-01-20T09:00:00+00:00"),
                ],
                "obs:quiet": [_Observation("2026-11-15", Decimal("0"), "2026-11-15", "2026-11-15T09:00:00+00:00")],
            }
        )
        ledger = _Ledger(
            {
                "ledger-user": [
                    _Settlement("2027-05-14", SettlementStatus.SETTLED, ClaimResult.MISSED, "2027-05-15T00:00:00+00:00"),
                    _Settlement("2027-05-14", SettlementStatus.SETTLED, ClaimResult.MET, "2027-05-16T00:00:00+00:00"),
                ],
                "ledger-li": [],
            }
        )
        return DebateWorkflow(debates=self.service, ledger=ledger, intel=intel)

    def test_watch_flags_moved_observables_and_due_dates_only(self):
        watch = self._workflow().watch(user_id="user:default", as_of_date="2027-02-05")

        flagged = {row["signpost"]["observation"]: row["reasons"] for row in watch["signposts"]}
        self.assertEqual(set(flagged), {"长鑫是否被列入实体清单", "AMD 复评"})
        self.assertIn("有新观测：1", flagged["长鑫是否被列入实体清单"][0])
        self.assertIn("14 天内到期", flagged["AMD 复评"][0])
        actions = debate_next_actions(watch)
        self.assertIn("回避长鑫", actions[0]["reason"])

    def test_scorecard_counts_latest_settlement_per_checkpoint_by_party(self):
        card = self._workflow().scorecard(user_id="user:default")

        user = card["parties"]["user"]
        self.assertEqual(user["checkpoints"]["met"], 1)
        self.assertEqual(user["checkpoints"]["missed"], 0)
        self.assertEqual(user["hit_rate"], 1.0)
        persona = card["parties"]["persona:li_lu"]
        self.assertEqual(persona["awaiting_entries"], 1)
        self.assertIsNone(persona["hit_rate"])

    def test_routine_suggests_the_agenda_when_no_recent_world_debate(self):
        stale = self._workflow().watch(user_id="user:default", as_of_date="2026-12-01")
        fresh = self._workflow().watch(user_id="user:default", as_of_date="2026-11-18")

        self.assertTrue(any("--action agenda" in item["command"] for item in debate_next_actions(stale)))
        self.assertFalse(any("--action agenda" in item["command"] for item in debate_next_actions(fresh)))

    def test_agenda_collects_archive_signals_and_world_topic_news(self):
        @dataclass(frozen=True)
        class _Entry:
            entry_id: str
            source_ref: str
            subject_id: str
            as_of_date: str
            payload: dict

        class _AgendaLedger(_Ledger):
            def list_entries(self, *, user_id, entry_type):
                return [
                    _Entry("ledger-m1", "market_archive:2026-11-14:a", "market:cn_a", "2026-11-14",
                           {"label": "估值中等", "change_signals": ["近 60 日宽度下降"]}),
                    _Entry("ledger-s1", "sector_archive:850818:2026-11-01:b", "sector:sw:850818", "2026-11-01",
                           {"label": "过期", "change_signals": ["太早"]}),
                    _Entry("ledger-v1", "valuation_model:x:2026-11-14", "asset:x", "2026-11-14",
                           {"label": "不是档案", "change_signals": ["x"]}),
                ]

        class _SearchIntel(_Intel):
            def __init__(self):
                super().__init__({})
                self.calls = []

            def search(self, source, *, query, language, since, until, limit, allow_remote):
                self.calls.append((language, query, since, until))
                return {"search_id": len(self.calls), "items": [
                    {"item_id": f"intel:{len(self.calls)}", "published_on": "2026-11-15", "title": query}
                ], "error": None}

        intel = _SearchIntel()
        workflow = DebateWorkflow(debates=self.service, ledger=_AgendaLedger({}), intel=intel)

        agenda = workflow.agenda(user_id="user:default", as_of_date="2026-11-16", days=7)

        self.assertEqual([row["ledger_entry_id"] for row in agenda["archive_signals"]], ["ledger-m1"])
        self.assertEqual(len(agenda["topics"]), len(intel.calls) // 2)
        self.assertEqual(intel.calls[0][2:], ("2026-11-09", "2026-11-16"))
        self.assertEqual({lang for lang, *_ in intel.calls}, {"en", "zh"})
        self.assertTrue(agenda["topics"][0]["headlines"])
        self.assertEqual(agenda["recent_world_debates"][0]["debate_id"], self.debate_id)


if __name__ == "__main__":
    unittest.main()
