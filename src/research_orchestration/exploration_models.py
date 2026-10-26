"""Structured state for persistent, multi-round research exploration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from src.research_contracts import (
    DecisionLane,
    ResearchHorizon,
    ResearchTaskSpec,
)

from .entry_models import (
    TimingConditionState,
    TimingDimension,
    TimingInputKind,
    validate_timing_condition_state,
)
from .suitability_models import (
    SuitabilityDimension,
    SuitabilityInputKind,
)


class ExplorationStatus(str, Enum):
    EXPLORING = "exploring"
    READY_FOR_REPORT = "ready_for_report"
    BLOCKED = "blocked"


class QuestionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"
    DEFERRED = "deferred"


class QuestionPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class HypothesisStatus(str, Enum):
    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"


class HypothesisRole(str, Enum):
    THESIS = "thesis"
    COUNTERARGUMENT = "counterargument"
    ALTERNATIVE = "alternative"


class ExplorationTrack(str, Enum):
    GENERAL = "general"
    SUITABILITY = DecisionLane.SUITABILITY.value
    TIMING = DecisionLane.TIMING.value
    POSITION_SIZING = DecisionLane.POSITION_SIZING.value
    COUNTERARGUMENT = "counterargument"
    ALTERNATIVE = "alternative"

    @classmethod
    def from_decision_lane(
        cls,
        lane: DecisionLane,
    ) -> "ExplorationTrack":
        return cls(lane.value)


class ResearchFindingKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class EvidenceTier(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    DERIVED = "derived"
    USER_CONTEXT = "user_context"
    UNKNOWN = "unknown"


class HypothesisImpact(str, Enum):
    SUPPORTS = "supports"
    WEAKENS = "weakens"
    REJECTS = "rejects"
    NONE = "none"


class ExplorationGapKind(str, Enum):
    CAPABILITY = "capability"
    REMOTE_FAILURE = "remote_failure"
    BUSINESS_EMPTY = "business_empty"
    EXECUTION = "execution"
    USER_CONTEXT = "user_context"


@dataclass(frozen=True)
class ExplorationQueryGap:
    query_id: str
    capability_id: str
    kind: ExplorationGapKind
    code: str
    message: str
    blocking: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.query_id, "query_id"),
            (self.capability_id, "capability_id"),
            (self.code, "code"),
            (self.message, "message"),
        ):
            _text(value, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "capability_id": self.capability_id,
            "kind": self.kind.value,
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ExplorationQueryGap":
        return cls(
            query_id=str(payload["query_id"]),
            capability_id=str(payload["capability_id"]),
            kind=ExplorationGapKind(payload["kind"]),
            code=str(payload["code"]),
            message=str(payload["message"]),
            blocking=bool(payload["blocking"]),
        )


@dataclass(frozen=True)
class HypothesisImpactRecord:
    hypothesis_id: str
    impact: HypothesisImpact
    rationale: str

    def __post_init__(self) -> None:
        _text(self.hypothesis_id, "hypothesis_id")
        _text(self.rationale, "rationale")

    def to_dict(self) -> dict[str, str]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "impact": self.impact.value,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "HypothesisImpactRecord":
        return cls(
            hypothesis_id=str(payload["hypothesis_id"]),
            impact=HypothesisImpact(payload["impact"]),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True)
class ResearchFinding:
    finding_id: str
    text: str
    kind: ResearchFindingKind
    as_of_date: str
    track: ExplorationTrack = ExplorationTrack.GENERAL
    evidence_refs: tuple[str, ...] = ()
    source_tier: EvidenceTier = EvidenceTier.UNKNOWN
    hypothesis_impacts: tuple[HypothesisImpactRecord, ...] = ()
    limitations: tuple[str, ...] = ()
    suitability_dimension: SuitabilityDimension | None = None
    suitability_input_kind: SuitabilityInputKind | None = None
    subject_asset_id: str | None = None
    comparable_exposure: str | None = None
    timing_dimension: TimingDimension | None = None
    timing_input_kind: TimingInputKind | None = None
    timing_state: TimingConditionState | None = None
    signal_horizon: ResearchHorizon | None = None
    timing_valid_from: str | None = None
    timing_valid_until: str | None = None
    observability_key: str | None = None
    catalyst_window_start: str | None = None
    catalyst_window_end: str | None = None
    next_tradable_at: str | None = None

    def __post_init__(self) -> None:
        _text(self.finding_id, "finding_id")
        _text(self.text, "text")
        date.fromisoformat(self.as_of_date)
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.limitations, "limitations")
        _optional_text(
            self.subject_asset_id,
            "subject_asset_id",
        )
        _optional_text(
            self.comparable_exposure,
            "comparable_exposure",
        )
        for value, name in (
            (self.timing_valid_from, "timing_valid_from"),
            (self.timing_valid_until, "timing_valid_until"),
            (self.observability_key, "observability_key"),
            (
                self.catalyst_window_start,
                "catalyst_window_start",
            ),
            (
                self.catalyst_window_end,
                "catalyst_window_end",
            ),
            (self.next_tradable_at, "next_tradable_at"),
        ):
            _optional_text(value, name)
        _unique(
            (
                item.hypothesis_id
                for item in self.hypothesis_impacts
            ),
            "finding hypothesis_id",
        )
        if (
            self.kind is ResearchFindingKind.FACT
            and (
                not self.evidence_refs
                or self.source_tier is EvidenceTier.UNKNOWN
            )
        ):
            raise ValueError(
                "fact finding requires evidence_refs and source_tier"
            )
        if self.suitability_dimension is not None:
            if (
                not self.evidence_refs
                or not self.limitations
                or self.suitability_input_kind is None
                or self.source_tier is EvidenceTier.UNKNOWN
            ):
                raise ValueError(
                    "suitability finding requires evidence, "
                    "limitations, source tier, and input kind"
                )
            expected_track = (
                ExplorationTrack.ALTERNATIVE
                if self.suitability_dimension
                in (
                    SuitabilityDimension.ALTERNATIVE,
                    SuitabilityDimension.OPPORTUNITY_COST,
                )
                else ExplorationTrack.SUITABILITY
            )
            if self.track is not expected_track:
                raise ValueError(
                    "suitability dimension does not match track"
                )
            if (
                self.suitability_dimension
                in (
                    SuitabilityDimension.ALTERNATIVE,
                    SuitabilityDimension.OPPORTUNITY_COST,
                )
                and (
                    self.subject_asset_id is None
                    or self.comparable_exposure is None
                )
            ):
                raise ValueError(
                    "alternative suitability finding requires "
                    "subject asset and comparable exposure"
                )
        _validate_timing_finding(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "text": self.text,
            "kind": self.kind.value,
            "as_of_date": self.as_of_date,
            "track": self.track.value,
            "evidence_refs": list(self.evidence_refs),
            "source_tier": self.source_tier.value,
            "hypothesis_impacts": [
                item.to_dict()
                for item in self.hypothesis_impacts
            ],
            "limitations": list(self.limitations),
            "suitability_dimension": (
                self.suitability_dimension.value
                if self.suitability_dimension is not None
                else None
            ),
            "suitability_input_kind": (
                self.suitability_input_kind.value
                if self.suitability_input_kind is not None
                else None
            ),
            "subject_asset_id": self.subject_asset_id,
            "comparable_exposure": self.comparable_exposure,
            "timing_dimension": (
                self.timing_dimension.value
                if self.timing_dimension is not None
                else None
            ),
            "timing_input_kind": (
                self.timing_input_kind.value
                if self.timing_input_kind is not None
                else None
            ),
            "timing_state": (
                self.timing_state.value
                if self.timing_state is not None
                else None
            ),
            "signal_horizon": (
                self.signal_horizon.value
                if self.signal_horizon is not None
                else None
            ),
            "timing_valid_from": self.timing_valid_from,
            "timing_valid_until": self.timing_valid_until,
            "observability_key": self.observability_key,
            "catalyst_window_start": (
                self.catalyst_window_start
            ),
            "catalyst_window_end": self.catalyst_window_end,
            "next_tradable_at": self.next_tradable_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchFinding":
        return cls(
            finding_id=str(payload["finding_id"]),
            text=str(payload["text"]),
            kind=ResearchFindingKind(payload["kind"]),
            as_of_date=str(payload["as_of_date"]),
            track=ExplorationTrack(
                payload.get("track", ExplorationTrack.GENERAL.value)
            ),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            source_tier=EvidenceTier(
                payload.get(
                    "source_tier",
                    EvidenceTier.UNKNOWN.value,
                )
            ),
            hypothesis_impacts=tuple(
                HypothesisImpactRecord.from_dict(item)
                for item in payload.get(
                    "hypothesis_impacts",
                    (),
                )
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
            suitability_dimension=(
                SuitabilityDimension(
                    payload["suitability_dimension"]
                )
                if payload.get("suitability_dimension")
                is not None
                else None
            ),
            suitability_input_kind=(
                SuitabilityInputKind(
                    payload["suitability_input_kind"]
                )
                if payload.get("suitability_input_kind")
                is not None
                else None
            ),
            subject_asset_id=_optional_string(
                payload.get("subject_asset_id")
            ),
            comparable_exposure=_optional_string(
                payload.get("comparable_exposure")
            ),
            timing_dimension=(
                TimingDimension(payload["timing_dimension"])
                if payload.get("timing_dimension") is not None
                else None
            ),
            timing_input_kind=(
                TimingInputKind(payload["timing_input_kind"])
                if payload.get("timing_input_kind") is not None
                else None
            ),
            timing_state=(
                TimingConditionState(payload["timing_state"])
                if payload.get("timing_state") is not None
                else None
            ),
            signal_horizon=(
                ResearchHorizon(payload["signal_horizon"])
                if payload.get("signal_horizon") is not None
                else None
            ),
            timing_valid_from=_optional_string(
                payload.get("timing_valid_from")
            ),
            timing_valid_until=_optional_string(
                payload.get("timing_valid_until")
            ),
            observability_key=_optional_string(
                payload.get("observability_key")
            ),
            catalyst_window_start=_optional_string(
                payload.get("catalyst_window_start")
            ),
            catalyst_window_end=_optional_string(
                payload.get("catalyst_window_end")
            ),
            next_tradable_at=_optional_string(
                payload.get("next_tradable_at")
            ),
        )


@dataclass(frozen=True)
class ExplorationQuestion:
    question_id: str
    text: str
    reason: str
    blocking: bool
    status: QuestionStatus = QuestionStatus.OPEN
    answer: str | None = None
    evidence_refs: tuple[str, ...] = ()
    track: ExplorationTrack = ExplorationTrack.GENERAL
    priority: QuestionPriority = QuestionPriority.MEDIUM
    required_capability: str | None = None
    query_ids: tuple[str, ...] = ()
    last_query_outcome: str | None = None

    def __post_init__(self) -> None:
        _text(self.question_id, "question_id")
        _text(self.text, "text")
        _text(self.reason, "reason")
        if not isinstance(self.blocking, bool):
            raise TypeError("blocking must be bool")
        if (
            self.status is QuestionStatus.ANSWERED
            and not self.answer
        ):
            raise ValueError(
                "answered question requires answer"
            )
        _optional_text(
            self.required_capability,
            "required_capability",
        )
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.query_ids, "query_ids")
        _optional_text(
            self.last_query_outcome,
            "last_query_outcome",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "reason": self.reason,
            "blocking": self.blocking,
            "status": self.status.value,
            "answer": self.answer,
            "evidence_refs": list(self.evidence_refs),
            "track": self.track.value,
            "priority": self.priority.value,
            "required_capability": self.required_capability,
            "query_ids": list(self.query_ids),
            "last_query_outcome": self.last_query_outcome,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ExplorationQuestion":
        return cls(
            question_id=str(payload["question_id"]),
            text=str(payload["text"]),
            reason=str(payload["reason"]),
            blocking=bool(payload["blocking"]),
            status=QuestionStatus(
                payload.get("status", QuestionStatus.OPEN.value)
            ),
            answer=_optional_string(payload.get("answer")),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            track=ExplorationTrack(
                payload.get("track", ExplorationTrack.GENERAL.value)
            ),
            priority=QuestionPriority(
                payload.get(
                    "priority",
                    QuestionPriority.MEDIUM.value,
                )
            ),
            required_capability=_optional_string(
                payload.get("required_capability")
            ),
            query_ids=tuple(
                str(item)
                for item in payload.get("query_ids", ())
            ),
            last_query_outcome=_optional_string(
                payload.get("last_query_outcome")
            ),
        )


@dataclass(frozen=True)
class ResearchHypothesis:
    hypothesis_id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supporting_evidence_refs: tuple[str, ...] = ()
    counterevidence_refs: tuple[str, ...] = ()
    track: ExplorationTrack = ExplorationTrack.GENERAL
    role: HypothesisRole = HypothesisRole.THESIS
    expected_observations: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()
    unresolved_conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.hypothesis_id, "hypothesis_id")
        _text(self.statement, "statement")
        for values, name in (
            (
                self.supporting_evidence_refs,
                "supporting_evidence_refs",
            ),
            (
                self.counterevidence_refs,
                "counterevidence_refs",
            ),
            (
                self.expected_observations,
                "expected_observations",
            ),
            (
                self.falsification_conditions,
                "falsification_conditions",
            ),
            (
                self.unresolved_conflicts,
                "unresolved_conflicts",
            ),
        ):
            _unique_texts(values, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "status": self.status.value,
            "supporting_evidence_refs": list(
                self.supporting_evidence_refs
            ),
            "counterevidence_refs": list(
                self.counterevidence_refs
            ),
            "track": self.track.value,
            "role": self.role.value,
            "expected_observations": list(
                self.expected_observations
            ),
            "falsification_conditions": list(
                self.falsification_conditions
            ),
            "unresolved_conflicts": list(
                self.unresolved_conflicts
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchHypothesis":
        return cls(
            hypothesis_id=str(payload["hypothesis_id"]),
            statement=str(payload["statement"]),
            status=HypothesisStatus(
                payload.get(
                    "status",
                    HypothesisStatus.ACTIVE.value,
                )
            ),
            supporting_evidence_refs=tuple(
                str(item)
                for item in payload.get(
                    "supporting_evidence_refs",
                    (),
                )
            ),
            counterevidence_refs=tuple(
                str(item)
                for item in payload.get(
                    "counterevidence_refs",
                    (),
                )
            ),
            track=ExplorationTrack(
                payload.get("track", ExplorationTrack.GENERAL.value)
            ),
            role=HypothesisRole(
                payload.get("role", HypothesisRole.THESIS.value)
            ),
            expected_observations=tuple(
                str(item)
                for item in payload.get(
                    "expected_observations",
                    (),
                )
            ),
            falsification_conditions=tuple(
                str(item)
                for item in payload.get(
                    "falsification_conditions",
                    (),
                )
            ),
            unresolved_conflicts=tuple(
                str(item)
                for item in payload.get(
                    "unresolved_conflicts",
                    (),
                )
            ),
        )


@dataclass(frozen=True)
class HypothesisStatusEvent:
    event_id: str
    hypothesis_id: str
    previous_status: HypothesisStatus | None
    new_status: HypothesisStatus
    reason: str
    evidence_refs: tuple[str, ...]
    recorded_at: str

    def __post_init__(self) -> None:
        _text(self.event_id, "event_id")
        _text(self.hypothesis_id, "hypothesis_id")
        _text(self.reason, "reason")
        datetime.fromisoformat(self.recorded_at)
        _unique_texts(self.evidence_refs, "evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "hypothesis_id": self.hypothesis_id,
            "previous_status": (
                self.previous_status.value
                if self.previous_status is not None
                else None
            ),
            "new_status": self.new_status.value,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "HypothesisStatusEvent":
        previous = payload.get("previous_status")
        return cls(
            event_id=str(payload["event_id"]),
            hypothesis_id=str(payload["hypothesis_id"]),
            previous_status=(
                HypothesisStatus(previous)
                if previous is not None
                else None
            ),
            new_status=HypothesisStatus(
                payload["new_status"]
            ),
            reason=str(payload["reason"]),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            recorded_at=str(payload["recorded_at"]),
        )


@dataclass(frozen=True)
class ResearchExplorationRound:
    round_id: str
    objective: str
    queries: tuple[str, ...]
    findings: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    next_question_ids: tuple[str, ...] = ()
    user_interactions: tuple[str, ...] = ()
    structured_findings: tuple[ResearchFinding, ...] = ()
    hypothesis_impacts: tuple[HypothesisImpactRecord, ...] = ()
    next_step: str = ""

    def __post_init__(self) -> None:
        _text(self.round_id, "round_id")
        _text(self.objective, "objective")
        if not self.queries:
            raise ValueError(
                "exploration round requires queries"
            )
        if not self.findings and not self.structured_findings:
            raise ValueError(
                "exploration round requires findings"
            )
        for values, name in (
            (self.queries, "queries"),
            (self.findings, "findings"),
            (self.evidence_refs, "evidence_refs"),
            (
                self.next_question_ids,
                "next_question_ids",
            ),
            (
                self.user_interactions,
                "user_interactions",
            ),
        ):
            _unique_texts(values, name)
        _unique(
            (
                item.finding_id
                for item in self.structured_findings
            ),
            "finding_id",
        )
        if self.next_step:
            _text(self.next_step, "next_step")

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_id": self.round_id,
            "objective": self.objective,
            "queries": list(self.queries),
            "findings": list(self.findings),
            "evidence_refs": list(self.evidence_refs),
            "next_question_ids": list(
                self.next_question_ids
            ),
            "user_interactions": list(
                self.user_interactions
            ),
            "structured_findings": [
                item.to_dict()
                for item in self.structured_findings
            ],
            "hypothesis_impacts": [
                item.to_dict()
                for item in self.hypothesis_impacts
            ],
            "next_step": self.next_step,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchExplorationRound":
        return cls(
            round_id=str(payload["round_id"]),
            objective=str(payload["objective"]),
            queries=tuple(
                str(item)
                for item in payload.get("queries", ())
            ),
            findings=tuple(
                str(item)
                for item in payload.get("findings", ())
            ),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            next_question_ids=tuple(
                str(item)
                for item in payload.get(
                    "next_question_ids",
                    (),
                )
            ),
            user_interactions=tuple(
                str(item)
                for item in payload.get(
                    "user_interactions",
                    (),
                )
            ),
            structured_findings=tuple(
                ResearchFinding.from_dict(item)
                for item in payload.get(
                    "structured_findings",
                    (),
                )
            ),
            hypothesis_impacts=tuple(
                HypothesisImpactRecord.from_dict(item)
                for item in payload.get(
                    "hypothesis_impacts",
                    (),
                )
            ),
            next_step=str(payload.get("next_step", "")),
        )


@dataclass(frozen=True)
class ResearchExplorationSession:
    session_id: str
    task: ResearchTaskSpec
    baseline_path: Path | None
    rounds: tuple[ResearchExplorationRound, ...] = ()
    questions: tuple[ExplorationQuestion, ...] = ()
    hypotheses: tuple[ResearchHypothesis, ...] = ()
    status: ExplorationStatus = ExplorationStatus.EXPLORING
    user_id: str | None = None
    workflow_run_id: str | None = None
    handoff_id: str | None = None
    decision_brief_id: str | None = None
    decision_lanes: tuple[DecisionLane, ...] = ()
    hypothesis_history: tuple[HypothesisStatusEvent, ...] = ()
    query_gaps: tuple[ExplorationQueryGap, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        for value, name in (
            (self.user_id, "user_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (self.handoff_id, "handoff_id"),
            (self.decision_brief_id, "decision_brief_id"),
        ):
            _optional_text(value, name)
        for value, name in (
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            if value:
                datetime.fromisoformat(value)
        _unique(
            (item.round_id for item in self.rounds),
            "round_id",
        )
        _unique(
            (item.question_id for item in self.questions),
            "question_id",
        )
        _unique(
            (
                item.hypothesis_id
                for item in self.hypotheses
            ),
            "hypothesis_id",
        )
        _unique(
            (
                item.event_id
                for item in self.hypothesis_history
            ),
            "hypothesis event_id",
        )
        _unique(
            (
                finding.finding_id
                for round_result in self.rounds
                for finding in round_result.structured_findings
            ),
            "session finding_id",
        )
        _unique(
            (
                (item.query_id, item.code)
                for item in self.query_gaps
            ),
            "query gap",
        )
        _unique(self.decision_lanes, "decision_lanes")
        if any(
            finding.as_of_date > self.task.as_of_date
            for round_result in self.rounds
            for finding in round_result.structured_findings
        ):
            raise ValueError(
                "exploration findings must not exceed task as_of_date"
            )

    def unresolved_blocking_query_gaps(
        self,
    ) -> tuple[ExplorationQueryGap, ...]:
        """Blocking gaps whose question was not later answered with evidence."""

        answered = {
            query_id
            for question in self.questions
            if question.status is QuestionStatus.ANSWERED
            for query_id in question.query_ids
        }
        return tuple(
            gap
            for gap in self.query_gaps
            if gap.blocking and gap.query_id not in answered
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task": self.task.to_dict(),
            "baseline_path": (
                str(self.baseline_path)
                if self.baseline_path is not None
                else None
            ),
            "rounds": [
                item.to_dict() for item in self.rounds
            ],
            "questions": [
                item.to_dict() for item in self.questions
            ],
            "hypotheses": [
                item.to_dict() for item in self.hypotheses
            ],
            "status": self.status.value,
            "user_id": self.user_id,
            "workflow_run_id": self.workflow_run_id,
            "handoff_id": self.handoff_id,
            "decision_brief_id": self.decision_brief_id,
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "hypothesis_history": [
                item.to_dict()
                for item in self.hypothesis_history
            ],
            "query_gaps": [
                item.to_dict() for item in self.query_gaps
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchExplorationSession":
        return cls(
            session_id=str(payload["session_id"]),
            task=ResearchTaskSpec.from_dict(payload["task"]),
            baseline_path=(
                Path(str(payload["baseline_path"]))
                if payload.get("baseline_path") is not None
                else None
            ),
            rounds=tuple(
                ResearchExplorationRound.from_dict(item)
                for item in payload.get("rounds", ())
            ),
            questions=tuple(
                ExplorationQuestion.from_dict(item)
                for item in payload.get("questions", ())
            ),
            hypotheses=tuple(
                ResearchHypothesis.from_dict(item)
                for item in payload.get("hypotheses", ())
            ),
            status=ExplorationStatus(
                payload.get(
                    "status",
                    ExplorationStatus.EXPLORING.value,
                )
            ),
            user_id=_optional_string(payload.get("user_id")),
            workflow_run_id=_optional_string(
                payload.get("workflow_run_id")
            ),
            handoff_id=_optional_string(
                payload.get("handoff_id")
            ),
            decision_brief_id=_optional_string(
                payload.get("decision_brief_id")
            ),
            decision_lanes=tuple(
                DecisionLane(item)
                for item in payload.get("decision_lanes", ())
            ),
            hypothesis_history=tuple(
                HypothesisStatusEvent.from_dict(item)
                for item in payload.get(
                    "hypothesis_history",
                    (),
                )
            ),
            query_gaps=tuple(
                ExplorationQueryGap.from_dict(item)
                for item in payload.get("query_gaps", ())
            ),
            created_at=str(payload.get("created_at", "")),
            updated_at=str(payload.get("updated_at", "")),
        )


def _validate_timing_finding(
    finding: ResearchFinding,
) -> None:
    timing_values = (
        finding.timing_input_kind,
        finding.timing_state,
        finding.signal_horizon,
        finding.timing_valid_from,
        finding.timing_valid_until,
        finding.observability_key,
        finding.catalyst_window_start,
        finding.catalyst_window_end,
        finding.next_tradable_at,
    )
    if finding.timing_dimension is None:
        if any(item is not None for item in timing_values):
            raise ValueError(
                "timing metadata requires timing_dimension"
            )
        return
    if finding.track is not ExplorationTrack.TIMING:
        raise ValueError(
            "timing dimension requires timing track"
        )
    if (
        not finding.evidence_refs
        or not finding.limitations
        or finding.source_tier is EvidenceTier.UNKNOWN
        or finding.timing_input_kind is None
        or finding.timing_state is None
        or finding.signal_horizon is None
        or finding.timing_valid_from is None
        or finding.timing_valid_until is None
        or finding.observability_key is None
    ):
        raise ValueError(
            "timing finding requires evidence, limitations, "
            "source tier, state, horizon, validity, "
            "observability, and input kind"
        )
    validate_timing_condition_state(
        finding.timing_dimension,
        finding.timing_state,
    )
    as_of = date.fromisoformat(finding.as_of_date)
    valid_from = date.fromisoformat(
        finding.timing_valid_from
    )
    valid_until = date.fromisoformat(
        finding.timing_valid_until
    )
    if valid_from > valid_until:
        raise ValueError(
            "timing finding valid_from exceeds valid_until"
        )
    if as_of > valid_until:
        raise ValueError(
            "timing finding is expired at as_of_date"
        )
    if finding.timing_dimension is TimingDimension.CATALYST_WINDOW:
        if (
            finding.catalyst_window_start is None
            or finding.catalyst_window_end is None
        ):
            raise ValueError(
                "catalyst timing finding requires window"
            )
        if date.fromisoformat(
            finding.catalyst_window_start
        ) > date.fromisoformat(finding.catalyst_window_end):
            raise ValueError(
                "catalyst timing window start exceeds end"
            )
        if (
            date.fromisoformat(
                finding.catalyst_window_start
            )
            < valid_from
            or date.fromisoformat(
                finding.catalyst_window_end
            )
            > valid_until
        ):
            raise ValueError(
                "catalyst timing window exceeds validity"
            )
    elif (
        finding.catalyst_window_start is not None
        or finding.catalyst_window_end is not None
    ):
        raise ValueError(
            "only catalyst timing finding may define window"
        )
    if (
        finding.timing_dimension
        is TimingDimension.NEXT_TRADABLE_TIME
    ):
        if finding.next_tradable_at is None:
            raise ValueError(
                "next tradable timing finding requires timestamp"
            )
        next_tradable = datetime.fromisoformat(
            finding.next_tradable_at
        )
        if next_tradable.utcoffset() is None:
            raise ValueError(
                "next tradable timestamp requires timezone"
            )
        if next_tradable.date() < as_of:
            raise ValueError(
                "next tradable timestamp precedes as_of_date"
            )
        if next_tradable.date() < valid_from:
            raise ValueError(
                "next tradable timestamp precedes validity"
            )
        if next_tradable.date() > valid_until:
            raise ValueError(
                "next tradable timestamp exceeds validity"
            )
    elif finding.next_tradable_at is not None:
        raise ValueError(
            "only next tradable timing finding may define timestamp"
        )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _text(value, name)


def _unique_texts(
    values: tuple[str, ...],
    name: str,
) -> None:
    if any(
        not isinstance(item, str) or not item.strip()
        for item in values
    ):
        raise ValueError(
            f"{name} must contain non-blank strings"
        )
    _unique(values, name)


def _unique(values, name: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
