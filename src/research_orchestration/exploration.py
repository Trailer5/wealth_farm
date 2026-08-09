"""State contracts for Agent-led, multi-round research exploration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from src.research_visualization import PublishedReport, ReportArtifactService
from src.research_workflow import ResearchTaskSpec


class ExplorationStatus(str, Enum):
    EXPLORING = "exploring"
    READY_FOR_REPORT = "ready_for_report"
    BLOCKED = "blocked"


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    DEFERRED = "deferred"


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExplorationQuestion:
    question_id: str
    text: str
    reason: str
    blocking: bool
    status: QuestionStatus = QuestionStatus.OPEN
    answer: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.question_id, "question_id")
        _text(self.text, "text")
        _text(self.reason, "reason")
        if not isinstance(self.blocking, bool):
            raise TypeError("blocking must be bool")
        if self.status is QuestionStatus.ANSWERED and not self.answer:
            raise ValueError("answered question requires answer")


@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis_id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supporting_evidence_refs: tuple[str, ...] = ()
    counterevidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.hypothesis_id, "hypothesis_id")
        _text(self.statement, "statement")


@dataclass(frozen=True)
class ResearchExplorationRound:
    round_id: str
    objective: str
    queries: tuple[str, ...]
    findings: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    next_question_ids: tuple[str, ...] = ()
    user_interactions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.round_id, "round_id")
        _text(self.objective, "objective")
        if not self.queries:
            raise ValueError("exploration round requires queries")
        if not self.findings:
            raise ValueError("exploration round requires findings")


@dataclass(frozen=True)
class ResearchExplorationSession:
    session_id: str
    task: ResearchTaskSpec
    baseline_path: Path | None
    rounds: tuple[ResearchExplorationRound, ...] = ()
    questions: tuple[ExplorationQuestion, ...] = ()
    hypotheses: tuple[ResearchHypothesis, ...] = ()
    status: ExplorationStatus = ExplorationStatus.EXPLORING

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _unique(
            (item.round_id for item in self.rounds),
            "round_id",
        )
        _unique(
            (item.question_id for item in self.questions),
            "question_id",
        )
        _unique(
            (item.hypothesis_id for item in self.hypotheses),
            "hypothesis_id",
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
    ) -> ResearchExplorationSession:
        return ResearchExplorationSession(
            session_id=session_id,
            task=task,
            baseline_path=baseline_path,
            hypotheses=hypotheses,
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
        return replace(session, questions=tuple(questions))

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
        return replace(
            session,
            rounds=(*session.rounds, round_result),
        )

    def update_hypothesis(
        self,
        session: ResearchExplorationSession,
        hypothesis: ResearchHypothesis,
    ) -> ResearchExplorationSession:
        _require_exploring(session)
        found = False
        hypotheses = []
        for current in session.hypotheses:
            if current.hypothesis_id != hypothesis.hypothesis_id:
                hypotheses.append(current)
                continue
            found = True
            hypotheses.append(hypothesis)
        if not found:
            hypotheses.append(hypothesis)
        return replace(session, hypotheses=tuple(hypotheses))

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
        return replace(
            session,
            status=ExplorationStatus.READY_FOR_REPORT,
        )

    def publish_final(
        self,
        session: ResearchExplorationSession,
        *,
        publisher: ReportArtifactService,
        filename: str,
        content: str,
    ) -> PublishedReport:
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            raise ValueError(
                "session must be ready_for_report before publishing"
            )
        return publisher.publish_markdown(
            filename=filename,
            content=content,
        )


def _require_exploring(session: ResearchExplorationSession) -> None:
    if session.status is not ExplorationStatus.EXPLORING:
        raise ValueError("exploration session is no longer editable")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _unique(values, name: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{name} values must be unique")
