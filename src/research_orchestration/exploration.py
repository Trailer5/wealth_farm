"""State contracts for Agent-led, multi-round research exploration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.research_visualization import PublishedReport, ReportArtifactService
from src.research_contracts import DecisionLane, ResearchTaskSpec

from .exploration_models import (
    EvidenceTier,
    ExplorationGapKind,
    ExplorationQueryGap,
    ExplorationQuestion,
    ExplorationStatus,
    ExplorationTrack,
    HypothesisImpact,
    HypothesisImpactRecord,
    HypothesisRole,
    HypothesisStatus,
    HypothesisStatusEvent,
    QuestionPriority,
    QuestionStatus,
    ResearchExplorationRound,
    ResearchExplorationSession,
    ResearchFinding,
    ResearchFindingKind,
    ResearchHypothesis,
)


class ResearchExplorationService:
    """Record Agent decisions while deterministic collection stays automated."""

    def start(
        self,
        *,
        session_id: str,
        task: ResearchTaskSpec,
        baseline_path: Path | None,
        hypotheses: tuple[ResearchHypothesis, ...] = (),
        questions: tuple[ExplorationQuestion, ...] = (),
        user_id: str | None = None,
        workflow_run_id: str | None = None,
        handoff_id: str | None = None,
        decision_brief_id: str | None = None,
        decision_lanes: tuple[DecisionLane, ...] = (),
    ) -> ResearchExplorationSession:
        now = _now()
        history = tuple(
            HypothesisStatusEvent(
                event_id=f"hypothesis-event:{uuid4()}",
                hypothesis_id=item.hypothesis_id,
                previous_status=None,
                new_status=item.status,
                reason="Hypothesis registered.",
                evidence_refs=_hypothesis_evidence(item),
                recorded_at=now,
            )
            for item in hypotheses
        )
        return ResearchExplorationSession(
            session_id=session_id,
            task=task,
            baseline_path=baseline_path,
            questions=questions,
            hypotheses=hypotheses,
            user_id=user_id,
            workflow_run_id=workflow_run_id,
            handoff_id=handoff_id,
            decision_brief_id=decision_brief_id,
            decision_lanes=decision_lanes,
            hypothesis_history=history,
            created_at=now,
            updated_at=now,
        )

    def add_question(
        self,
        session: ResearchExplorationSession,
        question: ExplorationQuestion,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        return replace(
            session,
            questions=(*session.questions, question),
            updated_at=_now(),
        )

    def answer_question(
        self,
        session: ResearchExplorationSession,
        *,
        question_id: str,
        answer: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        _text(answer, "answer")
        found = False
        questions = []
        for question in session.questions:
            if question.question_id != question_id:
                questions.append(question)
                continue
            found = True
            if question.status is not QuestionStatus.OPEN:
                raise ValueError(
                    "question is no longer open"
                )
            if question.blocking and not evidence_refs:
                raise ValueError(
                    "blocking question answer requires evidence_refs"
                )
            questions.append(
                replace(
                    question,
                    status=QuestionStatus.ANSWERED,
                    answer=answer,
                    evidence_refs=evidence_refs,
                )
            )
        if not found:
            raise KeyError(f"Unknown question_id: {question_id}")
        return replace(
            session,
            questions=tuple(questions),
            updated_at=_now(),
        )

    def defer_question(
        self,
        session: ResearchExplorationSession,
        *,
        question_id: str,
        reason: str,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        _text(reason, "reason")
        found = False
        questions = []
        for question in session.questions:
            if question.question_id != question_id:
                questions.append(question)
                continue
            found = True
            if question.status is not QuestionStatus.OPEN:
                raise ValueError(
                    "question is no longer open"
                )
            questions.append(
                replace(
                    question,
                    status=QuestionStatus.DEFERRED,
                    answer=reason,
                )
            )
        if not found:
            raise KeyError(f"Unknown question_id: {question_id}")
        return replace(
            session,
            questions=tuple(questions),
            updated_at=_now(),
        )

    def record_query_result(
        self,
        session: ResearchExplorationSession,
        *,
        question_id: str,
        query_id: str,
        outcome: str,
        summary: str,
        evidence_refs: tuple[str, ...] = (),
        resolve_question: bool = False,
        defer_question: bool = False,
        gap: ExplorationQueryGap | None = None,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        for value, name in (
            (query_id, "query_id"),
            (outcome, "outcome"),
            (summary, "summary"),
        ):
            _text(value, name)
        if resolve_question and defer_question:
            raise ValueError(
                "query result cannot resolve and defer together"
            )
        found = False
        questions = []
        for question in session.questions:
            if question.question_id != question_id:
                questions.append(question)
                continue
            found = True
            if question.status is not QuestionStatus.OPEN:
                raise ValueError(
                    "question is no longer open"
                )
            status = QuestionStatus.OPEN
            answer = question.answer
            if resolve_question:
                if not evidence_refs:
                    raise ValueError(
                        "resolved query requires evidence_refs"
                    )
                status = QuestionStatus.ANSWERED
                answer = summary
            elif defer_question:
                status = QuestionStatus.DEFERRED
                answer = summary
            questions.append(
                replace(
                    question,
                    status=status,
                    answer=answer,
                    evidence_refs=tuple(
                        dict.fromkeys(
                            (
                                *question.evidence_refs,
                                *evidence_refs,
                            )
                        )
                    ),
                    query_ids=(
                        *question.query_ids,
                        query_id,
                    ),
                    last_query_outcome=outcome,
                )
            )
        if not found:
            raise KeyError(f"Unknown question_id: {question_id}")
        return replace(
            session,
            questions=tuple(questions),
            query_gaps=(
                (*session.query_gaps, gap)
                if gap is not None
                else session.query_gaps
            ),
            updated_at=_now(),
        )

    def add_round(
        self,
        session: ResearchExplorationSession,
        round_result: ResearchExplorationRound,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        known_questions = {
            item.question_id for item in session.questions
        }
        unknown = set(round_result.next_question_ids) - known_questions
        if unknown:
            raise ValueError(
                f"round references unknown questions: {sorted(unknown)}"
            )
        if not round_result.structured_findings:
            raise ValueError(
                "exploration round requires structured findings"
            )
        if not round_result.evidence_refs:
            raise ValueError(
                "exploration round requires evidence_refs"
            )
        if not round_result.next_step.strip():
            raise ValueError(
                "exploration round requires next_step"
            )
        impacts = (
            *round_result.hypothesis_impacts,
            *(
                impact
                for finding in round_result.structured_findings
                for impact in finding.hypothesis_impacts
            ),
        )
        if not impacts:
            raise ValueError(
                "exploration round requires hypothesis impacts"
            )
        known_hypotheses = {
            item.hypothesis_id for item in session.hypotheses
        }
        unknown_hypotheses = {
            item.hypothesis_id for item in impacts
        } - known_hypotheses
        if unknown_hypotheses:
            raise ValueError(
                "round impacts unknown hypotheses: "
                f"{sorted(unknown_hypotheses)}"
            )
        if any(
            finding.as_of_date > session.task.as_of_date
            for finding in round_result.structured_findings
        ):
            raise ValueError(
                "exploration finding exceeds task as_of_date"
            )
        return replace(
            session,
            rounds=(*session.rounds, round_result),
            updated_at=_now(),
        )

    def update_hypothesis(
        self,
        session: ResearchExplorationSession,
        hypothesis: ResearchHypothesis,
        *,
        reason: str = "Hypothesis reassessed.",
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        _text(reason, "reason")
        found = False
        hypotheses = []
        previous_status = None
        for current in session.hypotheses:
            if current.hypothesis_id != hypothesis.hypothesis_id:
                hypotheses.append(current)
                continue
            found = True
            previous_status = current.status
            hypotheses.append(hypothesis)
        if not found:
            hypotheses.append(hypothesis)
        now = _now()
        event = HypothesisStatusEvent(
            event_id=f"hypothesis-event:{uuid4()}",
            hypothesis_id=hypothesis.hypothesis_id,
            previous_status=previous_status,
            new_status=hypothesis.status,
            reason=reason,
            evidence_refs=_hypothesis_evidence(hypothesis),
            recorded_at=now,
        )
        return replace(
            session,
            hypotheses=tuple(hypotheses),
            hypothesis_history=(
                *session.hypothesis_history,
                event,
            ),
            updated_at=now,
        )

    def mark_ready(
        self,
        session: ResearchExplorationSession,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        if not session.rounds:
            raise ValueError(
                "at least one exploration round is required"
            )
        blocking_open = [
            item.question_id
            for item in session.questions
            if item.blocking and item.status is QuestionStatus.OPEN
        ]
        if blocking_open:
            raise ValueError(
                f"blocking questions remain open: {blocking_open}"
            )
        finding_tracks = {
            finding.track
            for round_result in session.rounds
            for finding in round_result.structured_findings
        }
        for lane in session.decision_lanes:
            track = ExplorationTrack.from_decision_lane(lane)
            lane_questions = [
                item
                for item in session.questions
                if item.track is track
            ]
            if not lane_questions or any(
                item.status is QuestionStatus.OPEN
                for item in lane_questions
            ):
                raise ValueError(
                    f"decision track remains open: {lane.value}"
                )
            if track not in finding_tracks:
                raise ValueError(
                    "decision track lacks structured findings: "
                    f"{lane.value}"
                )
        if DecisionLane.SUITABILITY in session.decision_lanes:
            alternative_questions = [
                item
                for item in session.questions
                if item.track is ExplorationTrack.ALTERNATIVE
            ]
            if not alternative_questions or any(
                item.status is QuestionStatus.OPEN
                for item in alternative_questions
            ):
                raise ValueError(
                    "alternative track remains open"
                )
        theses = [
            item
            for item in session.hypotheses
            if item.role is HypothesisRole.THESIS
        ]
        if not theses:
            raise ValueError("at least one thesis is required")
        missing_falsification = [
            item.hypothesis_id
            for item in theses
            if not item.falsification_conditions
        ]
        if missing_falsification:
            raise ValueError(
                "theses lack falsification conditions: "
                f"{missing_falsification}"
            )
        unresolved_conflicts = [
            item.hypothesis_id
            for item in session.hypotheses
            if item.unresolved_conflicts
        ]
        if unresolved_conflicts:
            raise ValueError(
                "hypothesis conflicts remain unresolved: "
                f"{unresolved_conflicts}"
            )
        counterarguments = [
            item
            for item in session.hypotheses
            if item.role is HypothesisRole.COUNTERARGUMENT
        ]
        if not counterarguments:
            raise ValueError(
                "strongest counterargument is required"
            )
        unevaluated_counterarguments = [
            item.hypothesis_id
            for item in counterarguments
            if (
                item.status is HypothesisStatus.ACTIVE
                or not _hypothesis_evidence(item)
            )
        ]
        if unevaluated_counterarguments:
            raise ValueError(
                "strongest counterargument remains unevaluated: "
                f"{unevaluated_counterarguments}"
            )
        return replace(
            session,
            status=ExplorationStatus.READY_FOR_REPORT,
            updated_at=_now(),
        )

    def publish_final(
        self,
        session: ResearchExplorationSession,
        *,
        publisher: ReportArtifactService,
        filename: str,
        content: str,
    ) -> PublishedReport:
        raise RuntimeError(
            "direct final publication is disabled; use "
            "FinalDecisionReportService so structural and Markdown "
            "review cannot be bypassed"
        )


def _require_exploring(session: ResearchExplorationSession) -> None:
    if session.status is not ExplorationStatus.EXPLORING:
        raise ValueError("exploration session is no longer editable")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _hypothesis_evidence(
    hypothesis: ResearchHypothesis,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *hypothesis.supporting_evidence_refs,
                *hypothesis.counterevidence_refs,
            )
        )
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
