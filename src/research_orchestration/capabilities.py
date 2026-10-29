"""Queryable declarations for current deterministic research capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionContextRequirement,
    DecisionLane,
    ResearchQuestionType,
)


@dataclass(frozen=True)
class ResearchProfileCapability:
    profile_id: str
    supported_questions: tuple[ResearchQuestionType, ...]
    automated_capabilities: tuple[str, ...]
    agent_query_capabilities: tuple[str, ...]
    unsupported_capabilities: tuple[str, ...]
    public_entrypoints: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    candidate_questions: tuple[str, ...]
    candidate_hypotheses: tuple[str, ...]
    # Lane -> unsupported capability that blocks it for this profile.
    unsupported_lanes: tuple[tuple[DecisionLane, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        _unique(self.supported_questions, "supported_questions")
        lanes = [lane for lane, _ in self.unsupported_lanes]
        if len(set(lanes)) != len(lanes):
            raise ValueError("unsupported_lanes must not repeat a lane")
        for lane, capability in self.unsupported_lanes:
            if not isinstance(lane, DecisionLane):
                raise TypeError("unsupported_lanes lane must be DecisionLane")
            if capability not in self.unsupported_capabilities:
                raise ValueError(
                    "unsupported_lanes capability must be declared unsupported"
                )
        for values, name in (
            (
                self.automated_capabilities,
                "automated_capabilities",
            ),
            (
                self.agent_query_capabilities,
                "agent_query_capabilities",
            ),
            (
                self.unsupported_capabilities,
                "unsupported_capabilities",
            ),
            (self.public_entrypoints, "public_entrypoints"),
            (
                self.evidence_requirements,
                "evidence_requirements",
            ),
            (self.candidate_questions, "candidate_questions"),
            (
                self.candidate_hypotheses,
                "candidate_hypotheses",
            ),
        ):
            _text_values(values, name)


@dataclass(frozen=True)
class ResearchCapabilityView:
    profile_id: str
    question_type: ResearchQuestionType
    decision_lane: DecisionLane | None
    question_supported: bool
    automated_capabilities: tuple[str, ...]
    agent_query_capabilities: tuple[str, ...]
    required_user_context: tuple[
        DecisionContextRequirement,
        ...,
    ]
    unsupported_capabilities: tuple[str, ...]
    lane_unsupported_capabilities: tuple[str, ...]
    public_entrypoints: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    candidate_questions: tuple[str, ...]
    candidate_hypotheses: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "question_type": self.question_type.value,
            "decision_lane": (
                self.decision_lane.value
                if self.decision_lane is not None
                else None
            ),
            "question_supported": self.question_supported,
            "automated_capabilities": list(
                self.automated_capabilities
            ),
            "agent_query_capabilities": list(
                self.agent_query_capabilities
            ),
            "required_user_context": [
                item.value for item in self.required_user_context
            ],
            "unsupported_capabilities": list(
                self.unsupported_capabilities
            ),
            "lane_unsupported_capabilities": list(
                self.lane_unsupported_capabilities
            ),
            "public_entrypoints": list(self.public_entrypoints),
            "evidence_requirements": list(
                self.evidence_requirements
            ),
            "candidate_questions": list(
                self.candidate_questions
            ),
            "candidate_hypotheses": list(
                self.candidate_hypotheses
            ),
        }


class ResearchCapabilityRegistry:
    def __init__(
        self,
        profiles: tuple[ResearchProfileCapability, ...],
    ) -> None:
        if not profiles:
            raise ValueError("profiles must not be empty")
        self._profiles = {
            profile.profile_id: profile for profile in profiles
        }
        if len(self._profiles) != len(profiles):
            raise ValueError("profile_id must be unique")

    def list_profile_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def resolve(
        self,
        *,
        profile_id: str,
        question_type: ResearchQuestionType,
        decision_lane: DecisionLane | None,
    ) -> ResearchCapabilityView:
        try:
            profile = self._profiles[profile_id]
        except KeyError as exc:
            raise KeyError(
                f"Unknown capability profile: {profile_id}"
            ) from exc
        if not isinstance(question_type, ResearchQuestionType):
            raise TypeError(
                "question_type must be ResearchQuestionType"
            )
        if (
            decision_lane is not None
            and not isinstance(decision_lane, DecisionLane)
        ):
            raise TypeError(
                "decision_lane must be DecisionLane or None"
            )
        lane = _LANE_CAPABILITIES.get(
            decision_lane,
            _NO_DECISION_LANE,
        )
        question_supported = (
            question_type in profile.supported_questions
        )
        unsupported = [
            *profile.unsupported_capabilities,
            *lane.unsupported_capabilities,
        ]
        if not question_supported:
            unsupported.append(
                f"baseline_question:{question_type.value}"
            )
        lane_blockers = tuple(
            capability
            for lane_item, capability in profile.unsupported_lanes
            if lane_item is decision_lane
        )
        lane_automated = lane.automated_capabilities
        if (
            decision_lane is DecisionLane.POSITION_SIZING
            and lane_blockers
        ):
            lane_automated = tuple(
                item
                for item in lane_automated
                if item != "personalized_position_plan"
            )
        return ResearchCapabilityView(
            profile_id=profile_id,
            question_type=question_type,
            decision_lane=decision_lane,
            question_supported=question_supported,
            automated_capabilities=_merge(
                profile.automated_capabilities,
                lane_automated,
            ),
            agent_query_capabilities=_merge(
                profile.agent_query_capabilities,
                lane.agent_query_capabilities,
            ),
            required_user_context=lane.required_user_context,
            unsupported_capabilities=_merge(
                tuple(unsupported),
            ),
            lane_unsupported_capabilities=lane_blockers,
            public_entrypoints=profile.public_entrypoints,
            evidence_requirements=_merge(
                profile.evidence_requirements,
                lane.evidence_requirements,
            ),
            candidate_questions=_merge(
                profile.candidate_questions,
                lane.candidate_questions,
            ),
            candidate_hypotheses=_merge(
                profile.candidate_hypotheses,
                lane.candidate_hypotheses,
            ),
        )


@dataclass(frozen=True)
class _LaneCapability:
    automated_capabilities: tuple[str, ...] = ()
    agent_query_capabilities: tuple[str, ...] = ()
    required_user_context: tuple[
        DecisionContextRequirement,
        ...,
    ] = ()
    unsupported_capabilities: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    candidate_questions: tuple[str, ...] = ()
    candidate_hypotheses: tuple[str, ...] = ()


_NO_DECISION_LANE = _LaneCapability(
    agent_query_capabilities=("baseline_gap_interpretation",),
    unsupported_capabilities=(
        "programmatic_final_decision_review",
    ),
    evidence_requirements=(
        "facts_require_source_and_as_of_date",
    ),
)

_LANE_CAPABILITIES = {
    None: _NO_DECISION_LANE,
    DecisionLane.SUITABILITY: _LaneCapability(
        automated_capabilities=(
            "structured_suitability_assessment",
            "programmatic_final_decision_review",
        ),
        agent_query_capabilities=(
            "alternative_comparison",
            "priced_expectations_review",
            "counterargument_review",
        ),
        required_user_context=(
            DecisionContextRequirement.PORTFOLIO_ROLE,
        ),
        evidence_requirements=(
            "alternatives_use_comparable_exposure",
            "strongest_counterargument_required",
        ),
        candidate_questions=(
            "当前收益来源是否足以补偿主要下行风险？",
            "相同风险暴露下是否存在更优替代方案？",
        ),
        candidate_hypotheses=(
            "标的在当前价格下仍有可验证的回报来源。",
        ),
    ),
    DecisionLane.TIMING: _LaneCapability(
        automated_capabilities=(
            "structured_entry_plan",
            "programmatic_final_decision_review",
        ),
        agent_query_capabilities=(
            "entry_condition_review",
            "catalyst_timeline_review",
            "cross_horizon_conflict_review",
        ),
        required_user_context=(
            DecisionContextRequirement.PORTFOLIO_ROLE,
        ),
        evidence_requirements=(
            "timing_signal_matches_horizon",
            "entry_and_avoid_conditions_are_observable",
        ),
        candidate_questions=(
            "哪些可观察条件支持现在行动、分批或等待？",
        ),
        candidate_hypotheses=(
            "当前市场状态提供了可执行且有失效条件的窗口。",
        ),
    ),
    DecisionLane.POSITION_SIZING: _LaneCapability(
        automated_capabilities=(
            "personalized_position_plan",
            "programmatic_final_decision_review",
        ),
        agent_query_capabilities=(
            "portfolio_constraint_review",
            "position_budget_input_review",
        ),
        required_user_context=(
            DecisionContextRequirement.INVESTMENT_MANDATE,
            DecisionContextRequirement.PORTFOLIO_CONSTRAINT_SNAPSHOT,
        ),
        evidence_requirements=(
            "position_requires_user_constraints",
            "position_inputs_must_be_reproducible",
        ),
        candidate_questions=(
            "哪些风险和组合约束会绑定目标仓位？",
        ),
        candidate_hypotheses=(
            "在显式风险预算下存在可承受的非零仓位区间。",
        ),
    ),
}


def hypothesis_decision_lane(statement: str) -> DecisionLane | None:
    """Return the decision lane that declares a candidate hypothesis."""

    for lane, capability in _LANE_CAPABILITIES.items():
        if lane is not None and statement in capability.candidate_hypotheses:
            return lane
    return None


def default_capability_registry() -> ResearchCapabilityRegistry:
    all_questions = tuple(ResearchQuestionType)
    common_automated = (
        "identity_validation",
        "data_quality_review",
        "baseline_report",
        "static_charts",
        "persistent_exploration_ledger",
        "confirmed_local_monitoring_handoff",
    )
    common_agent_queries = (
        "official_source_follow_up",
        "hypothesis_and_counterevidence_review",
        "dataset_query",
        "official_document_query",
        "benchmark_comparison_query",
        "event_lookup",
        "user_context_query",
        "portfolio_snapshot_query",
        "external_official_web_research",
    )
    common_unsupported = (
        "automatic_gap_to_query_loop",
    )
    common_evidence = (
        "derived_metrics_require_methodology",
        "remote_failure_differs_from_business_empty",
    )
    common_entrypoints = (
        "src.research_orchestration.ResearchRequestPlanner",
        "src.research_orchestration.ResearchOrchestrationPipeline",
        (
            "src.research_orchestration."
            "ResearchExplorationQueryService"
        ),
        (
            "src.research_orchestration."
            "SuitabilityAssessmentService"
        ),
        "src.research_orchestration.EntryPlanService",
        "src.research_orchestration.PositionPlanService",
        "src.research_orchestration.FinalDecisionReportService",
        "src.research_orchestration.MonitoringHandoffService",
    )
    return ResearchCapabilityRegistry(
        (
            ResearchProfileCapability(
                profile_id=PROFILE_ORDINARY_COMPANY,
                supported_questions=all_questions,
                automated_capabilities=(
                    *common_automated,
                    "stock_bars",
                    "fundamental_metrics",
                    "benchmark_comparison",
                    "strategy_evaluation",
                ),
                agent_query_capabilities=(
                    *common_agent_queries,
                    "industry_assignment_query",
                    "peer_comparison_query",
                ),
                unsupported_capabilities=common_unsupported,
                public_entrypoints=(
                    *common_entrypoints,
                    "src.research_workflow.ResearchExecutionService",
                ),
                evidence_requirements=common_evidence,
                candidate_questions=(
                    "盈利、现金流和估值能否共同支持核心论文？",
                ),
                candidate_hypotheses=(
                    "盈利质量能够持续而非仅由一次性因素驱动。",
                ),
            ),
            ResearchProfileCapability(
                profile_id=PROFILE_HIGH_GROWTH_UNPROFITABLE,
                supported_questions=all_questions,
                automated_capabilities=(
                    *common_automated,
                    "stock_bars",
                    "fundamental_metrics",
                    "cash_runway_metrics",
                    "growth_trend",
                    "revenue_reverse_dcf",
                    "benchmark_comparison",
                    "strategy_evaluation",
                ),
                agent_query_capabilities=(
                    *common_agent_queries,
                    "industry_assignment_query",
                    "peer_comparison_query",
                ),
                unsupported_capabilities=common_unsupported,
                public_entrypoints=(
                    *common_entrypoints,
                    "src.research_workflow.ResearchExecutionService",
                ),
                evidence_requirements=common_evidence,
                candidate_questions=(
                    "现金跑道能否支撑到商业化或盈亏平衡？",
                    "当前市值隐含的营收增速是否超出可信区间？",
                ),
                candidate_hypotheses=(
                    "营收高增长可持续，且在现金耗尽前无需大幅稀释融资。",
                ),
            ),
            ResearchProfileCapability(
                profile_id=PROFILE_FINANCIAL_INSTITUTION,
                supported_questions=all_questions,
                automated_capabilities=(
                    *common_automated,
                    "stock_bars",
                    "financial_institution_metrics",
                    "benchmark_comparison",
                    "strategy_evaluation",
                ),
                agent_query_capabilities=(
                    *common_agent_queries,
                    "industry_assignment_query",
                    "peer_comparison_query",
                ),
                unsupported_capabilities=common_unsupported,
                public_entrypoints=(
                    *common_entrypoints,
                    (
                        "src.research_workflow."
                        "FinancialInstitutionResearchExecutionService"
                    ),
                ),
                evidence_requirements=common_evidence,
                candidate_questions=(
                    "资本、资产质量与盈利韧性是否一致？",
                ),
                candidate_hypotheses=(
                    "资产质量风险可控且资本缓冲足以覆盖压力。",
                ),
            ),
            ResearchProfileCapability(
                profile_id=PROFILE_CYCLICAL_RESOURCE,
                supported_questions=all_questions,
                automated_capabilities=(
                    *common_automated,
                    "stock_bars",
                    "commodity_benchmark",
                    "cycle_metrics",
                    "strategy_evaluation",
                ),
                agent_query_capabilities=(
                    *common_agent_queries,
                    "industry_assignment_query",
                    "peer_comparison_query",
                ),
                unsupported_capabilities=common_unsupported,
                public_entrypoints=(
                    *common_entrypoints,
                    (
                        "src.research_workflow."
                        "CyclicalResourceResearchExecutionService"
                    ),
                ),
                evidence_requirements=common_evidence,
                candidate_questions=(
                    "价格、成本曲线和产能周期处于什么位置？",
                ),
                candidate_hypotheses=(
                    "周期供需改善能够覆盖价格均值回归风险。",
                ),
            ),
            ResearchProfileCapability(
                profile_id=PROFILE_EXCHANGE_FUND,
                supported_questions=all_questions,
                automated_capabilities=(
                    *common_automated,
                    "exchange_fund_bars",
                    "underlying_index_comparison",
                    "fund_trading_metrics",
                    "strategy_evaluation",
                ),
                agent_query_capabilities=common_agent_queries,
                unsupported_capabilities=common_unsupported,
                public_entrypoints=(
                    *common_entrypoints,
                    "src.research_workflow.ETFResearchExecutionService",
                ),
                evidence_requirements=common_evidence,
                candidate_questions=(
                    "底层暴露、费用、跟踪和流动性是否适配任务？",
                ),
                candidate_hypotheses=(
                    "该工具能以可接受成本实现目标风险暴露。",
                ),
            ),
            ResearchProfileCapability(
                profile_id=PROFILE_OPEN_FUND,
                supported_questions=(
                    ResearchQuestionType.UNDERSTANDING,
                ),
                automated_capabilities=(
                    *common_automated,
                    "official_nav",
                    "standard_return_windows",
                    "fund_risk_metrics",
                    "official_disclosure_facts",
                    "composite_benchmark_comparison",
                    "fund_dealing_rules_and_costs",
                    "fund_fee_drag",
                    "current_manager_tenure",
                    "underlying_index_valuation",
                ),
                agent_query_capabilities=common_agent_queries,
                unsupported_capabilities=(
                    *common_unsupported,
                    "open_fund_investment_execution",
                ),
                public_entrypoints=(
                    *common_entrypoints,
                    (
                        "src.research_workflow."
                        "OpenFundResearchExecutionService"
                    ),
                ),
                evidence_requirements=(
                    *common_evidence,
                    "official_fund_facts_require_document_reference",
                ),
                candidate_questions=(
                    "收益来自市场暴露、经理选择还是风格漂移？",
                ),
                candidate_hypotheses=(
                    "历史超额收益有可解释且可持续的来源。",
                ),
            ),
        )
    )


def _merge(*groups: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in groups
            for item in group
        )
    )


def _text_values(values: tuple[str, ...], name: str) -> None:
    if not values:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-blank strings")
    _unique(values, name)


def _unique(values: tuple[Any, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
