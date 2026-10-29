"""Multi-strategy discovery funnel on on-demand cross-sections (R3)."""

from .features import CYCLICAL_L1, FINANCIAL_L1, Financials, MemberFeatures, ValuationSeries
from .service import (
    DEFAULT_BASE_PACKS,
    FUNNEL_VERSION,
    MAX_HISTORY_SCOPE,
    DiscoveryFunnelService,
    DiscoveryScope,
)
from .sqlite_repository import SQLiteDiscoveryFunnelRepository
from .strategies import STRATEGIES, STRATEGY_VERSION, Strategy, strategy

__all__ = [
    "CYCLICAL_L1",
    "DEFAULT_BASE_PACKS",
    "FINANCIAL_L1",
    "FUNNEL_VERSION",
    "MAX_HISTORY_SCOPE",
    "STRATEGIES",
    "STRATEGY_VERSION",
    "DiscoveryFunnelService",
    "DiscoveryScope",
    "Financials",
    "MemberFeatures",
    "SQLiteDiscoveryFunnelRepository",
    "Strategy",
    "ValuationSeries",
    "strategy",
]
