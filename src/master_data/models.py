"""Data models for master asset identity resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ASSET_TYPE_STOCK = "stock"
ASSET_TYPE_EXCHANGE_FUND = "exchange_fund"
ASSET_TYPE_OPEN_FUND = "open_fund"
ASSET_TYPE_INDEX = "index"
ASSET_TYPE_UNKNOWN = "unknown"

RESOLUTION_MATCHED = "matched"
RESOLUTION_CREATED = "created"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_UNRESOLVED = "unresolved"
RESOLUTION_CONFIRMED = "confirmed"
RESOLUTION_DISMISSED = "dismissed"


@dataclass(frozen=True)
class Asset:
    """Canonical project asset identity."""

    asset_id: str
    asset_type: str
    standard_symbol: str
    display_name: str | None = None
    market: str | None = None
    exchange: str | None = None
    status: str = "active"


@dataclass(frozen=True)
class AssetIdentifier:
    """Identifier that links a source or standard symbol to an asset."""

    asset_id: str
    source: str
    raw_symbol: str
    standard_symbol: str
    name: str | None = None
    asset_type: str | None = None
    market: str | None = None
    exchange: str | None = None
    identifier_type: str = "source_symbol"
    confidence: float = 1.0
    active: bool = True


@dataclass(frozen=True)
class AssetSourceObservation:
    """One observed external identity from a provider or user import."""

    source: str
    raw_symbol: str
    standard_symbol: str | None
    name: str | None = None
    asset_type_hint: str | None = None
    market_hint: str | None = None
    exchange_hint: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetMatchCandidate:
    """Candidate match found during identity resolution."""

    asset_id: str
    score: float
    reason: str


@dataclass(frozen=True)
class AssetResolution:
    """Result of resolving an observed external identity."""

    status: str
    observation: AssetSourceObservation
    asset: Asset | None = None
    candidates: tuple[AssetMatchCandidate, ...] = ()
    identifier: AssetIdentifier | None = None
    confidence: float = 0.0
    needs_review: bool = False
    reason: str = ""


@dataclass(frozen=True)
class StoredAssetObservation:
    """Persisted source observation and its resolution outcome."""

    observation_id: int
    observation: AssetSourceObservation
    resolution_status: str
    resolved_asset_id: str | None
    candidates: tuple[AssetMatchCandidate, ...] = ()
    confidence: float = 0.0
    needs_review: bool = False
    reason: str = ""
    observed_at: str = ""


@dataclass(frozen=True)
class AssetIdentifierRebinding:
    """Explicit correction that moves one source identifier to another asset."""

    rebinding_id: int
    source: str
    raw_symbol: str
    old_asset_id: str
    new_asset_id: str
    reason: str
    observation_id: int | None = None
    rebound_at: str = ""
