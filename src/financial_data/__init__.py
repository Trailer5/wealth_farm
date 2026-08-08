"""Normalized financial research data."""

from .models import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FINANCIAL_DATASETS,
    FinancialDataPoint,
    FinancialPeriodKind,
    FinancialPeriodSummary,
    FinancialResearchBundle,
    FinancialValueScope,
    TTMInputSet,
)
from .normalizer import (
    FinancialNormalizationError,
    normalize_financial_records,
)
from .periods import (
    infer_period_kind,
    summarize_financial_periods,
    ttm_input_periods,
)
from .sqlite_repository import SQLiteFinancialDataRepository

__all__ = [
    "DATASET_STOCK_BALANCE_SHEETS",
    "DATASET_STOCK_CASH_FLOW_STATEMENTS",
    "DATASET_STOCK_FINANCIAL_INDICATORS",
    "DATASET_STOCK_INCOME_STATEMENTS",
    "FINANCIAL_DATASETS",
    "FinancialDataPoint",
    "FinancialNormalizationError",
    "FinancialPeriodKind",
    "FinancialPeriodSummary",
    "FinancialResearchBundle",
    "FinancialValueScope",
    "SQLiteFinancialDataRepository",
    "TTMInputSet",
    "infer_period_kind",
    "normalize_financial_records",
    "summarize_financial_periods",
    "ttm_input_periods",
]
