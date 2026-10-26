"""Unified data access request, coverage, and response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.financial_data import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
)
from src.master_data import Asset


DATASET_STOCK_DAILY_BARS = "stock_daily_bars"
DATASET_EXCHANGE_FUND_DAILY_BARS = "fund_exchange_daily_bars"
DATASET_INDEX_DAILY_BARS = "index_daily_bars"
# Unadjusted bars plus the exchange reference previous close (ex-rights).
DATASET_STOCK_TOTAL_RETURN_BARS = "stock_total_return_bars"
DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS = "fund_exchange_total_return_bars"
TOTAL_RETURN_DATASET_BY_BARS = {
    DATASET_STOCK_DAILY_BARS: DATASET_STOCK_TOTAL_RETURN_BARS,
    DATASET_EXCHANGE_FUND_DAILY_BARS: DATASET_EXCHANGE_FUND_TOTAL_RETURN_BARS,
}
TOTAL_RETURN_BAR_DATASETS = frozenset(TOTAL_RETURN_DATASET_BY_BARS.values())
DATASET_DISCLOSURES = "disclosures"
DATASET_RESEARCH_REPORTS = "research_reports"
DATASET_OPEN_FUND_NAV = "open_fund_nav"
DATASET_FUND_ESTIMATES = "fund_estimates"
DATASET_FUND_STATUS = "fund_status"
DATASET_FUND_SCALE = "fund_scale"
DATASET_FUND_ALLOCATION = "fund_allocation"
FUND_DATASETS = frozenset(
    {
        DATASET_OPEN_FUND_NAV,
        DATASET_FUND_ESTIMATES,
        DATASET_FUND_STATUS,
        DATASET_FUND_SCALE,
        DATASET_FUND_ALLOCATION,
    }
)


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
    as_of_date: str | None = None
    max_age_seconds: int | None = 86400
    allow_remote: bool = True
    allow_stale_on_error: bool = False
    remote_sources: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    provider_timeout_seconds: float = 30.0
    # "concurrent" calls every remote source at once and merges by priority;
    # "sequential" stops at the first source that returns records.
    fetch_mode: str = "concurrent"


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
