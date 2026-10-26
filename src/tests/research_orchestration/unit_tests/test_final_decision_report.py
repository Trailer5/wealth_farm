from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.portfolio_risk import portfolio_valuation_digest
from src.research_orchestration import (
    EntryConditionKind,
    EntryPlanStatus,
    EvidenceTier,
    ExplorationStatus,
    FinalDecisionReportAssemblyService,
    FinalDecisionOutcome,
    FinalDecisionReportRenderer,
    FinalDecisionReportReviewer,
    FinalDecisionReportService,
    FinalDecisionResearchReport,
    FinalEvidenceCitation,
    FinalEvidenceKind,
    FinalReportNarrativeInput,
    FinalScenarioBlock,
    FinalScenarioKind,
    HypothesisRole,
    PositionAction,
    PositionPlanStatus,
    ResearchExplorationService,
    ResearchRunRequest,
    ResearchRunStatus,
    SuitabilityAction,
    SuitabilityClaimBlock,
    SuitabilityClaimKind,
    SuitabilityClaimState,
    SuitabilityConfidence,
    TimingConditionState,
    final_decision_report_filename,
)
from src.research_orchestration.final_decision_models import (
    final_decision_report_id,
)
from src.research_visualization import ReportArtifactService
from src.research_contracts import (
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchHorizon,
)


AS_OF = "2026-08-24"
EVIDENCE_URL = "https://official.example/disclosure"


def _claim(text: str) -> SuitabilityClaimBlock:
    return SuitabilityClaimBlock(
        claim_id=f"claim:{text}",
        text=text,
        kind=SuitabilityClaimKind.INFERENCE,
        state=SuitabilityClaimState.SUPPORTED,
        evidence_refs=(EVIDENCE_URL,),
        limitations=("结论受截止日与披露范围限制。",),
        time_horizon=ResearchHorizon.LONG,
        as_of_date=AS_OF,
    )


def _inputs() -> dict[str, object]:
    baseline_content = "# 基础研究包\n"
    task = SimpleNamespace(
        task_id="task:test",
        asset_id="stock:test",
        profile_id="ordinary_company",
        as_of_date=AS_OF,
        benchmark_asset_id="index:test",
    )
    handoff = SimpleNamespace(
        handoff_id="handoff:test",
        workflow_run_id="workflow:test",
        task=task,
        report_path="report/测试标的-基础研究包-2026-08-24.md",
        report_digest=hashlib.sha256(
            baseline_content.encode("utf-8")
        ).hexdigest(),
        chart_artifacts=(),
        run_status=ResearchRunStatus.COMPLETE,
        asset_identity=SimpleNamespace(
            asset_id="stock:test",
            symbol="TEST",
            display_name="测试标的",
        ),
        partial_gaps=(),
        blocking_gaps=(),
        capability_gaps=(),
    )
    hypothesis = SimpleNamespace(
        role=HypothesisRole.THESIS,
        supporting_evidence_refs=(EVIDENCE_URL,),
        counterevidence_refs=(),
        falsification_conditions=("核心收益来源连续两个复核期失效。",),
    )
    finding = SimpleNamespace(
        evidence_refs=(EVIDENCE_URL,),
        source_tier=EvidenceTier.PRIMARY,
        as_of_date=AS_OF,
        kind=SimpleNamespace(value="fact"),
    )
    session = SimpleNamespace(
        session_id="session:test",
        task=task,
        baseline_path=Path(handoff.report_path),
        rounds=(
            SimpleNamespace(structured_findings=(finding,)),
        ),
        hypotheses=(hypothesis,),
        status=ExplorationStatus.READY_FOR_REPORT,
        workflow_run_id=handoff.workflow_run_id,
        handoff_id=handoff.handoff_id,
        decision_brief_id="brief:test",
        query_gaps=(),
        user_id="user:test",
    )
    brief = SimpleNamespace(
        brief_id="brief:test",
        user_id="user:test",
        asset_id="stock:test",
        decision_intent=DecisionIntent.EVALUATE_PURCHASE,
        position_state=PositionState.UNKNOWN,
        horizon=ResearchHorizon.LONG,
        as_of_date=AS_OF,
        portfolio_role="卫星配置",
        mandate_id=None,
        portfolio_constraint_snapshot_id=None,
        constraints=("不使用杠杆。",),
        assumptions=("投资期限至少三年。",),
        missing_user_context=(),
    )
    thesis = _claim("收益取决于可验证的经营改善。")
    counterargument = _claim("改善可能只是短期周期反弹。")
    suitability = SimpleNamespace(
        assessment_id="suitability:test",
        session_id=session.session_id,
        workflow_run_id=handoff.workflow_run_id,
        decision_brief_id=brief.brief_id,
        asset_id="stock:test",
        profile_id="ordinary_company",
        as_of_date=AS_OF,
        time_horizon=ResearchHorizon.LONG,
        action=SuitabilityAction.CONSIDER,
        confidence=SuitabilityConfidence.SUPPORTED,
        thesis=thesis,
        expected_return_sources=(_claim("收益来自现金流改善。"),),
        priced_expectations=(_claim("当前定价隐含温和改善。"),),
        alternatives=(_claim("同类宽基资产可作为替代。"),),
        opportunity_costs=(_claim("配置会占用其他风险预算。"),),
        downside=(_claim("需求转弱会压低回报。"),),
        strongest_counterargument=counterargument,
        evidence_refs=(EVIDENCE_URL,),
        limitations=("适配结论不等于时点或仓位结论。",),
        assembly_gaps=(),
    )
    condition = SimpleNamespace(
        condition_id="condition:test",
        text="估值与经营验证同时满足。",
        kind=EntryConditionKind.INFERENCE,
        state=TimingConditionState.WATCH,
        evidence_refs=(EVIDENCE_URL,),
        limitations=("信号仅在有效窗口内适用。",),
        valid_from=AS_OF,
        valid_until="2026-09-30",
        observability_key="valuation_and_operations",
    )
    entry = SimpleNamespace(
        plan_id="entry:test",
        session_id=session.session_id,
        suitability_assessment_id=suitability.assessment_id,
        decision_brief_id=brief.brief_id,
        asset_id="stock:test",
        profile_id="ordinary_company",
        as_of_date=AS_OF,
        execution_horizon=ResearchHorizon.MEDIUM_SHORT,
        suitability_horizon=ResearchHorizon.LONG,
        status=EntryPlanStatus.WAIT,
        current_regime=(condition,),
        entry_conditions=(condition,),
        wait_conditions=(),
        tranche_rules=(),
        avoid_conditions=(),
        catalyst_windows=(),
        invalidation_conditions=(condition,),
        execution_constraints=(),
        horizon_conflicts=(),
        next_tradable_condition=None,
        signal_valid_until="2026-09-30",
        next_tradable_at=None,
        condition_blocks=(condition,),
        evidence_refs=(EVIDENCE_URL,),
        limitations=("时点结论不改变长期适配结论。",),
        assembly_gaps=(),
    )
    position = SimpleNamespace(
        plan_id="position:test",
        session_id=session.session_id,
        workflow_run_id=handoff.workflow_run_id,
        suitability_assessment_id=suitability.assessment_id,
        entry_plan_id=entry.plan_id,
        decision_brief_id=brief.brief_id,
        asset_id="stock:test",
        profile_id="ordinary_company",
        as_of_date=AS_OF,
        horizon=ResearchHorizon.LONG,
        status=PositionPlanStatus.USER_CONTEXT_REQUIRED,
        action=PositionAction.UNAVAILABLE,
        mandate_id=None,
        portfolio_snapshot_id=None,
        constraint_snapshot_id=None,
        calculation_inputs=None,
        evidence_refs=(),
        limitations=("没有组合输入时不提供假定比例。",),
        assembly_gaps=("需要用户确认组合估值与风险预算。",),
    )
    narrative = FinalReportNarrativeInput(
        scenarios=tuple(
            FinalScenarioBlock(
                kind=kind,
                assumptions=(f"{label}假设成立。",),
                observable_variables=("经营现金流与估值变化。",),
                decision_impact=f"{label}情形下重新评估当前动作。",
                limitations=("情景不附加无模型依据的精确概率。",),
                evidence_refs=(EVIDENCE_URL,),
            )
            for kind, label in (
                (FinalScenarioKind.OPTIMISTIC, "乐观"),
                (FinalScenarioKind.BASE, "基准"),
                (FinalScenarioKind.PESSIMISTIC, "悲观"),
            )
        ),
        evidence_citations=(
            FinalEvidenceCitation(
                reference_id=EVIDENCE_URL,
                kind=FinalEvidenceKind.OFFICIAL,
                title="测试标的官方披露",
                source_name="测试交易所",
                published_at="2026-08-20",
                url=EVIDENCE_URL,
                page="12",
            ),
        ),
        review_date="2026-09-30",
        additional_unknowns=("后续需求持续性仍待验证。",),
    )
    return {
        "handoff": handoff,
        "session": session,
        "decision_brief": brief,
        "suitability": suitability,
        "entry_plan": entry,
        "position_plan": position,
        "narrative": narrative,
    }


def _namespace(
    value: object,
    **changes: object,
) -> SimpleNamespace:
    return SimpleNamespace(**{**vars(value), **changes})


class _FakePortfolioRisk:
    def __init__(self):
        snapshot = SimpleNamespace(
            snapshot_id="portfolio-test",
            user_id="user:test",
            as_of_date=AS_OF,
            total_market_value=100000.0,
            total_cost_value=None,
            unrealized_pnl=None,
            max_weight=0.0,
            concentration_hhi=0.0,
            base_currency="CNY",
            holdings_as_of_date=AS_OF,
            evidence_refs=("user:holdings-confirmed",),
        )
        self.valuation = SimpleNamespace(
            snapshot=snapshot,
            positions=(),
        )
        self.constraints = SimpleNamespace(
            snapshot_id="constraints-test",
            user_id="user:test",
            mandate_id="mandate-test",
            portfolio_snapshot_id="portfolio-test",
            portfolio_valuation_digest=portfolio_valuation_digest(
                self.valuation
            ),
            subject_asset_id="stock:test",
            as_of_date=AS_OF,
            currency="CNY",
            evidence_refs=(),
        )

    def get_mandate(self, context, mandate_id):
        return SimpleNamespace(
            mandate_id=mandate_id,
            implementation_asset_id="stock:test",
        )

    def get_snapshot(self, context, snapshot_id):
        return self.valuation

    def get_constraint_snapshot(self, context, snapshot_id):
        return self.constraints


class FinalDecisionReportTests(unittest.TestCase):
    def test_assembly_round_trip_and_ten_section_renderer(self):
        inputs = _inputs()
        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**inputs)

        restored = FinalDecisionResearchReport.from_dict(
            report.to_dict()
        )
        markdown = FinalDecisionReportRenderer().render(restored)

        self.assertEqual(restored, report)
        self.assertIn("## 决策摘要", markdown)
        self.assertIn("## 用户目标与适用边界", markdown)
        self.assertIn("## 标的与核心论文", markdown)
        self.assertIn("## 是否值得买", markdown)
        self.assertIn("## 何时行动", markdown)
        self.assertIn("## 购买多少", markdown)
        self.assertIn("## 三情景", markdown)
        self.assertIn("## 最强反方", markdown)
        self.assertIn("## 失效、退出与复查", markdown)
        self.assertIn("## 关键证据与未知", markdown)
        self.assertIn("第12页", markdown)
        self.assertIn(EVIDENCE_URL, markdown)
        for value in (
            report.report_id,
            report.session_id,
            report.workflow_run_id,
            report.suitability_assessment_id,
            report.entry_plan_id,
            report.position_plan_id,
        ):
            self.assertNotIn(value, markdown)
        self.assertFalse(report.monitoring_plan.alert_rule_refs)
        self.assertEqual(len(report.monitoring_plan.conditions), 2)

    def test_renderer_shows_each_limitation_once_and_labels_falsification(
        self,
    ):
        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**_inputs())
        payload = report.to_dict()
        payload["thesis"]["limitations"] = ["证伪条件：毛利率下降", "限制甲"]
        for section in payload["decision_sections"]:
            section["limitations"] = (
                ["证伪条件：毛利率下降", "限制甲", "限制乙"]
                if section["lane"] == "suitability"
                else ["限制甲", "限制乙", "限制丙"]
            )
        payload["report_id"] = final_decision_report_id(payload)
        markdown = FinalDecisionReportRenderer().render(
            FinalDecisionResearchReport.from_dict(payload)
        )

        self.assertIn("  - 证伪条件：毛利率下降", markdown)
        self.assertNotIn("限制：证伪条件", markdown)
        for text in ("限制甲", "限制乙", "限制丙", "证伪条件：毛利率下降"):
            self.assertEqual(markdown.count(text), 1, text)

    def test_holding_review_uses_holding_wording(self):
        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**_inputs())
        payload = report.to_dict()
        payload["user_boundary"]["decision_intent"] = (
            DecisionIntent.REVIEW_HOLDING.value
        )
        payload["user_boundary"]["missing_context"] = ["组合角色"]
        payload["report_id"] = final_decision_report_id(payload)
        holding = FinalDecisionResearchReport.from_dict(payload)

        markdown = FinalDecisionReportRenderer().render(holding)
        review = FinalDecisionReportReviewer().review_markdown(
            holding,
            markdown,
        )

        self.assertIn("## 是否继续持有", markdown)
        self.assertIn("**持有多少：", markdown)
        self.assertIn("补齐后才能计算仓位：组合角色", markdown)
        self.assertNotIn("## 是否值得买", markdown)
        self.assertNotIn("用户确认的假设", markdown)
        codes = {item.code for item in review.findings}
        self.assertNotIn("final_report_section_missing", codes)
        self.assertNotIn("decision_summary_missing", codes)

    def test_report_content_id_rejects_tampering(self):
        report = FinalDecisionReportAssemblyService().assemble(
            **_inputs()
        )

        with self.assertRaisesRegex(
            ValueError,
            "report_id does not match",
        ):
            replace(report, asset_name="被改写的名称")

    def test_assembly_rejects_cross_user_inputs(self):
        inputs = _inputs()
        inputs["decision_brief"] = _namespace(
            inputs["decision_brief"],
            user_id="user:other",
        )

        with self.assertRaisesRegex(ValueError, "user ownership"):
            FinalDecisionReportAssemblyService().assemble(**inputs)

    def test_official_citation_requires_primary_ledger_provenance(self):
        inputs = _inputs()
        session = inputs["session"]
        finding = session.rounds[0].structured_findings[0]
        inputs["session"] = _namespace(
            session,
            rounds=(
                SimpleNamespace(
                    structured_findings=(
                        _namespace(
                            finding,
                            source_tier=EvidenceTier.DERIVED,
                        ),
                    )
                ),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "source tier",
        ):
            FinalDecisionReportAssemblyService().assemble(**inputs)

    def test_non_official_citation_also_requires_ledger_provenance(self):
        inputs = _inputs()
        narrative = inputs["narrative"]
        scenarios = (
            replace(
                narrative.scenarios[0],
                evidence_refs=("derived:fabricated",),
            ),
            *narrative.scenarios[1:],
        )
        fabricated = FinalEvidenceCitation(
            reference_id="derived:fabricated",
            kind=FinalEvidenceKind.DERIVED,
            title="未入账派生结果",
            source_name="未知计算",
            methodology="未验证方法",
            limitations=("来源未验证。",),
        )
        inputs["narrative"] = replace(
            narrative,
            scenarios=scenarios,
            evidence_citations=(
                *narrative.evidence_citations,
                fabricated,
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "trusted evidence provenance",
        ):
            FinalDecisionReportAssemblyService().assemble(**inputs)

    def test_open_fund_unavailable_lanes_are_user_readable(self):
        inputs = _inputs()
        task = _namespace(
            inputs["session"].task,
            profile_id="open_fund",
        )
        inputs["handoff"] = _namespace(
            inputs["handoff"],
            task=task,
        )
        inputs["session"] = _namespace(
            inputs["session"],
            task=task,
        )
        inputs["suitability"] = _namespace(
            inputs["suitability"],
            profile_id="open_fund",
        )
        inputs["entry_plan"] = _namespace(
            inputs["entry_plan"],
            profile_id="open_fund",
            status=EntryPlanStatus.INSUFFICIENT,
            current_regime=(),
            entry_conditions=(),
            invalidation_conditions=(),
            condition_blocks=(),
            signal_valid_until=None,
            evidence_refs=(),
            assembly_gaps=(
                "profile_timing_execution_unsupported:open_fund",
            ),
        )
        inputs["position_plan"] = _namespace(
            inputs["position_plan"],
            profile_id="open_fund",
            entry_plan_id=inputs["entry_plan"].plan_id,
            status=PositionPlanStatus.UNSUPPORTED,
            assembly_gaps=("open_fund_position_sizing",),
        )

        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**inputs)
        markdown = FinalDecisionReportRenderer().render(report)
        sections = {
            item.lane: item for item in report.decision_sections
        }

        self.assertIs(
            sections[DecisionLane.TIMING].outcome,
            FinalDecisionOutcome.TIMING_INSUFFICIENT,
        )
        self.assertIs(
            sections[DecisionLane.POSITION_SIZING].outcome,
            FinalDecisionOutcome.POSITION_UNSUPPORTED,
        )
        self.assertIn("尚无可执行的时点模型", markdown)
        self.assertIn("不支持个性化执行仓位", markdown)
        self.assertNotIn("profile_timing_execution_unsupported", markdown)
        self.assertNotIn("open_fund_position_sizing", markdown)

    def test_partial_baseline_status_remains_visible(self):
        inputs = _inputs()
        inputs["handoff"] = _namespace(
            inputs["handoff"],
            run_status=ResearchRunStatus.PARTIAL,
            partial_gaps=(
                SimpleNamespace(
                    code="research_evidence_coverage_incomplete",
                ),
                SimpleNamespace(
                    code="automatic_gap_to_query_loop",
                ),
            ),
        )

        report = FinalDecisionReportAssemblyService().assemble(
            **inputs
        )
        markdown = FinalDecisionReportRenderer().render(report)
        review = FinalDecisionReportReviewer().review(
            report,
            **{
                key: inputs[key]
                for key in (
                    "handoff",
                    "session",
                    "decision_brief",
                    "suitability",
                    "entry_plan",
                    "position_plan",
                )
            },
        )

        self.assertIs(
            report.baseline_status,
            ResearchRunStatus.PARTIAL,
        )
        self.assertIn("基础包状态：部分完成", markdown)
        self.assertIn("证据来源的覆盖区间不完整", markdown)
        self.assertNotIn("不会自动把缺口转成查询", markdown)
        self.assertTrue(review.passed, review.findings)

    def test_final_filename_is_scoped_by_publication_key(self):
        first = final_decision_report_filename(
            "测试标的",
            AS_OF,
            "user-one",
        )
        second = final_decision_report_filename(
            "测试标的",
            AS_OF,
            "user-two",
        )

        self.assertNotEqual(first, second)
        self.assertIn("完整决策报告", first)

    def test_legacy_direct_publish_and_baseline_masquerade_are_blocked(self):
        inputs = _inputs()
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            ResearchExplorationService().publish_final(
                inputs["session"],
                publisher=ReportArtifactService("report"),
                filename="任意.md",
                content="# 任意",
            )
        with self.assertRaisesRegex(
            ValueError,
            "baseline run cannot use",
        ):
            ResearchRunRequest(
                task=inputs["session"].task,
                report_filename="测试标的-完整决策报告.md",
            )

    def test_reviewer_passes_structured_and_markdown_output(self):
        inputs = _inputs()
        report = FinalDecisionReportAssemblyService().assemble(
            **inputs
        )
        reviewer = FinalDecisionReportReviewer()
        review = reviewer.review(report, **{
            key: inputs[key]
            for key in (
                "handoff",
                "session",
                "decision_brief",
                "suitability",
                "entry_plan",
                "position_plan",
            )
        })
        markdown = FinalDecisionReportRenderer().render(report)
        final_review = reviewer.review_markdown(
            report,
            markdown,
            prior=review,
        )

        self.assertTrue(final_review.passed, final_review.findings)

    def test_renderer_and_reviewer_hide_internal_lineage(self):
        inputs = _inputs()
        report = FinalDecisionReportAssemblyService().assemble(
            **inputs
        )
        markdown = FinalDecisionReportRenderer().render(report)
        leaked = markdown + f"\n{report.position_plan_id}\n"

        result = FinalDecisionReportReviewer().review_markdown(
            report,
            leaked,
        )

        self.assertIn(
            "internal_field_leak",
            {item.code for item in result.findings},
        )

    def test_service_publishes_only_after_artifact_review(self):
        inputs = _inputs()
        with tempfile.TemporaryDirectory() as directory:
            report_root = Path(directory)
            baseline_path = (
                report_root
                / "测试标的-基础研究包-2026-08-24.md"
            )
            baseline_path.write_text(
                "# 基础研究包\n",
                encoding="utf-8",
                newline="\n",
            )
            attestations = []
            service = FinalDecisionReportService(
                publisher=ReportArtifactService(report_root),
                publication_recorder=SimpleNamespace(
                    record_final_publication=attestations.append
                ),
            )

            result = service.assemble_and_publish(**inputs)

            self.assertTrue(result.review.passed, result.review.findings)
            self.assertIsNotNone(result.report_path)
            assert result.report_path is not None
            self.assertTrue(result.report_path.is_file())
            self.assertIn("完整决策报告", result.report_path.name)
            self.assertNotIn("基础研究包", result.report_path.name)
            self.assertEqual(len(attestations), 1)
            self.assertEqual(
                attestations[0].report_id,
                result.report.report_id,
            )
            self.assertEqual(
                attestations[0].markdown_digest,
                hashlib.sha256(
                    result.report_path.read_bytes()
                ).hexdigest(),
            )

    def test_service_blocks_when_baseline_artifact_is_missing(self):
        inputs = _inputs()
        with tempfile.TemporaryDirectory() as directory:
            service = FinalDecisionReportService(
                publisher=ReportArtifactService(directory)
            )

            result = service.assemble_and_publish(**inputs)

            self.assertFalse(result.review.passed)
            self.assertIsNone(result.report_path)
            self.assertIn(
                "baseline_artifact_missing",
                {item.code for item in result.review.findings},
            )

    def test_service_blocks_tampered_baseline_artifact(self):
        inputs = _inputs()
        with tempfile.TemporaryDirectory() as directory:
            report_root = Path(directory)
            (
                report_root
                / "测试标的-基础研究包-2026-08-24.md"
            ).write_text(
                "# 被替换的报告\n",
                encoding="utf-8",
                newline="\n",
            )
            service = FinalDecisionReportService(
                publisher=ReportArtifactService(report_root)
            )

            result = service.assemble_and_publish(**inputs)

            self.assertFalse(result.review.passed)
            self.assertIn(
                "baseline_artifact_digest_mismatch",
                {item.code for item in result.review.findings},
            )

    def test_available_position_is_rendered_as_range(self):
        inputs = _inputs()
        mandate_ref = "mandate:mandate-test"
        inputs["decision_brief"] = _namespace(
            inputs["decision_brief"],
            mandate_id="mandate-test",
            portfolio_constraint_snapshot_id="constraints-test",
        )
        old = inputs["position_plan"]
        inputs["position_plan"] = SimpleNamespace(
            **{
                **vars(old),
                "status": PositionPlanStatus.AVAILABLE,
                "action": PositionAction.WAIT,
                "current_weight": Decimal("0.05"),
                "target_weight_min": Decimal("0.03"),
                "target_weight_max": Decimal("0.07"),
                "target_notional_min": Decimal("3000"),
                "target_notional_max": Decimal("7000"),
                "risk_budget_weight": Decimal("0.08"),
                "stress_loss": Decimal("500"),
                "currency": "CNY",
                "binding_constraints": ("risk_budget",),
                "constraint_blocks": (SimpleNamespace(),),
                "tranche_adjustments": (),
                "mandate_id": "mandate-test",
                "portfolio_snapshot_id": "portfolio-test",
                "constraint_snapshot_id": "constraints-test",
                "calculation_inputs": SimpleNamespace(
                    evidence_refs=(EVIDENCE_URL,)
                ),
                "evidence_refs": (
                    EVIDENCE_URL,
                    mandate_ref,
                    "portfolio:portfolio-test",
                    "constraints:constraints-test",
                ),
                "assembly_gaps": (),
            }
        )
        context_citations = tuple(
            FinalEvidenceCitation(
                reference_id=reference,
                kind=FinalEvidenceKind.USER_CONTEXT,
                title=title,
                source_name="用户确认的组合上下文",
            )
            for reference, title in (
                (mandate_ref, "投资授权与风险预算"),
                (
                    "portfolio:portfolio-test",
                    "同日组合估值",
                ),
                (
                    "constraints:constraints-test",
                    "组合约束快照",
                ),
            )
        )
        inputs["narrative"] = replace(
            inputs["narrative"],
            evidence_citations=(
                *inputs["narrative"].evidence_citations,
                *context_citations,
            ),
        )

        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**inputs)
        markdown = FinalDecisionReportRenderer().render(report)

        self.assertIn("3%—7%", markdown)
        self.assertIn("CNY 3,000.00—7,000.00", markdown)
        self.assertIn("不是交易订单", markdown)

    def test_service_reuses_published_baseline_chart_path(self):
        inputs = _inputs()
        chart_path = "assets/baseline-chart/charts/chart.png"
        data_content = "{}"
        metadata_content = json.dumps(
            {
                "asset_id": "stock:test",
                "as_of_date": AS_OF,
                "input_digest": "a" * 64,
            }
        )
        inputs["handoff"] = _namespace(
            inputs["handoff"],
            chart_artifacts=(
                SimpleNamespace(
                    markdown_path=chart_path,
                    content_digest=hashlib.sha256(
                        b"png"
                    ).hexdigest(),
                    data_digest=hashlib.sha256(
                        data_content.encode("utf-8")
                    ).hexdigest(),
                    metadata_digest=hashlib.sha256(
                        metadata_content.encode("utf-8")
                    ).hexdigest(),
                ),
            ),
        )
        inputs["narrative"] = replace(
            inputs["narrative"],
            chart_paths=(chart_path,),
        )
        with tempfile.TemporaryDirectory() as directory:
            report_root = Path(directory)
            (
                report_root
                / "测试标的-基础研究包-2026-08-24.md"
            ).write_text(
                "# 基础研究包\n",
                encoding="utf-8",
                newline="\n",
            )
            chart = report_root / Path(chart_path)
            chart.parent.mkdir(parents=True)
            chart.write_bytes(b"png")
            (chart.parent / "data.json").write_text(
                data_content,
                encoding="utf-8",
            )
            (chart.parent / "metadata.json").write_text(
                metadata_content,
                encoding="utf-8",
            )
            service = FinalDecisionReportService(
                publisher=ReportArtifactService(report_root)
            )

            result = service.assemble_and_publish(**inputs)

            self.assertTrue(result.review.passed, result.review.findings)
            self.assertIn(f"]({chart_path})", result.markdown)
            chart.write_bytes(b"tampered")
            blocked = service.assemble_and_publish(**inputs)
            self.assertFalse(blocked.review.passed)
            self.assertIn(
                "chart_artifact_digest_mismatch",
                {item.code for item in blocked.review.findings},
            )


if __name__ == "__main__":
    unittest.main()
