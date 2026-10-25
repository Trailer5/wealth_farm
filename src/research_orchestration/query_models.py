"""Contracts for question-bound exploration queries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .exploration_models import (
    ExplorationQueryGap,
    ResearchExplorationSession,
    ResearchFinding,
)


class ResearchQueryRoute(str, Enum):
    DATA_ACCESS = "data_access"
    RESEARCH_EVIDENCE = "research_evidence"
    BENCHMARK_ANALYSIS = "benchmark_analysis"
    EVENT_KNOWLEDGE = "event_knowledge"
    INDUSTRY_ANALYSIS = "industry_analysis"
    PEER_ANALYSIS = "peer_analysis"
    USER_RESEARCH_STATE = "user_research_state"
    PORTFOLIO_RISK = "portfolio_risk"
    EXTERNAL_OFFICIAL_WEB = "external_official_web"


class ResearchQueryOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    BUSINESS_EMPTY = "business_empty"
    REMOTE_FAILURE = "remote_failure"
    USER_CONTEXT_REQUIRED = "user_context_required"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True)
class ResearchQueryPlan:
    query_id: str
    session_id: str
    question_id: str
    capability_id: str
    route: ResearchQueryRoute
    expected_evidence: tuple[str, ...]
    completion_criteria: str
    as_of_date: str
    parameters: dict[str, Any]

    def __post_init__(self) -> None:
        for value, name in (
            (self.query_id, "query_id"),
            (self.session_id, "session_id"),
            (self.question_id, "question_id"),
            (self.capability_id, "capability_id"),
            (
                self.completion_criteria,
                "completion_criteria",
            ),
        ):
            _text(value, name)
        date.fromisoformat(self.as_of_date)
        if not self.expected_evidence:
            raise ValueError(
                "query plan requires expected_evidence"
            )
        _unique_texts(
            self.expected_evidence,
            "expected_evidence",
        )
        try:
            json.dumps(self.parameters)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "query parameters must be JSON serializable"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "session_id": self.session_id,
            "question_id": self.question_id,
            "capability_id": self.capability_id,
            "route": self.route.value,
            "expected_evidence": list(
                self.expected_evidence
            ),
            "completion_criteria": self.completion_criteria,
            "as_of_date": self.as_of_date,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchQueryPlan":
        return cls(
            query_id=str(payload["query_id"]),
            session_id=str(payload["session_id"]),
            question_id=str(payload["question_id"]),
            capability_id=str(payload["capability_id"]),
            route=ResearchQueryRoute(payload["route"]),
            expected_evidence=tuple(
                str(item)
                for item in payload["expected_evidence"]
            ),
            completion_criteria=str(
                payload["completion_criteria"]
            ),
            as_of_date=str(payload["as_of_date"]),
            parameters=dict(payload.get("parameters", {})),
        )


@dataclass(frozen=True)
class ResearchQueryResult:
    query_id: str
    question_id: str
    capability_id: str
    route: ResearchQueryRoute
    outcome: ResearchQueryOutcome
    summary: str
    evidence_refs: tuple[str, ...]
    findings: tuple[ResearchFinding, ...]
    gap: ExplorationQueryGap | None
    metadata: dict[str, Any]
    completed_at: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.query_id, "query_id"),
            (self.question_id, "question_id"),
            (self.capability_id, "capability_id"),
            (self.summary, "summary"),
        ):
            _text(value, name)
        _unique_texts(self.evidence_refs, "evidence_refs")
        datetime.fromisoformat(self.completed_at)
        if (
            self.outcome
            in (
                ResearchQueryOutcome.SUCCESS,
                ResearchQueryOutcome.PARTIAL,
            )
            and not self.evidence_refs
        ):
            raise ValueError(
                "successful query requires evidence_refs"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "question_id": self.question_id,
            "capability_id": self.capability_id,
            "route": self.route.value,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "findings": [
                item.to_dict() for item in self.findings
            ],
            "gap": (
                self.gap.to_dict()
                if self.gap is not None
                else None
            ),
            "metadata": self.metadata,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchQueryResult":
        gap = payload.get("gap")
        return cls(
            query_id=str(payload["query_id"]),
            question_id=str(payload["question_id"]),
            capability_id=str(payload["capability_id"]),
            route=ResearchQueryRoute(payload["route"]),
            outcome=ResearchQueryOutcome(payload["outcome"]),
            summary=str(payload["summary"]),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            findings=tuple(
                ResearchFinding.from_dict(item)
                for item in payload.get("findings", ())
            ),
            gap=(
                ExplorationQueryGap.from_dict(gap)
                if gap is not None
                else None
            ),
            metadata=dict(payload.get("metadata", {})),
            completed_at=str(payload["completed_at"]),
        )


@dataclass(frozen=True)
class ResearchQueryExecutionResult:
    session: ResearchExplorationSession
    result: ResearchQueryResult


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
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
