"""Shared data models returned by external data source adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
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
    # Exchange reference close of the previous day, after any ex-rights adjustment.
    preclose: float | None = None


@dataclass(frozen=True)
class MacroObservation:
    """One value of a macro or rate series as reported by one source."""

    series_id: str
    # "YYYY-MM" monthly, "YYYY-Qn" quarterly, "YYYY-MM-DD" daily or event dates.
    period: str
    period_end: str
    value: float
    unit: str
    source: str
    # Only when the source states when the value became public.
    published_at: str | None = None
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
class IndexConstituent:
    """One constituent in a dated index snapshot."""

    index_symbol: str
    index_name: str
    constituent_symbol: str
    constituent_name: str
    exchange: str
    effective_date: str
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StockIndustryInfo:
    symbol: str
    name: str
    industry_name: str
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
class FundPurchaseFeeTier:
    """One front-end purchase fee tier; amounts in yuan, upper bound exclusive."""

    amount_text: str
    min_amount: float | None
    max_amount: float | None
    rate: Decimal | None
    discounted_rate: Decimal | None
    fixed_fee: float | None


@dataclass(frozen=True)
class FundRedemptionFeeTier:
    """One redemption fee tier by holding days; upper bound exclusive."""

    period_text: str
    min_days: int
    max_days: int | None
    rate: Decimal


@dataclass(frozen=True)
class FundManagerTerm:
    start_date: str
    end_date: str | None
    managers: tuple[str, ...]
    reported_return: str | None = None


@dataclass(frozen=True)
class OpenFundDealingInfo:
    """Current dealing rules, fees, managers and benchmark from fund F10 pages.

    These pages only show today's rules, so the record is dated by fetch day.
    """

    symbol: str
    fetched_on: str
    purchase_status: str | None
    redemption_status: str | None
    min_purchase_amount: float | None
    daily_purchase_limit: str | None
    purchase_confirm_days: int | None
    redemption_confirm_days: int | None
    management_fee_rate: Decimal | None
    custody_fee_rate: Decimal | None
    sales_service_fee_rate: Decimal | None
    purchase_fees: tuple[FundPurchaseFeeTier, ...]
    redemption_fees: tuple[FundRedemptionFeeTier, ...]
    manager_terms: tuple[FundManagerTerm, ...]
    benchmark_text: str | None
    tracking_target: str | None
    fund_type: str | None
    source: str
    source_urls: tuple[str, ...]
    parse_issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OpenFundDealingInfo":
        def rate(value: Any) -> Decimal | None:
            return None if value is None else Decimal(str(value))

        return cls(
            **{
                **payload,
                "management_fee_rate": rate(payload["management_fee_rate"]),
                "custody_fee_rate": rate(payload["custody_fee_rate"]),
                "sales_service_fee_rate": rate(payload["sales_service_fee_rate"]),
                "purchase_fees": tuple(
                    FundPurchaseFeeTier(
                        **{
                            **item,
                            "rate": rate(item["rate"]),
                            "discounted_rate": rate(item["discounted_rate"]),
                        }
                    )
                    for item in payload["purchase_fees"]
                ),
                "redemption_fees": tuple(
                    FundRedemptionFeeTier(**{**item, "rate": Decimal(str(item["rate"]))})
                    for item in payload["redemption_fees"]
                ),
                "manager_terms": tuple(
                    FundManagerTerm(**{**item, "managers": tuple(item["managers"])})
                    for item in payload["manager_terms"]
                ),
                "source_urls": tuple(payload["source_urls"]),
                "parse_issues": tuple(payload.get("parse_issues") or ()),
            }
        )


@dataclass(frozen=True)
class FundTopHolding:
    """One of a fund's ten largest stock holdings at a quarter end; ratio is a fraction of net assets."""

    period_end: str
    rank: int
    code: str
    name: str
    market: str
    net_asset_ratio: Decimal | None


@dataclass(frozen=True)
class FundAssetAllocationPeriod:
    """Stock, bond and cash as fractions of net assets at a quarter end."""

    period_end: str
    stock_ratio: Decimal | None
    bond_ratio: Decimal | None
    cash_ratio: Decimal | None
    net_assets_100m: Decimal | None


@dataclass(frozen=True)
class FundIndustryAllocationItem:
    """Industry share of net assets at a quarter end; scheme csrc for A shares, hk for Hong Kong (GICS-like)."""

    period_end: str
    code: str
    name: str
    scheme: str
    net_asset_ratio: Decimal


@dataclass(frozen=True)
class FundPortfolioDisclosure:
    """Quarterly allocation history and top-ten holdings as published in fund reports."""

    symbol: str
    fetched_on: str
    allocations: tuple[FundAssetAllocationPeriod, ...]
    holdings: tuple[FundTopHolding, ...]
    source: str
    source_urls: tuple[str, ...]
    parse_issues: tuple[str, ...] = ()
    industries: tuple[FundIndustryAllocationItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FundPortfolioDisclosure":
        def number(value: Any) -> Decimal | None:
            return None if value is None else Decimal(str(value))

        return cls(
            symbol=payload["symbol"],
            fetched_on=payload["fetched_on"],
            allocations=tuple(
                FundAssetAllocationPeriod(
                    period_end=item["period_end"],
                    stock_ratio=number(item["stock_ratio"]),
                    bond_ratio=number(item["bond_ratio"]),
                    cash_ratio=number(item["cash_ratio"]),
                    net_assets_100m=number(item["net_assets_100m"]),
                )
                for item in payload["allocations"]
            ),
            holdings=tuple(
                FundTopHolding(**{**item, "net_asset_ratio": number(item["net_asset_ratio"])})
                for item in payload["holdings"]
            ),
            source=payload["source"],
            source_urls=tuple(payload["source_urls"]),
            parse_issues=tuple(payload.get("parse_issues") or ()),
            industries=tuple(
                FundIndustryAllocationItem(**{**item, "net_asset_ratio": Decimal(str(item["net_asset_ratio"]))})
                for item in payload.get("industries") or ()
            ),
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


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
