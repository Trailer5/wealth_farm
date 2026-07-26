"""Unified data access request, coverage, and response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.master_data import Asset


DATASET_STOCK_DAILY_BARS = "stock_daily_bars"
DATASET_EXCHANGE_FUND_DAILY_BARS = "fund_exchange_daily_bars"


@dataclass(frozen=True)
class AssetInput:
    """Asset id or external identity supplied by an upper layer."""

    asset_id: str | None = None
    source: str = "user:input"
    raw_symbol: str | None = None
    name: str | None = None
    asset_type_hint: str | None = None
    market_hint: str | None = None
    exchange_hint: str | None = None


@dataclass(frozen=True)
class DataAccessRequest:
    """One normalized data requirement."""

    asset: AssetInput
    dataset: str
    start_date: str
    end_date: str
    max_age_seconds: int | None = 86400
    allow_remote: bool = True
    allow_stale_on_error: bool = False
    remote_sources: tuple[str, ...] = ()
    provider_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class DataCoverage:
    """Recorded successful coverage for one backend and dataset."""

    asset_id: str
    dataset: str
    backend: str
    range_start: str
    range_end: str
    source: str
    checked_at: str


@dataclass(frozen=True)
class DataAccessResponse:
    """Data returned with identity, provenance, and fallback state."""

    asset: Asset
    dataset: str
    records: tuple[dict[str, Any], ...] = ()
    sources: tuple[str, ...] = ()
    coverage_start: str | None = None
    coverage_end: str | None = None
    complete: bool = False
    fetched_remote: bool = False
    stale: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
