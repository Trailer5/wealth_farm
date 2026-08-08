"""Normalized financial statement and indicator models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.master_data import Asset


DATASET_STOCK_INCOME_STATEMENTS = "stock_income_statements"
DATASET_STOCK_BALANCE_SHEETS = "stock_balance_sheets"
DATASET_STOCK_CASH_FLOW_STATEMENTS = (
    "stock_cash_flow_statements"
)
DATASET_STOCK_FINANCIAL_INDICATORS = (
    "stock_financial_indicators"
)
FINANCIAL_DATASETS = frozenset(
    {
        DATASET_STOCK_INCOME_STATEMENTS,
        DATASET_STOCK_BALANCE_SHEETS,
        DATASET_STOCK_CASH_FLOW_STATEMENTS,
        DATASET_STOCK_FINANCIAL_INDICATORS,
    }
)


class FinancialPeriodKind(str, Enum):
    Q1 = "q1"
    HALF_YEAR = "half_year"
    Q3 = "q3"
    ANNUAL = "annual"


class FinancialValueScope(str, Enum):
    CUMULATIVE = "cumulative"
    POINT_IN_TIME = "point_in_time"
    REPORTED = "reported"
    TTM = "ttm"


@dataclass(frozen=True)
class FinancialDataPoint:
    asset_id: str
    dataset: str
    report_period: str
    published_at: str
    period_kind: FinancialPeriodKind
    value_scope: FinancialValueScope
    field_name: str
    field_label: str
    normalized_value: Decimal
    raw_value: str
    currency: str | None
    unit: str
    source: str
    revision: int = 1
    fetched_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.dataset, "dataset"),
            (self.field_name, "field_name"),
            (self.field_label, "field_label"),
            (self.raw_value, "raw_value"),
            (self.unit, "unit"),
            (self.source, "source"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        date.fromisoformat(self.report_period)
        date.fromisoformat(self.published_at)
        if self.published_at < self.report_period:
            raise ValueError(
                "published_at must not precede report_period"
            )
        if self.revision < 1:
            raise ValueError("revision must be positive")

    def to_record(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "dataset": self.dataset,
            "report_period": self.report_period,
            "published_at": self.published_at,
            "period_kind": self.period_kind.value,
            "value_scope": self.value_scope.value,
            "field_name": self.field_name,
            "field_label": self.field_label,
            "normalized_value": format(
                self.normalized_value,
                "f",
            ),
            "raw_value": self.raw_value,
            "currency": self.currency,
            "unit": self.unit,
            "source": self.source,
            "revision": self.revision,
            "fetched_at": self.fetched_at,
            "raw": dict(self.raw),
        }


@dataclass(frozen=True)
class TTMInputSet:
    target_period: str
    current_cumulative_period: str
    previous_annual_period: str
    prior_comparable_period: str | None


@dataclass(frozen=True)
class FinancialPeriodSummary:
    available_periods: tuple[str, ...]
    latest_annual_period: str | None
    latest_interim_period: str | None
    latest_ttm_inputs: TTMInputSet | None


@dataclass(frozen=True)
class FinancialResearchBundle:
    asset: Asset
    as_of_date: str
    start_date: str
    end_date: str
    records_by_dataset: dict[
        str,
        tuple[dict[str, Any], ...],
    ]
    sources: tuple[str, ...]
    complete: bool
    errors: dict[str, str] = field(default_factory=dict)

    def period_summary(self) -> FinancialPeriodSummary:
        from .periods import summarize_financial_periods

        return summarize_financial_periods(self)

    def filtered_as_of(
        self,
        cutoff_date: str,
    ) -> "FinancialResearchBundle":
        date.fromisoformat(cutoff_date)
        if cutoff_date > self.as_of_date:
            raise ValueError(
                "cutoff_date must not exceed bundle as_of_date"
            )
        records_by_dataset = {
            dataset: tuple(
                record
                for record in records
                if str(record.get("published_at") or "")
                <= cutoff_date
            )
            for dataset, records in self.records_by_dataset.items()
        }
        return FinancialResearchBundle(
            asset=self.asset,
            as_of_date=cutoff_date,
            start_date=self.start_date,
            end_date=min(self.end_date, cutoff_date),
            records_by_dataset=records_by_dataset,
            sources=self.sources,
            complete=(
                self.complete
                and bool(
                    records_by_dataset.get(
                        DATASET_STOCK_INCOME_STATEMENTS,
                        (),
                    )
                )
            ),
            errors=dict(self.errors),
        )
