"""Versioned research universe and constituent models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class MembershipStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class ResearchUniverse:
    universe_id: str
    name: str
    index_asset_id: str
    source: str
    version: str
    acquired_at: str
    effective_date: str
    historical_membership: bool = False

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        datetime.fromisoformat(
            self.acquired_at.replace("Z", "+00:00")
        )
        for value, field_name in (
            (self.universe_id, "universe_id"),
            (self.name, "name"),
            (self.index_asset_id, "index_asset_id"),
            (self.source, "source"),
            (self.version, "version"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")


@dataclass(frozen=True)
class UniverseMembership:
    universe_id: str
    universe_version: str
    source_symbol: str
    source_name: str
    exchange: str
    effective_date: str
    status: MembershipStatus
    asset_id: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        if not self.source_symbol.strip():
            raise ValueError("source_symbol must not be blank")
        if (
            self.status is MembershipStatus.RESOLVED
            and self.asset_id is None
        ):
            raise ValueError(
                "Resolved membership requires asset_id"
            )
        if (
            self.status is MembershipStatus.UNRESOLVED
            and not self.reason.strip()
        ):
            raise ValueError(
                "Unresolved membership requires a reason"
            )


@dataclass(frozen=True)
class UniverseQualitySummary:
    total_source_records: int
    unique_source_symbols: int
    resolved_count: int
    unresolved_count: int
    inactive_count: int
    duplicate_count: int


@dataclass(frozen=True)
class UniverseSnapshot:
    universe: ResearchUniverse
    memberships: tuple[UniverseMembership, ...]
    quality: UniverseQualitySummary


class HistoricalUniverseUsageError(ValueError):
    """Raised when a current snapshot is misused as history."""
