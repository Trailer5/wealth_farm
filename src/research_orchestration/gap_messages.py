"""User-facing Chinese text for assembly and query gap codes."""

from __future__ import annotations


# Gaps about the Agent's own tooling, not about the research subject.
AGENT_ONLY_GAP_CODES = frozenset({"automatic_gap_to_query_loop"})


_EXACT = {
    "exploration_not_ready_for_report": "探索会话尚未达到最终报告门禁。",
    "suitability_lane_not_requested": "用户未请求是否配置决策。",
    "suitability_question_not_answered": "是否配置的阻塞问题尚未回答。",
    "thesis_missing": "尚未形成核心论文。",
    "thesis_unresolved": "核心论文仍未完成验证。",
    "thesis_evidence_missing": "核心论文缺少支持证据。",
    "thesis_evidence_not_recorded": "核心论文证据尚未写入探索账本。",
    "thesis_falsification_missing": "核心论文缺少可复核的证伪条件。",
    "thesis_conflicts_unresolved": "核心论文仍有未解决冲突。",
    "strongest_counterargument_missing": "尚未形成最强反方。",
    "strongest_counterargument_unresolved": "最强反方仍未完成验证。",
    "strongest_counterargument_evidence_missing": "最强反方缺少证据。",
    "strongest_counterargument_evidence_not_recorded": "最强反方证据尚未写入探索账本。",
    "strongest_counterargument_conflicts_unresolved": "最强反方仍有未解决冲突。",
    "alternative_question_not_answered": "可比替代方案问题尚未回答。",
    "alternative_matches_research_asset": "替代方案不能与当前研究标的是同一资产。",
    "alternative_opportunity_cost_mismatch": "替代方案与机会成本没有使用同一可比暴露。",
    "decision_brief_unavailable": "缺少用户决策简报。",
    "portfolio_role_missing": "缺少用户确认的组合角色，结论最多到“观察”（非个性化判断）。",
    "decision_intent_not_actionable": "当前用户目标仅为理解标的，不支持行动结论。",
    "holding_review_requires_held_position": "持仓复核需要先确认当前确有持仓。",
    "timing_lane_not_requested": "用户未请求行动时点决策。",
    "decision_brief_timing_lane_not_requested": "用户简报未请求行动时点决策。",
    "suitability_action_avoid": "是否配置结论为回避，因此行动时点不适用。",
    "suitability_assessment_insufficient": "是否配置结论证据不足，行动时点与仓位暂不装配。",
    "timing_question_not_answered": "行动时点的阻塞问题尚未回答。",
    "timing_condition_windows_do_not_overlap": "各项行动条件的有效窗口没有交集。",
    "next_tradable_after_signal_expiry": "下一可交易时间晚于信号有效期。",
    "next_tradable_before_signal_validity": "下一可交易时间早于信号生效日。",
    "next_tradable_outside_signal_window": "下一可交易时间不在信号有效窗口内。",
    "cross_horizon_conflict_unrecorded": "长短周期冲突尚未正式记录。",
    "position_sizing_question_not_answered": "仓位决策的阻塞问题尚未回答。",
    "decision_brief_position_lane_not_requested": "用户简报未请求仓位决策。",
    "mandate_id_unavailable": "缺少用户确认的投资授权与风险预算。",
    "portfolio_constraint_snapshot_id_unavailable": "缺少用户确认的组合约束快照。",
    "personalized_session_user_unavailable": "探索会话尚未绑定用户。",
    "position_calculation_inputs_unavailable": "缺少带证据的价格、波动、回撤或基准风险输入。",
    "automatic_gap_to_query_loop": "当前不会自动把缺口转成查询并循环执行。",
    "peer_industry_assignment_missing": "缺少同行行业归属，未做同行比较。",
    "research_evidence_partial": "部分证据来源出错，证据可能不全。",
    "research_evidence_coverage_incomplete": "证据来源的覆盖区间不完整。",
}

_PREFIXES = {
    "blocking_query_gap": "存在尚未解决的阻塞查询缺口。",
    "finding_evidence_not_recorded": "有结论证据尚未写入探索账本。",
    "unsupported_suitability_profile": "当前资产研究类型尚不支持是否配置装配。",
    "profile_timing_execution_unsupported": "当前资产研究类型尚无可执行的时点模型。",
    "profile_position_sizing_unsupported": "当前资产研究类型尚不支持个性化执行仓位。",
    "unsupported_entry_profile": "当前资产研究类型尚不支持时点装配。",
    "timing_state_unknown": "有行动条件的当前状态仍未知。",
    "execution_horizon_input_missing": "缺少与执行周期一致的时点输入。",
    "capability_unavailable": "有查询所需的能力当前不支持。",
}

_USER_CONTEXT = {
    "position_state": "缺少当前是否持有的信息。",
    "current_position_details": "缺少当前持仓数量与成本信息。",
    "portfolio_role": "缺少用户确认的组合角色。",
    "investment_mandate": "缺少投资授权与风险预算。",
    "portfolio_constraint_snapshot": "缺少组合约束与估值快照。",
}

_SUITABILITY_DIMENSIONS = {
    "expected_return_source": "缺少可验证的收益来源证据。",
    "priced_expectation": "缺少当前定价隐含预期证据。",
    "downside": "缺少主要下行风险证据。",
    "alternative": "缺少同风险替代方案证据。",
    "opportunity_cost": "缺少机会成本证据。",
}

_QUERY_SUFFIXES = (
    ("_business_empty", "有查询没有返回业务数据。"),
    ("_source_unavailable", "有查询的数据源不可用。"),
    ("_user_context_required", "有查询需要用户补充信息。"),
    ("_partial", "有查询结果不完整。"),
    ("_query_failed", "有查询执行失败。"),
)

_GENERIC = "存在尚未解决的正式装配缺口，需要补齐对应输入。"


def final_report_gap_message(value: str) -> str:
    if value in _EXACT:
        return _EXACT[value]
    prefix, _, detail = value.partition(":")
    if prefix in _PREFIXES:
        return _PREFIXES[prefix]
    if prefix == "user_context_missing":
        return _USER_CONTEXT.get(
            detail,
            "缺少完成仓位决策所需的用户信息。",
        )
    if prefix == "suitability_dimension_missing":
        return _SUITABILITY_DIMENSIONS.get(
            detail,
            "缺少适配决策的必需证据。",
        )
    if prefix == "timing_dimension_missing":
        return "缺少行动时点的一项必需条件。"
    if prefix == "profile_input_missing":
        return "缺少当前资产研究类型要求的正式输入。"
    for suffix, message in _QUERY_SUFFIXES:
        if value.endswith(suffix):
            return message
    return _GENERIC
