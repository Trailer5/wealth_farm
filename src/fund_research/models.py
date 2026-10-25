"""Research bundles and relationships for funds and ETFs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.factor_engineering import FactorValue
from src.master_data import Asset


class FundRelationType(str, Enum):
    MANAGER = "manager"
    UNDERLYING_INDEX = "underlying_index"


class DisclosureExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    PARTIAL = "partial"
    EXTRACTION_FAILED = "extraction_failed"


@dataclass(frozen=True)
class OpenFundManagerTenure:
    name: str
    start_date: str
    page_number: int
    end_date: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("manager name must not be blank")
        date.fromisoformat(self.start_date)
        if self.end_date is not None:
            date.fromisoformat(self.end_date)
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundProfileSnapshot:
    fund_asset_id: str
    effective_date: str
    announcement_date: str
    fund_name: str
    share_class_name: str
    share_class_code: str
    manager_name: str
    custodian_name: str
    fund_managers: tuple[OpenFundManagerTenure, ...]
    benchmark_text: str
    management_fee_rate: Decimal | None
    custody_fee_rate: Decimal | None
    sales_service_fee_rate: Decimal | None
    source_document_id: str
    source_title: str
    source_url: str
    source: str
    parser_version: str
    extraction_status: DisclosureExtractionStatus
    page_numbers: dict[str, int] = field(default_factory=dict)
    extraction_errors: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_snapshot_identity(
            self.fund_asset_id,
            self.source_document_id,
            self.source_title,
            self.source_url,
            self.source,
            self.parser_version,
        )
        date.fromisoformat(self.effective_date)
        date.fromisoformat(self.announcement_date)
        if self.effective_date > self.announcement_date:
            raise ValueError(
                "profile effective_date must not follow announcement_date"
            )
        for value in (
            self.management_fee_rate,
            self.custody_fee_rate,
            self.sales_service_fee_rate,
        ):
            _validate_optional_ratio(value, "profile fee rate")
        for page_number in self.page_numbers.values():
            _validate_page_number(page_number)
        manager_names = [item.name for item in self.fund_managers]
        if len(manager_names) != len(set(manager_names)):
            raise ValueError("profile managers must be unique")


@dataclass(frozen=True)
class OpenFundShareClassSnapshot:
    share_class_name: str
    share_class_code: str
    ending_shares: Decimal
    unit_nav: Decimal | None
    net_asset_value: Decimal | None
    page_number: int

    def __post_init__(self) -> None:
        if (
            not self.share_class_name.strip()
            or not self.share_class_code.strip()
        ):
            raise ValueError("share class name and code are required")
        if self.ending_shares < 0:
            raise ValueError("ending_shares must not be negative")
        if self.unit_nav is not None and self.unit_nav <= 0:
            raise ValueError("unit_nav must be positive")
        if self.net_asset_value is not None and self.net_asset_value < 0:
            raise ValueError("net_asset_value must not be negative")
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundShareFlow:
    share_class_name: str
    share_class_code: str
    opening_shares: Decimal
    subscriptions: Decimal
    redemptions: Decimal
    ending_shares: Decimal
    page_number: int

    def __post_init__(self) -> None:
        values = (
            self.opening_shares,
            self.subscriptions,
            self.redemptions,
            self.ending_shares,
        )
        if any(value < 0 for value in values):
            raise ValueError("share-flow values must not be negative")
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundAssetAllocation:
    category: str
    amount: Decimal
    ratio: Decimal
    basis: str
    page_number: int

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.basis.strip():
            raise ValueError("allocation category and basis are required")
        if self.amount < 0:
            raise ValueError("allocation amount must not be negative")
        _validate_ratio(self.ratio, "allocation ratio")
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundIndustryAllocation:
    category: str
    fair_value: Decimal
    net_asset_ratio: Decimal
    market_scope: str
    portfolio_scope: str
    page_number: int

    def __post_init__(self) -> None:
        if (
            not self.category.strip()
            or not self.market_scope.strip()
            or not self.portfolio_scope.strip()
        ):
            raise ValueError(
                "industry category and scopes are required"
            )
        if self.fair_value < 0:
            raise ValueError("industry fair_value must not be negative")
        _validate_ratio(
            self.net_asset_ratio,
            "industry net-asset ratio",
        )
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundHolding:
    rank: int
    security_code: str
    security_name: str
    quantity: Decimal
    fair_value: Decimal
    net_asset_ratio: Decimal
    page_number: int

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise ValueError("holding rank must be positive")
        if (
            not self.security_code.strip()
            or not self.security_name.strip()
        ):
            raise ValueError("holding security identity is required")
        if self.quantity < 0 or self.fair_value < 0:
            raise ValueError(
                "holding quantity and fair_value must not be negative"
            )
        _validate_ratio(
            self.net_asset_ratio,
            "holding net-asset ratio",
        )
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundPerformanceComparison:
    period_label: str
    fund_return: Decimal
    benchmark_return: Decimal
    tracking_difference: Decimal
    page_number: int
    share_class_name: str = ""
    share_class_code: str = ""

    def __post_init__(self) -> None:
        if not self.period_label.strip():
            raise ValueError("performance comparison period is required")
        _validate_page_number(self.page_number)


@dataclass(frozen=True)
class OpenFundPeriodicSnapshot:
    fund_asset_id: str
    report_period_start: str
    report_period_end: str
    announcement_date: str
    share_classes: tuple[OpenFundShareClassSnapshot, ...]
    share_flows: tuple[OpenFundShareFlow, ...]
    asset_allocations: tuple[OpenFundAssetAllocation, ...]
    industry_allocations: tuple[OpenFundIndustryAllocation, ...]
    top_holdings: tuple[OpenFundHolding, ...]
    source_document_id: str
    source_title: str
    source_url: str
    source: str
    parser_version: str
    extraction_status: DisclosureExtractionStatus
    performance_comparisons: tuple[
        OpenFundPerformanceComparison, ...
    ] = ()
    extraction_errors: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_snapshot_identity(
            self.fund_asset_id,
            self.source_document_id,
            self.source_title,
            self.source_url,
            self.source,
            self.parser_version,
        )
        date.fromisoformat(self.report_period_start)
        date.fromisoformat(self.report_period_end)
        date.fromisoformat(self.announcement_date)
        if self.report_period_start > self.report_period_end:
            raise ValueError(
                "report_period_start must not follow report_period_end"
            )
        if self.report_period_end > self.announcement_date:
            raise ValueError(
                "report period must not follow announcement_date"
            )
        codes = [item.share_class_code for item in self.share_classes]
        if len(codes) != len(set(codes)):
            raise ValueError("periodic share classes must be unique")
        ranks = [item.rank for item in self.top_holdings]
        if len(ranks) != len(set(ranks)):
            raise ValueError("holding ranks must be unique")


@dataclass(frozen=True)
class FundAssetRelation:
    fund_asset_id: str
    relation_type: FundRelationType
    related_name: str
    source: str
    effective_date: str
    confidence: float
    related_asset_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fund_asset_id.strip():
            raise ValueError("fund_asset_id must not be blank")
        if not self.related_name.strip():
            raise ValueError("related_name must not be blank")
        if not self.source.strip():
            raise ValueError("source must not be blank")
        date.fromisoformat(self.effective_date)
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if (
            self.relation_type is FundRelationType.UNDERLYING_INDEX
            and self.related_asset_id is None
        ):
            raise ValueError(
                "Underlying index relation requires related_asset_id"
            )


@dataclass(frozen=True)
class FundResearchBundle:
    asset: Asset
    start_date: str
    end_date: str
    as_of_date: str
    nav_records: tuple[dict[str, Any], ...]
    estimate_records: tuple[dict[str, Any], ...]
    status_records: tuple[dict[str, Any], ...]
    scale_records: tuple[dict[str, Any], ...]
    allocation_records: tuple[dict[str, Any], ...]
    relations: tuple[FundAssetRelation, ...]
    sources: tuple[str, ...]
    complete: bool
    profile_snapshot: OpenFundProfileSnapshot | None = None
    periodic_snapshot: OpenFundPeriodicSnapshot | None = None
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ETFResearchBundle:
    asset: Asset
    start_date: str
    end_date: str
    as_of_date: str
    daily_bar_records: tuple[dict[str, Any], ...]
    relations: tuple[FundAssetRelation, ...]
    sources: tuple[str, ...]
    complete: bool
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ETFMarketSnapshot:
    asset_id: str
    as_of_date: str
    market_price: Decimal
    iopv: Decimal
    source: str
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if not self.asset_id.strip() or not self.source.strip():
            raise ValueError("asset_id and source must not be blank")
        if self.market_price <= 0:
            raise ValueError("market_price must be positive")
        if self.iopv <= 0:
            raise ValueError("iopv must be positive")


@dataclass(frozen=True)
class ETFExpenseSchedule:
    asset_id: str
    effective_date: str
    management_fee_rate: Decimal
    custody_fee_rate: Decimal
    comprehensive_expense_ratio: Decimal
    source: str
    source_url: str

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        if (
            not self.asset_id.strip()
            or not self.source.strip()
            or not self.source_url.strip()
        ):
            raise ValueError(
                "Expense schedule identity and source are required"
            )
        rates = (
            self.management_fee_rate,
            self.custody_fee_rate,
            self.comprehensive_expense_ratio,
        )
        if any(rate < 0 or rate >= Decimal("0.20") for rate in rates):
            raise ValueError(
                "ETF annual expense rates must be ratios in [0, 0.20)"
            )
        if self.comprehensive_expense_ratio < sum(
            rates[:2],
            Decimal("0"),
        ):
            raise ValueError(
                "Comprehensive expense ratio cannot be below "
                "management plus custody fees"
            )


@dataclass(frozen=True)
class ETFMetricSet:
    asset_id: str
    as_of_date: str
    values: tuple[FactorValue, ...]

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        if {value.factor_name for value in self.values} != {
            "premium_discount",
            "expense_ratio",
        }:
            raise ValueError(
                "ETF metric set requires premium_discount and "
                "expense_ratio"
            )


def _validate_snapshot_identity(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError(
            "snapshot asset, document, source, and parser identity "
            "must not be blank"
        )


def _validate_page_number(value: int) -> None:
    if value <= 0:
        raise ValueError("page_number must be positive")


def _validate_ratio(value: Decimal, label: str) -> None:
    if value < 0 or value > Decimal("1"):
        raise ValueError(f"{label} must be a ratio in [0, 1]")


def _validate_optional_ratio(
    value: Decimal | None,
    label: str,
) -> None:
    if value is not None:
        _validate_ratio(value, label)
