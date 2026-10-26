"""Immutable contracts for the user-facing final decision report."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from src.research_contracts import (
    DecisionIntent,
    DecisionLane,
    PositionState,
    ProbabilityInterval,
    ResearchHorizon,
    from_payload,
    to_payload,
)

from .models import ResearchRunStatus


class FinalStatementKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


class FinalEvidenceKind(str, Enum):
    OFFICIAL = "official"
    REPORTED = "reported"
    DERIVED = "derived"
    USER_CONTEXT = "user_context"


class FinalScenarioKind(str, Enum):
    OPTIMISTIC = "optimistic"
    BASE = "base"
    PESSIMISTIC = "pessimistic"


class FinalDecisionOutcome(str, Enum):
    SUITABILITY_CONSIDER = "suitability_consider"
    SUITABILITY_WATCH = "suitability_watch"
    SUITABILITY_AVOID = "suitability_avoid"
    SUITABILITY_HOLD_REVIEW = "suitability_hold_review"
    SUITABILITY_INSUFFICIENT = "suitability_insufficient"
    TIMING_ACT = "timing_act"
    TIMING_STAGE = "timing_stage"
    TIMING_WAIT = "timing_wait"
    TIMING_NOT_APPLICABLE = "timing_not_applicable"
    TIMING_INSUFFICIENT = "timing_insufficient"
    POSITION_AVAILABLE = "position_available"
    POSITION_USER_CONTEXT_REQUIRED = (
        "position_user_context_required"
    )
    POSITION_UNSUPPORTED = "position_unsupported"


class MonitoringConditionKind(str, Enum):
    MACHINE_OBSERVABLE = "machine_observable"
    MANUAL_REVIEW = "manual_review"


class MonitoringPlanStatus(str, Enum):
    USER_CONFIRMATION_REQUIRED = "user_confirmation_required"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class FinalStatement:
    kind: FinalStatementKind
    text: str
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.text, "statement text")
        _texts(self.evidence_refs, "statement evidence_refs")
        _texts(self.limitations, "statement limitations")
        if self.kind in (
            FinalStatementKind.FACT,
            FinalStatementKind.INFERENCE,
        ) and not self.evidence_refs:
            raise ValueError(
                "fact and inference statements require evidence"
            )
        if self.kind in (
            FinalStatementKind.INFERENCE,
            FinalStatementKind.HYPOTHESIS,
            FinalStatementKind.UNKNOWN,
        ) and not self.limitations:
            raise ValueError(
                "non-fact statements require limitations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FinalStatement":
        _reject_unknown(
            payload,
            {"kind", "text", "evidence_refs", "limitations"},
            "FinalStatement",
        )
        return cls(
            kind=FinalStatementKind(payload["kind"]),
            text=_payload_string(payload, "text"),
            evidence_refs=_payload_text_tuple(
                payload,
                "evidence_refs",
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
        )


@dataclass(frozen=True)
class FinalEvidenceCitation:
    reference_id: str
    kind: FinalEvidenceKind
    title: str
    source_name: str
    published_at: str | None = None
    url: str | None = None
    page: str | None = None
    methodology: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.reference_id, "reference_id"),
            (self.title, "citation title"),
            (self.source_name, "citation source_name"),
        ):
            _text(value, name)
        for value, name in (
            (self.published_at, "published_at"),
            (self.url, "url"),
            (self.page, "page"),
            (self.methodology, "methodology"),
        ):
            _optional_text(value, name)
        if self.published_at is not None:
            date.fromisoformat(self.published_at)
        _texts(self.limitations, "citation limitations")
        if self.kind is FinalEvidenceKind.OFFICIAL:
            if (
                self.published_at is None
                or self.url is None
                or not re.match(
                    r"^https?://",
                    self.url,
                    flags=re.IGNORECASE,
                )
            ):
                raise ValueError(
                    "official citation requires date and HTTP URL"
                )
        if (
            self.kind is FinalEvidenceKind.REPORTED
            and self.published_at is None
        ):
            raise ValueError(
                "reported citation requires published_at"
            )
        if self.kind is FinalEvidenceKind.DERIVED and (
            self.methodology is None or not self.limitations
        ):
            raise ValueError(
                "derived citation requires methodology and limitations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_id": self.reference_id,
            "kind": self.kind.value,
            "title": self.title,
            "source_name": self.source_name,
            "published_at": self.published_at,
            "url": self.url,
            "page": self.page,
            "methodology": self.methodology,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalEvidenceCitation":
        _reject_unknown(
            payload,
            {
                "reference_id",
                "kind",
                "title",
                "source_name",
                "published_at",
                "url",
                "page",
                "methodology",
                "limitations",
            },
            "FinalEvidenceCitation",
        )
        return cls(
            reference_id=_payload_string(
                payload,
                "reference_id",
            ),
            kind=FinalEvidenceKind(payload["kind"]),
            title=_payload_string(payload, "title"),
            source_name=_payload_string(payload, "source_name"),
            published_at=_payload_optional_string(
                payload,
                "published_at",
            ),
            url=_payload_optional_string(payload, "url"),
            page=_payload_optional_string(payload, "page"),
            methodology=_payload_optional_string(
                payload,
                "methodology",
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
        )


@dataclass(frozen=True)
class FinalScenarioBlock:
    kind: FinalScenarioKind
    assumptions: tuple[str, ...]
    observable_variables: tuple[str, ...]
    decision_impact: str
    limitations: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _texts(
            self.assumptions,
            "scenario assumptions",
            required=True,
        )
        _texts(
            self.observable_variables,
            "scenario observable_variables",
            required=True,
        )
        _text(self.decision_impact, "scenario decision_impact")
        _texts(
            self.limitations,
            "scenario limitations",
            required=True,
        )
        _texts(self.evidence_refs, "scenario evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "assumptions": list(self.assumptions),
            "observable_variables": list(
                self.observable_variables
            ),
            "decision_impact": self.decision_impact,
            "limitations": list(self.limitations),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalScenarioBlock":
        _reject_unknown(
            payload,
            {
                "kind",
                "assumptions",
                "observable_variables",
                "decision_impact",
                "limitations",
                "evidence_refs",
            },
            "FinalScenarioBlock",
        )
        return cls(
            kind=FinalScenarioKind(payload["kind"]),
            assumptions=_payload_text_tuple(
                payload,
                "assumptions",
            ),
            observable_variables=_payload_text_tuple(
                payload,
                "observable_variables",
            ),
            decision_impact=_payload_string(
                payload,
                "decision_impact",
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
            evidence_refs=_payload_text_tuple(
                payload,
                "evidence_refs",
            ),
        )


@dataclass(frozen=True)
class FinalDecisionSection:
    lane: DecisionLane
    outcome: FinalDecisionOutcome
    summary: str
    statements: tuple[FinalStatement, ...]
    gaps: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.summary, "decision summary")
        _texts(self.gaps, "decision gaps")
        _texts(
            self.limitations,
            "decision limitations",
            required=True,
        )
        expected_prefix = {
            DecisionLane.SUITABILITY: "suitability_",
            DecisionLane.TIMING: "timing_",
            DecisionLane.POSITION_SIZING: "position_",
        }[self.lane]
        if not self.outcome.value.startswith(expected_prefix):
            raise ValueError(
                "decision outcome does not match decision lane"
            )
        if not self.statements and not self.gaps:
            raise ValueError(
                "decision section requires statements or gaps"
            )

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return _merge_texts(
            *(item.evidence_refs for item in self.statements)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "outcome": self.outcome.value,
            "summary": self.summary,
            "statements": [
                item.to_dict() for item in self.statements
            ],
            "gaps": list(self.gaps),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalDecisionSection":
        _reject_unknown(
            payload,
            {
                "lane",
                "outcome",
                "summary",
                "statements",
                "gaps",
                "limitations",
            },
            "FinalDecisionSection",
        )
        return cls(
            lane=DecisionLane(payload["lane"]),
            outcome=FinalDecisionOutcome(payload["outcome"]),
            summary=_payload_string(payload, "summary"),
            statements=tuple(
                FinalStatement.from_dict(item)
                for item in _payload_list(payload, "statements")
            ),
            gaps=_payload_text_tuple(payload, "gaps"),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
        )


@dataclass(frozen=True)
class FinalUserBoundary:
    decision_intent: DecisionIntent
    horizon: ResearchHorizon
    position_state: PositionState
    portfolio_role: str | None
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    missing_context: tuple[str, ...]

    def __post_init__(self) -> None:
        _optional_text(self.portfolio_role, "portfolio_role")
        _texts(self.constraints, "user constraints")
        _texts(self.assumptions, "user assumptions")
        _texts(self.missing_context, "missing_context")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_intent": self.decision_intent.value,
            "horizon": self.horizon.value,
            "position_state": self.position_state.value,
            "portfolio_role": self.portfolio_role,
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
            "missing_context": list(self.missing_context),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalUserBoundary":
        _reject_unknown(
            payload,
            {
                "decision_intent",
                "horizon",
                "position_state",
                "portfolio_role",
                "constraints",
                "assumptions",
                "missing_context",
            },
            "FinalUserBoundary",
        )
        return cls(
            decision_intent=DecisionIntent(
                payload["decision_intent"]
            ),
            horizon=ResearchHorizon(payload["horizon"]),
            position_state=PositionState(
                payload["position_state"]
            ),
            portfolio_role=_payload_optional_string(
                payload,
                "portfolio_role",
            ),
            constraints=_payload_text_tuple(
                payload,
                "constraints",
            ),
            assumptions=_payload_text_tuple(
                payload,
                "assumptions",
            ),
            missing_context=_payload_text_tuple(
                payload,
                "missing_context",
            ),
        )


@dataclass(frozen=True)
class MonitoringCondition:
    condition_id: str
    text: str
    kind: MonitoringConditionKind
    source: str
    observability_key: str | None
    review_by: str | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.condition_id, "condition_id"),
            (self.text, "monitoring condition text"),
            (self.source, "monitoring condition source"),
        ):
            _text(value, name)
        _optional_text(
            self.observability_key,
            "observability_key",
        )
        if self.review_by is not None:
            date.fromisoformat(self.review_by)
        _texts(self.evidence_refs, "monitoring evidence_refs")
        _texts(
            self.limitations,
            "monitoring limitations",
            required=True,
        )
        if (
            self.kind is MonitoringConditionKind.MACHINE_OBSERVABLE
            and self.observability_key is None
        ):
            raise ValueError(
                "machine-observable condition requires key"
            )
        if (
            self.kind is MonitoringConditionKind.MANUAL_REVIEW
            and self.observability_key is not None
        ):
            raise ValueError(
                "manual-review condition cannot carry machine key"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "text": self.text,
            "kind": self.kind.value,
            "source": self.source,
            "observability_key": self.observability_key,
            "review_by": self.review_by,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringCondition":
        _reject_unknown(
            payload,
            {
                "condition_id",
                "text",
                "kind",
                "source",
                "observability_key",
                "review_by",
                "evidence_refs",
                "limitations",
            },
            "MonitoringCondition",
        )
        return cls(
            condition_id=_payload_string(
                payload,
                "condition_id",
            ),
            text=_payload_string(payload, "text"),
            kind=MonitoringConditionKind(payload["kind"]),
            source=_payload_string(payload, "source"),
            observability_key=_payload_optional_string(
                payload,
                "observability_key",
            ),
            review_by=_payload_optional_string(
                payload,
                "review_by",
            ),
            evidence_refs=_payload_text_tuple(
                payload,
                "evidence_refs",
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
        )


@dataclass(frozen=True)
class MonitoringPlan:
    plan_id: str
    status: MonitoringPlanStatus
    review_date: str | None
    conditions: tuple[MonitoringCondition, ...]
    alert_rule_refs: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.plan_id, "monitoring plan_id")
        if self.review_date is not None:
            date.fromisoformat(self.review_date)
        _texts(self.alert_rule_refs, "alert_rule_refs")
        _texts(
            self.limitations,
            "monitoring plan limitations",
            required=True,
        )
        ids = tuple(item.condition_id for item in self.conditions)
        if len(ids) != len(set(ids)):
            raise ValueError(
                "monitoring condition ids must be unique"
            )
        if (
            self.status
            is MonitoringPlanStatus.USER_CONFIRMATION_REQUIRED
            and not self.conditions
        ):
            raise ValueError(
                "confirmation-required monitoring needs conditions"
            )
        if self.alert_rule_refs:
            raise ValueError(
                "Slice 10 monitoring cannot contain alert rules"
            )
        if monitoring_plan_id(self.to_dict()) != self.plan_id:
            raise ValueError(
                "monitoring plan_id does not match payload"
            )

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return _merge_texts(
            *(item.evidence_refs for item in self.conditions)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "status": self.status.value,
            "review_date": self.review_date,
            "conditions": [
                item.to_dict() for item in self.conditions
            ],
            "alert_rule_refs": list(self.alert_rule_refs),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringPlan":
        _reject_unknown(
            payload,
            {
                "plan_id",
                "status",
                "review_date",
                "conditions",
                "alert_rule_refs",
                "limitations",
            },
            "MonitoringPlan",
        )
        return cls(
            plan_id=_payload_string(payload, "plan_id"),
            status=MonitoringPlanStatus(payload["status"]),
            review_date=_payload_optional_string(
                payload,
                "review_date",
            ),
            conditions=tuple(
                MonitoringCondition.from_dict(item)
                for item in _payload_list(payload, "conditions")
            ),
            alert_rule_refs=_payload_text_tuple(
                payload,
                "alert_rule_refs",
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
        )


@dataclass(frozen=True)
class FinalChartArtifactReference:
    markdown_path: str
    content_digest: str
    metadata_digest: str
    data_digest: str

    def __post_init__(self) -> None:
        _validate_chart_paths((self.markdown_path,))
        for value, name in (
            (self.content_digest, "content_digest"),
            (self.metadata_digest, "metadata_digest"),
            (self.data_digest, "data_digest"),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"{name} must be a SHA256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "markdown_path": self.markdown_path,
            "content_digest": self.content_digest,
            "metadata_digest": self.metadata_digest,
            "data_digest": self.data_digest,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalChartArtifactReference":
        _reject_unknown(
            payload,
            {
                "markdown_path",
                "content_digest",
                "metadata_digest",
                "data_digest",
            },
            "FinalChartArtifactReference",
        )
        return cls(
            markdown_path=_payload_string(
                payload,
                "markdown_path",
            ),
            content_digest=_payload_string(
                payload,
                "content_digest",
            ),
            metadata_digest=_payload_string(
                payload,
                "metadata_digest",
            ),
            data_digest=_payload_string(
                payload,
                "data_digest",
            ),
        )


@dataclass(frozen=True)
class FinalDecisionResearchReport:
    report_id: str
    user_id: str
    workflow_run_id: str
    session_id: str
    decision_brief_id: str
    handoff_id: str
    suitability_assessment_id: str
    entry_plan_id: str
    position_plan_id: str
    asset_id: str
    profile_id: str
    asset_name: str
    as_of_date: str
    horizon: ResearchHorizon
    baseline_status: ResearchRunStatus
    baseline_report_path: str
    baseline_report_digest: str
    user_boundary: FinalUserBoundary
    thesis: FinalStatement
    decision_sections: tuple[FinalDecisionSection, ...]
    scenarios: tuple[FinalScenarioBlock, ...]
    strongest_counterargument: FinalStatement
    monitoring_plan: MonitoringPlan
    evidence_citations: tuple[FinalEvidenceCitation, ...]
    baseline_gaps: tuple[str, ...]
    key_unknowns: tuple[str, ...]
    chart_paths: tuple[str, ...]
    chart_artifacts: tuple[FinalChartArtifactReference, ...]
    limitations: tuple[str, ...]
    # Probability, filled before publication, that the consider/avoid claim holds.
    claim_probability: ProbabilityInterval | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.report_id, "report_id"),
            (self.user_id, "user_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (self.session_id, "session_id"),
            (self.decision_brief_id, "decision_brief_id"),
            (self.handoff_id, "handoff_id"),
            (
                self.suitability_assessment_id,
                "suitability_assessment_id",
            ),
            (self.entry_plan_id, "entry_plan_id"),
            (self.position_plan_id, "position_plan_id"),
            (self.asset_id, "asset_id"),
            (self.profile_id, "profile_id"),
            (self.asset_name, "asset_name"),
            (
                self.baseline_report_path,
                "baseline_report_path",
            ),
            (
                self.baseline_report_digest,
                "baseline_report_digest",
            ),
        ):
            _text(value, name)
        date.fromisoformat(self.as_of_date)
        if not re.fullmatch(
            r"[a-f0-9]{64}",
            self.baseline_report_digest,
        ):
            raise ValueError(
                "baseline_report_digest must be a SHA256 digest"
            )
        _validate_baseline_path(self.baseline_report_path)
        _texts(self.baseline_gaps, "baseline_gaps")
        _texts(self.key_unknowns, "key_unknowns")
        _texts(
            self.limitations,
            "final report limitations",
            required=True,
        )
        _validate_chart_paths(self.chart_paths)
        artifact_paths = tuple(
            item.markdown_path for item in self.chart_artifacts
        )
        if artifact_paths != self.chart_paths:
            raise ValueError(
                "chart artifacts must exactly cover chart paths"
            )
        lanes = tuple(item.lane for item in self.decision_sections)
        if set(lanes) != set(DecisionLane) or len(lanes) != 3:
            raise ValueError(
                "final report requires all three decision lanes"
            )
        scenario_kinds = tuple(item.kind for item in self.scenarios)
        if (
            set(scenario_kinds) != set(FinalScenarioKind)
            or len(scenario_kinds) != 3
        ):
            raise ValueError(
                "final report requires exactly three scenarios"
            )
        citation_ids = tuple(
            item.reference_id for item in self.evidence_citations
        )
        if (
            not citation_ids
            or len(citation_ids) != len(set(citation_ids))
        ):
            raise ValueError(
                "evidence citations must be non-empty and unique"
            )
        cutoff = date.fromisoformat(self.as_of_date)
        if any(
            item.published_at is not None
            and date.fromisoformat(item.published_at) > cutoff
            for item in self.evidence_citations
        ):
            raise ValueError(
                "evidence citation exceeds report cutoff"
            )
        used_refs = self.evidence_refs
        if set(used_refs) != set(citation_ids):
            missing = sorted(set(used_refs) - set(citation_ids))
            extra = sorted(set(citation_ids) - set(used_refs))
            raise ValueError(
                "evidence citations must exactly cover report refs; "
                f"uncited={missing}; unused={extra}"
            )
        if final_decision_report_id(self.to_dict()) != self.report_id:
            raise ValueError(
                "final report_id does not match payload"
            )
        if self.claim_probability is not None and not any(
            item.lane is DecisionLane.SUITABILITY
            and item.outcome
            in (
                FinalDecisionOutcome.SUITABILITY_CONSIDER,
                FinalDecisionOutcome.SUITABILITY_AVOID,
            )
            for item in self.decision_sections
        ):
            raise ValueError(
                "claim_probability only applies to consider or avoid"
            )

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return _merge_texts(
            self.thesis.evidence_refs,
            *(
                item.evidence_refs
                for item in self.decision_sections
            ),
            *(
                item.evidence_refs for item in self.scenarios
            ),
            self.strongest_counterargument.evidence_refs,
            self.monitoring_plan.evidence_refs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "user_id": self.user_id,
            "workflow_run_id": self.workflow_run_id,
            "session_id": self.session_id,
            "decision_brief_id": self.decision_brief_id,
            "handoff_id": self.handoff_id,
            "suitability_assessment_id": (
                self.suitability_assessment_id
            ),
            "entry_plan_id": self.entry_plan_id,
            "position_plan_id": self.position_plan_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "asset_name": self.asset_name,
            "as_of_date": self.as_of_date,
            "horizon": self.horizon.value,
            "baseline_status": self.baseline_status.value,
            "baseline_report_path": self.baseline_report_path,
            "baseline_report_digest": (
                self.baseline_report_digest
            ),
            "user_boundary": self.user_boundary.to_dict(),
            "thesis": self.thesis.to_dict(),
            "decision_sections": [
                item.to_dict() for item in self.decision_sections
            ],
            "scenarios": [
                item.to_dict() for item in self.scenarios
            ],
            "strongest_counterargument": (
                self.strongest_counterargument.to_dict()
            ),
            "monitoring_plan": self.monitoring_plan.to_dict(),
            "evidence_citations": [
                item.to_dict() for item in self.evidence_citations
            ],
            "baseline_gaps": list(self.baseline_gaps),
            "key_unknowns": list(self.key_unknowns),
            "chart_paths": list(self.chart_paths),
            "chart_artifacts": [
                item.to_dict() for item in self.chart_artifacts
            ],
            "limitations": list(self.limitations),
            "claim_probability": (
                None
                if self.claim_probability is None
                else to_payload(self.claim_probability)
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalDecisionResearchReport":
        allowed = {
            "report_id",
            "user_id",
            "workflow_run_id",
            "session_id",
            "decision_brief_id",
            "handoff_id",
            "suitability_assessment_id",
            "entry_plan_id",
            "position_plan_id",
            "asset_id",
            "profile_id",
            "asset_name",
            "as_of_date",
            "horizon",
            "baseline_status",
            "baseline_report_path",
            "baseline_report_digest",
            "user_boundary",
            "thesis",
            "decision_sections",
            "scenarios",
            "strongest_counterargument",
            "monitoring_plan",
            "evidence_citations",
            "baseline_gaps",
            "key_unknowns",
            "chart_paths",
            "chart_artifacts",
            "limitations",
            "claim_probability",
        }
        _reject_unknown(payload, allowed, "FinalDecisionResearchReport")
        return cls(
            report_id=_payload_string(payload, "report_id"),
            user_id=_payload_string(payload, "user_id"),
            workflow_run_id=_payload_string(
                payload,
                "workflow_run_id",
            ),
            session_id=_payload_string(payload, "session_id"),
            decision_brief_id=_payload_string(
                payload,
                "decision_brief_id",
            ),
            handoff_id=_payload_string(payload, "handoff_id"),
            suitability_assessment_id=_payload_string(
                payload,
                "suitability_assessment_id",
            ),
            entry_plan_id=_payload_string(
                payload,
                "entry_plan_id",
            ),
            position_plan_id=_payload_string(
                payload,
                "position_plan_id",
            ),
            asset_id=_payload_string(payload, "asset_id"),
            profile_id=_payload_string(payload, "profile_id"),
            asset_name=_payload_string(payload, "asset_name"),
            as_of_date=_payload_string(payload, "as_of_date"),
            horizon=ResearchHorizon(payload["horizon"]),
            baseline_status=ResearchRunStatus(
                payload["baseline_status"]
            ),
            baseline_report_path=_payload_string(
                payload,
                "baseline_report_path",
            ),
            baseline_report_digest=_payload_string(
                payload,
                "baseline_report_digest",
            ),
            user_boundary=FinalUserBoundary.from_dict(
                _payload_dict(payload, "user_boundary")
            ),
            thesis=FinalStatement.from_dict(
                _payload_dict(payload, "thesis")
            ),
            decision_sections=tuple(
                FinalDecisionSection.from_dict(item)
                for item in _payload_list(
                    payload,
                    "decision_sections",
                )
            ),
            scenarios=tuple(
                FinalScenarioBlock.from_dict(item)
                for item in _payload_list(payload, "scenarios")
            ),
            strongest_counterargument=FinalStatement.from_dict(
                _payload_dict(
                    payload,
                    "strongest_counterargument",
                )
            ),
            monitoring_plan=MonitoringPlan.from_dict(
                _payload_dict(payload, "monitoring_plan")
            ),
            evidence_citations=tuple(
                FinalEvidenceCitation.from_dict(item)
                for item in _payload_list(
                    payload,
                    "evidence_citations",
                )
            ),
            baseline_gaps=_payload_text_tuple(
                payload,
                "baseline_gaps",
            ),
            key_unknowns=_payload_text_tuple(
                payload,
                "key_unknowns",
            ),
            chart_paths=_payload_text_tuple(
                payload,
                "chart_paths",
            ),
            chart_artifacts=tuple(
                FinalChartArtifactReference.from_dict(item)
                for item in _payload_list(
                    payload,
                    "chart_artifacts",
                )
            ),
            limitations=_payload_text_tuple(
                payload,
                "limitations",
            ),
            claim_probability=(
                None
                if payload.get("claim_probability") is None
                else from_payload(
                    ProbabilityInterval,
                    payload["claim_probability"],
                )
            ),
        )


def monitoring_plan_id(payload: dict[str, Any]) -> str:
    return _content_id("monitoring", payload, "plan_id")


def final_decision_report_id(payload: dict[str, Any]) -> str:
    return _content_id("final-decision", payload, "report_id")


def _content_id(
    prefix: str,
    payload: dict[str, Any],
    id_field: str,
) -> str:
    canonical = dict(payload)
    canonical.pop(id_field, None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(encoded).hexdigest()[:24]


def _validate_baseline_path(value: str) -> None:
    path = PurePosixPath(value.replace("\\", "/"))
    if (
        path.is_absolute()
        or ":" in value
        or ".." in path.parts
        or path.suffix.lower() != ".md"
        or len(path.parts) != 1
        or "基础研究包" not in path.name
        or "完整决策报告" in path.name
    ):
        raise ValueError(
            "baseline report path must be one relative baseline file"
        )


def _validate_chart_paths(values: tuple[str, ...]) -> None:
    _texts(values, "chart_paths")
    for value in values:
        path = PurePosixPath(value.replace("\\", "/"))
        if (
            path.is_absolute()
            or ":" in value
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "assets"
            or path.suffix.lower()
            not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
        ):
            raise ValueError(
                "chart paths must be relative static assets"
            )


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    name: str,
) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unknown {name} fields: {sorted(unknown)}")


def _payload_string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _payload_optional_string(
    payload: dict[str, Any],
    key: str,
) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or None")
    return value


def _payload_list(
    payload: dict[str, Any],
    key: str,
) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


def _payload_dict(
    payload: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    value = payload[key]
    if not isinstance(value, dict):
        raise TypeError(f"{key} must be an object")
    return value


def _payload_text_tuple(
    payload: dict[str, Any],
    key: str,
) -> tuple[str, ...]:
    values = _payload_list(payload, key)
    if any(not isinstance(item, str) for item in values):
        raise TypeError(f"{key} must contain strings")
    return tuple(values)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _text(value, name)


def _texts(
    values: tuple[str, ...],
    name: str,
    *,
    required: bool = False,
) -> None:
    if required and not values:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _merge_texts(
    *groups: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in groups
            for item in group
            if item.strip()
        )
    )
