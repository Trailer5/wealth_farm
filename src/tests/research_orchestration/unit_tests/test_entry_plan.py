import unittest
from dataclasses import replace

from src.research_orchestration import (
    EntryConfidence,
    EntryPlan,
    EntryPlanService,
    EntryPlanStatus,
    EvidenceTier,
    ExplorationQuestion,
    ExplorationTrack,
    HypothesisImpact,
    HypothesisImpactRecord,
    HypothesisRole,
    HypothesisStatus,
    QuestionStatus,
    ResearchFinding,
    ResearchFindingKind,
    SuitabilityAction,
    SuitabilityAssessment,
    SuitabilityAssessmentService,
    SuitabilityInputKind,
    TimingConditionState,
    TimingDimension,
    TimingInputKind,
)
from src.research_orchestration.entry_models import entry_plan_id
from src.research_orchestration.suitability_models import (
    suitability_assessment_id,
)
from src.research_contracts import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionLane,
    ResearchHorizon,
)

from src.tests.research_orchestration.unit_tests import (
    test_suitability_assessment as suitability_fixtures,
)

suitability_brief = suitability_fixtures._brief
suitability_session = suitability_fixtures._session


class EntryPlanTest(unittest.TestCase):
    def setUp(self):
        self.service = EntryPlanService()
        self.suitability_service = SuitabilityAssessmentService()

    def test_complete_observable_plan_can_act(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.ACT)
        self.assertIs(
            plan.confidence,
            EntryConfidence.SUPPORTED,
        )
        self.assertEqual(
            plan.signal_valid_until,
            "2026-08-28",
        )
        self.assertEqual(
            plan.next_tradable_at,
            "2026-08-24T09:30:00+08:00",
        )
        self.assertIs(
            plan.suitability_horizon,
            ResearchHorizon.SHORT,
        )
        self.assertFalse(plan.assembly_gaps)
        self.assertTrue(
            all(
                block.evidence_refs
                and block.limitations
                and block.observability_key
                and block.valid_until
                for block in plan.condition_blocks
            )
        )
        payload = plan.to_dict()
        self.assertNotIn("score", payload)
        self.assertNotIn("target_price", payload)
        self.assertNotIn("predicted_bottom_date", payload)
        self.assertEqual(EntryPlan.from_dict(payload), plan)

    def test_active_tranche_rule_produces_stage(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.MET,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.STAGE)
        self.assertTrue(plan.tranche_rules)

    def test_active_avoid_condition_produces_wait(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        session = _replace_timing_state(
            session,
            TimingDimension.AVOID_CONDITION,
            TimingConditionState.MET,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.WAIT)

    def test_avoid_suitability_makes_timing_not_applicable(self):
        session, brief, _ = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        session = replace(
            session,
            hypotheses=tuple(
                replace(item, status=HypothesisStatus.REJECTED)
                if item.role is HypothesisRole.THESIS
                else item
                for item in session.hypotheses
            ),
        )
        suitability = self.suitability_service.assemble(
            session=session,
            decision_brief=brief,
        )
        self.assertIs(
            suitability.action,
            SuitabilityAction.AVOID,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.NOT_APPLICABLE,
        )
        self.assertIn(
            "suitability_action_avoid",
            plan.assembly_gaps,
        )

    def test_unrecorded_timing_evidence_is_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            evidence_refs=(
                                "evidence:timing-not-recorded",
                            ),
                        )
                        if item.timing_dimension
                        is TimingDimension.ENTRY_CONDITION
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            (
                "finding_evidence_not_recorded:"
                "timing:entry-condition"
            ),
            plan.assembly_gaps,
        )
        self.assertNotIn(
            "evidence:timing-not-recorded",
            plan.evidence_refs,
        )

    def test_cross_horizon_conflict_is_preserved_and_waits(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_CYCLICAL_RESOURCE,
            tranche_state=TimingConditionState.MET,
            include_horizon_conflict=True,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.WAIT)
        self.assertTrue(plan.horizon_conflicts)
        self.assertEqual(
            {
                item.signal_horizon
                for item in (
                    *plan.current_regime,
                    *plan.entry_conditions,
                )
            },
            {
                ResearchHorizon.SHORT,
                ResearchHorizon.LONG,
            },
        )

    def test_explicit_conflict_never_becomes_actionable_stage(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_CYCLICAL_RESOURCE,
            tranche_state=TimingConditionState.MET,
            include_horizon_conflict=True,
        )
        session = _replace_timing_state(
            session,
            TimingDimension.CURRENT_REGIME,
            TimingConditionState.NEUTRAL,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.WAIT)
        self.assertTrue(plan.horizon_conflicts)

    def test_unrecorded_cross_horizon_conflict_is_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_CYCLICAL_RESOURCE,
            tranche_state=TimingConditionState.MET,
            include_horizon_conflict=True,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        item
                        for item in round_result.structured_findings
                        if item.timing_dimension
                        is not TimingDimension.HORIZON_CONFLICT
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "cross_horizon_conflict_unrecorded",
            plan.assembly_gaps,
        )

    def test_open_fund_timing_capability_is_explicitly_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_OPEN_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "profile_timing_execution_unsupported:open_fund",
            plan.assembly_gaps,
        )

    def test_profile_specific_timing_input_cannot_be_substituted(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            timing_input_kind=(
                                TimingInputKind.CYCLE_POSITION
                            ),
                        )
                        if item.timing_dimension
                        is TimingDimension.CURRENT_REGIME
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "profile_input_missing:valuation_regime",
            plan.assembly_gaps,
        )

    def test_long_only_execution_conditions_are_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            signal_horizon=ResearchHorizon.LONG,
                        )
                        if item.timing_dimension is not None
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "execution_horizon_input_missing:entry_condition",
            plan.assembly_gaps,
        )
        self.assertIn(
            "profile_input_missing:trend_state",
            plan.assembly_gaps,
        )

    def test_next_trade_after_other_signal_expiry_is_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            timing_valid_until="2026-08-30",
                            next_tradable_at=(
                                "2026-08-29T09:30:00+08:00"
                            ),
                        )
                        if item.timing_dimension
                        is TimingDimension.NEXT_TRADABLE_TIME
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "next_tradable_after_signal_expiry",
            plan.assembly_gaps,
        )
        self.assertIsNone(plan.next_tradable_at)
        self.assertIsNone(plan.next_tradable_condition)

    def test_next_trade_before_all_conditions_valid_is_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            timing_valid_from="2026-08-25",
                        )
                        if item.timing_dimension
                        is TimingDimension.ENTRY_CONDITION
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "next_tradable_before_signal_validity",
            plan.assembly_gaps,
        )
        self.assertIsNone(plan.next_tradable_at)

    def test_multiple_next_tradable_candidates_select_earliest_valid(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        later_candidate = _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.TRADING_CALENDAR,
            TimingConditionState.MET,
            next_tradable_at="2026-08-25T09:30:00+08:00",
            suffix="next-tradable-later",
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=(
                        *round_result.structured_findings,
                        later_candidate,
                    ),
                    findings=(
                        *round_result.findings,
                        later_candidate.text,
                    ),
                    evidence_refs=(
                        *round_result.evidence_refs,
                        *later_candidate.evidence_refs,
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.ACT)
        self.assertEqual(
            plan.next_tradable_at,
            "2026-08-24T09:30:00+08:00",
        )

    def test_unused_unknown_calendar_candidate_does_not_block(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        unknown_candidate = _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.TRADING_CALENDAR,
            TimingConditionState.UNKNOWN,
            next_tradable_at="2026-08-25T09:30:00+08:00",
            suffix="next-tradable-unknown",
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=(
                        *round_result.structured_findings,
                        unknown_candidate,
                    ),
                    findings=(
                        *round_result.findings,
                        unknown_candidate.text,
                    ),
                    evidence_refs=(
                        *round_result.evidence_refs,
                        *unknown_candidate.evidence_refs,
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.ACT)
        self.assertNotIn(
            "timing_state_unknown:"
            "entry:finding:timing:next-tradable-unknown",
            plan.assembly_gaps,
        )

    def test_wrong_horizon_calendar_candidate_is_not_selected(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        long_candidate = _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.TRADING_CALENDAR,
            TimingConditionState.MET,
            signal_horizon=ResearchHorizon.LONG,
            next_tradable_at="2026-08-24T08:30:00+08:00",
            suffix="next-tradable-long",
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=(
                        *round_result.structured_findings,
                        long_candidate,
                    ),
                    findings=(
                        *round_result.findings,
                        long_candidate.text,
                    ),
                    evidence_refs=(
                        *round_result.evidence_refs,
                        *long_candidate.evidence_refs,
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.ACT)
        self.assertEqual(
            plan.next_tradable_at,
            "2026-08-24T09:30:00+08:00",
        )
        self.assertIsNotNone(plan.next_tradable_condition)
        assert plan.next_tradable_condition is not None
        self.assertIs(
            plan.next_tradable_condition.signal_horizon,
            ResearchHorizon.SHORT,
        )

    def test_wrong_profile_calendar_candidate_is_not_selected(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        fund_candidate = _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.FUND_DEALING_RULE,
            TimingConditionState.MET,
            next_tradable_at="2026-08-24T08:30:00+08:00",
            suffix="next-tradable-fund-rule",
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=(
                        *round_result.structured_findings,
                        fund_candidate,
                    ),
                    findings=(
                        *round_result.findings,
                        fund_candidate.text,
                    ),
                    evidence_refs=(
                        *round_result.evidence_refs,
                        *fund_candidate.evidence_refs,
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(plan.status, EntryPlanStatus.ACT)
        self.assertEqual(
            plan.next_tradable_at,
            "2026-08-24T09:30:00+08:00",
        )

    def test_non_overlapping_condition_windows_are_insufficient(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            timing_valid_from="2026-08-27",
                        )
                        if item.timing_dimension
                        is TimingDimension.ENTRY_CONDITION
                        else (
                            replace(
                                item,
                                timing_valid_until="2026-08-26",
                            )
                            if item.timing_dimension
                            is TimingDimension.WAIT_CONDITION
                            else item
                        )
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "timing_condition_windows_do_not_overlap",
            plan.assembly_gaps,
        )
        self.assertIsNone(plan.signal_valid_from)
        self.assertIsNone(plan.signal_valid_until)

    def test_long_only_active_catalyst_cannot_support_short_action(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        round_result = session.rounds[0]
        session = replace(
            session,
            rounds=(
                replace(
                    round_result,
                    structured_findings=tuple(
                        replace(
                            item,
                            signal_horizon=ResearchHorizon.LONG,
                            timing_state=TimingConditionState.MET,
                        )
                        if item.timing_dimension
                        is TimingDimension.CATALYST_WINDOW
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.INSUFFICIENT,
        )
        self.assertIn(
            "execution_horizon_input_missing:catalyst_window",
            plan.assembly_gaps,
        )
        self.assertIn(
            "profile_input_missing:catalyst_timeline",
            plan.assembly_gaps,
        )

    def test_timing_lane_not_requested_is_not_applicable(self):
        session = suitability_session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )
        brief = suitability_brief(session)
        suitability = self.suitability_service.assemble(
            session=session,
            decision_brief=brief,
        )

        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )

        self.assertIs(
            plan.status,
            EntryPlanStatus.NOT_APPLICABLE,
        )
        self.assertIn(
            "timing_lane_not_requested",
            plan.assembly_gaps,
        )

    def test_deserialization_validates_confidence_and_content_id(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )
        payload = plan.to_dict()
        payload["confidence"] = EntryConfidence.INSUFFICIENT.value
        with self.assertRaisesRegex(
            ValueError,
            "confidence does not match",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["status"] = EntryPlanStatus.WAIT.value
        payload["confidence"] = EntryConfidence.BOUNDED.value
        with self.assertRaisesRegex(
            ValueError,
            "plan_id does not match",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["target_price"] = "not-allowed"
        with self.assertRaisesRegex(
            ValueError,
            "Unknown EntryPlan fields",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["entry_conditions"][0]["position_size"] = "10%"
        with self.assertRaisesRegex(
            ValueError,
            "Unknown EntryConditionBlock fields",
        ):
            EntryPlan.from_dict(payload)

    def test_deserialization_revalidates_status_and_sections(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        plan = self.service.assemble(
            session=session,
            suitability=suitability,
            decision_brief=brief,
        )
        payload = plan.to_dict()
        payload["avoid_conditions"][0][
            "state"
        ] = TimingConditionState.MET.value
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "active wait or conflict",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        (
            payload["entry_conditions"],
            payload["wait_conditions"],
        ) = (
            payload["wait_conditions"],
            payload["entry_conditions"],
        )
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "wrong section",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["execution_constraints"][0][
            "state"
        ] = TimingConditionState.UNKNOWN.value
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "unknown condition state",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["assembly_gaps"] = ["unexpected-gap"]
        payload["confidence"] = EntryConfidence.BOUNDED.value
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "cannot retain assembly gaps",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["signal_valid_until"] = "2026-08-30"
        payload["next_tradable_at"] = (
            "2026-08-29T09:30:00+08:00"
        )
        payload["next_tradable_condition"][
            "next_tradable_at"
        ] = "2026-08-29T09:30:00+08:00"
        payload["next_tradable_condition"][
            "valid_until"
        ] = "2026-08-30"
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "does not match condition expiry",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["next_tradable_condition"][
            "signal_horizon"
        ] = ResearchHorizon.LONG.value
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "horizon does not match execution",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["next_tradable_condition"][
            "input_kind"
        ] = TimingInputKind.FUND_DEALING_RULE.value
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "input is not allowed for profile",
        ):
            EntryPlan.from_dict(payload)

        payload = plan.to_dict()
        payload["profile_id"] = PROFILE_OPEN_FUND
        payload["policy_id"] = "open_fund:entry_v1"
        payload["plan_id"] = entry_plan_id(payload)
        with self.assertRaisesRegex(
            ValueError,
            "does not support actionable plans",
        ):
            EntryPlan.from_dict(payload)

    def test_missing_suitability_workflow_identity_is_rejected(self):
        session, brief, suitability = _entry_fixture(
            self.suitability_service,
            PROFILE_EXCHANGE_FUND,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        payload = suitability.to_dict()
        payload["workflow_run_id"] = None
        payload["assessment_id"] = suitability_assessment_id(
            payload
        )
        suitability_without_workflow = (
            SuitabilityAssessment.from_dict(payload)
        )

        with self.assertRaisesRegex(
            ValueError,
            "workflow does not match",
        ):
            self.service.assemble(
                session=session,
                suitability=suitability_without_workflow,
                decision_brief=brief,
            )

    def test_next_tradable_time_requires_timezone(self):
        with self.assertRaisesRegex(
            ValueError,
            "timestamp requires timezone",
        ):
            _timing_finding(
                TimingDimension.NEXT_TRADABLE_TIME,
                TimingInputKind.TRADING_CALENDAR,
                TimingConditionState.MET,
                next_tradable_at="2026-08-24T09:30:00",
            )

    def test_next_tradable_time_cannot_precede_validity(self):
        finding = _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.TRADING_CALENDAR,
            TimingConditionState.MET,
            next_tradable_at="2026-08-24T09:30:00+08:00",
        )
        with self.assertRaisesRegex(
            ValueError,
            "timestamp precedes validity",
        ):
            replace(
                finding,
                timing_valid_from="2026-08-25",
            )

    def test_duplicate_finding_ids_across_rounds_are_rejected(self):
        session, _, _ = _entry_fixture(
            self.suitability_service,
            PROFILE_ORDINARY_COMPANY,
            tranche_state=TimingConditionState.NOT_APPLICABLE,
        )
        with self.assertRaisesRegex(
            ValueError,
            "session finding_id values must be unique",
        ):
            replace(
                session,
                rounds=(
                    session.rounds[0],
                    replace(
                        session.rounds[0],
                        round_id="round:duplicate",
                    ),
                ),
            )


def _entry_fixture(
    suitability_service,
    profile_id,
    *,
    tranche_state,
    include_horizon_conflict=False,
):
    suitability_input = {
        PROFILE_ORDINARY_COMPANY: (
            SuitabilityInputKind.COMPANY_FUNDAMENTALS
        ),
        PROFILE_CYCLICAL_RESOURCE: (
            SuitabilityInputKind.CYCLE_ECONOMICS
        ),
        PROFILE_EXCHANGE_FUND: (
            SuitabilityInputKind.ETF_EXPOSURE_EFFICIENCY
        ),
        PROFILE_OPEN_FUND: (
            SuitabilityInputKind.OPEN_FUND_MANAGER_STYLE
        ),
    }[profile_id]
    profile_timing_input = (
        TimingInputKind.CYCLE_POSITION
        if profile_id == PROFILE_CYCLICAL_RESOURCE
        else TimingInputKind.VALUATION_REGIME
    )
    session = suitability_session(
        profile_id,
        suitability_input,
    )
    task = replace(
        session.task,
        horizon=ResearchHorizon.SHORT,
    )
    timing_findings = [
        _timing_finding(
            TimingDimension.CURRENT_REGIME,
            profile_timing_input,
            TimingConditionState.SUPPORTIVE,
        ),
        _timing_finding(
            TimingDimension.ENTRY_CONDITION,
            TimingInputKind.TREND_STATE,
            TimingConditionState.MET,
        ),
        _timing_finding(
            TimingDimension.WAIT_CONDITION,
            TimingInputKind.MARKET_RISK_STATE,
            TimingConditionState.NOT_MET,
        ),
        _timing_finding(
            TimingDimension.TRANCHE_RULE,
            TimingInputKind.TREND_STATE,
            tranche_state,
        ),
        _timing_finding(
            TimingDimension.AVOID_CONDITION,
            TimingInputKind.LIQUIDITY_STATE,
            TimingConditionState.NOT_MET,
        ),
        _timing_finding(
            TimingDimension.CATALYST_WINDOW,
            TimingInputKind.CATALYST_TIMELINE,
            TimingConditionState.NOT_APPLICABLE,
            catalyst_window_start="2026-08-24",
            catalyst_window_end="2026-08-28",
        ),
        _timing_finding(
            TimingDimension.INVALIDATION_CONDITION,
            TimingInputKind.MARKET_RISK_STATE,
            TimingConditionState.NOT_MET,
        ),
        _timing_finding(
            TimingDimension.NEXT_TRADABLE_TIME,
            TimingInputKind.TRADING_CALENDAR,
            TimingConditionState.MET,
            next_tradable_at="2026-08-24T09:30:00+08:00",
        ),
        _timing_finding(
            TimingDimension.EXECUTION_CONSTRAINT,
            TimingInputKind.LIQUIDITY_STATE,
            TimingConditionState.MET,
        ),
    ]
    if profile_id == PROFILE_EXCHANGE_FUND:
        timing_findings.append(
            _timing_finding(
                TimingDimension.CURRENT_REGIME,
                TimingInputKind.ETF_PREMIUM_DISCOUNT,
                TimingConditionState.NEUTRAL,
                suffix="etf-premium-discount",
            )
        )
    if include_horizon_conflict:
        timing_findings.extend(
            (
                _timing_finding(
                    TimingDimension.CURRENT_REGIME,
                    profile_timing_input,
                    TimingConditionState.ADVERSE,
                    signal_horizon=ResearchHorizon.LONG,
                    suffix="current-regime-long",
                ),
                _timing_finding(
                    TimingDimension.HORIZON_CONFLICT,
                    TimingInputKind.CROSS_HORIZON,
                    TimingConditionState.MET,
                    signal_horizon=ResearchHorizon.LONG,
                ),
            )
        )
    round_result = session.rounds[0]
    round_result = replace(
        round_result,
        structured_findings=(
            *round_result.structured_findings,
            *timing_findings,
        ),
        findings=(
            *round_result.findings,
            *(
                item.text
                for item in timing_findings
            ),
        ),
        evidence_refs=(
            *round_result.evidence_refs,
            *(
                ref
                for item in timing_findings
                for ref in item.evidence_refs
            ),
        ),
        hypothesis_impacts=(
            *round_result.hypothesis_impacts,
            HypothesisImpactRecord(
                hypothesis_id="thesis",
                impact=HypothesisImpact.SUPPORTS,
                rationale=(
                    "Timing evidence is kept separate from "
                    "the suitability thesis."
                ),
            ),
        ),
    )
    session = replace(
        session,
        task=task,
        rounds=(round_result,),
        questions=(
            *session.questions,
            ExplorationQuestion(
                question_id="decision-question:timing",
                text=(
                    "Which observable conditions support "
                    "action, staging, or waiting?"
                ),
                reason="Timing decision gate.",
                blocking=True,
                status=QuestionStatus.ANSWERED,
                answer="Observable timing conditions were recorded.",
                evidence_refs=("evidence:timing-entry",),
                track=ExplorationTrack.TIMING,
            ),
        ),
        decision_lanes=(
            DecisionLane.SUITABILITY,
            DecisionLane.TIMING,
        ),
    )
    brief = replace(
        suitability_brief(session),
        decision_lanes=(
            DecisionLane.SUITABILITY,
            DecisionLane.TIMING,
        ),
    )
    suitability = suitability_service.assemble(
        session=session,
        decision_brief=brief,
    )
    return session, brief, suitability


def _replace_timing_state(session, dimension, state):
    round_result = session.rounds[0]
    return replace(
        session,
        rounds=(
            replace(
                round_result,
                structured_findings=tuple(
                    replace(item, timing_state=state)
                    if item.timing_dimension is dimension
                    else item
                    for item in round_result.structured_findings
                ),
            ),
        ),
    )


def _timing_finding(
    dimension,
    input_kind,
    state,
    *,
    signal_horizon=ResearchHorizon.SHORT,
    catalyst_window_start=None,
    catalyst_window_end=None,
    next_tradable_at=None,
    suffix=None,
):
    suffix = suffix or dimension.value.replace("_", "-")
    return ResearchFinding(
        finding_id=f"timing:{suffix}",
        text=f"Observable timing evidence for {suffix}.",
        kind=ResearchFindingKind.INFERENCE,
        as_of_date="2026-08-23",
        track=ExplorationTrack.TIMING,
        evidence_refs=(f"evidence:timing-{suffix}",),
        source_tier=EvidenceTier.DERIVED,
        hypothesis_impacts=(
            HypothesisImpactRecord(
                hypothesis_id="thesis",
                impact=HypothesisImpact.SUPPORTS,
                rationale=f"{suffix} informs timing only.",
            ),
        ),
        limitations=(
            f"{suffix} is valid only through its stated window.",
        ),
        timing_dimension=dimension,
        timing_input_kind=input_kind,
        timing_state=state,
        signal_horizon=signal_horizon,
        timing_valid_from="2026-08-23",
        timing_valid_until="2026-08-28",
        observability_key=f"timing.{suffix}",
        catalyst_window_start=catalyst_window_start,
        catalyst_window_end=catalyst_window_end,
        next_tradable_at=next_tradable_at,
    )


if __name__ == "__main__":
    unittest.main()
