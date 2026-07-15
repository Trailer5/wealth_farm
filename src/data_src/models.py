"""Shared data models returned by external data source adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DailyBar:
    """Normalized daily OHLCV record."""

    symbol: str
    trade_date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    amount: float | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SecurityInfo:
    """Normalized security or fund basic information."""

    symbol: str
    name: str | None
    security_type: str | None
    exchange: str | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataSourceStatus:
    """Data source availability and field completeness status."""

    source: str
    category: str
    status: str
    checked_at: str
    field_completeness: float | None = None
    record_count: int | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FundNav:
    """Normalized open-end fund NAV record."""

    symbol: str
    nav_date: str
    unit_nav: float | None
    accumulated_nav: float | None
    daily_growth_rate: float | None
    source: str
    estimated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FundEstimate:
    """Intraday or platform-estimated open-end fund value."""

    symbol: str
    estimate_date: str
    estimate_time: str | None
    estimated_nav: float | None
    estimated_growth_rate: float | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FundPurchaseStatus:
    """Open-end fund purchase and redemption status."""

    symbol: str
    name: str | None
    fund_type: str | None
    purchase_status: str | None
    redemption_status: str | None
    next_open_date: str | None
    min_purchase_amount: float | None
    daily_limit_amount: float | None
    fee_rate: float | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FundReportRecord:
    """Generic public fund portfolio, scale, or allocation record."""

    symbol: str
    report_type: str
    report_date: str
    item_name: str
    item_value: float | None
    ratio: float | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisclosureDocument:
    """Official disclosure announcement metadata."""

    document_id: str
    symbol: str
    title: str
    announcement_date: str
    category: str | None
    source: str
    file_url: str
    page_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DisclosureSourceCheck:
    """Cross-source check result for disclosure metadata and files."""

    symbol: str
    left_source: str
    right_source: str
    left_document_id: str
    right_document_id: str | None
    title_match: bool
    date_match: bool
    report_period_match: bool | None
    hash_match: bool | None
    left_title: str
    right_title: str | None
    left_date: str | None
    right_date: str | None
    left_sha256: str | None = None
    right_sha256: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResearchReport:
    """Third-party research report metadata."""

    report_id: str
    symbol: str | None
    title: str
    publish_date: str
    institution: str | None
    rating: str | None
    industry: str | None
    pdf_url: str | None
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadedFile:
    """Downloaded official disclosure file metadata."""

    document_id: str
    file_path: str
    file_url: str
    sha256: str
    size_bytes: int
    source: str
    status: str = "downloaded"
    error: str | None = None


@dataclass(frozen=True)
class DocumentText:
    """Extracted text from an official disclosure file."""

    document_id: str
    text_content: str
    extractor: str
    page_count: int | None
    status: str
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentTable:
    """Extracted table from an official disclosure file."""

    document_id: str
    table_index: int
    page_number: int | None
    rows: list[list[str | None]]
    extractor: str
    status: str
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinancialField:
    """Standard financial field extracted from disclosure text or tables."""

    document_id: str
    field_name: str
    field_label: str
    value: float | None
    unit: str | None
    source: str
    table_index: int | None = None
    row_index: int | None = None
    column_index: int | None = None
    page_number: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OCRQueueItem:
    """PDF file queued for OCR processing."""

    document_id: str
    file_path: str
    reason: str
    status: str
    attempts: int
    source: str
    max_attempts: int = 3
    next_attempt_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
