"""A-share market breadth and relative-strength analysis."""

from .calculator import AShareMarketFactorCalculator
from .fundamentals import (
    IndexFundamentalAssessment,
    IndexFundamentalCalculator,
    IndexFundamentalTrend,
    IndexFundamentalTrendCalculator,
    IndexFundamentalTrendPoint,
    IndexValuationMethodComparison,
    IndexValuationMethodComparisonCalculator,
    IndexValuationMethodRow,
    IndexValuationSnapshot,
    IndustryFundamentalSnapshot,
    IndustryMetricRanking,
    MacroObservation,
    MarketIndustryAssessment,
    MarketIndustryAssessmentCalculator,
)
from .industry_contribution import (
    IndustryContribution,
    IndustryContributionCalculator,
    IndustryMarketMetric,
)
from .models import (
    A_SHARE_MARKET_CALCULATION_VERSION,
    MarketCrossSectionRow,
    MarketFactorParameters,
    MarketFactorSnapshot,
    MarketFactorValue,
    MarketSeriesObservation,
    SizeBucket,
)
from .service import MarketAnalysisService
from .archive import (
    MARKET_SUBJECT_ID,
    MarketArchive,
    MarketArchiveService,
    PercentileStat,
    days_before,
    historical_percentile,
    percentile_bucket,
    value_on_or_before,
    years_before,
)
from .sqlite_repository import SQLiteMarketFactorRepository
from .valuation_series import IndexValuationSeriesService

__all__ = [
    "MARKET_SUBJECT_ID",
    "MarketArchive",
    "MarketArchiveService",
    "PercentileStat",
    "days_before",
    "historical_percentile",
    "percentile_bucket",
    "value_on_or_before",
    "years_before",
    "A_SHARE_MARKET_CALCULATION_VERSION",
    "AShareMarketFactorCalculator",
    "IndexFundamentalAssessment",
    "IndexFundamentalCalculator",
    "IndexFundamentalTrend",
    "IndexFundamentalTrendCalculator",
    "IndexFundamentalTrendPoint",
    "IndexValuationMethodComparison",
    "IndexValuationMethodComparisonCalculator",
    "IndexValuationMethodRow",
    "IndexValuationSnapshot",
    "IndexValuationSeriesService",
    "IndustryContribution",
    "IndustryContributionCalculator",
    "IndustryFundamentalSnapshot",
    "IndustryMarketMetric",
    "IndustryMetricRanking",
    "MacroObservation",
    "MarketAnalysisService",
    "MarketCrossSectionRow",
    "MarketFactorParameters",
    "MarketFactorSnapshot",
    "MarketFactorValue",
    "MarketIndustryAssessment",
    "MarketIndustryAssessmentCalculator",
    "MarketSeriesObservation",
    "SQLiteMarketFactorRepository",
    "SizeBucket",
]
