"""Versioned, reusable research factors."""

from .calculator import CALCULATION_VERSION, MarketFactorCalculator
from .commodity import (
    COMMODITY_SENSITIVITY_VERSION,
    CommoditySensitivityService,
)
from .margin import (
    MARGIN_CALCULATION_VERSION,
    MarginFactorCalculator,
    MarginFactorParameters,
    MarginTradingRecord,
)
from .market_event import (
    CAPITAL_FLOW_VERSION,
    MARKET_EVENT_VERSION,
    CapitalFlowDisclosureRecord,
    CapitalFlowFactorCalculator,
    CapitalFlowFactorValue,
    MarketActivityRecord,
    MarketEventFactorCalculator,
)
from .models import FactorValue
from .open_fund import (
    OPEN_FUND_STANDARD_CALCULATION_VERSION,
    OPEN_FUND_STANDARD_WINDOWS,
    OpenFundStandardMetricCalculator,
    OpenFundStandardMetricResult,
    OpenFundWindowDefinition,
    OpenFundWindowUnavailable,
)
from .risk import (
    RISK_CALCULATION_VERSION,
    DrawdownEpisode,
    RiskCalculationResult,
    RiskMetricCalculator,
    RiskMetricParameters,
)
from .service import FactorService
from .sqlite_repository import SQLiteFactorRepository

__all__ = [
    "COMMODITY_SENSITIVITY_VERSION",
    "CommoditySensitivityService",
    "CALCULATION_VERSION",
    "CAPITAL_FLOW_VERSION",
    "CapitalFlowDisclosureRecord",
    "CapitalFlowFactorCalculator",
    "CapitalFlowFactorValue",
    "FactorValue",
    "OPEN_FUND_STANDARD_CALCULATION_VERSION",
    "OPEN_FUND_STANDARD_WINDOWS",
    "OpenFundStandardMetricCalculator",
    "OpenFundStandardMetricResult",
    "OpenFundWindowDefinition",
    "OpenFundWindowUnavailable",
    "MARGIN_CALCULATION_VERSION",
    "MARKET_EVENT_VERSION",
    "MarginFactorCalculator",
    "MarginFactorParameters",
    "MarginTradingRecord",
    "MarketActivityRecord",
    "MarketEventFactorCalculator",
    "RISK_CALCULATION_VERSION",
    "DrawdownEpisode",
    "RiskCalculationResult",
    "RiskMetricCalculator",
    "RiskMetricParameters",
    "MarketFactorCalculator",
    "FactorService",
    "SQLiteFactorRepository",
]
