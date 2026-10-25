"""Deterministic checks for representative new-conversation traces."""

from __future__ import annotations

from src.research_workflow import DecisionLane

from .conversation_models import (
    NEW_CONVERSATION_ACCEPTANCE_VERSION,
    ConversationAcceptanceFinding,
    ConversationAcceptanceStatus,
    ExpectedFailureSemantics,
    NewConversationAcceptanceResult,
    NewConversationObservation,
    NewConversationScenario,
)


class NewConversationAcceptanceService:
    """Evaluate public workflow observations without scoring prose."""

    def evaluate(
        self,
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
    ) -> NewConversationAcceptanceResult:
        findings: list[ConversationAcceptanceFinding] = []

        def fail(code: str, message: str) -> None:
            findings.append(
                ConversationAcceptanceFinding(code, message)
            )

        if observation.scenario_id != scenario.scenario_id:
            fail(
                "scenario_identity_mismatch",
                "Observation does not belong to the frozen scenario.",
            )
        if (
            observation.planning_status
            != scenario.expected_planning_status
        ):
            fail(
                "planning_status_mismatch",
                "Public planning status differs from the contract.",
            )

        self._check_questions(scenario, observation, fail)
        self._check_gap_contract(scenario, observation, fail)

        if scenario.expected_planning_status != "ready":
            self._check_non_ready(scenario, observation, fail)
            return _result(scenario.scenario_id, findings)

        if observation.resolved_asset_id is None:
            fail(
                "resolved_identity_missing",
                "Ready planning must expose one resolved asset.",
            )
        if observation.profile_id != scenario.expected_profile_id:
            fail(
                "profile_mismatch",
                "Resolved research profile differs from the contract.",
            )
        expected_question = scenario.expected_baseline_question
        if (
            expected_question is not None
            and observation.baseline_question
            != expected_question.value
        ):
            fail(
                "baseline_question_mismatch",
                "Baseline question type differs from public routing.",
            )
        if not observation.identity_chain_consistent:
            fail(
                "identity_chain_mismatch",
                "Planning, workflow, exploration, and final identities "
                "are not consistent.",
            )
        if observation.blocked_baseline_bypassed:
            fail(
                "blocked_baseline_bypassed",
                "A blocked baseline was bypassed by manual assembly.",
            )

        self._check_baseline(scenario, observation, fail)
        self._check_query_outcomes(scenario, observation, fail)

        if (
            scenario.expected_failure
            is ExpectedFailureSemantics.BASELINE_BLOCKED
        ):
            self._check_blocked_baseline(observation, fail)
            return _result(scenario.scenario_id, findings)
        if scenario.expected_failure in (
            ExpectedFailureSemantics.REMOTE_FAILURE,
            ExpectedFailureSemantics.BUSINESS_EMPTY,
        ):
            self._check_typed_query_failure(observation, fail)
            return _result(scenario.scenario_id, findings)

        self._check_exploration(scenario, observation, fail)
        self._check_decision_outputs(scenario, observation, fail)
        self._check_final_report(scenario, observation, fail)
        return _result(scenario.scenario_id, findings)

    @staticmethod
    def _check_questions(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        asked = set(observation.asked_user_questions)
        required = set(scenario.required_user_questions)
        allowed = set(scenario.allowed_user_questions)
        missing = required - asked
        unexpected = asked - allowed
        if missing:
            fail(
                "required_user_question_missing",
                f"Required minimal questions were not asked: "
                f"{sorted(missing)}",
            )
        if unexpected:
            fail(
                "unexpected_user_question",
                f"Questions outside the frozen minimum were asked: "
                f"{sorted(unexpected)}",
            )
        if (
            len(observation.asked_user_questions)
            > scenario.max_user_questions
        ):
            fail(
                "too_many_user_questions",
                "The run asked more questions than the frozen maximum.",
            )

    @staticmethod
    def _check_gap_contract(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        unexpected = tuple(
            code
            for code in observation.gap_codes
            if not any(
                code.startswith(prefix)
                for prefix in scenario.allowed_gap_prefixes
            )
        )
        if unexpected:
            fail(
                "unexpected_gap",
                f"Observed gaps are outside the scenario contract: "
                f"{list(unexpected)}",
            )

    @staticmethod
    def _check_non_ready(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if (
            scenario.expected_planning_status
            == "needs_clarification"
            and observation.candidate_count < 2
        ):
            fail(
                "clarification_candidates_missing",
                "Identity clarification requires multiple candidates.",
            )
        if any(
            (
                observation.baseline_status is not None,
                observation.workflow_created,
                observation.handoff_created,
                observation.exploration_status is not None,
                observation.final_report_published,
            )
        ):
            fail(
                "execution_started_before_identity",
                "Downstream execution started before identity was unique.",
            )

    @staticmethod
    def _check_baseline(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if not scenario.baseline_required:
            return
        if observation.baseline_status is None:
            fail(
                "baseline_missing",
                "Scenario requires a public baseline execution.",
            )
        if not observation.workflow_created:
            fail(
                "workflow_missing",
                "Baseline execution did not create a workflow record.",
            )
        if (
            scenario.expected_failure
            is not ExpectedFailureSemantics.BASELINE_BLOCKED
            and not observation.handoff_created
        ):
            fail(
                "baseline_handoff_missing",
                "Baseline execution did not create a handoff.",
            )
        if (
            scenario.expected_failure
            is ExpectedFailureSemantics.BASELINE_BLOCKED
            and observation.handoff_created
        ):
            fail(
                "blocked_baseline_has_handoff",
                "Blocked baseline must not create an exploration handoff.",
            )
        if (
            observation.baseline_status in {"complete", "partial"}
            and observation.baseline_report_path is None
        ):
            fail(
                "baseline_markdown_missing",
                "Publishable baseline did not produce Markdown.",
            )

    @staticmethod
    def _check_query_outcomes(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        missing = set(scenario.expected_query_outcomes) - set(
            observation.query_outcomes
        )
        if missing:
            fail(
                "query_outcome_missing",
                f"Typed query outcomes are missing: {sorted(missing)}",
            )

    @staticmethod
    def _check_blocked_baseline(
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if observation.baseline_status != "blocked":
            fail(
                "baseline_not_blocked",
                "Expected blocked baseline was not preserved.",
            )
        if observation.baseline_report_path is not None:
            fail(
                "blocked_baseline_published",
                "Blocked baseline must not publish a research package.",
            )
        if (
            observation.exploration_status is not None
            or observation.final_report_published
        ):
            fail(
                "blocked_baseline_downstream_started",
                "Blocked baseline must stop exploration and final report.",
            )

    @staticmethod
    def _check_typed_query_failure(
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if observation.final_report_published:
            fail(
                "query_failure_published_as_final",
                "Typed query failure must not be hidden by a final report.",
            )
        if observation.position_has_range:
            fail(
                "query_failure_has_position_range",
                "Failed evidence query must not produce a pseudo-position.",
            )

    @staticmethod
    def _check_exploration(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if not scenario.exploration_required:
            return
        if observation.exploration_status != "ready_for_report":
            fail(
                "exploration_not_ready",
                "Required exploration is not ready for final assembly.",
            )
        if observation.supporting_round_count < 1:
            fail(
                "supporting_evidence_round_missing",
                "No exploration round tested supporting evidence.",
            )
        if observation.counterevidence_round_count < 1:
            fail(
                "counterevidence_round_missing",
                "No exploration round tested counterevidence.",
            )

    @staticmethod
    def _check_decision_outputs(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if scenario.exploration_required and (
            observation.suitability_action is None
        ):
            fail(
                "suitability_missing",
                "Decision exploration has no suitability outcome.",
            )
        if scenario.timing_required:
            if observation.entry_status is None:
                fail(
                    "entry_plan_missing",
                    "Timing lane has no entry outcome.",
                )
            elif observation.entry_status == "not_applicable":
                fail(
                    "timing_marked_not_applicable",
                    "Requested timing lane was marked not applicable.",
                )
        if scenario.position_required:
            if observation.position_status is None:
                fail(
                    "position_plan_missing",
                    "Position lane has no formal outcome.",
                )
            elif observation.position_status != "available":
                fail(
                    "position_plan_unavailable",
                    "Frozen context is complete but position is unavailable.",
                )
            if not observation.position_has_range:
                fail(
                    "position_range_missing",
                    "Available position plan has no calculated range.",
                )
        elif (
            DecisionLane.POSITION_SIZING
            not in scenario.decision_lanes
            and observation.position_has_range
        ):
            fail(
                "unsolicited_position_range",
                "Run invented a position range without a position lane.",
            )

    @staticmethod
    def _check_final_report(
        scenario: NewConversationScenario,
        observation: NewConversationObservation,
        fail,
    ) -> None:
        if not scenario.final_report_required:
            if observation.final_report_published:
                fail(
                    "unexpected_final_report",
                    "Scenario published a final report before its "
                    "decision objective was complete.",
                )
            return
        if not observation.final_review_passed:
            fail(
                "final_review_failed",
                "Final report did not pass the programmatic reviewer.",
            )
        if (
            not observation.final_report_published
            or observation.final_report_path is None
        ):
            fail(
                "final_markdown_missing",
                "Final report was not published as Markdown.",
            )
        if observation.final_decision_lane_count != 3:
            fail(
                "final_decision_lanes_incomplete",
                "Final report must answer all three decision lanes.",
            )
        if (
            observation.baseline_report_path is not None
            and observation.final_report_path
            == observation.baseline_report_path
        ):
            fail(
                "baseline_masquerades_as_final",
                "Baseline package and final report share one artifact.",
            )


def _result(
    scenario_id: str,
    findings: list[ConversationAcceptanceFinding],
) -> NewConversationAcceptanceResult:
    return NewConversationAcceptanceResult(
        acceptance_version=NEW_CONVERSATION_ACCEPTANCE_VERSION,
        scenario_id=scenario_id,
        status=(
            ConversationAcceptanceStatus.FAILED
            if findings
            else ConversationAcceptanceStatus.PASSED
        ),
        findings=tuple(findings),
    )
