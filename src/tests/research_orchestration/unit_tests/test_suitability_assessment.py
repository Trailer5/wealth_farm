import unittest
from dataclasses import replace

from src.research_orchestration import (
    EvidenceTier,
    ExplorationQuestion,
    ExplorationStatus,
    ExplorationTrack,
    HypothesisImpact,
    HypothesisImpactRecord,
    HypothesisRole,
    HypothesisStatus,
    QuestionStatus,
    ResearchExplorationRound,
    ResearchExplorationSession,
    ResearchFinding,
    ResearchFindingKind,
    ResearchHypothesis,
    SuitabilityAction,
    SuitabilityAssessment,
    SuitabilityAssessmentService,
    SuitabilityConfidence,
    SuitabilityDimension,
    SuitabilityInputKind,
)
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchDecisionBrief,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)


class SuitabilityAssessmentTest(unittest.TestCase):
    def setUp(self):
        self.service = SuitabilityAssessmentService()

    def test_representative_stock_etf_and_open_fund_can_be_considered(self):
        cases = (
            (
                PROFILE_ORDINARY_COMPANY,
                SuitabilityInputKind.COMPANY_FUNDAMENTALS,
            ),
            (
                PROFILE_EXCHANGE_FUND,
                SuitabilityInputKind.ETF_EXPOSURE_EFFICIENCY,
            ),
            (
                PROFILE_OPEN_FUND,
                SuitabilityInputKind.OPEN_FUND_MANAGER_STYLE,
            ),
        )

        for profile_id, input_kind in cases:
            with self.subTest(profile_id=profile_id):
                session = _session(profile_id, input_kind)
                assessment = self.service.assemble(
                    session=session,
                    decision_brief=_brief(session),
                )

                self.assertIs(
                    assessment.action,
                    SuitabilityAction.CONSIDER,
                )
                self.assertIs(
                    assessment.confidence,
                    SuitabilityConfidence.SUPPORTED,
                )
                self.assertFalse(assessment.assembly_gaps)
                self.assertTrue(assessment.priced_expectations)
                self.assertTrue(assessment.alternatives)
                self.assertTrue(assessment.opportunity_costs)
                self.assertTrue(assessment.downside)
                self.assertTrue(
                    all(
                        block.evidence_refs
                        and block.limitations
                        and block.time_horizon
                        is ResearchHorizon.MEDIUM_LONG
                        for block in assessment.claim_blocks
                    )
                )
                payload = assessment.to_dict()
                self.assertNotIn("score", payload)
                self.assertEqual(
                    SuitabilityAssessment.from_dict(payload),
                    assessment,
                )

    def test_missing_comparable_alternative_cannot_be_consider(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
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
                        if item.suitability_dimension
                        not in (
                            SuitabilityDimension.ALTERNATIVE,
                            (
                                SuitabilityDimension
                                .OPPORTUNITY_COST
                            ),
                        )
                    ),
                ),
            ),
            questions=tuple(
                replace(
                    item,
                    status=QuestionStatus.DEFERRED,
                    answer="No comparable alternative was verified.",
                )
                if item.track is ExplorationTrack.ALTERNATIVE
                else item
                for item in session.questions
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.WATCH,
        )
        self.assertIn(
            "alternative_question_not_answered",
            assessment.assembly_gaps,
        )
        self.assertNotEqual(
            assessment.action,
            SuitabilityAction.CONSIDER,
        )

    def test_unresolved_counterargument_is_insufficient(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )
        session = replace(
            session,
            status=ExplorationStatus.EXPLORING,
            hypotheses=tuple(
                replace(item, status=HypothesisStatus.ACTIVE)
                if item.role is HypothesisRole.COUNTERARGUMENT
                else item
                for item in session.hypotheses
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.INSUFFICIENT,
        )
        self.assertIn(
            "strongest_counterargument_unresolved",
            assessment.assembly_gaps,
        )

    def test_rejected_thesis_is_avoid(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
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

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.AVOID,
        )

    def test_review_holding_intent_produces_hold_review(self):
        session = _session(
            PROFILE_EXCHANGE_FUND,
            SuitabilityInputKind.ETF_EXPOSURE_EFFICIENCY,
        )
        brief = replace(
            _brief(session),
            decision_intent=DecisionIntent.REVIEW_HOLDING,
            position_state=PositionState.HELD,
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=brief,
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.HOLD_REVIEW,
        )

    def test_profile_specific_input_cannot_be_substituted(self):
        session = _session(
            PROFILE_EXCHANGE_FUND,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.INSUFFICIENT,
        )
        self.assertIn(
            "profile_input_missing:etf_exposure_efficiency",
            assessment.assembly_gaps,
        )

    def test_missing_decision_brief_is_insufficient(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )

        assessment = self.service.assemble(session=session)

        self.assertIs(
            assessment.action,
            SuitabilityAction.INSUFFICIENT,
        )
        self.assertIn(
            "decision_brief_unavailable",
            assessment.assembly_gaps,
        )

    def test_assumption_does_not_satisfy_priced_expectations(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
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
                            kind=ResearchFindingKind.ASSUMPTION,
                        )
                        if item.suitability_dimension
                        is SuitabilityDimension.PRICED_EXPECTATION
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.WATCH,
        )
        self.assertIn(
            "suitability_dimension_missing:priced_expectation",
            assessment.assembly_gaps,
        )

    def test_cross_lane_hypothesis_cannot_override_suitability_thesis(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )
        suitability_thesis = replace(
            session.hypotheses[0],
            status=HypothesisStatus.REJECTED,
        )
        timing_thesis = replace(
            session.hypotheses[0],
            hypothesis_id="timing-thesis",
            status=HypothesisStatus.SUPPORTED,
            track=ExplorationTrack.TIMING,
        )
        session = replace(
            session,
            hypotheses=(
                suitability_thesis,
                timing_thesis,
                session.hypotheses[1],
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.AVOID,
        )
        self.assertIsNotNone(assessment.thesis)
        assert assessment.thesis is not None
        self.assertEqual(
            assessment.thesis.state.value,
            "rejected",
        )

    def test_cross_lane_counterargument_cannot_drive_suitability(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )
        valid_counterargument = replace(
            session.hypotheses[1],
            hypothesis_id="suitability-counterargument",
        )
        timing_counterargument = replace(
            session.hypotheses[1],
            hypothesis_id="strongest-counterargument",
            status=HypothesisStatus.SUPPORTED,
            track=ExplorationTrack.TIMING,
        )
        session = replace(
            session,
            hypotheses=(
                session.hypotheses[0],
                valid_counterargument,
                timing_counterargument,
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.CONSIDER,
        )
        self.assertIsNotNone(
            assessment.strongest_counterargument
        )
        assert assessment.strongest_counterargument is not None
        self.assertEqual(
            assessment.strongest_counterargument.text,
            valid_counterargument.statement,
        )

    def test_unrecorded_evidence_cannot_support_decision(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
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
                                "evidence:not-recorded",
                            ),
                        )
                        if item.suitability_dimension
                        is (
                            SuitabilityDimension
                            .EXPECTED_RETURN_SOURCE
                        )
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.INSUFFICIENT,
        )
        self.assertIn(
            "finding_evidence_not_recorded:finding:return-source",
            assessment.assembly_gaps,
        )
        self.assertNotIn(
            "evidence:not-recorded",
            assessment.evidence_refs,
        )
        self.assertFalse(
            any(
                block.source_finding_ids
                == ("finding:return-source",)
                for block in assessment.claim_blocks
            )
        )

    def test_alternative_and_opportunity_cost_must_match(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
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
                            subject_asset_id="alternative:other",
                            comparable_exposure="different-exposure",
                        )
                        if item.suitability_dimension
                        is SuitabilityDimension.OPPORTUNITY_COST
                        else item
                        for item in round_result.structured_findings
                    ),
                ),
            ),
        )

        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )

        self.assertIs(
            assessment.action,
            SuitabilityAction.WATCH,
        )
        self.assertIn(
            "alternative_opportunity_cost_mismatch",
            assessment.assembly_gaps,
        )

    def test_deserialization_validates_confidence_and_content_id(self):
        session = _session(
            PROFILE_ORDINARY_COMPANY,
            SuitabilityInputKind.COMPANY_FUNDAMENTALS,
        )
        assessment = self.service.assemble(
            session=session,
            decision_brief=_brief(session),
        )
        payload = assessment.to_dict()
        payload["confidence"] = SuitabilityConfidence.INSUFFICIENT.value
        with self.assertRaisesRegex(
            ValueError,
            "confidence does not match",
        ):
            SuitabilityAssessment.from_dict(payload)

        payload = assessment.to_dict()
        payload["action"] = SuitabilityAction.WATCH.value
        payload["confidence"] = SuitabilityConfidence.BOUNDED.value
        with self.assertRaisesRegex(
            ValueError,
            "assessment_id does not match",
        ):
            SuitabilityAssessment.from_dict(payload)

    def test_alternative_finding_requires_comparable_exposure(self):
        with self.assertRaisesRegex(
            ValueError,
            "subject asset and comparable exposure",
        ):
            _finding(
                "alternative-invalid",
                SuitabilityDimension.ALTERNATIVE,
                SuitabilityInputKind.COMPARABLE_ALTERNATIVE,
                track=ExplorationTrack.ALTERNATIVE,
                subject_asset_id=None,
                comparable_exposure=None,
            )


def _session(profile_id, profile_input_kind):
    task = ResearchTaskSpec(
        task_id=f"task:{profile_id}",
        asset_id=f"asset:{profile_id}",
        profile_id=profile_id,
        question_type=ResearchQuestionType.UNDERSTANDING,
        horizon=ResearchHorizon.MEDIUM_LONG,
        as_of_date="2026-08-23",
    )
    findings = (
        _finding(
            "return-source",
            SuitabilityDimension.EXPECTED_RETURN_SOURCE,
            profile_input_kind,
        ),
        _finding(
            "priced-expectation",
            SuitabilityDimension.PRICED_EXPECTATION,
            SuitabilityInputKind.BENCHMARK_RELATIVE,
        ),
        _finding(
            "downside",
            SuitabilityDimension.DOWNSIDE,
            SuitabilityInputKind.DOWNSIDE_RISK,
        ),
        _finding(
            "alternative",
            SuitabilityDimension.ALTERNATIVE,
            SuitabilityInputKind.COMPARABLE_ALTERNATIVE,
            track=ExplorationTrack.ALTERNATIVE,
            subject_asset_id=f"alternative:{profile_id}",
            comparable_exposure="same-risk-exposure",
        ),
        _finding(
            "opportunity-cost",
            SuitabilityDimension.OPPORTUNITY_COST,
            SuitabilityInputKind.COMPARABLE_ALTERNATIVE,
            track=ExplorationTrack.ALTERNATIVE,
            subject_asset_id=f"alternative:{profile_id}",
            comparable_exposure="same-risk-exposure",
        ),
    )
    evidence_refs = tuple(
        ref
        for finding in findings
        for ref in finding.evidence_refs
    )
    round_result = ResearchExplorationRound(
        round_id="round:suitability",
        objective="Assess return, downside, and alternatives.",
        queries=("query:profile", "query:alternative"),
        findings=tuple(item.text for item in findings),
        structured_findings=findings,
        evidence_refs=evidence_refs,
        hypothesis_impacts=(
            HypothesisImpactRecord(
                hypothesis_id="thesis",
                impact=HypothesisImpact.SUPPORTS,
                rationale="Recorded evidence supports the thesis.",
            ),
        ),
        next_step="Assemble the suitability decision.",
    )
    return ResearchExplorationSession(
        session_id=f"session:{profile_id}",
        task=task,
        baseline_path=None,
        rounds=(round_result,),
        questions=(
            ExplorationQuestion(
                question_id="decision-question:suitability",
                text="Do return sources compensate for downside?",
                reason="Suitability decision gate.",
                blocking=True,
                status=QuestionStatus.ANSWERED,
                answer="Yes, within recorded limitations.",
                evidence_refs=("evidence:return-source",),
                track=ExplorationTrack.SUITABILITY,
            ),
            ExplorationQuestion(
                question_id="decision-question:alternative",
                text="Is there a better comparable alternative?",
                reason="Alternative decision gate.",
                blocking=True,
                status=QuestionStatus.ANSWERED,
                answer="A comparable alternative was evaluated.",
                evidence_refs=("evidence:alternative",),
                track=ExplorationTrack.ALTERNATIVE,
            ),
        ),
        hypotheses=(
            ResearchHypothesis(
                hypothesis_id="thesis",
                statement=(
                    "Recorded return sources compensate for the "
                    "identified downside over the stated horizon."
                ),
                status=HypothesisStatus.SUPPORTED,
                supporting_evidence_refs=(
                    "evidence:return-source",
                    "evidence:priced-expectation",
                ),
                counterevidence_refs=("evidence:downside",),
                track=ExplorationTrack.SUITABILITY,
                role=HypothesisRole.THESIS,
                expected_observations=(
                    "Return-source evidence remains observable.",
                ),
                falsification_conditions=(
                    "Return sources weaken or downside exceeds evidence.",
                ),
            ),
            ResearchHypothesis(
                hypothesis_id="strongest-counterargument",
                statement=(
                    "The comparable alternative offers a better "
                    "risk-adjusted use of capital."
                ),
                status=HypothesisStatus.WEAKENED,
                supporting_evidence_refs=("evidence:alternative",),
                counterevidence_refs=("evidence:opportunity-cost",),
                track=ExplorationTrack.COUNTERARGUMENT,
                role=HypothesisRole.COUNTERARGUMENT,
                expected_observations=(
                    "Alternative advantage persists after exposure "
                    "normalization.",
                ),
                falsification_conditions=(
                    "Alternative loses its exposure-adjusted advantage.",
                ),
            ),
        ),
        status=ExplorationStatus.READY_FOR_REPORT,
        user_id="user:default",
        workflow_run_id=f"workflow:{profile_id}",
        handoff_id=f"handoff:{profile_id}",
        decision_brief_id=f"brief:{profile_id}",
        decision_lanes=(DecisionLane.SUITABILITY,),
        created_at="2026-08-23T00:00:00+00:00",
        updated_at="2026-08-23T01:00:00+00:00",
    )


def _finding(
    suffix,
    dimension,
    input_kind,
    *,
    track=ExplorationTrack.SUITABILITY,
    subject_asset_id=None,
    comparable_exposure=None,
):
    evidence_ref = f"evidence:{suffix}"
    return ResearchFinding(
        finding_id=f"finding:{suffix}",
        text=f"Recorded suitability evidence for {suffix}.",
        kind=ResearchFindingKind.INFERENCE,
        as_of_date="2026-08-23",
        track=track,
        evidence_refs=(evidence_ref,),
        source_tier=EvidenceTier.DERIVED,
        hypothesis_impacts=(
            HypothesisImpactRecord(
                hypothesis_id="thesis",
                impact=HypothesisImpact.SUPPORTS,
                rationale=f"{suffix} informs the thesis.",
            ),
        ),
        limitations=(
            f"{suffix} remains bounded by the recorded horizon.",
        ),
        suitability_dimension=dimension,
        suitability_input_kind=input_kind,
        subject_asset_id=subject_asset_id,
        comparable_exposure=comparable_exposure,
    )


def _brief(session):
    return ResearchDecisionBrief(
        brief_id=session.decision_brief_id,
        user_id=session.user_id,
        asset_id=session.task.asset_id,
        decision_intent=DecisionIntent.EVALUATE_PURCHASE,
        decision_lanes=(DecisionLane.SUITABILITY,),
        position_state=PositionState.NOT_HELD,
        horizon=session.task.horizon,
        as_of_date=session.task.as_of_date,
        portfolio_role="portfolio diversifier",
        mandate_id=None,
        portfolio_constraint_snapshot_id=None,
        constraints=(),
        assumptions=(),
        missing_user_context=(),
        created_at="2026-08-23T00:00:00+00:00",
        updated_at="2026-08-23T00:30:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
