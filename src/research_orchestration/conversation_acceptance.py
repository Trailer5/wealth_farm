"""Adapter from public orchestration outputs to Slice 12 acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.research_acceptance import (
    NewConversationAcceptanceResult,
    NewConversationAcceptanceService,
    NewConversationObservation,
    NewConversationScenario,
)

from .entry_models import EntryPlan
from .exploration_models import (
    ExplorationTrack,
    HypothesisImpact,
    ResearchExplorationSession,
)
from .final_decision_pipeline import FinalDecisionPublishResult
from .models import ResearchRunResult
from .position_models import PositionPlan
from .query_models import ResearchQueryResult
from .request_planner import (
    ResearchPlanningRequest,
    ResearchPlanningResult,
    ResearchRequestPlanner,
)
from .suitability_models import SuitabilityAssessment


@dataclass(frozen=True)
class NewConversationRunArtifacts:
    baseline: ResearchRunResult | None = None
    session: ResearchExplorationSession | None = None
    query_results: tuple[ResearchQueryResult, ...] = ()
    suitability: SuitabilityAssessment | None = None
    entry_plan: EntryPlan | None = None
    position_plan: PositionPlan | None = None
    final_result: FinalDecisionPublishResult | None = None
    asked_user_questions: tuple[str, ...] = ()
    blocked_baseline_bypassed: bool = False

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.asked_user_questions
        ):
            raise ValueError(
                "asked_user_questions must contain non-blank strings"
            )
        if len(self.asked_user_questions) != len(
            set(self.asked_user_questions)
        ):
            raise ValueError("asked_user_questions must be unique")


class NewConversationAcceptanceRunner:
    """Plan a frozen prompt and inspect outputs from public services."""

    def __init__(
        self,
        *,
        planner: ResearchRequestPlanner,
        acceptance: NewConversationAcceptanceService | None = None,
    ) -> None:
        self.planner = planner
        self.acceptance = (
            acceptance or NewConversationAcceptanceService()
        )

    def plan(
        self,
        scenario: NewConversationScenario,
    ) -> ResearchPlanningResult:
        return self.planner.plan(
            ResearchPlanningRequest(
                as_of_date=scenario.as_of_date,
                symbol=scenario.symbol,
                asset_type=scenario.asset_type_hint,
                market=scenario.market_hint,
                exchange=scenario.exchange_hint,
                decision_intent=scenario.decision_intent,
                decision_lanes=scenario.decision_lanes,
                horizon=scenario.horizon,
            )
        )

    def observe(
        self,
        scenario: NewConversationScenario,
        artifacts: NewConversationRunArtifacts,
    ) -> NewConversationObservation:
        planning = self.plan(scenario)
        baseline = artifacts.baseline
        session = artifacts.session
        final_result = artifacts.final_result
        position = artifacts.position_plan
        support_rounds, counter_rounds = _round_counts(session)
        final_path = (
            final_result.report_path
            if final_result is not None
            else None
        )
        return NewConversationObservation(
            scenario_id=scenario.scenario_id,
            planning_status=planning.status.value,
            candidate_count=len(planning.candidates),
            resolved_asset_id=(
                planning.identity.asset_id
                if planning.identity is not None
                else None
            ),
            profile_id=planning.profile_id,
            baseline_question=(
                planning.task.question_type.value
                if planning.task is not None
                else None
            ),
            baseline_status=(
                baseline.status.value
                if baseline is not None
                else None
            ),
            workflow_created=(
                baseline is not None
                and baseline.workflow_run is not None
            ),
            handoff_created=(
                baseline is not None and baseline.handoff is not None
            ),
            baseline_report_path=_existing_path(
                baseline.report_path
                if baseline is not None
                else None
            ),
            exploration_status=(
                session.status.value
                if session is not None
                else None
            ),
            supporting_round_count=support_rounds,
            counterevidence_round_count=counter_rounds,
            query_outcomes=tuple(
                item.outcome.value
                for item in artifacts.query_results
            ),
            asked_user_questions=artifacts.asked_user_questions,
            gap_codes=_gap_codes(artifacts),
            suitability_action=(
                artifacts.suitability.action.value
                if artifacts.suitability is not None
                else None
            ),
            entry_status=(
                artifacts.entry_plan.status.value
                if artifacts.entry_plan is not None
                else None
            ),
            position_status=(
                position.status.value if position is not None else None
            ),
            position_has_range=(
                position is not None
                and position.target_weight_min is not None
                and position.target_weight_max is not None
            ),
            final_review_passed=(
                final_result is not None
                and final_result.review.passed
            ),
            final_report_published=(
                final_result is not None
                and final_result.published is not None
                and _existing_path(final_path) is not None
            ),
            final_report_path=_existing_path(final_path),
            final_decision_lane_count=(
                len(final_result.report.decision_sections)
                if final_result is not None
                else 0
            ),
            identity_chain_consistent=_identity_chain_consistent(
                planning,
                artifacts,
            ),
            blocked_baseline_bypassed=(
                artifacts.blocked_baseline_bypassed
            ),
        )

    def evaluate(
        self,
        scenario: NewConversationScenario,
        artifacts: NewConversationRunArtifacts,
    ) -> NewConversationAcceptanceResult:
        return self.acceptance.evaluate(
            scenario,
            self.observe(scenario, artifacts),
        )


def _round_counts(
    session: ResearchExplorationSession | None,
) -> tuple[int, int]:
    if session is None:
        return 0, 0
    supporting = 0
    counter = 0
    for round_result in session.rounds:
        impacts = (
            *round_result.hypothesis_impacts,
            *(
                impact
                for finding in round_result.structured_findings
                for impact in finding.hypothesis_impacts
            ),
        )
        if any(
            item.impact is HypothesisImpact.SUPPORTS
            for item in impacts
        ):
            supporting += 1
        if any(
            item.impact
            in (HypothesisImpact.WEAKENS, HypothesisImpact.REJECTS)
            for item in impacts
        ) or any(
            finding.track is ExplorationTrack.COUNTERARGUMENT
            for finding in round_result.structured_findings
        ):
            counter += 1
    return supporting, counter


def _gap_codes(
    artifacts: NewConversationRunArtifacts,
) -> tuple[str, ...]:
    codes: list[str] = []
    baseline = artifacts.baseline
    if baseline is not None:
        codes.extend(
            str(item.code) for item in baseline.execution.gaps
        )
        if baseline.report is not None:
            codes.extend(
                str(item.code) for item in baseline.report.gaps
            )
        if baseline.workflow_run is not None:
            codes.extend(baseline.workflow_run.blocking_gap_codes)
        if baseline.handoff is not None:
            for gaps in (
                baseline.handoff.partial_gaps,
                baseline.handoff.blocking_gaps,
                baseline.handoff.capability_gaps,
            ):
                codes.extend(str(item.code) for item in gaps)
    if artifacts.session is not None:
        codes.extend(
            str(item.code)
            for item in artifacts.session.query_gaps
        )
    for output in (
        artifacts.suitability,
        artifacts.entry_plan,
        artifacts.position_plan,
    ):
        if output is not None:
            codes.extend(str(item) for item in output.assembly_gaps)
    return tuple(dict.fromkeys(codes))


def _identity_chain_consistent(
    planning: ResearchPlanningResult,
    artifacts: NewConversationRunArtifacts,
) -> bool:
    if planning.task is None:
        return all(
            item is None
            for item in (
                artifacts.baseline,
                artifacts.session,
                artifacts.suitability,
                artifacts.entry_plan,
                artifacts.position_plan,
                artifacts.final_result,
            )
        )
    task = planning.task
    asset_ids = [task.asset_id]
    profile_ids = [task.profile_id]
    task_ids = [task.task_id]
    baseline = artifacts.baseline
    if baseline is not None:
        asset_ids.append(baseline.task.asset_id)
        profile_ids.append(baseline.task.profile_id)
        task_ids.append(baseline.task.task_id)
        if baseline.workflow_run is not None:
            asset_ids.append(
                baseline.workflow_run.task.asset_id
            )
            profile_ids.append(
                baseline.workflow_run.task.profile_id
            )
            task_ids.append(baseline.workflow_run.task.task_id)
        if baseline.handoff is not None:
            asset_ids.append(baseline.handoff.task.asset_id)
            profile_ids.append(baseline.handoff.task.profile_id)
            task_ids.append(baseline.handoff.task.task_id)
    if artifacts.session is not None:
        asset_ids.append(artifacts.session.task.asset_id)
        profile_ids.append(artifacts.session.task.profile_id)
        task_ids.append(artifacts.session.task.task_id)
    for output in (
        artifacts.suitability,
        artifacts.entry_plan,
        artifacts.position_plan,
    ):
        if output is not None:
            asset_ids.append(output.asset_id)
            profile_ids.append(output.profile_id)
    if artifacts.final_result is not None:
        asset_ids.append(artifacts.final_result.report.asset_id)
        profile_ids.append(artifacts.final_result.report.profile_id)
    return (
        len(set(asset_ids)) == 1
        and len(set(profile_ids)) == 1
        and len(set(task_ids)) == 1
    )


def _existing_path(value: Path | str | None) -> str | None:
    if value is None:
        return None
    path = Path(value)
    return str(path) if path.is_file() else None
