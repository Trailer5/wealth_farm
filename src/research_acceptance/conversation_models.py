"""Frozen contracts for representative new-conversation acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from src.research_workflow import (
    DecisionIntent,
    DecisionLane,
    ResearchHorizon,
    ResearchQuestionType,
)


NEW_CONVERSATION_ACCEPTANCE_VERSION = "slice12_v1"

_PLANNING_STATUSES = {
    "ready",
    "needs_clarification",
    "unresolved",
    "unsupported",
}


class ExpectedFailureSemantics(str, Enum):
    NONE = "none"
    REMOTE_FAILURE = "remote_failure"
    BUSINESS_EMPTY = "business_empty"
    BASELINE_BLOCKED = "baseline_blocked"


class ConversationAcceptanceStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class NewConversationScenario:
    scenario_id: str
    prompt: str
    symbol: str
    as_of_date: str
    decision_intent: DecisionIntent
    decision_lanes: tuple[DecisionLane, ...]
    horizon: ResearchHorizon
    expected_planning_status: str
    expected_profile_id: str | None
    expected_baseline_question: ResearchQuestionType | None
    required_user_questions: tuple[str, ...]
    allowed_user_questions: tuple[str, ...]
    max_user_questions: int
    baseline_required: bool
    exploration_required: bool
    final_report_required: bool
    timing_required: bool
    position_required: bool
    expected_query_outcomes: tuple[str, ...] = ()
    allowed_gap_prefixes: tuple[str, ...] = ()
    expected_failure: ExpectedFailureSemantics = (
        ExpectedFailureSemantics.NONE
    )
    asset_type_hint: str | None = None
    market_hint: str | None = None
    exchange_hint: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.scenario_id, "scenario_id"),
            (self.prompt, "prompt"),
            (self.symbol, "symbol"),
        ):
            _text(value, name)
        date.fromisoformat(self.as_of_date)
        if self.expected_planning_status not in _PLANNING_STATUSES:
            raise ValueError("unsupported expected_planning_status")
        _optional_text(self.expected_profile_id, "expected_profile_id")
        for value, name in (
            (self.asset_type_hint, "asset_type_hint"),
            (self.market_hint, "market_hint"),
            (self.exchange_hint, "exchange_hint"),
        ):
            _optional_text(value, name)
        _unique(self.decision_lanes, "decision_lanes")
        _texts(
            self.required_user_questions,
            "required_user_questions",
        )
        _texts(
            self.allowed_user_questions,
            "allowed_user_questions",
        )
        _texts(
            self.expected_query_outcomes,
            "expected_query_outcomes",
        )
        _texts(self.allowed_gap_prefixes, "allowed_gap_prefixes")
        if not set(self.required_user_questions).issubset(
            self.allowed_user_questions
        ):
            raise ValueError(
                "required questions must be allowed questions"
            )
        if self.max_user_questions < len(
            self.required_user_questions
        ):
            raise ValueError(
                "max_user_questions is below required question count"
            )
        if self.max_user_questions < 0:
            raise ValueError("max_user_questions must not be negative")
        if self.exploration_required and not self.baseline_required:
            raise ValueError("exploration requires a baseline")
        if self.final_report_required and not self.exploration_required:
            raise ValueError("final report requires exploration")
        if (
            self.timing_required
            and DecisionLane.TIMING not in self.decision_lanes
        ):
            raise ValueError("timing_required requires the timing lane")
        if (
            self.position_required
            and DecisionLane.POSITION_SIZING
            not in self.decision_lanes
        ):
            raise ValueError(
                "position_required requires the position lane"
            )
        if self.expected_planning_status != "ready" and any(
            (
                self.baseline_required,
                self.exploration_required,
                self.final_report_required,
            )
        ):
            raise ValueError(
                "non-ready planning scenario cannot require downstream "
                "execution"
            )
        if self.expected_planning_status == "ready" and (
            self.expected_profile_id is None
            or self.expected_baseline_question is None
        ):
            raise ValueError(
                "ready scenario requires profile and baseline question"
            )
        if (
            self.expected_failure
            in (
                ExpectedFailureSemantics.REMOTE_FAILURE,
                ExpectedFailureSemantics.BUSINESS_EMPTY,
            )
            and self.expected_failure.value
            not in self.expected_query_outcomes
        ):
            raise ValueError(
                "query failure scenario must require its typed outcome"
            )
        if (
            self.expected_failure
            is ExpectedFailureSemantics.BASELINE_BLOCKED
            and not self.baseline_required
        ):
            raise ValueError(
                "baseline_blocked scenario requires baseline execution"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "prompt": self.prompt,
            "symbol": self.symbol,
            "as_of_date": self.as_of_date,
            "decision_intent": self.decision_intent.value,
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "horizon": self.horizon.value,
            "expected_planning_status": (
                self.expected_planning_status
            ),
            "expected_profile_id": self.expected_profile_id,
            "expected_baseline_question": (
                self.expected_baseline_question.value
                if self.expected_baseline_question is not None
                else None
            ),
            "required_user_questions": list(
                self.required_user_questions
            ),
            "allowed_user_questions": list(
                self.allowed_user_questions
            ),
            "max_user_questions": self.max_user_questions,
            "baseline_required": self.baseline_required,
            "exploration_required": self.exploration_required,
            "final_report_required": self.final_report_required,
            "timing_required": self.timing_required,
            "position_required": self.position_required,
            "expected_query_outcomes": list(
                self.expected_query_outcomes
            ),
            "allowed_gap_prefixes": list(
                self.allowed_gap_prefixes
            ),
            "expected_failure": self.expected_failure.value,
            "asset_type_hint": self.asset_type_hint,
            "market_hint": self.market_hint,
            "exchange_hint": self.exchange_hint,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "NewConversationScenario":
        question = payload.get("expected_baseline_question")
        return cls(
            scenario_id=str(payload["scenario_id"]),
            prompt=str(payload["prompt"]),
            symbol=str(payload["symbol"]),
            as_of_date=str(payload["as_of_date"]),
            decision_intent=DecisionIntent(
                payload["decision_intent"]
            ),
            decision_lanes=tuple(
                DecisionLane(item)
                for item in payload.get("decision_lanes", ())
            ),
            horizon=ResearchHorizon(payload["horizon"]),
            expected_planning_status=str(
                payload["expected_planning_status"]
            ),
            expected_profile_id=_optional_string(
                payload.get("expected_profile_id")
            ),
            expected_baseline_question=(
                ResearchQuestionType(question)
                if question is not None
                else None
            ),
            required_user_questions=tuple(
                str(item)
                for item in payload.get(
                    "required_user_questions",
                    (),
                )
            ),
            allowed_user_questions=tuple(
                str(item)
                for item in payload.get(
                    "allowed_user_questions",
                    (),
                )
            ),
            max_user_questions=int(payload["max_user_questions"]),
            baseline_required=bool(payload["baseline_required"]),
            exploration_required=bool(
                payload["exploration_required"]
            ),
            final_report_required=bool(
                payload["final_report_required"]
            ),
            timing_required=bool(payload["timing_required"]),
            position_required=bool(payload["position_required"]),
            expected_query_outcomes=tuple(
                str(item)
                for item in payload.get(
                    "expected_query_outcomes",
                    (),
                )
            ),
            allowed_gap_prefixes=tuple(
                str(item)
                for item in payload.get(
                    "allowed_gap_prefixes",
                    (),
                )
            ),
            expected_failure=ExpectedFailureSemantics(
                payload.get(
                    "expected_failure",
                    ExpectedFailureSemantics.NONE.value,
                )
            ),
            asset_type_hint=_optional_string(
                payload.get("asset_type_hint")
            ),
            market_hint=_optional_string(
                payload.get("market_hint")
            ),
            exchange_hint=_optional_string(
                payload.get("exchange_hint")
            ),
        )


@dataclass(frozen=True)
class NewConversationObservation:
    scenario_id: str
    planning_status: str
    candidate_count: int = 0
    resolved_asset_id: str | None = None
    profile_id: str | None = None
    baseline_question: str | None = None
    baseline_status: str | None = None
    workflow_created: bool = False
    handoff_created: bool = False
    baseline_report_path: str | None = None
    exploration_status: str | None = None
    supporting_round_count: int = 0
    counterevidence_round_count: int = 0
    query_outcomes: tuple[str, ...] = ()
    asked_user_questions: tuple[str, ...] = ()
    gap_codes: tuple[str, ...] = ()
    suitability_action: str | None = None
    entry_status: str | None = None
    position_status: str | None = None
    position_has_range: bool = False
    final_review_passed: bool = False
    final_report_published: bool = False
    final_report_path: str | None = None
    final_decision_lane_count: int = 0
    identity_chain_consistent: bool = True
    blocked_baseline_bypassed: bool = False

    def __post_init__(self) -> None:
        _text(self.scenario_id, "scenario_id")
        if self.planning_status not in _PLANNING_STATUSES:
            raise ValueError("unsupported planning_status")
        for value, name in (
            (self.resolved_asset_id, "resolved_asset_id"),
            (self.profile_id, "profile_id"),
            (self.baseline_question, "baseline_question"),
            (self.baseline_status, "baseline_status"),
            (self.baseline_report_path, "baseline_report_path"),
            (self.exploration_status, "exploration_status"),
            (self.suitability_action, "suitability_action"),
            (self.entry_status, "entry_status"),
            (self.position_status, "position_status"),
            (self.final_report_path, "final_report_path"),
        ):
            _optional_text(value, name)
        for value, name in (
            (self.candidate_count, "candidate_count"),
            (self.supporting_round_count, "supporting_round_count"),
            (
                self.counterevidence_round_count,
                "counterevidence_round_count",
            ),
            (
                self.final_decision_lane_count,
                "final_decision_lane_count",
            ),
        ):
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        _texts(self.query_outcomes, "query_outcomes")
        _texts(self.asked_user_questions, "asked_user_questions")
        _texts(self.gap_codes, "gap_codes")

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        } | {
            "query_outcomes": list(self.query_outcomes),
            "asked_user_questions": list(
                self.asked_user_questions
            ),
            "gap_codes": list(self.gap_codes),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "NewConversationObservation":
        values = dict(payload)
        for key in (
            "query_outcomes",
            "asked_user_questions",
            "gap_codes",
        ):
            values[key] = tuple(
                str(item) for item in payload.get(key, ())
            )
        return cls(**values)


@dataclass(frozen=True)
class ConversationAcceptanceFinding:
    code: str
    message: str

    def __post_init__(self) -> None:
        _text(self.code, "finding code")
        _text(self.message, "finding message")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class NewConversationAcceptanceResult:
    acceptance_version: str
    scenario_id: str
    status: ConversationAcceptanceStatus
    findings: tuple[ConversationAcceptanceFinding, ...]

    @property
    def passed(self) -> bool:
        return self.status is ConversationAcceptanceStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance_version": self.acceptance_version,
            "scenario_id": self.scenario_id,
            "status": self.status.value,
            "findings": [
                item.to_dict() for item in self.findings
            ],
        }


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _text(value, name)


def _texts(values: tuple[str, ...], name: str) -> None:
    if any(
        not isinstance(item, str) or not item.strip()
        for item in values
    ):
        raise ValueError(f"{name} must contain non-blank strings")
    _unique(values, name)


def _unique(values: tuple[object, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
