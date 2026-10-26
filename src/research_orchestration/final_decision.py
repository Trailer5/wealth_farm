"""Deterministic assembly for final decision research reports."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath

from src.portfolio_risk import (
    PortfolioRiskService,
    portfolio_valuation_digest,
)
from src.research_contracts import (
    DecisionContextRequirement,
    DecisionLane,
    ProbabilityInterval,
    ResearchDecisionBrief,
    to_payload,
)
from src.user_space import UserContext

from .entry_models import (
    EntryConditionBlock,
    EntryConditionKind,
    EntryPlan,
    EntryPlanStatus,
    TimingConditionState,
)
from .exploration_models import (
    EvidenceTier,
    ExplorationStatus,
    HypothesisRole,
    ResearchExplorationSession,
    ResearchFindingKind,
)
from .final_decision_models import (
    FinalChartArtifactReference,
    FinalDecisionOutcome,
    FinalDecisionResearchReport,
    FinalDecisionSection,
    FinalEvidenceCitation,
    FinalEvidenceKind,
    FinalScenarioBlock,
    FinalStatement,
    FinalStatementKind,
    FinalUserBoundary,
    MonitoringCondition,
    MonitoringConditionKind,
    MonitoringPlan,
    MonitoringPlanStatus,
    final_decision_report_id,
    monitoring_plan_id,
)
from .gap_messages import (
    AGENT_ONLY_GAP_CODES,
    final_report_gap_message,
)
from .position_models import (
    PositionAction,
    PositionPlan,
    PositionPlanStatus,
)
from .suitability_models import (
    SuitabilityAction,
    SuitabilityAssessment,
    SuitabilityClaimBlock,
    SuitabilityClaimKind,
)
from .workflow import BaselineResearchHandoff


@dataclass(frozen=True)
class FinalReportNarrativeInput:
    """Agent-reviewed scenario and citation metadata for assembly."""

    scenarios: tuple[FinalScenarioBlock, ...]
    evidence_citations: tuple[FinalEvidenceCitation, ...]
    chart_paths: tuple[str, ...] = ()
    review_date: str | None = None
    additional_unknowns: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "报告结论仅适用于声明的截止日、用户边界和证据范围。",
    )
    claim_probability: ProbabilityInterval | None = None

    def __post_init__(self) -> None:
        if self.review_date is not None:
            date.fromisoformat(self.review_date)
        _texts(self.additional_unknowns, "additional_unknowns")
        _texts(self.limitations, "limitations", required=True)
        if self.claim_probability is not None and not isinstance(
            self.claim_probability,
            ProbabilityInterval,
        ):
            raise TypeError("claim_probability must be ProbabilityInterval")


class FinalDecisionReportAssemblyService:
    """Project validated lane outputs into one immutable report."""

    def __init__(
        self,
        portfolio_risk: PortfolioRiskService | None = None,
    ) -> None:
        self.portfolio_risk = portfolio_risk

    def assemble(
        self,
        *,
        handoff: BaselineResearchHandoff,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        position_plan: PositionPlan,
        narrative: FinalReportNarrativeInput,
    ) -> FinalDecisionResearchReport:
        self._validate_identity(
            handoff=handoff,
            session=session,
            decision_brief=decision_brief,
            suitability=suitability,
            entry_plan=entry_plan,
            position_plan=position_plan,
        )
        baseline_path = _baseline_filename(
            handoff.report_path,
            session.baseline_path,
        )
        thesis = _thesis_statement(suitability)
        counterargument = _counterargument_statement(suitability)
        decision_sections = (
            _suitability_section(suitability),
            _timing_section(entry_plan),
            _position_section(position_plan),
        )
        monitoring_plan = _monitoring_plan(
            session=session,
            entry_plan=entry_plan,
            requested_review_date=narrative.review_date,
        )
        baseline_gaps = tuple(
            dict.fromkeys(
                final_report_gap_message(item.code)
                for item in (
                    *handoff.partial_gaps,
                    *handoff.capability_gaps,
                )
                if item.code not in AGENT_ONLY_GAP_CODES
            )
        )
        unknowns = _merge_texts(
            narrative.additional_unknowns,
            baseline_gaps,
            tuple(
                final_report_gap_message(item.code)
                for item in session.query_gaps
            ),
            tuple(
                finding.text
                for round_result in session.rounds
                for finding in round_result.structured_findings
                if finding.kind is ResearchFindingKind.UNKNOWN
            ),
            tuple(
                final_report_gap_message(item)
                for item in suitability.assembly_gaps
            ),
            tuple(
                final_report_gap_message(item)
                for item in entry_plan.assembly_gaps
            ),
            tuple(
                final_report_gap_message(item)
                for item in position_plan.assembly_gaps
            ),
        )
        position_context_refs = self._position_context_refs(
            decision_brief=decision_brief,
            position_plan=position_plan,
        )
        _validate_evidence_provenance(
            session,
            narrative.evidence_citations,
            suitability=suitability,
            entry_plan=entry_plan,
            position_plan=position_plan,
            position_context_refs=position_context_refs,
        )
        chart_manifest = {
            item.markdown_path: item
            for item in handoff.chart_artifacts
        }
        if any(
            path not in chart_manifest
            for path in narrative.chart_paths
        ):
            raise ValueError(
                "requested chart is not bound to baseline handoff"
            )
        chart_artifacts = tuple(
            FinalChartArtifactReference(
                markdown_path=chart_manifest[path].markdown_path,
                content_digest=chart_manifest[path].content_digest,
                metadata_digest=chart_manifest[path].metadata_digest,
                data_digest=chart_manifest[path].data_digest,
            )
            for path in narrative.chart_paths
        )
        asset_name = (
            handoff.asset_identity.display_name
            or handoff.asset_identity.symbol
        )
        report_payload = {
            "report_id": "",
            "user_id": decision_brief.user_id,
            "workflow_run_id": handoff.workflow_run_id,
            "session_id": session.session_id,
            "decision_brief_id": decision_brief.brief_id,
            "handoff_id": handoff.handoff_id,
            "suitability_assessment_id": suitability.assessment_id,
            "entry_plan_id": entry_plan.plan_id,
            "position_plan_id": position_plan.plan_id,
            "asset_id": session.task.asset_id,
            "profile_id": session.task.profile_id,
            "asset_name": asset_name,
            "as_of_date": session.task.as_of_date,
            "horizon": decision_brief.horizon.value,
            "baseline_status": handoff.run_status.value,
            "baseline_report_path": baseline_path,
            "baseline_report_digest": handoff.report_digest,
            "user_boundary": _user_boundary(
                decision_brief
            ).to_dict(),
            "thesis": thesis.to_dict(),
            "decision_sections": [
                item.to_dict() for item in decision_sections
            ],
            "scenarios": [
                item.to_dict() for item in narrative.scenarios
            ],
            "strongest_counterargument": (
                counterargument.to_dict()
            ),
            "monitoring_plan": monitoring_plan.to_dict(),
            "evidence_citations": [
                item.to_dict()
                for item in narrative.evidence_citations
            ],
            "baseline_gaps": list(baseline_gaps),
            "key_unknowns": list(unknowns),
            "chart_paths": list(narrative.chart_paths),
            "chart_artifacts": [
                item.to_dict() for item in chart_artifacts
            ],
            "limitations": list(narrative.limitations),
            "claim_probability": (
                None
                if narrative.claim_probability is None
                else to_payload(narrative.claim_probability)
            ),
        }
        report_payload["report_id"] = final_decision_report_id(
            report_payload
        )
        return FinalDecisionResearchReport.from_dict(report_payload)

    def _position_context_refs(
        self,
        *,
        decision_brief: ResearchDecisionBrief,
        position_plan: PositionPlan,
    ) -> frozenset[str]:
        if position_plan.status is not PositionPlanStatus.AVAILABLE:
            return frozenset()
        if self.portfolio_risk is None:
            raise ValueError(
                "available position report requires portfolio context "
                "verification service"
            )
        assert position_plan.mandate_id is not None
        assert position_plan.portfolio_snapshot_id is not None
        assert position_plan.constraint_snapshot_id is not None
        context = UserContext(decision_brief.user_id)
        mandate = self.portfolio_risk.get_mandate(
            context,
            position_plan.mandate_id,
        )
        valuation = self.portfolio_risk.get_snapshot(
            context,
            position_plan.portfolio_snapshot_id,
        )
        constraints = self.portfolio_risk.get_constraint_snapshot(
            context,
            position_plan.constraint_snapshot_id,
        )
        if (
            mandate is None
            or valuation is None
            or constraints is None
            or decision_brief.mandate_id
            != position_plan.mandate_id
            or decision_brief.portfolio_constraint_snapshot_id
            != position_plan.constraint_snapshot_id
            or constraints.mandate_id != mandate.mandate_id
            or constraints.portfolio_snapshot_id
            != valuation.snapshot.snapshot_id
            or constraints.user_id != decision_brief.user_id
            or valuation.snapshot.user_id != decision_brief.user_id
            or mandate.implementation_asset_id
            != position_plan.asset_id
            or constraints.subject_asset_id
            != position_plan.asset_id
            or constraints.as_of_date != position_plan.as_of_date
            or valuation.snapshot.as_of_date
            != position_plan.as_of_date
            or valuation.snapshot.holdings_as_of_date
            != position_plan.as_of_date
            or not valuation.snapshot.evidence_refs
            or constraints.currency != position_plan.currency
            or valuation.snapshot.base_currency
            != position_plan.currency
            or constraints.portfolio_valuation_digest
            != portfolio_valuation_digest(valuation)
        ):
            raise ValueError(
                "position context cannot be verified for final report"
            )
        return frozenset(
            _merge_texts(
                constraints.evidence_refs,
                valuation.snapshot.evidence_refs,
                (
                    f"mandate:{mandate.mandate_id}",
                    f"portfolio:{valuation.snapshot.snapshot_id}",
                    f"constraints:{constraints.snapshot_id}",
                ),
            )
        )

    @staticmethod
    def _validate_identity(
        *,
        handoff: BaselineResearchHandoff,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        position_plan: PositionPlan,
    ) -> None:
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            raise ValueError(
                "exploration session is not ready for final report"
            )
        if handoff.report_path is None or session.baseline_path is None:
            raise ValueError(
                "final report requires published baseline package"
            )
        if handoff.report_digest is None:
            raise ValueError(
                "final report requires baseline artifact digest"
            )
        if handoff.blocking_gaps:
            raise ValueError(
                "handoff with blocking gaps cannot publish final report"
            )
        ids = {
            session.task.asset_id,
            handoff.task.asset_id,
            handoff.asset_identity.asset_id,
            decision_brief.asset_id,
            suitability.asset_id,
            entry_plan.asset_id,
            position_plan.asset_id,
        }
        profiles = {
            session.task.profile_id,
            handoff.task.profile_id,
            suitability.profile_id,
            entry_plan.profile_id,
            position_plan.profile_id,
        }
        dates = {
            session.task.as_of_date,
            handoff.task.as_of_date,
            decision_brief.as_of_date,
            suitability.as_of_date,
            entry_plan.as_of_date,
            position_plan.as_of_date,
        }
        horizons = {
            decision_brief.horizon,
            suitability.time_horizon,
            entry_plan.suitability_horizon,
            position_plan.horizon,
        }
        if len(ids) != 1 or len(profiles) != 1 or len(dates) != 1:
            raise ValueError(
                "final report identity or cutoff does not align"
            )
        if len(horizons) != 1:
            raise ValueError(
                "final report decision horizons do not align"
            )
        if (
            session.workflow_run_id != handoff.workflow_run_id
            or suitability.workflow_run_id != handoff.workflow_run_id
            or position_plan.workflow_run_id != handoff.workflow_run_id
        ):
            raise ValueError(
                "final report workflow lineage does not align"
            )
        if (
            session.handoff_id != handoff.handoff_id
            or session.decision_brief_id != decision_brief.brief_id
            or suitability.session_id != session.session_id
            or entry_plan.session_id != session.session_id
            or position_plan.session_id != session.session_id
        ):
            raise ValueError(
                "final report session lineage does not align"
            )
        if (
            session.user_id is None
            or session.user_id != decision_brief.user_id
        ):
            raise ValueError(
                "final report user ownership does not align"
            )
        if any(
            value != decision_brief.brief_id
            for value in (
                suitability.decision_brief_id,
                entry_plan.decision_brief_id,
                position_plan.decision_brief_id,
            )
        ):
            raise ValueError(
                "final report decision brief lineage does not align"
            )
        if (
            entry_plan.suitability_assessment_id
            != suitability.assessment_id
            or position_plan.suitability_assessment_id
            != suitability.assessment_id
            or position_plan.entry_plan_id != entry_plan.plan_id
        ):
            raise ValueError(
                "final report decision plan lineage does not align"
            )


def final_decision_report_filename(
    asset_name: str,
    as_of_date: str,
    publication_key: str,
) -> str:
    date.fromisoformat(as_of_date)
    if not isinstance(asset_name, str):
        raise TypeError("asset_name must be a string")
    safe_name = re.sub(
        r'[<>:"/\\|?*]+',
        "-",
        asset_name,
    ).strip(" .-")
    if not safe_name:
        safe_name = "研究"
    if (
        not isinstance(publication_key, str)
        or not publication_key.strip()
    ):
        raise ValueError("publication_key must not be blank")
    suffix = hashlib.sha256(
        publication_key.encode("utf-8")
    ).hexdigest()[:10]
    return (
        f"{safe_name}-完整决策报告-{as_of_date}-"
        f"{suffix}.md"
    )


def _user_boundary(
    brief: ResearchDecisionBrief,
) -> FinalUserBoundary:
    missing_labels = {
        DecisionContextRequirement.POSITION_STATE: "当前是否持有",
        DecisionContextRequirement.CURRENT_POSITION_DETAILS: (
            "当前持仓数量与成本"
        ),
        DecisionContextRequirement.PORTFOLIO_ROLE: "组合角色",
        DecisionContextRequirement.INVESTMENT_MANDATE: "投资授权与风险预算",
        DecisionContextRequirement.PORTFOLIO_CONSTRAINT_SNAPSHOT: (
            "组合约束与估值快照"
        ),
    }
    return FinalUserBoundary(
        decision_intent=brief.decision_intent,
        horizon=brief.horizon,
        position_state=brief.position_state,
        portfolio_role=brief.portfolio_role,
        constraints=brief.constraints,
        assumptions=brief.assumptions,
        missing_context=tuple(
            missing_labels[item]
            for item in brief.missing_user_context
        ),
    )


def _thesis_statement(
    suitability: SuitabilityAssessment,
) -> FinalStatement:
    if suitability.thesis is not None:
        return _claim_statement(suitability.thesis)
    return FinalStatement(
        kind=FinalStatementKind.UNKNOWN,
        text="当前证据不足以形成稳定的核心论文。",
        limitations=(
            suitability.assembly_gaps
            or suitability.limitations
        ),
    )


def _counterargument_statement(
    suitability: SuitabilityAssessment,
) -> FinalStatement:
    if suitability.strongest_counterargument is not None:
        return _claim_statement(
            suitability.strongest_counterargument
        )
    return FinalStatement(
        kind=FinalStatementKind.UNKNOWN,
        text="当前证据不足以形成可复核的最强反方。",
        limitations=(
            suitability.assembly_gaps
            or suitability.limitations
        ),
    )


def _suitability_section(
    assessment: SuitabilityAssessment,
) -> FinalDecisionSection:
    outcomes = {
        SuitabilityAction.CONSIDER: (
            FinalDecisionOutcome.SUITABILITY_CONSIDER
        ),
        SuitabilityAction.WATCH: (
            FinalDecisionOutcome.SUITABILITY_WATCH
        ),
        SuitabilityAction.AVOID: (
            FinalDecisionOutcome.SUITABILITY_AVOID
        ),
        SuitabilityAction.HOLD_REVIEW: (
            FinalDecisionOutcome.SUITABILITY_HOLD_REVIEW
        ),
        SuitabilityAction.INSUFFICIENT: (
            FinalDecisionOutcome.SUITABILITY_INSUFFICIENT
        ),
    }
    summaries = {
        SuitabilityAction.CONSIDER: (
            "当前证据支持在用户边界内考虑配置。"
        ),
        SuitabilityAction.WATCH: (
            "当前更适合观察，尚未达到考虑配置的证据边界。"
        ),
        SuitabilityAction.AVOID: (
            "当前风险收益与替代机会不支持配置。"
        ),
        SuitabilityAction.HOLD_REVIEW: (
            "已有持仓应复核论文与组合角色，不直接等同于加仓。"
        ),
        SuitabilityAction.INSUFFICIENT: (
            "当前证据不足，无法可靠回答是否值得配置或继续持有。"
        ),
    }
    groups = (
        ("收益来源", assessment.expected_return_sources),
        ("定价隐含预期", assessment.priced_expectations),
        ("主要下行", assessment.downside),
        ("可比替代", assessment.alternatives),
        ("机会成本", assessment.opportunity_costs),
    )
    statements = tuple(
        _claim_statement(item, prefix=label)
        for label, values in groups
        for item in values
    )
    return FinalDecisionSection(
        lane=DecisionLane.SUITABILITY,
        outcome=outcomes[assessment.action],
        summary=summaries[assessment.action],
        statements=statements,
        gaps=_humanize_gaps(assessment.assembly_gaps),
        limitations=assessment.limitations,
    )


def _timing_section(plan: EntryPlan) -> FinalDecisionSection:
    outcomes = {
        EntryPlanStatus.ACT: FinalDecisionOutcome.TIMING_ACT,
        EntryPlanStatus.STAGE: FinalDecisionOutcome.TIMING_STAGE,
        EntryPlanStatus.WAIT: FinalDecisionOutcome.TIMING_WAIT,
        EntryPlanStatus.NOT_APPLICABLE: (
            FinalDecisionOutcome.TIMING_NOT_APPLICABLE
        ),
        EntryPlanStatus.INSUFFICIENT: (
            FinalDecisionOutcome.TIMING_INSUFFICIENT
        ),
    }
    summaries = {
        EntryPlanStatus.ACT: "当前条件支持按执行约束行动。",
        EntryPlanStatus.STAGE: (
            "当前仅支持按已确认条件分批规划，不构成订单。"
        ),
        EntryPlanStatus.WAIT: (
            "当前应等待可观察条件变化后再复核。"
        ),
        EntryPlanStatus.NOT_APPLICABLE: (
            "当前是否配置结论使时点问题不适用。"
        ),
        EntryPlanStatus.INSUFFICIENT: (
            "当前证据不足，无法给出可靠行动时点。"
        ),
    }
    groups = (
        ("当前状态", plan.current_regime),
        ("进入条件", plan.entry_conditions),
        ("等待条件", plan.wait_conditions),
        ("分批规则", plan.tranche_rules),
        ("回避条件", plan.avoid_conditions),
        ("催化窗口", plan.catalyst_windows),
        ("执行约束", plan.execution_constraints),
        ("周期冲突", plan.horizon_conflicts),
    )
    statements = [
        _entry_statement(item, label)
        for label, values in groups
        for item in values
    ]
    if plan.signal_valid_until is not None:
        statements.append(
            FinalStatement(
                kind=FinalStatementKind.FACT,
                text=(
                    "当前信号有效期截至"
                    f"{plan.signal_valid_until}。"
                ),
                evidence_refs=_merge_texts(
                    *(
                        item.evidence_refs
                        for item in plan.condition_blocks
                    )
                ),
            )
        )
    if plan.next_tradable_at is not None:
        statements.append(
            FinalStatement(
                kind=FinalStatementKind.FACT,
                text=(
                    "下一可交易候选时间为"
                    f"{plan.next_tradable_at}。"
                ),
                evidence_refs=(
                    plan.next_tradable_condition.evidence_refs
                    if plan.next_tradable_condition is not None
                    else plan.evidence_refs
                ),
            )
        )
    return FinalDecisionSection(
        lane=DecisionLane.TIMING,
        outcome=outcomes[plan.status],
        summary=summaries[plan.status],
        statements=tuple(statements),
        gaps=_humanize_gaps(plan.assembly_gaps),
        limitations=plan.limitations,
    )


def _position_section(plan: PositionPlan) -> FinalDecisionSection:
    outcomes = {
        PositionPlanStatus.AVAILABLE: (
            FinalDecisionOutcome.POSITION_AVAILABLE
        ),
        PositionPlanStatus.USER_CONTEXT_REQUIRED: (
            FinalDecisionOutcome.POSITION_USER_CONTEXT_REQUIRED
        ),
        PositionPlanStatus.UNSUPPORTED: (
            FinalDecisionOutcome.POSITION_UNSUPPORTED
        ),
    }
    if plan.status is not PositionPlanStatus.AVAILABLE:
        if plan.status is PositionPlanStatus.USER_CONTEXT_REQUIRED:
            summary = "缺少用户确认的组合上下文，当前不能计算仓位区间。"
        elif "suitability_assessment_insufficient" in plan.assembly_gaps:
            summary = "是否配置结论证据不足，暂不计算仓位区间。"
        else:
            summary = "当前研究类型尚不支持个性化仓位计算。"
        return FinalDecisionSection(
            lane=DecisionLane.POSITION_SIZING,
            outcome=outcomes[plan.status],
            summary=summary,
            statements=(),
            gaps=_humanize_gaps(plan.assembly_gaps),
            limitations=plan.limitations,
        )
    assert plan.current_weight is not None
    assert plan.target_weight_min is not None
    assert plan.target_weight_max is not None
    assert plan.target_notional_min is not None
    assert plan.target_notional_max is not None
    assert plan.risk_budget_weight is not None
    assert plan.stress_loss is not None
    assert plan.currency is not None
    action_label = {
        PositionAction.NEW_ALLOCATION: "新建配置",
        PositionAction.INCREASE: "增加配置",
        PositionAction.REDUCE: "降低配置",
        PositionAction.MAINTAIN: "维持当前区间",
        PositionAction.WAIT: "等待时点条件",
        PositionAction.NO_ACTION: "不执行调整",
        PositionAction.UNAVAILABLE: "当前不可用",
    }[plan.action]
    constraint_labels = {
        "risk_budget": "风险预算",
        "stress_loss": "压力损失",
        "concentration": "单标的集中度",
        "positive_correlation": "正相关暴露",
        "liquidity": "流动性",
        "cash_floor": "现金底线",
        "planned_deployable_funds": "计划可用资金",
    }
    binding = "、".join(
        constraint_labels.get(item, "其他正式约束")
        for item in plan.binding_constraints
    )
    statements = [
        FinalStatement(
            kind=FinalStatementKind.INFERENCE,
            text=(
                f"规划动作为{action_label}；当前权重"
                f"{_percent(plan.current_weight)}，目标区间"
                f"{_percent(plan.target_weight_min)}—"
                f"{_percent(plan.target_weight_max)}，对应金额"
                f"{plan.currency} "
                f"{_money(plan.target_notional_min)}—"
                f"{_money(plan.target_notional_max)}。"
            ),
            evidence_refs=plan.evidence_refs,
            limitations=plan.limitations,
        ),
        FinalStatement(
            kind=FinalStatementKind.INFERENCE,
            text=(
                f"风险预算上限为{_percent(plan.risk_budget_weight)}，"
                f"目标上限对应压力损失为"
                f"{plan.currency} {_money(plan.stress_loss)}；"
                f"当前绑定约束为{binding}。"
            ),
            evidence_refs=plan.evidence_refs,
            limitations=plan.limitations,
        ),
    ]
    for index, tranche in enumerate(plan.tranche_adjustments, start=1):
        statements.append(
            FinalStatement(
                kind=FinalStatementKind.INFERENCE,
                text=(
                    f"第{index}批规划占本次调整"
                    f"{_percent(tranche.target_fraction)}，金额区间"
                    f"{plan.currency} "
                    f"{_money(tranche.notional_min)}—"
                    f"{_money(tranche.notional_max)}；"
                    "该数值是规划边界，不是订单。"
                ),
                evidence_refs=plan.evidence_refs,
                limitations=plan.limitations,
            )
        )
    return FinalDecisionSection(
        lane=DecisionLane.POSITION_SIZING,
        outcome=outcomes[plan.status],
        summary=(
            "仓位仅按用户确认的风险预算与组合约束给出区间，"
            "不把上限改写为推荐点位。"
        ),
        statements=tuple(statements),
        gaps=(),
        limitations=plan.limitations,
    )


def _claim_statement(
    block: SuitabilityClaimBlock,
    *,
    prefix: str | None = None,
) -> FinalStatement:
    kinds = {
        SuitabilityClaimKind.FACT: FinalStatementKind.FACT,
        SuitabilityClaimKind.INFERENCE: FinalStatementKind.INFERENCE,
        SuitabilityClaimKind.ASSUMPTION: FinalStatementKind.HYPOTHESIS,
        SuitabilityClaimKind.UNKNOWN: FinalStatementKind.UNKNOWN,
    }
    return FinalStatement(
        kind=kinds[block.kind],
        text=(
            f"{prefix}：{block.text}"
            if prefix is not None
            else block.text
        ),
        evidence_refs=block.evidence_refs,
        limitations=block.limitations,
    )


def _entry_statement(
    block: EntryConditionBlock,
    label: str,
) -> FinalStatement:
    kind = (
        FinalStatementKind.FACT
        if block.kind is EntryConditionKind.FACT
        else FinalStatementKind.INFERENCE
    )
    state_label = {
        TimingConditionState.SUPPORTIVE: "支持行动",
        TimingConditionState.NEUTRAL: "中性",
        TimingConditionState.ADVERSE: "不利",
        TimingConditionState.MET: "已满足",
        TimingConditionState.NOT_MET: "未满足",
        TimingConditionState.WATCH: "观察中",
        TimingConditionState.UNKNOWN: "状态未知",
        TimingConditionState.NOT_APPLICABLE: "不适用",
    }[block.state]
    return FinalStatement(
        kind=kind,
        text=f"{label}（{state_label}）：{block.text}",
        evidence_refs=block.evidence_refs,
        limitations=block.limitations,
    )


def _monitoring_plan(
    *,
    session: ResearchExplorationSession,
    entry_plan: EntryPlan,
    requested_review_date: str | None,
) -> MonitoringPlan:
    conditions: list[MonitoringCondition] = []
    for block in entry_plan.invalidation_conditions:
        conditions.append(
            MonitoringCondition(
                condition_id=_monitoring_condition_id(
                    block.text,
                    block.observability_key,
                ),
                text=block.text,
                kind=MonitoringConditionKind.MACHINE_OBSERVABLE,
                source="时点计划失效条件",
                observability_key=block.observability_key,
                review_by=block.valid_until,
                evidence_refs=block.evidence_refs,
                limitations=(
                    *block.limitations,
                    "监测适配器与启用状态需在后续由用户确认。",
                ),
            )
        )
    for hypothesis in session.hypotheses:
        if hypothesis.role is not HypothesisRole.THESIS:
            continue
        refs = _merge_texts(
            hypothesis.supporting_evidence_refs,
            hypothesis.counterevidence_refs,
        )
        for condition in hypothesis.falsification_conditions:
            conditions.append(
                MonitoringCondition(
                    condition_id=_monitoring_condition_id(
                        condition,
                        None,
                    ),
                    text=condition,
                    kind=MonitoringConditionKind.MANUAL_REVIEW,
                    source="核心论文证伪条件",
                    observability_key=None,
                    review_by=requested_review_date,
                    evidence_refs=refs,
                    limitations=(
                        "当前未绑定可机器执行的公共监测适配器。",
                    ),
                )
            )
    unique_conditions = tuple(
        {
            (
                item.text,
                item.observability_key,
            ): item
            for item in conditions
        }.values()
    )
    review_candidates = tuple(
        value
        for value in (
            requested_review_date,
            entry_plan.signal_valid_until,
        )
        if value is not None
    )
    review_date = min(review_candidates) if review_candidates else None
    status = (
        MonitoringPlanStatus.USER_CONFIRMATION_REQUIRED
        if unique_conditions
        else MonitoringPlanStatus.UNSUPPORTED
    )
    payload = {
        "plan_id": "",
        "status": status.value,
        "review_date": review_date,
        "conditions": [
            item.to_dict() for item in unique_conditions
        ],
        "alert_rule_refs": [],
        "limitations": [
            "本计划仅为待确认建议；未经用户确认不会启用规则或通知。"
        ],
    }
    payload["plan_id"] = monitoring_plan_id(payload)
    return MonitoringPlan.from_dict(payload)


def _monitoring_condition_id(
    text: str,
    observability_key: str | None,
) -> str:
    encoded = f"{text}\0{observability_key or ''}".encode("utf-8")
    return "monitor-condition:" + hashlib.sha256(
        encoded
    ).hexdigest()[:20]


def _baseline_filename(
    handoff_path: str | None,
    session_path: Path | None,
) -> str:
    if handoff_path is None or session_path is None:
        raise ValueError("baseline path is required")
    handoff_parts = PurePosixPath(
        str(handoff_path).replace("\\", "/")
    ).parts
    session_parts = PurePosixPath(
        str(session_path).replace("\\", "/")
    ).parts
    if not handoff_parts or not session_parts:
        raise ValueError("baseline path is invalid")
    handoff_name = handoff_parts[-1]
    session_name = session_parts[-1]
    if handoff_name != session_name:
        raise ValueError(
            "session and handoff baseline paths do not match"
        )
    return handoff_name


def _validate_evidence_provenance(
    session: ResearchExplorationSession,
    citations: tuple[FinalEvidenceCitation, ...],
    *,
    suitability: SuitabilityAssessment,
    entry_plan: EntryPlan,
    position_plan: PositionPlan,
    position_context_refs: frozenset[str],
) -> None:
    origins: dict[str, list[object]] = {}
    for round_result in session.rounds:
        for finding in round_result.structured_findings:
            for reference in finding.evidence_refs:
                origins.setdefault(reference, []).append(finding)
    compatible_kinds = {
        EvidenceTier.PRIMARY: {
            FinalEvidenceKind.OFFICIAL,
            FinalEvidenceKind.REPORTED,
        },
        EvidenceTier.SECONDARY: {FinalEvidenceKind.REPORTED},
        EvidenceTier.DERIVED: {FinalEvidenceKind.DERIVED},
        EvidenceTier.USER_CONTEXT: {
            FinalEvidenceKind.USER_CONTEXT
        },
        EvidenceTier.UNKNOWN: set(),
    }
    calculation_refs = set(
        position_plan.calculation_inputs.evidence_refs
        if position_plan.calculation_inputs is not None
        else ()
    )
    if not calculation_refs.issubset(origins):
        raise ValueError(
            "position calculation evidence is not recorded "
            "in exploration ledger"
        )
    upstream_refs = {
        *suitability.evidence_refs,
        *entry_plan.evidence_refs,
    }
    unexplained_refs = (
        set(position_plan.evidence_refs)
        - upstream_refs
        - calculation_refs
        - position_context_refs
    )
    if unexplained_refs:
        raise ValueError(
            "position plan contains unverified evidence references"
        )
    trusted_position_kinds = {
        **{
            reference: {FinalEvidenceKind.USER_CONTEXT}
            for reference in position_context_refs
        },
    }
    for citation in citations:
        source_findings = origins.get(citation.reference_id, [])
        if not source_findings:
            if citation.kind not in trusted_position_kinds.get(
                citation.reference_id,
                set(),
            ):
                raise ValueError(
                    "citation lacks trusted evidence provenance"
                )
        elif not any(
                citation.kind
                in compatible_kinds[finding.source_tier]
                for finding in source_findings
            ):
            raise ValueError(
                "citation kind conflicts with evidence source tier"
            )
        if citation.kind is FinalEvidenceKind.OFFICIAL:
            assert citation.url is not None
            associated_urls = {
                reference
                for finding in source_findings
                for reference in finding.evidence_refs
                if re.match(
                    r"^https?://",
                    reference,
                    flags=re.IGNORECASE,
                )
            }
            if citation.url not in associated_urls:
                raise ValueError(
                    "official citation URL is not registered with evidence"
                )
        if citation.published_at is not None and any(
            citation.published_at > finding.as_of_date
            for finding in source_findings
        ):
            raise ValueError(
                "citation publication date exceeds finding cutoff"
            )


def _humanize_gaps(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            final_report_gap_message(item) for item in values
        )
    )


def _percent(value: Decimal) -> str:
    percentage = (value * Decimal("100")).quantize(
        Decimal("0.01")
    )
    return f"{percentage.normalize()}%"


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,.2f}"


def _merge_texts(
    *groups: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in groups
            for item in group
            if item.strip()
        )
    )


def _texts(
    values: tuple[str, ...],
    name: str,
    *,
    required: bool = False,
) -> None:
    if required and not values:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")
