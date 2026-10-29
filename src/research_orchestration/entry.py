"""Deterministic assembly of observable entry and waiting plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionLane,
    ResearchDecisionBrief,
    ResearchHorizon,
)

from .entry_models import (
    EntryConditionBlock,
    EntryConditionKind,
    EntryConfidence,
    EntryPlan,
    EntryPlanStatus,
    EntryProfilePolicy,
    TimingConditionState,
    TimingDimension,
    TimingInputKind,
    allowed_next_tradable_input_kinds,
    entry_plan_id,
)
from .exploration_models import (
    ExplorationStatus,
    ExplorationTrack,
    QuestionStatus,
    ResearchExplorationSession,
    ResearchFinding,
    ResearchFindingKind,
)
from .gap_messages import final_report_gap_message
from .suitability_models import (
    SuitabilityAction,
    SuitabilityAssessment,
)


@dataclass(frozen=True)
class _PlanState:
    not_applicable_reasons: tuple[str, ...]
    core_gaps: tuple[str, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        return _merge_texts(
            self.not_applicable_reasons,
            self.core_gaps,
        )


class EntryPlanService:
    """Project timing findings without predicting prices or dates."""

    def __init__(
        self,
        policies: tuple[EntryProfilePolicy, ...] | None = None,
    ) -> None:
        values = policies or default_entry_policies()
        self._policies = {
            item.profile_id: item for item in values
        }
        if len(self._policies) != len(values):
            raise ValueError(
                "entry profile policies must be unique"
            )

    def resolve_policy(
        self,
        profile_id: str,
    ) -> EntryProfilePolicy | None:
        return self._policies.get(profile_id)

    def assemble(
        self,
        *,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        decision_brief: ResearchDecisionBrief | None = None,
    ) -> EntryPlan:
        self._validate_identity(
            session,
            suitability,
            decision_brief,
        )
        policy = self.resolve_policy(session.task.profile_id)
        recorded_evidence = frozenset(
            ref
            for round_result in session.rounds
            for ref in round_result.evidence_refs
        )
        findings = tuple(
            finding
            for round_result in session.rounds
            for finding in round_result.structured_findings
            if finding.timing_dimension is not None
        )
        blocks = {
            dimension: tuple(
                block
                for finding in findings
                if finding.timing_dimension is dimension
                and (
                    block := _finding_block(
                        finding,
                        recorded_evidence,
                    )
                )
                is not None
            )
            for dimension in TimingDimension
        }
        state = self._state(
            session=session,
            suitability=suitability,
            decision_brief=decision_brief,
            policy=policy,
            findings=findings,
            blocks=blocks,
            recorded_evidence=recorded_evidence,
        )
        status = _status(
            state=state,
            suitability=suitability,
            blocks=blocks,
        )
        confidence = _confidence(status, state)
        base_blocks = tuple(
            item
            for values in blocks.values()
            for item in values
            if item.dimension
            is not TimingDimension.NEXT_TRADABLE_TIME
        )
        next_tradable_condition = _next_tradable_condition(
            blocks[TimingDimension.NEXT_TRADABLE_TIME],
            base_blocks,
            session.task.horizon,
            session.task.profile_id,
        )
        next_tradable_at = (
            next_tradable_condition.next_tradable_at
            if next_tradable_condition is not None
            else None
        )
        emitted_blocks = (
            *base_blocks,
            *(
                (next_tradable_condition,)
                if next_tradable_condition is not None
                else ()
            ),
        )
        aggregate_valid_from = (
            max(item.valid_from for item in emitted_blocks)
            if emitted_blocks
            else None
        )
        aggregate_valid_until = (
            min(item.valid_until for item in emitted_blocks)
            if emitted_blocks
            else None
        )
        windows_overlap = (
            aggregate_valid_from is None
            or aggregate_valid_until is None
            or aggregate_valid_from <= aggregate_valid_until
        )
        signal_valid_from = (
            aggregate_valid_from if windows_overlap else None
        )
        signal_valid_until = (
            aggregate_valid_until if windows_overlap else None
        )
        evidence_refs = _merge_texts(
            suitability.evidence_refs,
            *(item.evidence_refs for item in emitted_blocks),
        )
        limitations = _merge_texts(
            suitability.limitations,
            *(item.limitations for item in emitted_blocks),
            *(
                (final_report_gap_message(gap.code),)
                for gap in session.query_gaps
            ),
        ) or ("时点判断只基于截止日前已入账的证据。",)
        policy_id = (
            policy.policy_id
            if policy is not None
            else f"{session.task.profile_id}:entry_unsupported"
        )
        values = {
            "session_id": session.session_id,
            "suitability_assessment_id": (
                suitability.assessment_id
            ),
            "decision_brief_id": (
                decision_brief.brief_id
                if decision_brief is not None
                else session.decision_brief_id
            ),
            "asset_id": session.task.asset_id,
            "profile_id": session.task.profile_id,
            "policy_id": policy_id,
            "as_of_date": session.task.as_of_date,
            "execution_horizon": session.task.horizon,
            "suitability_horizon": suitability.time_horizon,
            "status": status,
            "confidence": confidence,
            "current_regime": blocks[
                TimingDimension.CURRENT_REGIME
            ],
            "entry_conditions": blocks[
                TimingDimension.ENTRY_CONDITION
            ],
            "wait_conditions": blocks[
                TimingDimension.WAIT_CONDITION
            ],
            "tranche_rules": blocks[
                TimingDimension.TRANCHE_RULE
            ],
            "avoid_conditions": blocks[
                TimingDimension.AVOID_CONDITION
            ],
            "catalyst_windows": blocks[
                TimingDimension.CATALYST_WINDOW
            ],
            "invalidation_conditions": blocks[
                TimingDimension.INVALIDATION_CONDITION
            ],
            "execution_constraints": blocks[
                TimingDimension.EXECUTION_CONSTRAINT
            ],
            "horizon_conflicts": blocks[
                TimingDimension.HORIZON_CONFLICT
            ],
            "next_tradable_condition": (
                next_tradable_condition
            ),
            "signal_valid_from": signal_valid_from,
            "signal_valid_until": signal_valid_until,
            "next_tradable_at": next_tradable_at,
            "evidence_refs": evidence_refs,
            "limitations": limitations,
            "assembly_gaps": state.reasons,
        }
        payload = _entry_payload(values)
        return EntryPlan(
            plan_id=entry_plan_id(payload),
            **values,
        )

    def _validate_identity(
        self,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        decision_brief: ResearchDecisionBrief | None,
    ) -> None:
        if suitability.session_id != session.session_id:
            raise ValueError(
                "suitability session does not match exploration"
            )
        if suitability.asset_id != session.task.asset_id:
            raise ValueError(
                "suitability asset does not match exploration"
            )
        if suitability.profile_id != session.task.profile_id:
            raise ValueError(
                "suitability profile does not match exploration"
            )
        if suitability.as_of_date != session.task.as_of_date:
            raise ValueError(
                "suitability cutoff does not match exploration"
            )
        if (
            suitability.workflow_run_id
            != session.workflow_run_id
        ):
            raise ValueError(
                "suitability workflow does not match exploration"
            )
        if decision_brief is None:
            return
        if decision_brief.asset_id != session.task.asset_id:
            raise ValueError(
                "decision brief asset does not match exploration"
            )
        if (
            session.user_id is not None
            and decision_brief.user_id != session.user_id
        ):
            raise ValueError(
                "decision brief user does not match exploration"
            )
        if (
            session.decision_brief_id is not None
            and decision_brief.brief_id
            != session.decision_brief_id
        ):
            raise ValueError(
                "decision brief id does not match exploration"
            )
        if (
            suitability.decision_brief_id is not None
            and decision_brief.brief_id
            != suitability.decision_brief_id
        ):
            raise ValueError(
                "decision brief id does not match suitability"
            )
        if decision_brief.horizon is not session.task.horizon:
            raise ValueError(
                "decision brief horizon does not match exploration"
            )
        if decision_brief.as_of_date > session.task.as_of_date:
            raise ValueError(
                "decision brief exceeds exploration as_of_date"
            )

    def _state(
        self,
        *,
        session: ResearchExplorationSession,
        suitability: SuitabilityAssessment,
        decision_brief: ResearchDecisionBrief | None,
        policy: EntryProfilePolicy | None,
        findings: tuple[ResearchFinding, ...],
        blocks: dict[
            TimingDimension,
            tuple[EntryConditionBlock, ...],
        ],
        recorded_evidence: frozenset[str],
    ) -> _PlanState:
        not_applicable = []
        core = []
        if DecisionLane.TIMING not in session.decision_lanes:
            not_applicable.append("timing_lane_not_requested")
        if (
            decision_brief is not None
            and DecisionLane.TIMING
            not in decision_brief.decision_lanes
        ):
            not_applicable.append(
                "decision_brief_timing_lane_not_requested"
            )
        if suitability.action is SuitabilityAction.AVOID:
            not_applicable.append("suitability_action_avoid")
        if not_applicable:
            return _PlanState(
                not_applicable_reasons=_merge_texts(
                    tuple(not_applicable)
                ),
                core_gaps=(),
            )
        if suitability.action is SuitabilityAction.INSUFFICIENT:
            core.append("suitability_assessment_insufficient")
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            core.append("exploration_not_ready_for_report")
        if decision_brief is None:
            core.append("decision_brief_unavailable")
        timing_questions = tuple(
            item
            for item in session.questions
            if item.track is ExplorationTrack.TIMING
            and item.blocking
        )
        if not timing_questions or any(
            item.status is not QuestionStatus.ANSWERED
            for item in timing_questions
        ):
            core.append("timing_question_not_answered")
        if policy is None:
            core.append(
                f"unsupported_entry_profile:"
                f"{session.task.profile_id}"
            )
        elif not policy.timing_supported:
            core.append(
                f"profile_timing_execution_unsupported:"
                f"{session.task.profile_id}"
            )
        for gap in session.unresolved_blocking_query_gaps():
            core.append(f"blocking_query_gap:{gap.code}")
        for finding in findings:
            if not set(finding.evidence_refs).issubset(
                recorded_evidence
            ):
                core.append(
                    f"finding_evidence_not_recorded:"
                    f"{finding.finding_id}"
                )
        for dimension in _REQUIRED_DIMENSIONS:
            if not blocks[dimension]:
                core.append(
                    f"timing_dimension_missing:{dimension.value}"
                )
        for dimension in _EXECUTION_HORIZON_DIMENSIONS:
            if blocks[dimension] and not any(
                item.signal_horizon is session.task.horizon
                for item in blocks[dimension]
            ):
                core.append(
                    f"execution_horizon_input_missing:"
                    f"{dimension.value}"
                )
        if policy is not None:
            for input_kind in policy.required_input_kinds:
                if not _has_profile_input(
                    findings,
                    input_kind,
                    recorded_evidence,
                    session.task.horizon,
                ):
                    core.append(
                        f"profile_input_missing:{input_kind.value}"
                    )
        for block in (
            item
            for values in blocks.values()
            for item in values
        ):
            if (
                block.state is TimingConditionState.UNKNOWN
                and block.dimension
                is not TimingDimension.NEXT_TRADABLE_TIME
            ):
                core.append(
                    f"timing_state_unknown:{block.condition_id}"
                )
        base_blocks = tuple(
            item
            for values in blocks.values()
            for item in values
            if item.dimension
            is not TimingDimension.NEXT_TRADABLE_TIME
        )
        next_tradable = _next_tradable_condition(
            blocks[TimingDimension.NEXT_TRADABLE_TIME],
            base_blocks,
            session.task.horizon,
            session.task.profile_id,
        )
        if base_blocks and max(
            item.valid_from for item in base_blocks
        ) > min(item.valid_until for item in base_blocks):
            core.append(
                "timing_condition_windows_do_not_overlap"
            )
        if next_tradable is None:
            for item in blocks[
                TimingDimension.NEXT_TRADABLE_TIME
            ]:
                if item.state is TimingConditionState.UNKNOWN:
                    core.append(
                        f"timing_state_unknown:{item.condition_id}"
                    )
        met_candidates = tuple(
            item
            for item in blocks[
                TimingDimension.NEXT_TRADABLE_TIME
            ]
            if item.state is TimingConditionState.MET
            and item.signal_horizon is session.task.horizon
            and item.input_kind
            in allowed_next_tradable_input_kinds(
                session.task.profile_id
            )
        )
        if met_candidates and next_tradable is None:
            base_valid_from = max(
                item.valid_from for item in base_blocks
            )
            base_valid_until = min(
                item.valid_until for item in base_blocks
            )
            candidate_dates = tuple(
                datetime.fromisoformat(
                    item.next_tradable_at
                ).date()
                for item in met_candidates
            )
            if all(
                value
                > datetime.fromisoformat(
                    base_valid_until
                ).date()
                for value in candidate_dates
            ):
                core.append(
                    "next_tradable_after_signal_expiry"
                )
            elif all(
                value
                < datetime.fromisoformat(
                    base_valid_from
                ).date()
                for value in candidate_dates
            ):
                core.append(
                    "next_tradable_before_signal_validity"
                )
            else:
                core.append(
                    "next_tradable_outside_signal_window"
                )
        if _has_unrecorded_horizon_conflict(blocks):
            core.append("cross_horizon_conflict_unrecorded")
        return _PlanState(
            not_applicable_reasons=(),
            core_gaps=_merge_texts(tuple(core)),
        )


_REQUIRED_DIMENSIONS = (
    TimingDimension.CURRENT_REGIME,
    TimingDimension.ENTRY_CONDITION,
    TimingDimension.WAIT_CONDITION,
    TimingDimension.TRANCHE_RULE,
    TimingDimension.AVOID_CONDITION,
    TimingDimension.CATALYST_WINDOW,
    TimingDimension.INVALIDATION_CONDITION,
    TimingDimension.NEXT_TRADABLE_TIME,
    TimingDimension.EXECUTION_CONSTRAINT,
)


_EXECUTION_HORIZON_DIMENSIONS = (
    TimingDimension.CURRENT_REGIME,
    TimingDimension.ENTRY_CONDITION,
    TimingDimension.WAIT_CONDITION,
    TimingDimension.TRANCHE_RULE,
    TimingDimension.AVOID_CONDITION,
    TimingDimension.CATALYST_WINDOW,
    TimingDimension.INVALIDATION_CONDITION,
    TimingDimension.NEXT_TRADABLE_TIME,
    TimingDimension.EXECUTION_CONSTRAINT,
)


_INPUT_DIMENSIONS = {
    TimingInputKind.VALUATION_REGIME: (
        TimingDimension.CURRENT_REGIME,
        TimingDimension.ENTRY_CONDITION,
    ),
    TimingInputKind.TREND_STATE: (
        TimingDimension.CURRENT_REGIME,
        TimingDimension.ENTRY_CONDITION,
        TimingDimension.WAIT_CONDITION,
        TimingDimension.TRANCHE_RULE,
    ),
    TimingInputKind.CYCLE_POSITION: (
        TimingDimension.CURRENT_REGIME,
        TimingDimension.ENTRY_CONDITION,
        TimingDimension.WAIT_CONDITION,
    ),
    TimingInputKind.CATALYST_TIMELINE: (
        TimingDimension.CATALYST_WINDOW,
    ),
    TimingInputKind.LIQUIDITY_STATE: (
        TimingDimension.AVOID_CONDITION,
        TimingDimension.EXECUTION_CONSTRAINT,
    ),
    TimingInputKind.ETF_PREMIUM_DISCOUNT: (
        TimingDimension.CURRENT_REGIME,
        TimingDimension.AVOID_CONDITION,
        TimingDimension.EXECUTION_CONSTRAINT,
    ),
    TimingInputKind.MARKET_RISK_STATE: (
        TimingDimension.CURRENT_REGIME,
        TimingDimension.WAIT_CONDITION,
        TimingDimension.INVALIDATION_CONDITION,
    ),
    TimingInputKind.TRADING_CALENDAR: (
        TimingDimension.NEXT_TRADABLE_TIME,
    ),
    TimingInputKind.FUND_DEALING_RULE: (
        TimingDimension.NEXT_TRADABLE_TIME,
        TimingDimension.EXECUTION_CONSTRAINT,
    ),
    TimingInputKind.CROSS_HORIZON: (
        TimingDimension.HORIZON_CONFLICT,
    ),
}


def default_entry_policies() -> tuple[EntryProfilePolicy, ...]:
    market_execution = (
        TimingInputKind.TREND_STATE,
        TimingInputKind.CATALYST_TIMELINE,
        TimingInputKind.LIQUIDITY_STATE,
        TimingInputKind.MARKET_RISK_STATE,
        TimingInputKind.TRADING_CALENDAR,
    )
    return (
        EntryProfilePolicy(
            profile_id=PROFILE_ORDINARY_COMPANY,
            policy_id="ordinary_company:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.VALUATION_REGIME,
                *market_execution,
            ),
            execution_semantics=(
                "valuation, trend, catalyst, liquidity, and "
                "market-risk conditions"
            ),
        ),
        EntryProfilePolicy(
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            policy_id="financial_institution:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.VALUATION_REGIME,
                *market_execution,
            ),
            execution_semantics=(
                "book-value regime, trend, liquidity, and "
                "financial market-risk conditions"
            ),
        ),
        EntryProfilePolicy(
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            policy_id="cyclical_resource:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.CYCLE_POSITION,
                *market_execution,
            ),
            execution_semantics=(
                "cycle position, trend, catalyst, liquidity, "
                "and market-risk conditions"
            ),
        ),
        EntryProfilePolicy(
            profile_id=PROFILE_HIGH_GROWTH_UNPROFITABLE,
            policy_id="high_growth_unprofitable:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.VALUATION_REGIME,
                *market_execution,
            ),
            execution_semantics=(
                "PS regime, financing and catalyst timeline, trend, "
                "liquidity, and market-risk conditions"
            ),
        ),
        EntryProfilePolicy(
            profile_id=PROFILE_EXCHANGE_FUND,
            policy_id="exchange_fund:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.VALUATION_REGIME,
                TimingInputKind.ETF_PREMIUM_DISCOUNT,
                *market_execution,
            ),
            execution_semantics=(
                "underlying valuation, trend, premium, "
                "liquidity, and market-risk conditions"
            ),
        ),
        EntryProfilePolicy(
            profile_id=PROFILE_OPEN_FUND,
            policy_id="open_fund:entry_v1",
            timing_supported=True,
            required_input_kinds=(
                TimingInputKind.FUND_DEALING_RULE,
                TimingInputKind.VALUATION_REGIME,
                TimingInputKind.MARKET_RISK_STATE,
                TimingInputKind.TRADING_CALENDAR,
            ),
            execution_semantics=(
                "NAV dealing at the 15:00 cutoff with T+n confirmation, "
                "redemption-fee holding periods, underlying index valuation "
                "regime, and market-risk conditions; no intraday price"
            ),
        ),
    )


def _finding_block(
    finding: ResearchFinding,
    recorded_evidence: frozenset[str],
) -> EntryConditionBlock | None:
    if finding.kind not in (
        ResearchFindingKind.FACT,
        ResearchFindingKind.INFERENCE,
    ):
        return None
    if not set(finding.evidence_refs).issubset(
        recorded_evidence
    ):
        return None
    assert finding.timing_dimension is not None
    assert finding.timing_input_kind is not None
    assert finding.timing_state is not None
    assert finding.signal_horizon is not None
    assert finding.timing_valid_from is not None
    assert finding.timing_valid_until is not None
    assert finding.observability_key is not None
    return EntryConditionBlock(
        condition_id=f"entry:finding:{finding.finding_id}",
        text=finding.text,
        kind=(
            EntryConditionKind.FACT
            if finding.kind is ResearchFindingKind.FACT
            else EntryConditionKind.INFERENCE
        ),
        dimension=finding.timing_dimension,
        input_kind=finding.timing_input_kind,
        state=finding.timing_state,
        evidence_refs=finding.evidence_refs,
        limitations=finding.limitations,
        signal_horizon=finding.signal_horizon,
        as_of_date=finding.as_of_date,
        valid_from=finding.timing_valid_from,
        valid_until=finding.timing_valid_until,
        observability_key=finding.observability_key,
        source_finding_ids=(finding.finding_id,),
        window_start=finding.catalyst_window_start,
        window_end=finding.catalyst_window_end,
        next_tradable_at=finding.next_tradable_at,
    )


def _has_profile_input(
    findings: tuple[ResearchFinding, ...],
    input_kind: TimingInputKind,
    recorded_evidence: frozenset[str],
    execution_horizon: ResearchHorizon,
) -> bool:
    dimensions = _INPUT_DIMENSIONS[input_kind]
    require_execution_horizon = input_kind in (
        TimingInputKind.TREND_STATE,
        TimingInputKind.LIQUIDITY_STATE,
        TimingInputKind.ETF_PREMIUM_DISCOUNT,
        TimingInputKind.MARKET_RISK_STATE,
        TimingInputKind.CATALYST_TIMELINE,
        TimingInputKind.TRADING_CALENDAR,
        TimingInputKind.FUND_DEALING_RULE,
    )
    return any(
        item.timing_input_kind is input_kind
        and item.timing_dimension in dimensions
        and item.kind
        in (
            ResearchFindingKind.FACT,
            ResearchFindingKind.INFERENCE,
        )
        and set(item.evidence_refs).issubset(
            recorded_evidence
        )
        and item.timing_state
        is not TimingConditionState.UNKNOWN
        and (
            not require_execution_horizon
            or item.signal_horizon is execution_horizon
        )
        for item in findings
    )


def _has_unrecorded_horizon_conflict(
    blocks: dict[
        TimingDimension,
        tuple[EntryConditionBlock, ...],
    ],
) -> bool:
    supportive = {
        item.signal_horizon
        for item in (
            *blocks[TimingDimension.CURRENT_REGIME],
            *blocks[TimingDimension.ENTRY_CONDITION],
        )
        if item.state
        in (
            TimingConditionState.SUPPORTIVE,
            TimingConditionState.MET,
        )
    }
    adverse = {
        item.signal_horizon
        for item in (
            *blocks[TimingDimension.CURRENT_REGIME],
            *blocks[TimingDimension.WAIT_CONDITION],
            *blocks[TimingDimension.AVOID_CONDITION],
        )
        if item.state
        in (
            TimingConditionState.ADVERSE,
            TimingConditionState.MET,
        )
    }
    detected = any(
        support_horizon is not adverse_horizon
        for support_horizon in supportive
        for adverse_horizon in adverse
    )
    active_conflict = any(
        item.state is TimingConditionState.MET
        for item in blocks[TimingDimension.HORIZON_CONFLICT]
    )
    return detected and not active_conflict


def _status(
    *,
    state: _PlanState,
    suitability: SuitabilityAssessment,
    blocks: dict[
        TimingDimension,
        tuple[EntryConditionBlock, ...],
    ],
) -> EntryPlanStatus:
    if state.not_applicable_reasons:
        return EntryPlanStatus.NOT_APPLICABLE
    if state.core_gaps:
        return EntryPlanStatus.INSUFFICIENT
    if suitability.action is SuitabilityAction.WATCH:
        return EntryPlanStatus.WAIT
    if any(
        item.state
        in (
            TimingConditionState.ADVERSE,
            TimingConditionState.MET,
            TimingConditionState.WATCH,
        )
        for item in (
            *blocks[TimingDimension.WAIT_CONDITION],
            *blocks[TimingDimension.AVOID_CONDITION],
            *blocks[TimingDimension.INVALIDATION_CONDITION],
        )
    ) or any(
        item.state is TimingConditionState.ADVERSE
        for item in blocks[TimingDimension.CURRENT_REGIME]
    ):
        return EntryPlanStatus.WAIT
    if any(
        item.state
        in (
            TimingConditionState.NOT_MET,
            TimingConditionState.ADVERSE,
        )
        for item in blocks[
            TimingDimension.EXECUTION_CONSTRAINT
        ]
    ):
        return EntryPlanStatus.WAIT
    if any(
        item.state
        in (
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
        )
        for item in blocks[TimingDimension.CATALYST_WINDOW]
    ):
        return EntryPlanStatus.WAIT
    if any(
        item.state is TimingConditionState.MET
        for item in blocks[TimingDimension.HORIZON_CONFLICT]
    ):
        return EntryPlanStatus.WAIT
    if not all(
        item.state is TimingConditionState.MET
        for item in blocks[TimingDimension.ENTRY_CONDITION]
    ):
        return EntryPlanStatus.WAIT
    if _staging_is_active(blocks):
        return EntryPlanStatus.STAGE
    if all(
        item.state is TimingConditionState.NOT_APPLICABLE
        for item in blocks[TimingDimension.TRANCHE_RULE]
    ):
        return EntryPlanStatus.ACT
    return EntryPlanStatus.WAIT


def _staging_is_active(
    blocks: dict[
        TimingDimension,
        tuple[EntryConditionBlock, ...],
    ],
) -> bool:
    return any(
        item.state is TimingConditionState.MET
        for item in blocks[TimingDimension.TRANCHE_RULE]
    )


def _confidence(
    status: EntryPlanStatus,
    state: _PlanState,
) -> EntryConfidence:
    if status is EntryPlanStatus.INSUFFICIENT:
        return EntryConfidence.INSUFFICIENT
    if status is EntryPlanStatus.NOT_APPLICABLE:
        return EntryConfidence.NOT_APPLICABLE
    if state.reasons or status in (
        EntryPlanStatus.STAGE,
        EntryPlanStatus.WAIT,
    ):
        return EntryConfidence.BOUNDED
    return EntryConfidence.SUPPORTED


def _next_tradable_condition(
    blocks: tuple[EntryConditionBlock, ...],
    base_blocks: tuple[EntryConditionBlock, ...],
    execution_horizon: ResearchHorizon,
    profile_id: str,
) -> EntryConditionBlock | None:
    tradable = tuple(
        item
        for item in blocks
        if item.state is TimingConditionState.MET
        and item.signal_horizon is execution_horizon
        and item.input_kind
        in allowed_next_tradable_input_kinds(profile_id)
        and item.next_tradable_at is not None
        and datetime.fromisoformat(
            item.next_tradable_at
        ).date()
        >= datetime.fromisoformat(
            max(
                block.valid_from
                for block in (*base_blocks, item)
            )
        ).date()
        and datetime.fromisoformat(
            item.next_tradable_at
        ).date()
        <= datetime.fromisoformat(
            min(
                block.valid_until
                for block in (*base_blocks, item)
            )
        ).date()
    )
    return (
        min(
            tradable,
            key=lambda item: datetime.fromisoformat(
                item.next_tradable_at
            ),
        )
        if tradable
        else None
    )


def _entry_payload(values: dict[str, object]) -> dict[str, object]:
    return {
        "session_id": values["session_id"],
        "suitability_assessment_id": values[
            "suitability_assessment_id"
        ],
        "decision_brief_id": values["decision_brief_id"],
        "asset_id": values["asset_id"],
        "profile_id": values["profile_id"],
        "policy_id": values["policy_id"],
        "as_of_date": values["as_of_date"],
        "execution_horizon": values[
            "execution_horizon"
        ].value,
        "suitability_horizon": values[
            "suitability_horizon"
        ].value,
        "status": values["status"].value,
        "confidence": values["confidence"].value,
        "current_regime": _blocks_payload(
            values["current_regime"]
        ),
        "entry_conditions": _blocks_payload(
            values["entry_conditions"]
        ),
        "wait_conditions": _blocks_payload(
            values["wait_conditions"]
        ),
        "tranche_rules": _blocks_payload(
            values["tranche_rules"]
        ),
        "avoid_conditions": _blocks_payload(
            values["avoid_conditions"]
        ),
        "catalyst_windows": _blocks_payload(
            values["catalyst_windows"]
        ),
        "invalidation_conditions": _blocks_payload(
            values["invalidation_conditions"]
        ),
        "execution_constraints": _blocks_payload(
            values["execution_constraints"]
        ),
        "horizon_conflicts": _blocks_payload(
            values["horizon_conflicts"]
        ),
        "next_tradable_condition": (
            values["next_tradable_condition"].to_dict()
            if values["next_tradable_condition"] is not None
            else None
        ),
        "signal_valid_from": values["signal_valid_from"],
        "signal_valid_until": values["signal_valid_until"],
        "next_tradable_at": values["next_tradable_at"],
        "evidence_refs": list(values["evidence_refs"]),
        "limitations": list(values["limitations"]),
        "assembly_gaps": list(values["assembly_gaps"]),
    }


def _blocks_payload(
    blocks: object,
) -> list[dict[str, object]]:
    return [item.to_dict() for item in blocks]


def _merge_texts(
    *values: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in values
            for item in group
            if item
        )
    )
