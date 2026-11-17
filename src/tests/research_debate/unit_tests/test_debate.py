import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.research_debate import (
    POSITION_REFINED,
    POSITION_REFUTED,
    POSITION_STANDS,
    DebateService,
    SQLiteDebateRepository,
    persona,
    personas_for,
)


class DebateDisciplineTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = DebateService(
            SQLiteDebateRepository(Path(self.temp_dir.name) / "debate.sqlite3"),
            clock=lambda: datetime(2026, 11, 15, tzinfo=timezone.utc),
        )
        self.debate = self.service.start(
            user_id="user:default",
            topic="民主党获胜与黄仁勋离任对 AI 产业链的影响",
            trigger="2026 年中期选举后英伟达 CEO 离任",
            as_of_date="2026-11-15",
        )
        self.debate_id = self.debate.debate_id

    def tearDown(self):
        self.temp_dir.cleanup()

    def _position(self, party="user", **overrides):
        values = {
            "party": party,
            "statement": "美国数据中心建设四年内受限",
            "mechanism": "国会与州政府收紧审批和电价",
            "horizon": "long",
            "falsifiers": ("2027 年无州级暂停",) if party != "user" else (),
        }
        values.update(overrides)
        return self.service.add_position(self.debate_id, **values)

    def test_unchallenged_view_cannot_stand(self):
        position = self._position()

        with self.assertRaisesRegex(ValueError, "没有经过驳斥"):
            self.service.resolve(self.debate_id, position_id=position.position_id, status="stands", note="同意")

    def test_rebuttal_must_come_from_another_party(self):
        position = self._position()

        with self.assertRaisesRegex(ValueError, "其他参与方"):
            self.service.rebut(
                self.debate_id, position_id=position.position_id, party="user", argument="自我反驳", kind="inference"
            )

    def test_view_stands_after_answered_rebuttal_and_refuted_needs_accepted_one(self):
        position = self._position()
        rebuttal = self.service.rebut(
            self.debate_id,
            position_id=position.position_id,
            party="persona:munger",
            argument="总统否决权到 2029 年，联邦立法难",
            kind="fact",
        )
        with self.assertRaisesRegex(ValueError, "未回应"):
            self.service.resolve(self.debate_id, position_id=position.position_id, status="stands", note="x")
        self.service.answer(self.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=False, response="州级与地方渠道仍成立")
        with self.assertRaisesRegex(ValueError, "已接受的驳斥"):
            self.service.resolve(self.debate_id, position_id=position.position_id, status="refuted", note="x")

        resolved = self.service.resolve(
            self.debate_id,
            position_id=position.position_id,
            status="stands",
            note="驳不倒，作为阶段推论",
            ledger_refs=("ledger-1",),
        )

        self.assertEqual(resolved.status, POSITION_STANDS)
        self.assertEqual(resolved.ledger_refs, ("ledger-1",))

    def test_refinement_inherits_the_rebuttals_of_the_version_it_replaces(self):
        original = self._position()
        rebuttal = self.service.rebut(
            self.debate_id, position_id=original.position_id, party="agent", argument="开支外移而非消失", kind="inference"
        )
        self.service.answer(self.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=True, response="接受，改写幅度")
        refined = self._position(party="user", statement="美国本土数据中心增速 2028 年后放缓", supersedes=original.position_id)

        record = self.service.show(self.debate_id)
        by_id = {item["position_id"]: item for item in record["positions"]}
        self.assertEqual(by_id[original.position_id]["status"], POSITION_REFINED)
        stands = self.service.resolve(self.debate_id, position_id=refined.position_id, status="stands", note="修正后立住")
        self.assertEqual(stands.status, POSITION_STANDS)

    def test_another_partys_rewrite_must_face_its_own_rebuttal(self):
        original = self._position()
        rebuttal = self.service.rebut(
            self.debate_id, position_id=original.position_id, party="agent", argument="价格已计入", kind="fact"
        )
        self.service.answer(self.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=True, response="接受")
        rewrite = self._position(party="agent", statement="机会在瓶颈松动的时点", supersedes=original.position_id)

        with self.assertRaisesRegex(ValueError, "没有经过驳斥"):
            self.service.resolve(self.debate_id, position_id=rewrite.position_id, status="stands", note="立住")

    def test_conclude_waits_for_open_positions_and_flags_unlinked_consensus(self):
        position = self._position()
        with self.assertRaisesRegex(ValueError, "待结论"):
            self.service.conclude(self.debate_id, conclusion="结论")
        rebuttal = self.service.rebut(
            self.debate_id, position_id=position.position_id, party="agent", argument="反方", kind="domain"
        )
        self.service.answer(self.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=False, response="依据")
        self.service.resolve(self.debate_id, position_id=position.position_id, status="stands", note="立住")
        commands = [item["command"] for item in self.service.show(self.debate_id)["next_actions"]]
        self.assertTrue(any("--action brief" in item for item in commands))  # world lenses have not spoken
        self.assertTrue(any("--action signpost" in item for item in commands))

        payload = self.service.conclude(
            self.debate_id,
            conclusion="回避北美链，产能环节分批",
            decisions=({"subject": "北美 AI 硬件链", "action": "avoid", "timing": "现在"},),
        )

        self.assertEqual(payload["debate"]["status"], "concluded")
        self.assertEqual(payload["debate"]["decisions"][0]["action"], "avoid")
        self.assertTrue(any("没有关联账本条目" in gap for gap in payload["gaps"]))
        with self.assertRaisesRegex(ValueError, "已结论"):
            self._position()

    def _settled_debate(self, significance):
        debate = self.service.start(
            user_id="user:default",
            topic=f"{significance} 辩题",
            trigger="事件",
            as_of_date="2026-11-16",
            significance=significance,
        )
        position = self.service.add_position(
            debate.debate_id, party="user", statement="命题", mechanism="机制", horizon="short"
        )
        rebuttal = self.service.rebut(
            debate.debate_id, position_id=position.position_id, party="agent", argument="反方", kind="fact"
        )
        self.service.answer(debate.debate_id, rebuttal_id=rebuttal.rebuttal_id, accepted=True, response="接受")
        self.service.resolve(debate.debate_id, position_id=position.position_id, status="refuted", note="驳倒")
        return debate.debate_id

    def test_major_event_must_end_in_decisions_or_named_blockers(self):
        major = self._settled_debate("major")
        with self.assertRaisesRegex(ValueError, "重大事件必须给出 decisions"):
            self.service.conclude(major, conclusion="没有结论")
        with self.assertRaisesRegex(ValueError, "exit"):
            self.service.conclude(
                major, conclusion="买", decisions=({"subject": "688012 北方华创", "action": "buy", "size": "5%", "timing": "现在"},)
            )

        blocked = self.service.conclude(major, conclusion="做不出", blockers=("缺少设备订单数据",))

        self.assertTrue(any("缺少设备订单数据" in gap for gap in blocked["gaps"]))
        routine = self.service.conclude(self._settled_debate("routine"), conclusion="日常观察，无动作")
        self.assertEqual(routine["debate"]["decisions"], [])
        self.assertFalse(any("重大事件" in gap for gap in routine["gaps"]))

    def test_concluded_decisions_can_be_amended_with_a_trail(self):
        debate_id = self._settled_debate("major")
        with self.assertRaisesRegex(ValueError, "已结论"):
            self.service.amend(debate_id, reason="x", as_of_date="2026-11-17", decisions=({"subject": "a", "action": "avoid", "timing": "现在"},))
        buy = {"subject": "688019 安集科技", "action": "buy", "size": "1%", "timing": "现在", "exit": "毛利率<52%"}
        self.service.conclude(debate_id, conclusion="买", decisions=(buy, {"subject": "NVDA", "action": "avoid", "timing": "现在"}))

        amended = self.service.amend(
            debate_id,
            reason="用户把单只损失预算提高到 5%",
            as_of_date="2026-11-17",
            decisions=({**buy, "size": "2%（目标 6%）"},),
        )

        decisions = {item["subject"]: item for item in amended["debate"]["decisions"]}
        self.assertEqual(decisions["688019 安集科技"]["size"], "2%（目标 6%）")
        self.assertIn("NVDA", decisions)
        (trail,) = amended["debate"]["amendments"]
        self.assertEqual(trail["replaced"][0]["size"], "1%")
        with self.assertRaisesRegex(ValueError, "reason"):
            self.service.amend(debate_id, reason=" ", as_of_date="2026-11-17", decisions=(buy,))

    def test_signpost_must_be_checkable(self):
        with self.assertRaisesRegex(ValueError, "refs"):
            self.service.add_signpost(self.debate_id, observation="云厂商资本开支", trigger="下调", action="减仓")

        signpost = self.service.add_signpost(
            self.debate_id,
            observation="云厂商资本开支",
            trigger="任一家下调",
            action="维持回避北美链",
            refs=("obs:us-hyperscaler-capex-cut",),
        )
        self.assertTrue(signpost.signpost_id.endswith(":s1"))

        revised = self.service.add_signpost(
            self.debate_id,
            observation="云厂商资本开支",
            trigger="任一家下调或明显上调",
            action="下调维持回避；上调重新评估",
            refs=("obs:us-hyperscaler-capex-cut",),
            replaces=signpost.signpost_id,
        )
        stored = {item["signpost_id"]: item for item in self.service.show(self.debate_id)["signposts"]}
        self.assertEqual(stored[signpost.signpost_id]["replaced_by"], revised.signpost_id)
        with self.assertRaisesRegex(ValueError, "已被取代"):
            self.service.add_signpost(
                self.debate_id, observation="x", trigger="y", action="z", due_on="2027-01-01", replaces=signpost.signpost_id
            )

    def test_next_round_signpost_replaces_the_previous_rounds_plan(self):
        old = self.service.add_signpost(
            self.debate_id, observation="北方华创建仓条件", trigger="≤480", action="建第一批", due_on="2027-04-30"
        )
        round_two = self.service.start(
            user_id="user:default", topic="第二轮", trigger="新事实", as_of_date="2026-11-16", parent_debate_id=self.debate_id
        )

        new = self.service.add_signpost(
            round_two.debate_id,
            observation="北方华创建仓条件（第二轮）",
            trigger="≤500",
            action="建第一批",
            due_on="2027-04-30",
            replaces=old.signpost_id,
        )

        stored = {item["signpost_id"]: item for item in self.service.show(self.debate_id)["signposts"]}
        self.assertEqual(stored[old.signpost_id]["replaced_by"], new.signpost_id)

    def test_persona_import_records_positions_and_rebuttals_under_the_persona(self):
        user = self._position()
        brief = self.service.brief(self.debate_id, "munger")["brief"]
        self.assertIn(user.position_id, brief)
        self.assertIn("record-view", brief)  # the brief forbids writing commands by name

        result = self.service.import_persona(
            self.debate_id,
            "munger",
            {
                "positions": [
                    {
                        "statement": "瓶颈环节的租金不归可买的公司",
                        "mechanism": "光刻未上市，HBM 在周期顶点",
                        "horizon": "medium_long",
                        "falsifiers": ["设备龙头收入重新加速"],
                    }
                ],
                "rebuttals": [{"position_id": user.position_id, "argument": "开支外移", "kind": "inference"}],
                "cannot_rebut": [],
            },
        )

        self.assertEqual(result["party"], "persona:munger")
        record = self.service.show(self.debate_id)
        self.assertEqual({item["party"] for item in record["positions"]}, {"user", "persona:munger"})
        self.assertEqual(record["rebuttals"][0]["party"], "persona:munger")

    def test_unknown_persona_and_bad_horizon_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知角色"):
            persona("keynes")
        with self.assertRaisesRegex(ValueError, "horizon"):
            self._position(party="agent", horizon="forever")


class DebateLayersTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = DebateService(
            SQLiteDebateRepository(Path(self.temp_dir.name) / "debate.sqlite3"),
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_each_layer_has_its_own_lenses(self):
        self.assertEqual(
            {item.persona_id for item in personas_for("world")},
            {"dalio", "soros", "marks", "industry_analyst", "policy_analyst"},
        )
        self.assertIn("buffett", {item.persona_id for item in personas_for("asset")})

    def test_asset_layer_needs_subjects(self):
        with self.assertRaisesRegex(ValueError, "subjects"):
            self.service.start(user_id="user:default", topic="北方华创", trigger="候选", as_of_date="2026-11-16", layers=("asset",))
        debate = self.service.start(
            user_id="user:default",
            topic="北方华创值不值得买",
            trigger="第一层辩论外推的候选",
            as_of_date="2026-11-16",
            layers=("asset",),
            subjects=("002371 北方华创",),
        )
        with self.assertRaisesRegex(ValueError, "subject"):
            self.service.add_position(
                debate.debate_id, party="agent", statement="低估", mechanism="隐含增速低", horizon="medium_long",
                falsifiers=("x",), layer="asset",
            )

        brief = self.service.brief(debate.debate_id, "buffett")["brief"]
        self.assertIn("002371 北方华创", brief)
        self.assertIn("valuation-model --no-record", brief)

    def test_coverage_marks_absent_lenses_and_records_declared_no_view(self):
        debate = self.service.start(
            user_id="user:default", topic="美联储降息节奏", trigger="议程", as_of_date="2026-11-16"
        )
        world_brief = self.service.brief(debate.debate_id, "dalio")["brief"]
        self.assertIn("第一层", world_brief)
        self.assertNotIn("valuation-model", world_brief)

        self.service.import_persona(
            debate.debate_id,
            "dalio",
            {
                "positions": [
                    {
                        "statement": "美元流动性转松推动新兴市场",
                        "mechanism": "降息 → 美元走弱 → 资本流入",
                        "horizon": "medium_short",
                        "falsifiers": ["美元指数半年内上涨"],
                    }
                ]
            },
        )
        self.service.import_persona(debate.debate_id, "marks", {"no_view": "信用利差数据缺失"})
        with self.assertRaisesRegex(ValueError, "no_view"):
            self.service.import_persona(debate.debate_id, "soros", {})

        record = self.service.show(debate.debate_id)
        status = {row["party"]: row["status"] for row in record["coverage"]["world"]}
        self.assertEqual(status["persona:dalio"], "spoke")
        self.assertEqual(status["persona:marks"], "no_view")
        self.assertEqual(status["persona:soros"], "absent")
        self.assertEqual(status["user"], "absent")
        self.assertTrue(any("索罗斯" in gap for gap in record["gaps"]))
        self.assertFalse(any("马克斯" in gap for gap in record["gaps"]))
        self.assertEqual(record["positions"][0]["layer"], "world")

    def test_asset_persona_in_world_debate_writes_world_layer_views(self):
        debate = self.service.start(user_id="user:default", topic="产能瓶颈", trigger="辩论", as_of_date="2026-11-16")

        result = self.service.import_persona(
            debate.debate_id,
            "munger",
            {"positions": [{"statement": "租金不归可买的公司", "mechanism": "瓶颈未上市", "horizon": "long", "falsifiers": ["x"]}]},
        )

        record = self.service.show(debate.debate_id)
        self.assertEqual(record["positions"][0]["layer"], "world")
        self.assertEqual(record["participations"][0]["participation_id"], result["participation_id"])


if __name__ == "__main__":
    unittest.main()
