"""Immutable contracts for reviewed research monitoring handoffs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from .final_decision_models import MonitoringPlan


class MonitoringConditionDisposition(str, Enum):
    RULE_CANDIDATE = "rule_candidate"
    MANUAL_REVIEW = "manual_review"
    UNSUPPORTED = "unsupported"


class MonitoringHandoffStatus(str, Enum):
    USER_CONFIRMATION_REQUIRED = "user_confirmation_required"
    ACTIVATION_PENDING = "activation_pending"
    ARMED = "armed"
    PARTIALLY_ARMED = "partially_armed"
    MANUAL_REVIEW_ONLY = "manual_review_only"
    UNSUPPORTED = "unsupported"
    REGISTRATION_FAILED = "registration_failed"


class MonitoringRuleType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    EVENT_TYPE = "event_type"
    MAX_WEIGHT_ABOVE = "max_weight_above"
    REVIEW_DATE = "review_date"


def monitoring_rule_matches_observability(
    rule_type: MonitoringRuleType,
    observability_key: str | None,
) -> bool:
    if observability_key is None:
        return False
    key = observability_key.strip().lower()
    if rule_type in {
        MonitoringRuleType.PRICE_ABOVE,
        MonitoringRuleType.PRICE_BELOW,
    }:
        return key in {
            "price",
            "market.price",
            "market.close",
            "close_price",
            rule_type.value,
        }
    if rule_type is MonitoringRuleType.EVENT_TYPE:
        return key in {"event", "event_type", "research.event"}
    if rule_type is MonitoringRuleType.MAX_WEIGHT_ABOVE:
        return key in {
            "max_weight",
            "portfolio.max_weight",
            "portfolio_weight",
        }
    return key in {"date", "review_date", "calendar.date"}


@dataclass(frozen=True)
class FinalDecisionPublicationAttestation:
    """Persisted proof that the reviewed final Markdown was published."""

    report_id: str
    user_id: str
    workflow_run_id: str
    session_id: str
    decision_brief_id: str
    baseline_handoff_id: str
    monitoring_plan_id: str
    asset_id: str
    profile_id: str
    as_of_date: str
    report_path: str
    markdown_digest: str
    monitoring_plan_payload_json: str
    published_at: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.report_id, "report_id"),
            (self.user_id, "user_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (self.session_id, "session_id"),
            (self.decision_brief_id, "decision_brief_id"),
            (self.baseline_handoff_id, "baseline_handoff_id"),
            (self.monitoring_plan_id, "monitoring_plan_id"),
            (self.asset_id, "asset_id"),
            (self.profile_id, "profile_id"),
            (self.report_path, "report_path"),
            (
                self.monitoring_plan_payload_json,
                "monitoring_plan_payload_json",
            ),
        ):
            _text(value, name)
        date.fromisoformat(self.as_of_date)
        datetime.fromisoformat(self.published_at)
        if (
            len(self.markdown_digest) != 64
            or any(
                item not in "0123456789abcdef"
                for item in self.markdown_digest
            )
        ):
            raise ValueError(
                "markdown_digest must be a SHA256 digest"
            )
        try:
            plan_payload = json.loads(
                self.monitoring_plan_payload_json
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "monitoring plan attestation must be valid JSON"
            ) from exc
        if not isinstance(plan_payload, dict):
            raise ValueError(
                "monitoring plan attestation must be an object"
            )
        plan = MonitoringPlan.from_dict(plan_payload)
        if plan.plan_id != self.monitoring_plan_id:
            raise ValueError(
                "attested monitoring plan ID does not match payload"
            )

    @property
    def monitoring_plan(self) -> MonitoringPlan:
        return MonitoringPlan.from_dict(
            json.loads(self.monitoring_plan_payload_json)
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "report_id": self.report_id,
            "user_id": self.user_id,
            "workflow_run_id": self.workflow_run_id,
            "session_id": self.session_id,
            "decision_brief_id": self.decision_brief_id,
            "baseline_handoff_id": self.baseline_handoff_id,
            "monitoring_plan_id": self.monitoring_plan_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "as_of_date": self.as_of_date,
            "report_path": self.report_path,
            "markdown_digest": self.markdown_digest,
            "monitoring_plan_payload_json": (
                self.monitoring_plan_payload_json
            ),
            "published_at": self.published_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "FinalDecisionPublicationAttestation":
        return cls(
            report_id=str(payload["report_id"]),
            user_id=str(payload["user_id"]),
            workflow_run_id=str(payload["workflow_run_id"]),
            session_id=str(payload["session_id"]),
            decision_brief_id=str(payload["decision_brief_id"]),
            baseline_handoff_id=str(
                payload["baseline_handoff_id"]
            ),
            monitoring_plan_id=str(
                payload["monitoring_plan_id"]
            ),
            asset_id=str(payload["asset_id"]),
            profile_id=str(payload["profile_id"]),
            as_of_date=str(payload["as_of_date"]),
            report_path=str(payload["report_path"]),
            markdown_digest=str(payload["markdown_digest"]),
            monitoring_plan_payload_json=str(
                payload["monitoring_plan_payload_json"]
            ),
            published_at=str(payload["published_at"]),
        )


@dataclass(frozen=True)
class MonitoringRuleBinding:
    """Explicit typed adapter input for one report condition."""

    condition_id: str
    rule_type: MonitoringRuleType
    threshold: float | None = None
    event_type: str | None = None
    review_date: str | None = None

    def __post_init__(self) -> None:
        _text(self.condition_id, "condition_id")
        _validate_rule_parameters(
            self.rule_type,
            threshold=self.threshold,
            event_type=self.event_type,
            review_date=self.review_date,
        )


@dataclass(frozen=True)
class MonitoringRuleCandidate:
    candidate_id: str
    condition_id: str
    rule_type: MonitoringRuleType
    subject_asset_id: str
    alert_asset_id: str | None
    condition_source: str
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    threshold: float | None = None
    event_type: str | None = None
    review_date: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.candidate_id, "candidate_id"),
            (self.condition_id, "condition_id"),
            (self.subject_asset_id, "subject_asset_id"),
            (self.condition_source, "condition_source"),
        ):
            _text(value, name)
        _optional_text(self.alert_asset_id, "alert_asset_id")
        _texts(self.evidence_refs, "evidence_refs")
        _texts(self.limitations, "limitations", required=True)
        _validate_rule_parameters(
            self.rule_type,
            threshold=self.threshold,
            event_type=self.event_type,
            review_date=self.review_date,
        )
        if (
            self.rule_type
            in {
                MonitoringRuleType.PRICE_ABOVE,
                MonitoringRuleType.PRICE_BELOW,
                MonitoringRuleType.EVENT_TYPE,
                MonitoringRuleType.REVIEW_DATE,
            }
            and self.alert_asset_id is None
        ):
            raise ValueError(
                "asset-scoped monitoring candidate requires alert_asset_id"
            )
        if (
            self.rule_type is MonitoringRuleType.MAX_WEIGHT_ABOVE
            and self.alert_asset_id is not None
        ):
            raise ValueError(
                "max-weight monitoring candidate is portfolio-scoped"
            )
        if monitoring_rule_candidate_id(self.to_dict()) != self.candidate_id:
            raise ValueError("candidate_id does not match payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "condition_id": self.condition_id,
            "rule_type": self.rule_type.value,
            "subject_asset_id": self.subject_asset_id,
            "alert_asset_id": self.alert_asset_id,
            "condition_source": self.condition_source,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "threshold": self.threshold,
            "event_type": self.event_type,
            "review_date": self.review_date,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringRuleCandidate":
        return cls(
            candidate_id=str(payload["candidate_id"]),
            condition_id=str(payload["condition_id"]),
            rule_type=MonitoringRuleType(payload["rule_type"]),
            subject_asset_id=str(payload["subject_asset_id"]),
            alert_asset_id=_optional_string(
                payload.get("alert_asset_id")
            ),
            condition_source=str(payload["condition_source"]),
            evidence_refs=_string_tuple(
                payload.get("evidence_refs", [])
            ),
            limitations=_string_tuple(
                payload.get("limitations", [])
            ),
            threshold=_optional_float(payload.get("threshold")),
            event_type=_optional_string(payload.get("event_type")),
            review_date=_optional_string(payload.get("review_date")),
        )


@dataclass(frozen=True)
class MonitoringConditionHandoff:
    condition_id: str
    text: str
    source: str
    disposition: MonitoringConditionDisposition
    review_by: str | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    candidate_id: str | None = None
    gap_code: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.condition_id, "condition_id"),
            (self.text, "condition text"),
            (self.source, "condition source"),
        ):
            _text(value, name)
        if self.review_by is not None:
            date.fromisoformat(self.review_by)
        _texts(self.evidence_refs, "evidence_refs")
        _texts(self.limitations, "limitations", required=True)
        _optional_text(self.candidate_id, "candidate_id")
        _optional_text(self.gap_code, "gap_code")
        if (
            self.disposition
            is MonitoringConditionDisposition.RULE_CANDIDATE
        ) != (self.candidate_id is not None):
            raise ValueError(
                "rule-candidate disposition must carry candidate_id"
            )
        if (
            self.disposition
            is MonitoringConditionDisposition.UNSUPPORTED
        ) != (self.gap_code is not None):
            raise ValueError(
                "unsupported disposition must carry gap_code"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "text": self.text,
            "source": self.source,
            "disposition": self.disposition.value,
            "review_by": self.review_by,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "candidate_id": self.candidate_id,
            "gap_code": self.gap_code,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringConditionHandoff":
        return cls(
            condition_id=str(payload["condition_id"]),
            text=str(payload["text"]),
            source=str(payload["source"]),
            disposition=MonitoringConditionDisposition(
                payload["disposition"]
            ),
            review_by=_optional_string(payload.get("review_by")),
            evidence_refs=_string_tuple(
                payload.get("evidence_refs", [])
            ),
            limitations=_string_tuple(
                payload.get("limitations", [])
            ),
            candidate_id=_optional_string(
                payload.get("candidate_id")
            ),
            gap_code=_optional_string(payload.get("gap_code")),
        )


@dataclass(frozen=True)
class MonitoringRuleRegistration:
    candidate_id: str
    rule_id: str
    activated_at: str

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        _text(self.rule_id, "rule_id")
        datetime.fromisoformat(self.activated_at)

    def to_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.candidate_id,
            "rule_id": self.rule_id,
            "activated_at": self.activated_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringRuleRegistration":
        return cls(
            candidate_id=str(payload["candidate_id"]),
            rule_id=str(payload["rule_id"]),
            activated_at=str(payload["activated_at"]),
        )


@dataclass(frozen=True)
class MonitoringGap:
    code: str
    message: str
    retryable: bool
    condition_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.code, "gap code")
        _text(self.message, "gap message")
        _optional_text(self.condition_id, "condition_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "condition_id": self.condition_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MonitoringGap":
        return cls(
            code=str(payload["code"]),
            message=str(payload["message"]),
            retryable=bool(payload["retryable"]),
            condition_id=_optional_string(
                payload.get("condition_id")
            ),
        )


@dataclass(frozen=True)
class MonitoringHandoff:
    handoff_id: str
    user_id: str
    workflow_run_id: str
    final_report_id: str
    monitoring_plan_id: str
    asset_id: str
    status: MonitoringHandoffStatus
    conditions: tuple[MonitoringConditionHandoff, ...]
    rule_candidates: tuple[MonitoringRuleCandidate, ...]
    confirmed_candidate_ids: tuple[str, ...]
    registrations: tuple[MonitoringRuleRegistration, ...]
    gaps: tuple[MonitoringGap, ...]
    created_at: str
    updated_at: str
    confirmed_at: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.handoff_id, "handoff_id"),
            (self.user_id, "user_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (self.final_report_id, "final_report_id"),
            (self.monitoring_plan_id, "monitoring_plan_id"),
            (self.asset_id, "asset_id"),
        ):
            _text(value, name)
        datetime.fromisoformat(self.created_at)
        datetime.fromisoformat(self.updated_at)
        if self.confirmed_at is not None:
            datetime.fromisoformat(self.confirmed_at)
        expected_id = monitoring_handoff_id(
            self.final_report_id,
            self.monitoring_plan_id,
        )
        if self.handoff_id != expected_id:
            raise ValueError("handoff_id does not match report and plan")
        condition_ids = tuple(
            item.condition_id for item in self.conditions
        )
        candidate_ids = tuple(
            item.candidate_id for item in self.rule_candidates
        )
        candidate_condition_ids = tuple(
            item.condition_id for item in self.rule_candidates
        )
        registration_candidates = tuple(
            item.candidate_id for item in self.registrations
        )
        for values, name in (
            (condition_ids, "condition IDs"),
            (candidate_ids, "candidate IDs"),
            (
                self.confirmed_candidate_ids,
                "confirmed candidate IDs",
            ),
            (registration_candidates, "registered candidate IDs"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        if not set(candidate_condition_ids).issubset(condition_ids):
            raise ValueError("rule candidates must reference conditions")
        if not set(self.confirmed_candidate_ids).issubset(
            candidate_ids
        ):
            raise ValueError("confirmed candidates are unknown")
        if not set(registration_candidates).issubset(
            self.confirmed_candidate_ids
        ):
            raise ValueError("registered candidates must be confirmed")
        condition_candidates = {
            item.candidate_id
            for item in self.conditions
            if item.candidate_id is not None
        }
        if condition_candidates != set(candidate_ids):
            raise ValueError(
                "condition handoffs must exactly cover rule candidates"
            )
        if self.confirmed_candidate_ids and self.confirmed_at is None:
            raise ValueError(
                "confirmed candidates require confirmation time"
            )
        self._validate_status(candidate_ids, registration_candidates)

    def _validate_status(
        self,
        candidate_ids: tuple[str, ...],
        registration_candidates: tuple[str, ...],
    ) -> None:
        candidates = set(candidate_ids)
        registrations = set(registration_candidates)
        manual = any(
            item.disposition
            is MonitoringConditionDisposition.MANUAL_REVIEW
            for item in self.conditions
        )
        if self.status is MonitoringHandoffStatus.ARMED and (
            not candidates or registrations != candidates
        ):
            raise ValueError("armed monitoring requires all rules")
        if (
            self.status is MonitoringHandoffStatus.PARTIALLY_ARMED
            and (
                not registrations
                or registrations == candidates
            )
        ):
            raise ValueError(
                "partially armed monitoring requires a rule subset"
            )
        if (
            self.status
            is MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED
            and (not candidates or registrations)
        ):
            raise ValueError(
                "confirmation-required monitoring needs unarmed candidates"
            )
        if self.status is MonitoringHandoffStatus.ACTIVATION_PENDING and (
            not candidates
            or not self.confirmed_candidate_ids
            or registrations == candidates
        ):
            raise ValueError(
                "activation-pending monitoring requires confirmed candidates "
                "with at least one unregistered rule"
            )
        if (
            self.status is MonitoringHandoffStatus.REGISTRATION_FAILED
            and (
                registrations
                or not any(
                    item.code == "alert_rule_registration_failed"
                    for item in self.gaps
                )
            )
        ):
            raise ValueError(
                "registration-failed status requires a failure gap"
            )
        if (
            self.status is MonitoringHandoffStatus.MANUAL_REVIEW_ONLY
            and (candidates or registrations or not manual)
        ):
            raise ValueError(
                "manual-review-only status cannot contain rules"
            )
        if self.status is MonitoringHandoffStatus.UNSUPPORTED and (
            candidates or registrations or manual
        ):
            raise ValueError(
                "unsupported monitoring cannot contain actionable items"
            )

    @property
    def alert_rule_refs(self) -> tuple[str, ...]:
        return tuple(item.rule_id for item in self.registrations)

    @property
    def gap_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.code for item in self.gaps))

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "user_id": self.user_id,
            "workflow_run_id": self.workflow_run_id,
            "final_report_id": self.final_report_id,
            "monitoring_plan_id": self.monitoring_plan_id,
            "asset_id": self.asset_id,
            "status": self.status.value,
            "conditions": [
                item.to_dict() for item in self.conditions
            ],
            "rule_candidates": [
                item.to_dict() for item in self.rule_candidates
            ],
            "confirmed_candidate_ids": list(
                self.confirmed_candidate_ids
            ),
            "registrations": [
                item.to_dict() for item in self.registrations
            ],
            "gaps": [item.to_dict() for item in self.gaps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confirmed_at": self.confirmed_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "MonitoringHandoff":
        return cls(
            handoff_id=str(payload["handoff_id"]),
            user_id=str(payload["user_id"]),
            workflow_run_id=str(payload["workflow_run_id"]),
            final_report_id=str(payload["final_report_id"]),
            monitoring_plan_id=str(payload["monitoring_plan_id"]),
            asset_id=str(payload["asset_id"]),
            status=MonitoringHandoffStatus(payload["status"]),
            conditions=tuple(
                MonitoringConditionHandoff.from_dict(item)
                for item in payload.get("conditions", [])
            ),
            rule_candidates=tuple(
                MonitoringRuleCandidate.from_dict(item)
                for item in payload.get("rule_candidates", [])
            ),
            confirmed_candidate_ids=_string_tuple(
                payload.get("confirmed_candidate_ids", [])
            ),
            registrations=tuple(
                MonitoringRuleRegistration.from_dict(item)
                for item in payload.get("registrations", [])
            ),
            gaps=tuple(
                MonitoringGap.from_dict(item)
                for item in payload.get("gaps", [])
            ),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            confirmed_at=_optional_string(
                payload.get("confirmed_at")
            ),
        )


def monitoring_handoff_id(
    final_report_id: str,
    monitoring_plan_id: str,
) -> str:
    _text(final_report_id, "final_report_id")
    _text(monitoring_plan_id, "monitoring_plan_id")
    encoded = (
        f"{final_report_id}\0{monitoring_plan_id}".encode("utf-8")
    )
    return "monitoring-handoff:" + hashlib.sha256(
        encoded
    ).hexdigest()[:24]


def monitoring_rule_candidate_id(
    payload: dict[str, Any],
) -> str:
    normalized = dict(payload)
    normalized["candidate_id"] = ""
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "monitor-candidate:" + hashlib.sha256(
        encoded
    ).hexdigest()[:24]


def _validate_rule_parameters(
    rule_type: MonitoringRuleType,
    *,
    threshold: float | None,
    event_type: str | None,
    review_date: str | None,
) -> None:
    if rule_type in {
        MonitoringRuleType.PRICE_ABOVE,
        MonitoringRuleType.PRICE_BELOW,
        MonitoringRuleType.MAX_WEIGHT_ABOVE,
    }:
        if (
            threshold is None
            or not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not math.isfinite(float(threshold))
        ):
            raise ValueError("threshold rule requires a finite value")
        if rule_type is MonitoringRuleType.MAX_WEIGHT_ABOVE:
            if threshold < 0 or threshold > 1:
                raise ValueError(
                    "max-weight threshold must be in [0, 1]"
                )
        elif threshold < 0:
            raise ValueError("price threshold must be non-negative")
        if event_type is not None or review_date is not None:
            raise ValueError("threshold rule has incompatible parameters")
        return
    if rule_type is MonitoringRuleType.EVENT_TYPE:
        _text(event_type, "event_type")
        if threshold is not None or review_date is not None:
            raise ValueError("event rule has incompatible parameters")
        return
    if rule_type is MonitoringRuleType.REVIEW_DATE:
        if review_date is None:
            raise ValueError("review-date rule requires a date")
        date.fromisoformat(review_date)
        if threshold is not None or event_type is not None:
            raise ValueError(
                "review-date rule has incompatible parameters"
            )


def _text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _optional_text(value: object, name: str) -> None:
    if value is not None:
        _text(value, name)


def _texts(
    values: tuple[str, ...],
    name: str,
    *,
    required: bool = False,
) -> None:
    if required and not values:
        raise ValueError(f"{name} must be non-empty")
    if any(
        not isinstance(item, str) or not item.strip()
        for item in values
    ):
        raise ValueError(f"{name} must contain non-empty strings")


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("serialized tuple must be a list")
    return tuple(str(item) for item in value)


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _optional_float(value: object) -> float | None:
    return float(value) if value is not None else None
