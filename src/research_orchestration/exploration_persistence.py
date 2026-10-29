"""User-scoped persistence and recovery for research exploration."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from uuid import uuid4

from src.research_state import (
    ExplorationLedgerRecord,
    ExplorationSessionRecord,
    SQLiteResearchStateRepository,
)
from src.research_contracts import (
    DecisionLane,
    ResearchDecisionBrief,
)
from src.user_space import UserContext, UserSpaceService

from .capabilities import hypothesis_decision_lane
from .exploration import (
    ExplorationQuestion,
    ExplorationQueryGap,
    ExplorationTrack,
    HypothesisRole,
    QuestionPriority,
    ResearchExplorationRound,
    ResearchExplorationService,
    ResearchExplorationSession,
    ResearchHypothesis,
)
from .workflow import (
    BaselineResearchHandoff,
    ResearchWorkflowCoordinator,
    ResearchWorkflowRun,
)


class ExplorationLedgerEntryKind(str, Enum):
    SESSION_STARTED = "session_started"
    QUESTION_ADDED = "question_added"
    QUESTION_ANSWERED = "question_answered"
    QUESTION_DEFERRED = "question_deferred"
    ROUND_ADDED = "round_added"
    HYPOTHESIS_UPDATED = "hypothesis_updated"
    QUERY_EXECUTED = "query_executed"
    READY_FOR_REPORT = "ready_for_report"


class PersistentResearchExplorationService:
    """Load, mutate, and append exploration state by user."""

    def __init__(
        self,
        *,
        repository: SQLiteResearchStateRepository,
        workflow_coordinator: ResearchWorkflowCoordinator,
        user_space: UserSpaceService,
        state_machine: ResearchExplorationService | None = None,
    ) -> None:
        self.repository = repository
        self.workflow_coordinator = workflow_coordinator
        self.user_space = user_space
        self.state_machine = (
            state_machine or ResearchExplorationService()
        )
        self.repository.initialize()

    def start_from_handoff(
        self,
        context: UserContext,
        *,
        workflow_run_id: str,
        decision_brief: ResearchDecisionBrief | None = None,
        session_id: str | None = None,
    ) -> ResearchExplorationSession:
        self._require_user(context)
        workflow_run = self._require_workflow_run(
            workflow_run_id
        )
        handoff = self._require_handoff(workflow_run)
        existing = (
            self.repository
            .get_exploration_session_for_workflow(
                context.user_id,
                workflow_run_id,
            )
        )
        if existing is not None:
            session = ResearchExplorationSession.from_dict(
                existing.payload
            )
            self.workflow_coordinator.link_exploration_session(
                workflow_run_id=workflow_run_id,
                session_id=session.session_id,
                user_id=context.user_id,
            )
            return session
        if workflow_run.exploration_session_id is not None:
            raise ValueError(
                "workflow run is linked to another user's "
                "or missing exploration session"
            )
        self._validate_decision_brief(
            context=context,
            workflow_run=workflow_run,
            decision_brief=decision_brief,
        )
        decision_lanes = (
            decision_brief.decision_lanes
            if decision_brief is not None
            else workflow_run.decision_lanes
        )
        questions = _seed_questions(
            handoff,
            decision_lanes,
        )
        hypotheses = _seed_hypotheses(
            handoff,
            decision_lanes,
        )
        session = self.state_machine.start(
            session_id=(
                session_id
                or f"exploration:{uuid4()}"
            ),
            task=workflow_run.task,
            baseline_path=(
                Path(handoff.report_path)
                if handoff.report_path is not None
                else None
            ),
            questions=questions,
            hypotheses=hypotheses,
            user_id=context.user_id,
            workflow_run_id=workflow_run.workflow_run_id,
            handoff_id=handoff.handoff_id,
            decision_brief_id=(
                decision_brief.brief_id
                if decision_brief is not None
                else workflow_run.decision_brief_id
            ),
            decision_lanes=decision_lanes,
        )
        self.repository.insert_exploration_session(
            _session_record(session),
            _ledger_entry(
                session=session,
                kind=(
                    ExplorationLedgerEntryKind.SESSION_STARTED
                ),
                payload={
                    "workflow_run_id": workflow_run_id,
                    "handoff_id": handoff.handoff_id,
                    "seed_question_ids": [
                        item.question_id
                        for item in questions
                    ],
                    "seed_hypothesis_ids": [
                        item.hypothesis_id
                        for item in hypotheses
                    ],
                },
            ),
        )
        self.workflow_coordinator.link_exploration_session(
            workflow_run_id=workflow_run_id,
            session_id=session.session_id,
            user_id=context.user_id,
        )
        return session

    def get_session(
        self,
        context: UserContext,
        *,
        session_id: str,
    ) -> ResearchExplorationSession | None:
        self._require_user(context)
        record = self.repository.get_exploration_session(
            context.user_id,
            session_id,
        )
        return (
            ResearchExplorationSession.from_dict(record.payload)
            if record is not None
            else None
        )

    def resume_for_workflow(
        self,
        context: UserContext,
        *,
        workflow_run_id: str,
    ) -> ResearchExplorationSession | None:
        self._require_user(context)
        record = (
            self.repository
            .get_exploration_session_for_workflow(
                context.user_id,
                workflow_run_id,
            )
        )
        return (
            ResearchExplorationSession.from_dict(record.payload)
            if record is not None
            else None
        )

    def list_ledger(
        self,
        context: UserContext,
        *,
        session_id: str,
    ) -> tuple[ExplorationLedgerRecord, ...]:
        self._require_user(context)
        return self.repository.list_exploration_ledger_entries(
            context.user_id,
            session_id,
        )

    def add_question(
        self,
        context: UserContext,
        *,
        session_id: str,
        question: ExplorationQuestion,
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.add_question(
            current,
            question,
        )
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.QUESTION_ADDED,
            {"question": question.to_dict()},
        )

    def answer_question(
        self,
        context: UserContext,
        *,
        session_id: str,
        question_id: str,
        answer: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.answer_question(
            current,
            question_id=question_id,
            answer=answer,
            evidence_refs=evidence_refs,
        )
        question = next(
            item
            for item in updated.questions
            if item.question_id == question_id
        )
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.QUESTION_ANSWERED,
            {"question": question.to_dict()},
        )

    def defer_question(
        self,
        context: UserContext,
        *,
        session_id: str,
        question_id: str,
        reason: str,
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.defer_question(
            current,
            question_id=question_id,
            reason=reason,
        )
        question = next(
            item
            for item in updated.questions
            if item.question_id == question_id
        )
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.QUESTION_DEFERRED,
            {"question": question.to_dict()},
        )

    def add_round(
        self,
        context: UserContext,
        *,
        session_id: str,
        round_result: ResearchExplorationRound,
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.add_round(
            current,
            round_result,
        )
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.ROUND_ADDED,
            {"round": round_result.to_dict()},
        )

    def update_hypothesis(
        self,
        context: UserContext,
        *,
        session_id: str,
        hypothesis: ResearchHypothesis,
        reason: str,
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.update_hypothesis(
            current,
            hypothesis,
            reason=reason,
        )
        event = updated.hypothesis_history[-1]
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.HYPOTHESIS_UPDATED,
            {
                "hypothesis": hypothesis.to_dict(),
                "status_event": event.to_dict(),
            },
        )

    def record_query_result(
        self,
        context: UserContext,
        *,
        session_id: str,
        question_id: str,
        query_id: str,
        outcome: str,
        summary: str,
        evidence_refs: tuple[str, ...],
        plan_payload: dict[str, object],
        result_payload: dict[str, object],
        resolve_question: bool = False,
        defer_question: bool = False,
        gap: ExplorationQueryGap | None = None,
    ) -> ResearchExplorationSession:
        current = self._require_session(context, session_id)
        updated = self.state_machine.record_query_result(
            current,
            question_id=question_id,
            query_id=query_id,
            outcome=outcome,
            summary=summary,
            evidence_refs=evidence_refs,
            resolve_question=resolve_question,
            defer_question=defer_question,
            gap=gap,
        )
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.QUERY_EXECUTED,
            {
                "plan": plan_payload,
                "result": result_payload,
                "question_status": next(
                    item.status.value
                    for item in updated.questions
                    if item.question_id == question_id
                ),
            },
        )

    def mark_ready(
        self,
        context: UserContext,
        *,
        session_id: str,
    ) -> ResearchExplorationSession:
        """Lock the session for assembly; afterwards no rounds or findings can be added.

        Requires: at least one round; no open blocking question; every requested track has no open question
        and has findings; the alternative question is not open when suitability is requested; every main
        hypothesis (seeds included) has falsification conditions and no unresolved conflict;
        strongest-counterargument is no longer active and has evidence. Seed hypotheses you will not test
        become weakened or rejected with a reason. Assemble the three tracks in memory and check
        assembly_gaps first; a locked session with errors needs a new run-recipe.
        """

        current = self._require_session(context, session_id)
        updated = self.state_machine.mark_ready(current)
        return self._save_mutation(
            updated,
            ExplorationLedgerEntryKind.READY_FOR_REPORT,
            {"status": updated.status.value},
        )

    def _save_mutation(
        self,
        session: ResearchExplorationSession,
        kind: ExplorationLedgerEntryKind,
        payload: dict[str, object],
    ) -> ResearchExplorationSession:
        self.repository.save_exploration_mutation(
            _session_record(session),
            _ledger_entry(
                session=session,
                kind=kind,
                payload=payload,
            ),
        )
        return session

    def _require_session(
        self,
        context: UserContext,
        session_id: str,
    ) -> ResearchExplorationSession:
        session = self.get_session(
            context,
            session_id=session_id,
        )
        if session is None:
            raise KeyError(
                "Unknown exploration session for user: "
                f"{session_id}"
            )
        return session

    def _require_user(self, context: UserContext) -> None:
        self.user_space.context_for_user(context.user_id)

    def _require_workflow_run(
        self,
        workflow_run_id: str,
    ) -> ResearchWorkflowRun:
        workflow_run = (
            self.workflow_coordinator.get_workflow_run(
                workflow_run_id
            )
        )
        if workflow_run is None:
            raise KeyError(
                f"Unknown workflow_run_id: {workflow_run_id}"
            )
        return workflow_run

    def _require_handoff(
        self,
        workflow_run: ResearchWorkflowRun,
    ) -> BaselineResearchHandoff:
        handoff = (
            self.workflow_coordinator.get_handoff_for_run(
                workflow_run.workflow_run_id
            )
        )
        if handoff is None:
            raise ValueError(
                "workflow run has no baseline handoff"
            )
        return handoff

    def _validate_decision_brief(
        self,
        *,
        context: UserContext,
        workflow_run: ResearchWorkflowRun,
        decision_brief: ResearchDecisionBrief | None,
    ) -> None:
        if decision_brief is None:
            return
        if decision_brief.user_id != context.user_id:
            raise ValueError(
                "decision brief belongs to another user"
            )
        if decision_brief.asset_id != workflow_run.task.asset_id:
            raise ValueError(
                "decision brief asset does not match workflow"
            )
        if (
            workflow_run.decision_brief_id is not None
            and workflow_run.decision_brief_id
            != decision_brief.brief_id
        ):
            raise ValueError(
                "decision brief does not match workflow"
            )


def _session_record(
    session: ResearchExplorationSession,
) -> ExplorationSessionRecord:
    if (
        session.user_id is None
        or session.workflow_run_id is None
        or session.handoff_id is None
    ):
        raise ValueError(
            "persistent exploration requires user, workflow, and handoff"
        )
    return ExplorationSessionRecord(
        session_id=session.session_id,
        user_id=session.user_id,
        workflow_run_id=session.workflow_run_id,
        handoff_id=session.handoff_id,
        decision_brief_id=session.decision_brief_id,
        asset_id=session.task.asset_id,
        status=session.status.value,
        payload=session.to_dict(),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _ledger_entry(
    *,
    session: ResearchExplorationSession,
    kind: ExplorationLedgerEntryKind,
    payload: dict[str, object],
) -> ExplorationLedgerRecord:
    return ExplorationLedgerRecord(
        entry_id=f"exploration-ledger:{uuid4()}",
        session_id=session.session_id,
        sequence_no=0,
        entry_kind=kind.value,
        payload=payload,
        recorded_at=session.updated_at,
    )


def _seed_questions(
    handoff: BaselineResearchHandoff,
    decision_lanes: tuple[DecisionLane, ...],
) -> tuple[ExplorationQuestion, ...]:
    questions = [
        ExplorationQuestion(
            question_id=f"candidate-question:{index}",
            text=text,
            reason="Baseline handoff candidate question.",
            blocking=False,
            track=ExplorationTrack.GENERAL,
        )
        for index, text in enumerate(
            handoff.candidate_questions,
            start=1,
        )
    ]
    for lane in decision_lanes:
        questions.append(
            ExplorationQuestion(
                question_id=f"decision-question:{lane.value}",
                text=_DECISION_QUESTIONS[lane],
                reason=(
                    "A requested decision track requires an "
                    "answer or formal unavailability."
                ),
                blocking=True,
                track=ExplorationTrack.from_decision_lane(
                    lane
                ),
                priority=QuestionPriority.HIGH,
            )
        )
    if DecisionLane.SUITABILITY in decision_lanes:
        questions.append(
            ExplorationQuestion(
                question_id="decision-question:alternative",
                text="相同风险暴露下是否存在更优替代方案？",
                reason="Suitability requires an alternative check.",
                blocking=True,
                track=ExplorationTrack.ALTERNATIVE,
                priority=QuestionPriority.HIGH,
            )
        )
    return tuple(questions)


def _seed_hypotheses(
    handoff: BaselineResearchHandoff,
    decision_lanes: tuple[DecisionLane, ...],
) -> tuple[ResearchHypothesis, ...]:
    default_track = (
        ExplorationTrack.from_decision_lane(decision_lanes[0])
        if decision_lanes
        else ExplorationTrack.GENERAL
    )
    thesis_track = (
        ExplorationTrack.from_decision_lane(DecisionLane.SUITABILITY)
        if DecisionLane.SUITABILITY in decision_lanes
        else default_track
    )

    def track_for(statement: str) -> ExplorationTrack:
        lane = hypothesis_decision_lane(statement)
        if lane is not None and lane in decision_lanes:
            return ExplorationTrack.from_decision_lane(lane)
        return thesis_track

    statements = (
        handoff.candidate_hypotheses
        or ("基础研究中的主要解释仍需进一步验证。",)
    )
    theses = tuple(
        ResearchHypothesis(
            hypothesis_id=f"candidate-hypothesis:{index}",
            statement=statement,
            track=track_for(statement),
            role=HypothesisRole.THESIS,
        )
        for index, statement in enumerate(statements, start=1)
    )
    return (
        *theses,
        ResearchHypothesis(
            hypothesis_id="strongest-counterargument",
            statement="当前核心论文的最强反方尚待独立验证。",
            track=ExplorationTrack.COUNTERARGUMENT,
            role=HypothesisRole.COUNTERARGUMENT,
        ),
    )


_DECISION_QUESTIONS = {
    DecisionLane.SUITABILITY: (
        "当前收益来源是否足以补偿主要下行风险？"
    ),
    DecisionLane.TIMING: (
        "哪些可观察条件支持现在行动、分批或等待？"
    ),
    DecisionLane.POSITION_SIZING: (
        "哪些用户约束和风险预算会绑定可用仓位？"
    ),
}
