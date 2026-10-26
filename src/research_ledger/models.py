"""Append-only ledger records: entries, settlements and reviews."""

from __future__ import annotations

import hashlib
import json
from dataclasses import MISSING, dataclass, fields, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Union

from src.research_contracts import (
    Plan,
    SettleableAction,
    StateAssessment,
    View,
    from_payload,
    to_payload,
)

Artifact = Union[View, SettleableAction, StateAssessment, Plan]


class LedgerEntryType(str, Enum):
    ASSESSMENT = "assessment"
    VIEW = "view"
    ACTION = "action"
    PLAN = "plan"


class LedgerScope(str, Enum):
    OBJECTIVE = "objective"
    PERSONAL = "personal"


_ARTIFACT_TYPES: dict[LedgerEntryType, type] = {
    LedgerEntryType.ASSESSMENT: StateAssessment,
    LedgerEntryType.VIEW: View,
    LedgerEntryType.ACTION: SettleableAction,
    LedgerEntryType.PLAN: Plan,
}


class LedgerAccessError(PermissionError):
    """A personal ledger record was requested by another user."""


class LedgerConflictError(ValueError):
    """An append would change an already recorded fact."""


def content_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def entry_type_for(artifact: Artifact) -> LedgerEntryType:
    for entry_type, artifact_type in _ARTIFACT_TYPES.items():
        if isinstance(artifact, artifact_type):
            return entry_type
    raise TypeError(f"Unsupported ledger artifact: {type(artifact)!r}")


def ledger_entry_id(entry_type: LedgerEntryType, artifact_id: str) -> str:
    digest = hashlib.sha256(
        f"{entry_type.value}\0{artifact_id}".encode("utf-8")
    ).hexdigest()
    return f"ledger-{digest[:24]}"


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    entry_type: LedgerEntryType
    scope: LedgerScope
    user_id: str | None
    source_ref: str
    artifact_id: str
    subject_id: str
    as_of_date: str
    effective_until: str
    payload: dict[str, Any]
    content_digest: str
    supersedes_entry_id: str | None
    recorded_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.entry_type, LedgerEntryType):
            raise TypeError("entry_type must be LedgerEntryType")
        if not isinstance(self.scope, LedgerScope):
            raise TypeError("scope must be LedgerScope")
        if (self.scope is LedgerScope.PERSONAL) != (
            self.user_id is not None
        ):
            raise ValueError(
                "personal entries need user_id; objective entries none"
            )
        for value, name in (
            (self.source_ref, "source_ref"),
            (self.artifact_id, "artifact_id"),
            (self.subject_id, "subject_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.entry_id != ledger_entry_id(
            self.entry_type,
            self.artifact_id,
        ):
            raise ValueError("entry_id does not match artifact")
        if content_digest(self.payload) != self.content_digest:
            raise ValueError("content_digest does not match payload")
        if date.fromisoformat(self.effective_until) < date.fromisoformat(
            self.as_of_date
        ):
            raise ValueError("effective_until precedes as_of_date")
        datetime.fromisoformat(self.recorded_at)

    @property
    def artifact(self) -> Artifact:
        return from_payload(_ARTIFACT_TYPES[self.entry_type], self.payload)

    def visible_to(self, user_id: str | None) -> bool:
        return self.scope is LedgerScope.OBJECTIVE or (
            user_id is not None and user_id == self.user_id
        )

    def same_fact(self, other: "LedgerEntry") -> bool:
        return replace(other, recorded_at=self.recorded_at) == self


def build_entry(
    artifact: Artifact,
    *,
    scope: LedgerScope,
    user_id: str | None,
    source_ref: str,
    recorded_at: str,
    supersedes_entry_id: str | None = None,
) -> LedgerEntry:
    entry_type = entry_type_for(artifact)
    artifact_id, subject_id, as_of, until = _artifact_index(artifact)
    payload = to_payload(artifact)
    return LedgerEntry(
        entry_id=ledger_entry_id(entry_type, artifact_id),
        entry_type=entry_type,
        scope=scope,
        user_id=user_id,
        source_ref=source_ref,
        artifact_id=artifact_id,
        subject_id=subject_id,
        as_of_date=as_of,
        effective_until=until,
        payload=payload,
        content_digest=content_digest(payload),
        supersedes_entry_id=supersedes_entry_id,
        recorded_at=recorded_at,
    )


def _artifact_index(artifact: Artifact) -> tuple[str, str, str, str]:
    if isinstance(artifact, View):
        return (
            artifact.view_id,
            artifact.subject_id,
            artifact.as_of_date,
            artifact.expires_on,
        )
    if isinstance(artifact, SettleableAction):
        return (
            artifact.action_id,
            artifact.asset_id,
            artifact.trigger.valid_from,
            artifact.trigger.valid_until,
        )
    if isinstance(artifact, StateAssessment):
        return (
            artifact.assessment_id,
            artifact.subject_id,
            artifact.as_of_date,
            artifact.as_of_date,
        )
    dates = [
        artifact.as_of_date,
        *(item.trigger.valid_until for item in artifact.actions),
        *(item.review_on for item in artifact.review_nodes),
    ]
    return (
        artifact.plan_id,
        f"portfolio:{artifact.user_id}",
        artifact.as_of_date,
        max(dates),
    )


class SettlementStatus(str, Enum):
    SETTLED = "settled"
    DATA_INSUFFICIENT = "data_insufficient"
    NOT_SETTLEABLE = "not_settleable"


class ClaimResult(str, Enum):
    MET = "met"
    MISSED = "missed"
    NOT_DIRECTIONAL = "not_directional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Settlement:
    """One checkpoint observation; revisions append new settlements."""

    settlement_id: str
    entry_id: str
    checkpoint_date: str
    status: SettlementStatus
    claim_result: ClaimResult
    computed_at: str
    reference_date: str | None = None
    reference_value: Decimal | None = None
    observed_date: str | None = None
    observed_value: Decimal | None = None
    asset_return: Decimal | None = None
    benchmark_return: Decimal | None = None
    excess_return: Decimal | None = None
    alternative_return: Decimal | None = None
    max_adverse: Decimal | None = None
    max_favorable: Decimal | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.checkpoint_date)
        datetime.fromisoformat(self.computed_at)
        if not isinstance(self.status, SettlementStatus):
            raise TypeError("status must be SettlementStatus")
        if not isinstance(self.claim_result, ClaimResult):
            raise TypeError("claim_result must be ClaimResult")
        if self.status is SettlementStatus.SETTLED:
            # Metric settlements carry levels only; asset_return is price-only.
            required = (
                self.reference_date,
                self.reference_value,
                self.observed_date,
                self.observed_value,
            )
            if any(item is None for item in required):
                raise ValueError(
                    "settled checkpoints need reference and observation"
                )
            if self.claim_result is ClaimResult.UNKNOWN:
                raise ValueError("settled checkpoints need a claim result")
            if date.fromisoformat(self.observed_date) > date.fromisoformat(
                self.checkpoint_date
            ):
                raise ValueError("observation is after the checkpoint")
        else:
            if not self.note:
                raise ValueError("unsettled checkpoints need a note")
            if self.claim_result is not ClaimResult.UNKNOWN:
                raise ValueError("unsettled checkpoints have no result")
        if self.settlement_id != _content_id("settlement", self):
            raise ValueError("settlement_id does not match content")

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Settlement":
        return from_payload(cls, payload)


def new_settlement(**values: Any) -> Settlement:
    return Settlement(
        settlement_id=_content_id("settlement", values, Settlement),
        **values,
    )


class ReviewProcessClass(str, Enum):
    SOUND_PROCESS_FAVORABLE = "sound_process_favorable"
    SOUND_PROCESS_UNFAVORABLE = "sound_process_unfavorable"
    FLAWED_PROCESS_FAVORABLE = "flawed_process_favorable"
    FLAWED_PROCESS_UNFAVORABLE = "flawed_process_unfavorable"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class Review:
    review_id: str
    entry_id: str
    settlement_id: str
    process_class: ReviewProcessClass
    lessons: tuple[str, ...]
    recorded_at: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.process_class, ReviewProcessClass):
            raise TypeError("process_class must be ReviewProcessClass")
        if not self.lessons or any(
            not isinstance(item, str) or not item.strip()
            for item in self.lessons
        ):
            raise ValueError("reviews need non-blank lessons")
        datetime.fromisoformat(self.recorded_at)
        if self.review_id != _content_id("review", self):
            raise ValueError("review_id does not match content")

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Review":
        return from_payload(cls, payload)


def new_review(**values: Any) -> Review:
    return Review(
        review_id=_content_id("review", values, Review),
        **values,
    )


_UNHASHED_FIELDS = frozenset(
    {"settlement_id", "review_id", "computed_at", "recorded_at"}
)


def _content_id(
    prefix: str,
    source: Any,
    cls: type | None = None,
) -> str:
    """Hash every field except IDs and timestamps, defaults included."""

    target = cls or type(source)
    if isinstance(source, dict):
        values = {
            item.name: source.get(item.name, item.default)
            for item in fields(target)
        }
    else:
        values = {
            item.name: getattr(source, item.name)
            for item in fields(target)
        }
    payload = {
        name: to_payload(value)
        for name, value in values.items()
        if name not in _UNHASHED_FIELDS and value is not MISSING
    }
    return f"{prefix}-{content_digest(payload)[:24]}"
