"""Structured outputs for the independent suitability decision lane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from src.research_contracts import ResearchHorizon


class SuitabilityAction(str, Enum):
    CONSIDER = "consider"
    WATCH = "watch"
    AVOID = "avoid"
    HOLD_REVIEW = "hold_review"
    INSUFFICIENT = "insufficient"


class SuitabilityConfidence(str, Enum):
    SUPPORTED = "supported"
    BOUNDED = "bounded"
    INSUFFICIENT = "insufficient"


class SuitabilityClaimKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class SuitabilityClaimState(str, Enum):
    RECORDED = "recorded"
    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class SuitabilityDimension(str, Enum):
    EXPECTED_RETURN_SOURCE = "expected_return_source"
    PRICED_EXPECTATION = "priced_expectation"
    DOWNSIDE = "downside"
    ALTERNATIVE = "alternative"
    OPPORTUNITY_COST = "opportunity_cost"


class SuitabilityInputKind(str, Enum):
    COMPANY_FUNDAMENTALS = "company_fundamentals"
    FINANCIAL_INSTITUTION_QUALITY = (
        "financial_institution_quality"
    )
    CYCLE_ECONOMICS = "cycle_economics"
    GROWTH_RUNWAY = "growth_runway"
    ETF_EXPOSURE_EFFICIENCY = "etf_exposure_efficiency"
    OPEN_FUND_MANAGER_STYLE = "open_fund_manager_style"
    BENCHMARK_RELATIVE = "benchmark_relative"
    DOWNSIDE_RISK = "downside_risk"
    COMPARABLE_ALTERNATIVE = "comparable_alternative"


@dataclass(frozen=True)
class SuitabilityClaimBlock:
    claim_id: str
    text: str
    kind: SuitabilityClaimKind
    state: SuitabilityClaimState
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    time_horizon: ResearchHorizon
    as_of_date: str
    subject_asset_id: str | None = None
    comparable_exposure: str | None = None
    source_finding_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.claim_id, "claim_id")
        _text(self.text, "text")
        date.fromisoformat(self.as_of_date)
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.limitations, "limitations")
        _unique_texts(
            self.source_finding_ids,
            "source_finding_ids",
        )
        _optional_text(
            self.subject_asset_id,
            "subject_asset_id",
        )
        _optional_text(
            self.comparable_exposure,
            "comparable_exposure",
        )
        if not self.limitations:
            raise ValueError(
                "suitability claim requires limitations"
            )
        if not self.evidence_refs:
            raise ValueError(
                "suitability claim requires evidence_refs"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "kind": self.kind.value,
            "state": self.state.value,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "time_horizon": self.time_horizon.value,
            "as_of_date": self.as_of_date,
            "subject_asset_id": self.subject_asset_id,
            "comparable_exposure": self.comparable_exposure,
            "source_finding_ids": list(
                self.source_finding_ids
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "SuitabilityClaimBlock":
        return cls(
            claim_id=str(payload["claim_id"]),
            text=str(payload["text"]),
            kind=SuitabilityClaimKind(payload["kind"]),
            state=SuitabilityClaimState(payload["state"]),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
            time_horizon=ResearchHorizon(
                payload["time_horizon"]
            ),
            as_of_date=str(payload["as_of_date"]),
            subject_asset_id=_optional_string(
                payload.get("subject_asset_id")
            ),
            comparable_exposure=_optional_string(
                payload.get("comparable_exposure")
            ),
            source_finding_ids=tuple(
                str(item)
                for item in payload.get(
                    "source_finding_ids",
                    (),
                )
            ),
        )


@dataclass(frozen=True)
class SuitabilityProfilePolicy:
    profile_id: str
    policy_id: str
    core_input_kinds: tuple[SuitabilityInputKind, ...]
    positive_input_kinds: tuple[SuitabilityInputKind, ...]
    return_source_basis: str
    priced_expectations_basis: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.profile_id, "profile_id"),
            (self.policy_id, "policy_id"),
            (self.return_source_basis, "return_source_basis"),
            (
                self.priced_expectations_basis,
                "priced_expectations_basis",
            ),
        ):
            _text(value, name)
        _unique(self.core_input_kinds, "core_input_kinds")
        _unique(
            self.positive_input_kinds,
            "positive_input_kinds",
        )


@dataclass(frozen=True)
class SuitabilityAssessment:
    assessment_id: str
    session_id: str
    workflow_run_id: str | None
    decision_brief_id: str | None
    asset_id: str
    profile_id: str
    policy_id: str
    as_of_date: str
    time_horizon: ResearchHorizon
    action: SuitabilityAction
    confidence: SuitabilityConfidence
    asset_role: str | None
    thesis: SuitabilityClaimBlock | None
    expected_return_sources: tuple[
        SuitabilityClaimBlock,
        ...,
    ]
    priced_expectations: tuple[
        SuitabilityClaimBlock,
        ...,
    ]
    alternatives: tuple[SuitabilityClaimBlock, ...]
    opportunity_costs: tuple[
        SuitabilityClaimBlock,
        ...,
    ]
    downside: tuple[SuitabilityClaimBlock, ...]
    strongest_counterargument: SuitabilityClaimBlock | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    assembly_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.assessment_id, "assessment_id"),
            (self.session_id, "session_id"),
            (self.asset_id, "asset_id"),
            (self.profile_id, "profile_id"),
            (self.policy_id, "policy_id"),
        ):
            _text(value, name)
        _optional_text(
            self.workflow_run_id,
            "workflow_run_id",
        )
        _optional_text(
            self.decision_brief_id,
            "decision_brief_id",
        )
        _optional_text(self.asset_role, "asset_role")
        date.fromisoformat(self.as_of_date)
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.limitations, "limitations")
        _unique_texts(self.assembly_gaps, "assembly_gaps")
        if not self.limitations:
            raise ValueError(
                "suitability assessment requires limitations"
            )
        blocks = self.claim_blocks
        block_refs = {
            ref
            for block in blocks
            for ref in block.evidence_refs
        }
        if not block_refs.issubset(set(self.evidence_refs)):
            raise ValueError(
                "assessment evidence_refs must cover claim blocks"
            )
        if self.action is not SuitabilityAction.INSUFFICIENT:
            if (
                self.thesis is None
                or self.strongest_counterargument is None
            ):
                raise ValueError(
                    "decisive suitability action requires thesis "
                    "and counterargument"
                )
        if self.action is SuitabilityAction.CONSIDER and any(
            not values
            for values in (
                self.expected_return_sources,
                self.priced_expectations,
                self.alternatives,
                self.opportunity_costs,
                self.downside,
            )
        ):
            raise ValueError(
                "consider action requires every suitability section"
            )
        if self.action is SuitabilityAction.CONSIDER:
            if self.assembly_gaps or self.asset_role is None:
                raise ValueError(
                    "consider action requires role and no assembly gaps"
                )
        if (
            self.action is SuitabilityAction.INSUFFICIENT
            and self.confidence
            is not SuitabilityConfidence.INSUFFICIENT
        ):
            raise ValueError(
                "insufficient action requires insufficient confidence"
            )
        expected_confidence = (
            SuitabilityConfidence.INSUFFICIENT
            if self.action is SuitabilityAction.INSUFFICIENT
            else (
                SuitabilityConfidence.BOUNDED
                if self.assembly_gaps
                or self.action
                in (
                    SuitabilityAction.WATCH,
                    SuitabilityAction.HOLD_REVIEW,
                )
                else SuitabilityConfidence.SUPPORTED
            )
        )
        if self.confidence is not expected_confidence:
            raise ValueError(
                "suitability confidence does not match action "
                "and assembly gaps"
            )
        if (
            self.action is SuitabilityAction.INSUFFICIENT
            and not self.assembly_gaps
        ):
            raise ValueError(
                "insufficient action requires assembly gaps"
            )
        expected_id = suitability_assessment_id(
            self.to_dict()
        )
        if self.assessment_id != expected_id:
            raise ValueError(
                "suitability assessment_id does not match content"
            )

    @property
    def claim_blocks(
        self,
    ) -> tuple[SuitabilityClaimBlock, ...]:
        return (
            *((self.thesis,) if self.thesis is not None else ()),
            *self.expected_return_sources,
            *self.priced_expectations,
            *self.alternatives,
            *self.opportunity_costs,
            *self.downside,
            *(
                (self.strongest_counterargument,)
                if self.strongest_counterargument is not None
                else ()
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assessment_id": self.assessment_id,
            "session_id": self.session_id,
            "workflow_run_id": self.workflow_run_id,
            "decision_brief_id": self.decision_brief_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "policy_id": self.policy_id,
            "as_of_date": self.as_of_date,
            "time_horizon": self.time_horizon.value,
            "action": self.action.value,
            "confidence": self.confidence.value,
            "asset_role": self.asset_role,
            "thesis": (
                self.thesis.to_dict()
                if self.thesis is not None
                else None
            ),
            "expected_return_sources": [
                item.to_dict()
                for item in self.expected_return_sources
            ],
            "priced_expectations": [
                item.to_dict()
                for item in self.priced_expectations
            ],
            "alternatives": [
                item.to_dict() for item in self.alternatives
            ],
            "opportunity_costs": [
                item.to_dict()
                for item in self.opportunity_costs
            ],
            "downside": [
                item.to_dict() for item in self.downside
            ],
            "strongest_counterargument": (
                self.strongest_counterargument.to_dict()
                if self.strongest_counterargument is not None
                else None
            ),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "assembly_gaps": list(self.assembly_gaps),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "SuitabilityAssessment":
        thesis = payload.get("thesis")
        counterargument = payload.get(
            "strongest_counterargument"
        )
        return cls(
            assessment_id=str(payload["assessment_id"]),
            session_id=str(payload["session_id"]),
            workflow_run_id=_optional_string(
                payload.get("workflow_run_id")
            ),
            decision_brief_id=_optional_string(
                payload.get("decision_brief_id")
            ),
            asset_id=str(payload["asset_id"]),
            profile_id=str(payload["profile_id"]),
            policy_id=str(payload["policy_id"]),
            as_of_date=str(payload["as_of_date"]),
            time_horizon=ResearchHorizon(
                payload["time_horizon"]
            ),
            action=SuitabilityAction(payload["action"]),
            confidence=SuitabilityConfidence(
                payload["confidence"]
            ),
            asset_role=_optional_string(
                payload.get("asset_role")
            ),
            thesis=(
                SuitabilityClaimBlock.from_dict(thesis)
                if thesis is not None
                else None
            ),
            expected_return_sources=_claim_blocks(
                payload,
                "expected_return_sources",
            ),
            priced_expectations=_claim_blocks(
                payload,
                "priced_expectations",
            ),
            alternatives=_claim_blocks(
                payload,
                "alternatives",
            ),
            opportunity_costs=_claim_blocks(
                payload,
                "opportunity_costs",
            ),
            downside=_claim_blocks(payload, "downside"),
            strongest_counterargument=(
                SuitabilityClaimBlock.from_dict(
                    counterargument
                )
                if counterargument is not None
                else None
            ),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
            assembly_gaps=tuple(
                str(item)
                for item in payload.get("assembly_gaps", ())
            ),
        )


def _claim_blocks(
    payload: dict[str, Any],
    key: str,
) -> tuple[SuitabilityClaimBlock, ...]:
    return tuple(
        SuitabilityClaimBlock.from_dict(item)
        for item in payload.get(key, ())
    )


def suitability_assessment_id(
    payload: dict[str, Any],
) -> str:
    canonical = dict(payload)
    canonical.pop("assessment_id", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        "suitability:"
        + hashlib.sha256(encoded).hexdigest()[:24]
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


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


def _unique(values: tuple[object, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
