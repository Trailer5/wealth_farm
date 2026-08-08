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
