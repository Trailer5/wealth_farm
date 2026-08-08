"""A-share market breadth and relative-strength analysis."""

from .calculator import AShareMarketFactorCalculator
from .models import (
    A_SHARE_MARKET_CALCULATION_VERSION,
    MarketCrossSectionRow,
    MarketFactorParameters,
    MarketFactorSnapshot,
    MarketFactorValue,
    SizeBucket,
)
from .service import MarketAnalysisService
from .sqlite_repository import SQLiteMarketFactorRepository

__all__ = [
    "A_SHARE_MARKET_CALCULATION_VERSION",
    "AShareMarketFactorCalculator",
    "MarketAnalysisService",
    "MarketCrossSectionRow",
    "MarketFactorParameters",
    "MarketFactorSnapshot",
    "MarketFactorValue",
    "SQLiteMarketFactorRepository",
    "SizeBucket",
]
