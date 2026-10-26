"""Deterministic assembly of the independent suitability decision."""

from __future__ import annotations

from dataclasses import dataclass

from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionContextRequirement,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
)

from .exploration_models import (
    ExplorationStatus,
    ExplorationTrack,
    HypothesisRole,
    HypothesisStatus,
    QuestionStatus,
    ResearchExplorationSession,
    ResearchFinding,
    ResearchFindingKind,
    ResearchHypothesis,
)
from .gap_messages import final_report_gap_message
from .suitability_models import (
    SuitabilityAction,
    SuitabilityAssessment,
    SuitabilityClaimBlock,
    SuitabilityClaimKind,
    SuitabilityClaimState,
    SuitabilityConfidence,
    SuitabilityDimension,
    SuitabilityInputKind,
    SuitabilityProfilePolicy,
    suitability_assessment_id,
)


@dataclass(frozen=True)
class _AssemblyState:
    core_gaps: tuple[str, ...]
    positive_gaps: tuple[str, ...]

    @property
    def gaps(self) -> tuple[str, ...]:
        return _merge_texts(
            self.core_gaps,
            self.positive_gaps,
        )


class SuitabilityAssessmentService:
    """Project recorded exploration evidence without scoring."""

    def __init__(
        self,
        policies: tuple[
            SuitabilityProfilePolicy,
            ...,
        ] | None = None,
    ) -> None:
        values = policies or default_suitability_policies()
        self._policies = {
            item.profile_id: item for item in values
        }
        if len(self._policies) != len(values):
            raise ValueError(
                "suitability profile policies must be unique"
            )

    def resolve_policy(
        self,
        profile_id: str,
    ) -> SuitabilityProfilePolicy | None:
        return self._policies.get(profile_id)

    def assemble(
        self,
        *,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief | None = None,
    ) -> SuitabilityAssessment:
        self._validate_identity(session, decision_brief)
        policy = self.resolve_policy(session.task.profile_id)
        findings = tuple(
            finding
            for round_result in session.rounds
            for finding in round_result.structured_findings
            if finding.suitability_dimension is not None
        )
        recorded_evidence = frozenset(
            ref
            for round_result in session.rounds
            for ref in round_result.evidence_refs
        )
        blocks = {
            dimension: tuple(
                block
                for finding in findings
                if finding.suitability_dimension is dimension
                and (
                    block := _finding_block(
                        finding,
                        horizon=session.task.horizon,
                        recorded_evidence=recorded_evidence,
                    )
                )
                is not None
            )
            for dimension in SuitabilityDimension
        }
        thesis_hypothesis = _select_thesis(session)
        counter_hypothesis = _select_counterargument(session)
        thesis = _hypothesis_block(
            thesis_hypothesis,
            claim_id="suitability:thesis",
            session=session,
            recorded_evidence=recorded_evidence,
        )
        counterargument = _hypothesis_block(
            counter_hypothesis,
            claim_id="suitability:counterargument",
            session=session,
            recorded_evidence=recorded_evidence,
        )
        state = self._gaps(
            session=session,
            decision_brief=decision_brief,
            policy=policy,
            findings=findings,
            thesis=thesis_hypothesis,
            counterargument=counter_hypothesis,
            blocks=blocks,
            recorded_evidence=recorded_evidence,
        )
        action = _action(
            state=state,
            decision_brief=decision_brief,
            theses=tuple(
                item
                for item in session.hypotheses
                if item.role is HypothesisRole.THESIS
                and item.track is ExplorationTrack.SUITABILITY
            ),
            counterargument=counter_hypothesis,
        )
        confidence = _confidence(action, state)
        all_blocks = (
            *((thesis,) if thesis is not None else ()),
            *(
                item
                for values in blocks.values()
                for item in values
            ),
            *(
                (counterargument,)
                if counterargument is not None
                else ()
            ),
        )
        evidence_refs = _merge_texts(
            *(
                block.evidence_refs
                for block in all_blocks
            )
        )
        limitations = _merge_texts(
            *(
                block.limitations
                for block in all_blocks
            ),
            *(
                (final_report_gap_message(gap.code),)
                for gap in session.query_gaps
            ),
        ) or ("评估只基于截止日前已入账的证据。",)
        policy_id = (
            policy.policy_id
            if policy is not None
            else f"{session.task.profile_id}:suitability_unsupported"
        )
        payload = {
            "session_id": session.session_id,
            "workflow_run_id": session.workflow_run_id,
            "decision_brief_id": (
                decision_brief.brief_id
                if decision_brief is not None
                else session.decision_brief_id
            ),
            "asset_id": session.task.asset_id,
            "profile_id": session.task.profile_id,
            "policy_id": policy_id,
            "as_of_date": session.task.as_of_date,
            "time_horizon": session.task.horizon.value,
            "action": action.value,
            "confidence": confidence.value,
            "asset_role": (
                decision_brief.portfolio_role
                if decision_brief is not None
                else None
            ),
            "thesis": (
                thesis.to_dict()
                if thesis is not None
                else None
            ),
            "expected_return_sources": [
                item.to_dict()
                for item in blocks[
                    SuitabilityDimension.EXPECTED_RETURN_SOURCE
                ]
            ],
            "priced_expectations": [
                item.to_dict()
                for item in blocks[
                    SuitabilityDimension.PRICED_EXPECTATION
                ]
            ],
            "alternatives": [
                item.to_dict()
                for item in blocks[
                    SuitabilityDimension.ALTERNATIVE
                ]
            ],
            "opportunity_costs": [
                item.to_dict()
                for item in blocks[
                    SuitabilityDimension.OPPORTUNITY_COST
                ]
            ],
            "downside": [
                item.to_dict()
                for item in blocks[
                    SuitabilityDimension.DOWNSIDE
                ]
            ],
            "strongest_counterargument": (
                counterargument.to_dict()
                if counterargument is not None
                else None
            ),
            "evidence_refs": list(evidence_refs),
            "limitations": list(limitations),
            "assembly_gaps": list(state.gaps),
        }
        return SuitabilityAssessment(
            assessment_id=suitability_assessment_id(payload),
            session_id=session.session_id,
            workflow_run_id=session.workflow_run_id,
            decision_brief_id=(
                decision_brief.brief_id
                if decision_brief is not None
                else session.decision_brief_id
            ),
            asset_id=session.task.asset_id,
            profile_id=session.task.profile_id,
            policy_id=policy_id,
            as_of_date=session.task.as_of_date,
            time_horizon=session.task.horizon,
            action=action,
            confidence=confidence,
            asset_role=(
                decision_brief.portfolio_role
                if decision_brief is not None
                else None
            ),
            thesis=thesis,
            expected_return_sources=blocks[
                SuitabilityDimension.EXPECTED_RETURN_SOURCE
            ],
            priced_expectations=blocks[
                SuitabilityDimension.PRICED_EXPECTATION
            ],
            alternatives=blocks[
                SuitabilityDimension.ALTERNATIVE
            ],
            opportunity_costs=blocks[
                SuitabilityDimension.OPPORTUNITY_COST
            ],
            downside=blocks[SuitabilityDimension.DOWNSIDE],
            strongest_counterargument=counterargument,
            evidence_refs=evidence_refs,
            limitations=limitations,
            assembly_gaps=state.gaps,
        )

    def _validate_identity(
        self,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief | None,
    ) -> None:
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
        if decision_brief.horizon is not session.task.horizon:
            raise ValueError(
                "decision brief horizon does not match exploration"
            )
        if decision_brief.as_of_date > session.task.as_of_date:
            raise ValueError(
                "decision brief exceeds exploration as_of_date"
            )

    def _gaps(
        self,
        *,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief | None,
        policy: SuitabilityProfilePolicy | None,
        findings: tuple[ResearchFinding, ...],
        thesis: ResearchHypothesis | None,
        counterargument: ResearchHypothesis | None,
        blocks: dict[
            SuitabilityDimension,
            tuple[SuitabilityClaimBlock, ...],
        ],
        recorded_evidence: frozenset[str],
    ) -> _AssemblyState:
        core = []
        positive = []
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            core.append("exploration_not_ready_for_report")
        if DecisionLane.SUITABILITY not in session.decision_lanes:
            core.append("suitability_lane_not_requested")
        suitability_questions = tuple(
            item
            for item in session.questions
            if item.track is ExplorationTrack.SUITABILITY
            and item.blocking
        )
        if not suitability_questions or any(
            item.status is not QuestionStatus.ANSWERED
            for item in suitability_questions
        ):
            core.append("suitability_question_not_answered")
        if thesis is None:
            core.append("thesis_missing")
        else:
            if thesis.status is HypothesisStatus.ACTIVE:
                core.append("thesis_unresolved")
            if not _hypothesis_evidence(thesis):
                core.append("thesis_evidence_missing")
            elif not set(
                _hypothesis_evidence(thesis)
            ).issubset(recorded_evidence):
                core.append("thesis_evidence_not_recorded")
            if not thesis.falsification_conditions:
                core.append("thesis_falsification_missing")
            if thesis.unresolved_conflicts:
                core.append("thesis_conflicts_unresolved")
        if counterargument is None:
            core.append("strongest_counterargument_missing")
        else:
            if counterargument.status is HypothesisStatus.ACTIVE:
                core.append("strongest_counterargument_unresolved")
            if not _hypothesis_evidence(counterargument):
                core.append(
                    "strongest_counterargument_evidence_missing"
                )
            elif not set(
                _hypothesis_evidence(counterargument)
            ).issubset(recorded_evidence):
                core.append(
                    "strongest_counterargument_evidence_not_recorded"
                )
            if counterargument.unresolved_conflicts:
                core.append(
                    "strongest_counterargument_conflicts_unresolved"
                )
        for gap in session.unresolved_blocking_query_gaps():
            core.append(f"blocking_query_gap:{gap.code}")
        for dimension in (
            SuitabilityDimension.EXPECTED_RETURN_SOURCE,
            SuitabilityDimension.DOWNSIDE,
        ):
            if not _has_decision_evidence(blocks[dimension]):
                core.append(
                    f"suitability_dimension_missing:{dimension.value}"
                )
        for dimension in (
            SuitabilityDimension.PRICED_EXPECTATION,
            SuitabilityDimension.ALTERNATIVE,
            SuitabilityDimension.OPPORTUNITY_COST,
        ):
            if not _has_decision_evidence(blocks[dimension]):
                positive.append(
                    f"suitability_dimension_missing:{dimension.value}"
                )
        for finding in findings:
            if set(finding.evidence_refs).issubset(
                recorded_evidence
            ):
                continue
            gap = f"finding_evidence_not_recorded:{finding.finding_id}"
            if finding.suitability_dimension in (
                SuitabilityDimension.EXPECTED_RETURN_SOURCE,
                SuitabilityDimension.DOWNSIDE,
            ):
                core.append(gap)
            else:
                positive.append(gap)
        alternative_questions = tuple(
            item
            for item in session.questions
            if item.track is ExplorationTrack.ALTERNATIVE
            and item.blocking
        )
        if not alternative_questions or any(
            item.status is not QuestionStatus.ANSWERED
            for item in alternative_questions
        ):
            positive.append("alternative_question_not_answered")
        if any(
            finding.subject_asset_id == session.task.asset_id
            for finding in findings
            if finding.suitability_dimension
            in (
                SuitabilityDimension.ALTERNATIVE,
                SuitabilityDimension.OPPORTUNITY_COST,
            )
        ):
            positive.append("alternative_matches_research_asset")
        if policy is None:
            core.append(
                f"unsupported_suitability_profile:"
                f"{session.task.profile_id}"
            )
        else:
            for input_kind in policy.core_input_kinds:
                dimension = (
                    SuitabilityDimension.DOWNSIDE
                    if input_kind
                    is SuitabilityInputKind.DOWNSIDE_RISK
                    else (
                        SuitabilityDimension
                        .EXPECTED_RETURN_SOURCE
                    )
                )
                if not _has_profile_input(
                    findings,
                    input_kind,
                    dimension,
                    recorded_evidence,
                ):
                    core.append(
                        f"profile_input_missing:{input_kind.value}"
                    )
            for input_kind in policy.positive_input_kinds:
                dimensions = (
                    (
                        SuitabilityDimension.ALTERNATIVE,
                        SuitabilityDimension.OPPORTUNITY_COST,
                    )
                    if input_kind
                    is SuitabilityInputKind.COMPARABLE_ALTERNATIVE
                    else (
                        SuitabilityDimension.PRICED_EXPECTATION,
                    )
                )
                if any(
                    not _has_profile_input(
                        findings,
                        input_kind,
                        dimension,
                        recorded_evidence,
                    )
                    for dimension in dimensions
                ):
                    positive.append(
                        f"profile_input_missing:{input_kind.value}"
                    )
        alternative_keys = _comparison_keys(
            findings,
            SuitabilityDimension.ALTERNATIVE,
            recorded_evidence,
        )
        opportunity_cost_keys = _comparison_keys(
            findings,
            SuitabilityDimension.OPPORTUNITY_COST,
            recorded_evidence,
        )
        if not (
            alternative_keys
            and opportunity_cost_keys
            and alternative_keys & opportunity_cost_keys
        ):
            positive.append(
                "alternative_opportunity_cost_mismatch"
            )
        if decision_brief is None:
            core.append("decision_brief_unavailable")
            core.append("portfolio_role_missing")
        else:
            if DecisionLane.SUITABILITY not in (
                decision_brief.decision_lanes
            ):
                core.append(
                    "decision_brief_suitability_lane_missing"
                )
            if (
                decision_brief.portfolio_role is None
                or DecisionContextRequirement.PORTFOLIO_ROLE
                in decision_brief.missing_user_context
            ):
                core.append("portfolio_role_missing")
            if (
                decision_brief.decision_intent
                is DecisionIntent.UNDERSTANDING
            ):
                core.append("decision_intent_not_actionable")
            if (
                decision_brief.decision_intent
                is DecisionIntent.REVIEW_HOLDING
                and decision_brief.position_state
                is not PositionState.HELD
            ):
                core.append(
                    "holding_review_requires_held_position"
                )
        return _AssemblyState(
            core_gaps=_merge_texts(tuple(core)),
            positive_gaps=_merge_texts(tuple(positive)),
        )


def default_suitability_policies(
) -> tuple[SuitabilityProfilePolicy, ...]:
    common_core = (SuitabilityInputKind.DOWNSIDE_RISK,)
    common_positive = (
        SuitabilityInputKind.BENCHMARK_RELATIVE,
        SuitabilityInputKind.COMPARABLE_ALTERNATIVE,
    )
    return (
        SuitabilityProfilePolicy(
            profile_id=PROFILE_ORDINARY_COMPANY,
            policy_id="ordinary_company:suitability_v1",
            core_input_kinds=(
                SuitabilityInputKind.COMPANY_FUNDAMENTALS,
                *common_core,
            ),
            positive_input_kinds=common_positive,
            return_source_basis=(
                "earnings, cash flow, reinvestment, and valuation"
            ),
            priced_expectations_basis=(
                "company valuation relative to fundamentals and benchmark"
            ),
        ),
        SuitabilityProfilePolicy(
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            policy_id="financial_institution:suitability_v1",
            core_input_kinds=(
                (
                    SuitabilityInputKind
                    .FINANCIAL_INSTITUTION_QUALITY
                ),
                *common_core,
            ),
            positive_input_kinds=common_positive,
            return_source_basis=(
                "asset quality, capital, margins, and valuation"
            ),
            priced_expectations_basis=(
                "book-value expectations and credit-cycle benchmark"
            ),
        ),
        SuitabilityProfilePolicy(
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            policy_id="cyclical_resource:suitability_v1",
            core_input_kinds=(
                SuitabilityInputKind.CYCLE_ECONOMICS,
                *common_core,
            ),
            positive_input_kinds=common_positive,
            return_source_basis=(
                "cycle position, cost curve, and balance-sheet resilience"
            ),
            priced_expectations_basis=(
                "cycle-normalized valuation and commodity benchmark"
            ),
        ),
        SuitabilityProfilePolicy(
            profile_id=PROFILE_EXCHANGE_FUND,
            policy_id="exchange_fund:suitability_v1",
            core_input_kinds=(
                SuitabilityInputKind.ETF_EXPOSURE_EFFICIENCY,
                *common_core,
            ),
            positive_input_kinds=common_positive,
            return_source_basis=(
                "underlying exposure less fees and tracking drag"
            ),
            priced_expectations_basis=(
                "underlying index valuation, premium, and tracking"
            ),
        ),
        SuitabilityProfilePolicy(
            profile_id=PROFILE_OPEN_FUND,
            policy_id="open_fund:suitability_v1",
            core_input_kinds=(
                SuitabilityInputKind.OPEN_FUND_MANAGER_STYLE,
                *common_core,
            ),
            positive_input_kinds=common_positive,
            return_source_basis=(
                "manager process, portfolio exposures, and fee hurdle"
            ),
            priced_expectations_basis=(
                "NAV-relative benchmark evidence and fee-adjusted hurdle"
            ),
        ),
    )


def _select_thesis(
    session: ResearchExplorationSession,
) -> ResearchHypothesis | None:
    theses = tuple(
        item
        for item in session.hypotheses
        if item.role is HypothesisRole.THESIS
        and item.track is ExplorationTrack.SUITABILITY
    )
    ranking = {
        HypothesisStatus.SUPPORTED: 0,
        HypothesisStatus.WEAKENED: 1,
        HypothesisStatus.REJECTED: 2,
        HypothesisStatus.ACTIVE: 3,
    }
    return (
        min(
            theses,
            key=lambda item: ranking[item.status],
        )
        if theses
        else None
    )


def _select_counterargument(
    session: ResearchExplorationSession,
) -> ResearchHypothesis | None:
    counterarguments = tuple(
        item
        for item in session.hypotheses
        if item.role is HypothesisRole.COUNTERARGUMENT
        and item.track
        in (
            ExplorationTrack.COUNTERARGUMENT,
            ExplorationTrack.SUITABILITY,
        )
    )
    return next(
        (
            item
            for item in counterarguments
            if item.hypothesis_id == "strongest-counterargument"
        ),
        counterarguments[0] if counterarguments else None,
    )


def _finding_block(
    finding: ResearchFinding,
    *,
    horizon: ResearchHorizon,
    recorded_evidence: frozenset[str],
) -> SuitabilityClaimBlock | None:
    kind_mapping = {
        ResearchFindingKind.FACT: SuitabilityClaimKind.FACT,
        ResearchFindingKind.INFERENCE: (
            SuitabilityClaimKind.INFERENCE
        ),
        ResearchFindingKind.ASSUMPTION: (
            SuitabilityClaimKind.ASSUMPTION
        ),
        ResearchFindingKind.UNKNOWN: SuitabilityClaimKind.UNKNOWN,
    }
    evidence_refs = tuple(
        ref
        for ref in finding.evidence_refs
        if ref in recorded_evidence
    )
    all_recorded = len(evidence_refs) == len(
        finding.evidence_refs
    )
    if not evidence_refs or not all_recorded:
        return None
    return SuitabilityClaimBlock(
        claim_id=f"suitability:finding:{finding.finding_id}",
        text=finding.text,
        kind=kind_mapping[finding.kind],
        state=SuitabilityClaimState.RECORDED,
        evidence_refs=evidence_refs,
        limitations=finding.limitations,
        time_horizon=horizon,
        as_of_date=finding.as_of_date,
        subject_asset_id=finding.subject_asset_id,
        comparable_exposure=finding.comparable_exposure,
        source_finding_ids=(finding.finding_id,),
    )


def _hypothesis_block(
    hypothesis: ResearchHypothesis | None,
    *,
    claim_id: str,
    session: ResearchExplorationSession,
    recorded_evidence: frozenset[str],
) -> SuitabilityClaimBlock | None:
    if hypothesis is None:
        return None
    hypothesis_evidence = _hypothesis_evidence(hypothesis)
    evidence_refs = tuple(
        ref
        for ref in hypothesis_evidence
        if ref in recorded_evidence
    )
    all_recorded = len(evidence_refs) == len(
        hypothesis_evidence
    )
    if not evidence_refs or not all_recorded:
        return None
    limitations = _merge_texts(
        tuple(
            f"证伪条件：{item}"
            for item in hypothesis.falsification_conditions
        ),
        tuple(
            f"预期观察：{item}"
            for item in hypothesis.expected_observations
        ),
        tuple(
            f"未解决冲突：{item}"
            for item in hypothesis.unresolved_conflicts
        ),
    ) or ("假设未记录证伪条件或预期观察。",)
    return SuitabilityClaimBlock(
        claim_id=claim_id,
        text=hypothesis.statement,
        kind=SuitabilityClaimKind.INFERENCE,
        state=SuitabilityClaimState(
            hypothesis.status.value
        ),
        evidence_refs=evidence_refs,
        limitations=limitations,
        time_horizon=session.task.horizon,
        as_of_date=session.task.as_of_date,
    )


def _hypothesis_evidence(
    hypothesis: ResearchHypothesis,
) -> tuple[str, ...]:
    return _merge_texts(
        hypothesis.supporting_evidence_refs,
        hypothesis.counterevidence_refs,
    )


def _has_decision_evidence(
    blocks: tuple[SuitabilityClaimBlock, ...],
) -> bool:
    return any(
        item.kind
        in (
            SuitabilityClaimKind.FACT,
            SuitabilityClaimKind.INFERENCE,
        )
        and bool(item.evidence_refs)
        for item in blocks
    )


def _has_profile_input(
    findings: tuple[ResearchFinding, ...],
    input_kind: SuitabilityInputKind,
    dimension: SuitabilityDimension,
    recorded_evidence: frozenset[str],
) -> bool:
    return any(
        item.suitability_input_kind is input_kind
        and item.suitability_dimension is dimension
        and item.kind
        in (
            ResearchFindingKind.FACT,
            ResearchFindingKind.INFERENCE,
        )
        and bool(item.evidence_refs)
        and set(item.evidence_refs).issubset(
            recorded_evidence
        )
        for item in findings
    )


def _comparison_keys(
    findings: tuple[ResearchFinding, ...],
    dimension: SuitabilityDimension,
    recorded_evidence: frozenset[str],
) -> set[tuple[str, str]]:
    return {
        (
            item.subject_asset_id,
            item.comparable_exposure,
        )
        for item in findings
        if item.suitability_dimension is dimension
        and item.kind
        in (
            ResearchFindingKind.FACT,
            ResearchFindingKind.INFERENCE,
        )
        and set(item.evidence_refs).issubset(
            recorded_evidence
        )
        and item.subject_asset_id is not None
        and item.comparable_exposure is not None
    }


def _action(
    *,
    state: _AssemblyState,
    decision_brief: ResearchDecisionBrief | None,
    theses: tuple[ResearchHypothesis, ...],
    counterargument: ResearchHypothesis | None,
) -> SuitabilityAction:
    if state.core_gaps:
        return SuitabilityAction.INSUFFICIENT
    if (
        theses
        and all(
            item.status is HypothesisStatus.REJECTED
            for item in theses
        )
    ) or (
        counterargument is not None
        and counterargument.status is HypothesisStatus.SUPPORTED
    ):
        return SuitabilityAction.AVOID
    if (
        decision_brief is not None
        and decision_brief.decision_intent
        is DecisionIntent.REVIEW_HOLDING
    ):
        return SuitabilityAction.HOLD_REVIEW
    if any(
        item.status is HypothesisStatus.SUPPORTED
        for item in theses
    ):
        return (
            SuitabilityAction.CONSIDER
            if not state.positive_gaps
            else SuitabilityAction.WATCH
        )
    if any(
        item.status is HypothesisStatus.WEAKENED
        for item in theses
    ):
        return SuitabilityAction.WATCH
    return SuitabilityAction.INSUFFICIENT


def _confidence(
    action: SuitabilityAction,
    state: _AssemblyState,
) -> SuitabilityConfidence:
    if action is SuitabilityAction.INSUFFICIENT:
        return SuitabilityConfidence.INSUFFICIENT
    if state.gaps or action in (
        SuitabilityAction.WATCH,
        SuitabilityAction.HOLD_REVIEW,
    ):
        return SuitabilityConfidence.BOUNDED
    return SuitabilityConfidence.SUPPORTED


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
