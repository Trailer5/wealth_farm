import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
AGENT_ROOT = PROJECT_ROOT / ".agent"
RESEARCH_DOCS = AGENT_ROOT / "docs" / "research"

REQUIRED_RESEARCH_GUIDES = (
    "mainline.md",
    "cold_start_intake.md",
    "agent_charter.md",
    "user_decision_model.md",
    "user_context_questions.md",
    "profile_routing.md",
    "project_capability_map.md",
    "research_strategy_playbook.md",
    "exploration_playbook.md",
    "final_report_contract.md",
    "agent_acceptance.md",
    "discovery_handoff.md",
    "event_name_discovery.md",
)


class AgentGuidanceTest(unittest.TestCase):
    def test_mainline_routes_every_decision_research_stage(self):
        mainline = self._read(RESEARCH_DOCS / "mainline.md")

        for guide in (
            "cold_start_intake.md",
            "user_decision_model.md",
            "profile_routing.md",
            "project_capability_map.md",
            "research_strategy_playbook.md",
            "exploration_playbook.md",
            "user_context_questions.md",
            "final_report_contract.md",
            "agent_acceptance.md",
        ):
            self.assertIn(guide, mainline)

    def test_research_guidance_references_existing_markdown(self):
        paths = [
            AGENT_ROOT / "SKILL.md",
            AGENT_ROOT / "docs" / "README.md",
            *(
                RESEARCH_DOCS / filename
                for filename in REQUIRED_RESEARCH_GUIDES
            ),
        ]
        missing = []
        for path in paths:
            content = self._read(path)
            for reference in re.findall(r"`([^`\n]+\.md)`", content):
                candidates = (
                    PROJECT_ROOT / reference,
                    path.parent / reference,
                )
                if not any(candidate.is_file() for candidate in candidates):
                    missing.append(f"{path.name}: {reference}")

        self.assertEqual(missing, [])

    def test_event_name_rules_have_one_specialized_owner(self):
        strategy = self._read(
            RESEARCH_DOCS / "research_strategy_playbook.md"
        )
        event_guide = self._read(
            RESEARCH_DOCS / "event_name_discovery.md"
        )

        self.assertNotIn("## 市场共识与名称注意力专项", strategy)
        self.assertIn("event_name_discovery.md", strategy)
        self.assertIn(
            "EventNameDiscoveryOrchestrationPipeline",
            event_guide,
        )
        self.assertIn("预测验证", event_guide)

    def test_current_capability_gaps_are_not_presented_as_implemented(self):
        capability_map = self._read(
            RESEARCH_DOCS / "project_capability_map.md"
        )
        final_contract = self._read(
            RESEARCH_DOCS / "final_report_contract.md"
        )

        self.assertIn("`ResearchRequestPlanner`", capability_map)
        self.assertIn("规划 ready 不等于已经执行", capability_map)
        self.assertIn("不存在自然语言意图分类", capability_map)
        self.assertIn(
            "`PersistentResearchExplorationService`",
            capability_map,
        )
        self.assertIn(
            "`ResearchExplorationQueryService`",
            capability_map,
        )
        self.assertIn(
            "`SuitabilityAssessmentService`",
            capability_map,
        )
        self.assertIn(
            "`EntryPlanService`",
            capability_map,
        )
        self.assertIn(
            "`PositionPlanService`",
            capability_map,
        )
        self.assertIn(
            "静默自动生成与循环执行",
            capability_map,
        )
        self.assertIn("`FinalDecisionReportReviewer`", capability_map)
        self.assertIn(
            "`container.final_decision.assemble_and_publish(...)`",
            final_contract,
        )

    def test_capability_map_defers_to_registry_and_module_docs(self):
        capability_map = self._read(
            RESEARCH_DOCS / "project_capability_map.md"
        )
        mainline = self._read(RESEARCH_DOCS / "mainline.md")

        self.assertIn("`ResearchCapabilityRegistry`", capability_map)
        self.assertIn("docs/modules/研究编排模块.md", capability_map)
        for profile_id in (
            "ordinary_company",
            "financial_institution",
            "cyclical_resource",
            "exchange_fund",
            "open_fund",
        ):
            self.assertNotIn(profile_id, capability_map)
        self.assertIn("`BaselineResearchHandoff`", mainline)
        self.assertIn("没有 handoff", mainline)

    def test_final_report_answers_three_decisions_without_fake_sizing(self):
        decision_model = self._read(
            RESEARCH_DOCS / "user_decision_model.md"
        )
        report_contract = self._read(
            RESEARCH_DOCS / "final_report_contract.md"
        )

        for decision in ("是否值得持有或购买", "何时行动", "购买或持有多少"):
            self.assertIn(decision, decision_model)
        for section in (
            "决策摘要",
            "用户目标与适用边界",
            "是否值得买",
            "何时行动",
            "购买多少",
            "最强反方",
            "失效、退出与复查",
            "关键证据与未知",
        ):
            self.assertIn(section, report_contract)
        self.assertIn("固定给出 5%、10%", decision_model)
        self.assertIn("不给假定比例", report_contract)

    def test_new_conversation_acceptance_matches_public_runner(self):
        acceptance = self._read(
            RESEARCH_DOCS / "agent_acceptance.md"
        )
        mainline = self._read(RESEARCH_DOCS / "mainline.md")

        for value in (
            "slice12_v1",
            "NewConversationAcceptanceRunner",
            "remote_failure",
            "business_empty",
            "BaselineResearchHandoff",
            "一轮支持证据",
            "一轮反证",
        ):
            self.assertIn(value, acceptance)
        self.assertIn("NewConversationAcceptanceRunner", mainline)
        self.assertIn("runner 只检查真实传入的公开输出", mainline)

    def _read(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"Missing guidance file: {path}")
        return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
