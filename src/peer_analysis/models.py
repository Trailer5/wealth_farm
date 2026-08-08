"""Industry assignments, peer sets, and comparison outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class PeerComparisonStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class IndustryAssignment:
    asset_id: str
    industry_code: str
    industry_name: str
    classification_source: str
    classification_version: str
    effective_date: str

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.industry_code, "industry_code"),
            (self.industry_name, "industry_name"),
            (
                self.classification_source,
                "classification_source",
            ),
            (
                self.classification_version,
                "classification_version",
            ),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True)
class PeerSetMember:
    asset_id: str
    included: bool
    reason: str
    cycle_note: str = ""

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.reason.strip():
            raise ValueError(
                "Peer member asset_id and reason are required"
            )


@dataclass(frozen=True)
class PeerSet:
    peer_set_id: str
    target_asset_id: str
    name: str
    source: str
    version: str
    effective_date: str
    members: tuple[PeerSetMember, ...]

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        if not self.peer_set_id.strip() or not self.name.strip():
            raise ValueError("Peer set identity and name are required")
        if not self.members:
            raise ValueError("Peer set members must not be empty")
        member_ids = [member.asset_id for member in self.members]
        if len(set(member_ids)) != len(member_ids):
            raise ValueError("Peer set members must be unique")
        if self.target_asset_id in member_ids:
            raise ValueError(
                "Target asset must not appear in peer members"
            )
        if not any(member.included for member in self.members):
            raise ValueError(
                "Peer set requires at least one included member"
            )


@dataclass(frozen=True)
class PeerMetricValue:
    asset_id: str
    value: Decimal
    unit: str
    report_period: str
    as_of_date: str


@dataclass(frozen=True)
class PeerMetricComparison:
    metric_name: str
    status: PeerComparisonStatus
    target: PeerMetricValue | None
    peers: tuple[PeerMetricValue, ...]
    sample_count: int
    median: Decimal | None
    mean: Decimal | None
    standard_deviation: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    target_percentile: Decimal | None
    target_vs_median: Decimal | None
    excluded_assets: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PeerComparisonBundle:
    peer_set: PeerSet
    industry: IndustryAssignment | None
    as_of_date: str
    comparisons: tuple[PeerMetricComparison, ...]
    chart_data: dict[str, Any]


@dataclass(frozen=True)
class PeerMetricExecutionResult:
    peer_set: PeerSet
    common_report_period: str
    prepared_asset_ids: tuple[str, ...]
    comparison: PeerComparisonBundle
