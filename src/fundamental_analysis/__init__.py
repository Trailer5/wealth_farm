"""Exact valuation and financial-quality analysis."""

from .assembler import (
    FundamentalSnapshotAssembler,
    FundamentalSnapshotError,
)
from .calculator import (
    FundamentalCalculationError,
    FundamentalMetricCalculator,
    FundamentalMetricParameters,
)
from .financial_institution import (
    FINANCIAL_INSTITUTION_VERSION,
    FinancialInstitutionAnalysisService,
)
from .models import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalInputEvidence,
    FundamentalMetricResult,
    FundamentalMetricSet,
    FundamentalSnapshot,
    MetricStatus,
)
from .service import FundamentalAnalysisService
from .metric_history import (
    COMPUTED_METRICS,
    INDICATOR_PREFIX,
    is_period_end_indicator,
    SNAPSHOT_FIELDS,
    MetricObservation,
    format_metric_value,
    metric_history,
    metric_label,
)
from .recurring import growth_profile_signals, recurring_profit_summary
from .sqlite_repository import SQLiteFundamentalMetricRepository
from .dividends import (
    DividendEvent,
    DividendHistoryService,
    dividends_by_year,
    trailing_dividends,
)
from .valuation_model_service import (
    VALUATION_MODEL_VERSION,
    ValuationAssumptions,
    ValuationModelService,
    ValuationModelUnavailable,
    valuation_model_lines,
)

__all__ = [
    "DividendEvent",
    "DividendHistoryService",
    "dividends_by_year",
    "trailing_dividends",
    "COMPUTED_METRICS",
    "INDICATOR_PREFIX",
    "is_period_end_indicator",
    "MetricObservation",
    "SNAPSHOT_FIELDS",
    "format_metric_value",
    "metric_history",
    "metric_label",
    "FUNDAMENTAL_CALCULATION_VERSION",
    "FINANCIAL_INSTITUTION_VERSION",
    "FinancialInstitutionAnalysisService",
    "FundamentalAnalysisService",
    "FundamentalCalculationError",
    "FundamentalInputEvidence",
    "FundamentalMetricCalculator",
    "FundamentalMetricParameters",
    "FundamentalMetricResult",
    "FundamentalMetricSet",
    "FundamentalSnapshot",
    "FundamentalSnapshotAssembler",
    "FundamentalSnapshotError",
    "MetricStatus",
    "SQLiteFundamentalMetricRepository",
    "VALUATION_MODEL_VERSION",
    "ValuationAssumptions",
    "ValuationModelService",
    "ValuationModelUnavailable",
    "recurring_profit_summary",
    "growth_profile_signals",
    "valuation_model_lines",
]
