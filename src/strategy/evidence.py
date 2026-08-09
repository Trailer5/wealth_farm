"""External evidence contracts and the strategy implementation gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal
from urllib.parse import urlsplit


SourceKind = Literal["paper", "repository", "documentation"]
LicenseStatus = Literal["verified", "restricted", "unknown"]
ReusePolicy = Literal[
    "adapt_allowed",
    "dependency_allowed",
    "reference_only",
    "rejected",
]
EvidenceRole = Literal["mechanism", "code", "validation", "counterevidence"]
EvidenceGateStatus = Literal["blocked", "implementation_ready"]

_SOURCE_KINDS = frozenset(("paper", "repository", "documentation"))
_LICENSE_STATUSES = frozenset(("verified", "restricted", "unknown"))
_REUSE_POLICIES = frozenset(
    (
        "adapt_allowed",
        "dependency_allowed",
        "reference_only",
        "rejected",
    )
)
_EVIDENCE_ROLES = frozenset(
    ("mechanism", "code", "validation", "counterevidence")
)
_REUSABLE_POLICIES = frozenset(("adapt_allowed", "dependency_allowed"))
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


@dataclass(frozen=True)
class StrategyEvidenceRecord:
    """A reviewed external source for one strategy and its mechanisms."""

    evidence_id: str
    strategy_id: str
    source_kind: SourceKind
    title: str
    canonical_url: str
    immutable_ref: str
    retrieved_at: str
    license_spdx: str | None
    license_status: LicenseStatus
    reuse_policy: ReusePolicy
    evidence_roles: tuple[EvidenceRole, ...]
    mechanism_ids: tuple[str, ...]
    reviewed_paths: tuple[str, ...]
    borrowable_mechanisms: tuple[str, ...]
    prohibited_transfers: tuple[str, ...]
    applicability: str
    known_biases: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_text(self.evidence_id, "evidence_id")
        _require_nonempty_text(self.strategy_id, "strategy_id")
        _require_nonempty_text(self.title, "title")
        _require_nonempty_text(self.immutable_ref, "immutable_ref")
        _require_nonempty_text(self.applicability, "applicability")

        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError(f"unsupported source_kind: {self.source_kind!r}")
        if self.license_status not in _LICENSE_STATUSES:
            raise ValueError(
                f"unsupported license_status: {self.license_status!r}"
            )
        if self.reuse_policy not in _REUSE_POLICIES:
            raise ValueError(f"unsupported reuse_policy: {self.reuse_policy!r}")

        _validate_https_url(self.canonical_url)
        _validate_iso_date(self.retrieved_at)
        _validate_string_tuple(
            self.evidence_roles,
            "evidence_roles",
            allowed_values=_EVIDENCE_ROLES,
            require_nonempty=True,
        )
        _validate_string_tuple(self.mechanism_ids, "mechanism_ids")
        _validate_string_tuple(self.reviewed_paths, "reviewed_paths")
        _validate_string_tuple(
            self.borrowable_mechanisms,
            "borrowable_mechanisms",
        )
        _validate_string_tuple(
            self.prohibited_transfers,
            "prohibited_transfers",
        )
        _validate_string_tuple(self.known_biases, "known_biases")

        if self.license_spdx is not None:
            _require_nonempty_text(self.license_spdx, "license_spdx")
        if self.license_status == "verified" and self.license_spdx is None:
            raise ValueError("verified license_status requires license_spdx")

        if (
            self.source_kind == "repository"
            and "code" in self.evidence_roles
            and not self.reviewed_paths
        ):
            raise ValueError(
                "repository code evidence requires reviewed_paths"
            )
        if (
            self.source_kind == "repository"
            and self.reuse_policy in _REUSABLE_POLICIES
        ):
            if self.license_status != "verified":
                raise ValueError(
                    "reusable repository evidence requires a verified license"
                )
            if not self.reviewed_paths:
                raise ValueError(
                    "reusable repository evidence requires reviewed_paths"
                )


@dataclass(frozen=True)
class StrategyEvidenceAssessment:
    """Deterministic result of the pre-implementation evidence gate."""

    strategy_id: str
    mechanism_ids: tuple[str, ...]
    status: EvidenceGateStatus
    missing_mechanism_ids: tuple[str, ...]
    paper_evidence_ids: tuple[str, ...]
    repository_code_evidence_ids: tuple[str, ...]
    reusable_repository_evidence_ids: tuple[str, ...]
    counterevidence_ids: tuple[str, ...]
    mismatched_evidence_ids: tuple[str, ...]
    blocking_reasons: tuple[str, ...]

    @property
    def implementation_ready(self) -> bool:
        return self.status == "implementation_ready"


class StrategyEvidenceGate:
    """Assess whether a strategy has enough external evidence to be coded."""

    @staticmethod
    def assess(
        strategy_id: str,
        mechanism_ids: Iterable[str],
        records: Iterable[StrategyEvidenceRecord],
    ) -> StrategyEvidenceAssessment:
        _require_nonempty_text(strategy_id, "strategy_id")
        requested_mechanisms = _normalized_unique_texts(
            mechanism_ids,
            "mechanism_ids",
        )
        supplied_records = tuple(records)
        if not all(
            isinstance(record, StrategyEvidenceRecord)
            for record in supplied_records
        ):
            raise TypeError(
                "records must contain only StrategyEvidenceRecord instances"
            )

        matching_records = tuple(
            record
            for record in supplied_records
            if record.strategy_id == strategy_id
        )
        mismatched_ids = tuple(
            record.evidence_id
            for record in supplied_records
            if record.strategy_id != strategy_id
        )
        requested_set = frozenset(requested_mechanisms)
        covered_mechanisms = {
            mechanism_id
            for record in matching_records
            for mechanism_id in record.mechanism_ids
            if mechanism_id in requested_set
        }
        missing_mechanisms = tuple(
            mechanism_id
            for mechanism_id in requested_mechanisms
            if mechanism_id not in covered_mechanisms
        )

        paper_ids = tuple(
            record.evidence_id
            for record in matching_records
            if record.source_kind == "paper"
            and "mechanism" in record.evidence_roles
            and bool(requested_set.intersection(record.mechanism_ids))
        )
        repository_code_ids = tuple(
            record.evidence_id
            for record in matching_records
            if record.source_kind == "repository"
            and "code" in record.evidence_roles
            and record.reuse_policy != "rejected"
            and bool(record.reviewed_paths)
            and bool(requested_set.intersection(record.mechanism_ids))
        )
        reusable_repository_ids = tuple(
            record.evidence_id
            for record in matching_records
            if record.source_kind == "repository"
            and "code" in record.evidence_roles
            and record.license_status == "verified"
            and record.reuse_policy in _REUSABLE_POLICIES
            and bool(requested_set.intersection(record.mechanism_ids))
        )
        counterevidence_ids = tuple(
            record.evidence_id
            for record in matching_records
            if "counterevidence" in record.evidence_roles
        )

        blocking_reasons: list[str] = []
        if not requested_mechanisms:
            blocking_reasons.append("mechanisms_not_declared")
        if missing_mechanisms:
            blocking_reasons.append("mechanisms_not_covered")
        if not paper_ids:
            blocking_reasons.append("paper_mechanism_source_missing")
        if not repository_code_ids:
            blocking_reasons.append("reviewed_repository_source_missing")
        if not counterevidence_ids:
            blocking_reasons.append("counterevidence_source_missing")

        return StrategyEvidenceAssessment(
            strategy_id=strategy_id,
            mechanism_ids=requested_mechanisms,
            status=(
                "blocked" if blocking_reasons else "implementation_ready"
            ),
            missing_mechanism_ids=missing_mechanisms,
            paper_evidence_ids=paper_ids,
            repository_code_evidence_ids=repository_code_ids,
            reusable_repository_evidence_ids=reusable_repository_ids,
            counterevidence_ids=counterevidence_ids,
            mismatched_evidence_ids=mismatched_ids,
            blocking_reasons=tuple(blocking_reasons),
        )


def _validate_iso_date(value: str) -> None:
    if not isinstance(value, str) or not _ISO_DATE_PATTERN.fullmatch(value):
        raise ValueError("retrieved_at must be an ISO date in YYYY-MM-DD form")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "retrieved_at must be an ISO date in YYYY-MM-DD form"
        ) from exc
    if parsed.isoformat() != value:
        raise ValueError("retrieved_at must be an ISO date in YYYY-MM-DD form")


def _validate_https_url(value: str) -> None:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ValueError("canonical_url must be an absolute https URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("canonical_url must be an absolute https URL")


def _require_nonempty_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty text")


def _validate_string_tuple(
    values: object,
    field_name: str,
    *,
    allowed_values: frozenset[str] | None = None,
    require_nonempty: bool = False,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if require_nonempty and not values:
        raise ValueError(f"{field_name} must not be empty")
    for value in values:
        _require_nonempty_text(value, field_name)
        if allowed_values is not None and value not in allowed_values:
            raise ValueError(f"unsupported {field_name} value: {value!r}")
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")


def _normalized_unique_texts(
    values: Iterable[str],
    field_name: str,
) -> tuple[str, ...]:
    try:
        supplied = tuple(values)
    except TypeError as exc:
        raise TypeError(f"{field_name} must be iterable") from exc
    _validate_string_tuple(supplied, field_name)
    return supplied
