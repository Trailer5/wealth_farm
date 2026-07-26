"""Current portfolio valuation and concentration models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_id: str
    user_id: str
    as_of_date: str
    total_market_value: float
    total_cost_value: float | None
    unrealized_pnl: float | None
    max_weight: float
    concentration_hhi: float
    created_at: str = ""


@dataclass(frozen=True)
class PortfolioPositionMetric:
    snapshot_id: str
    asset_id: str
    quantity: float
    market_price: float
    market_value: float
    cost_value: float | None
    unrealized_pnl: float | None
    weight: float


@dataclass(frozen=True)
class PortfolioValuation:
    snapshot: PortfolioSnapshot
    positions: tuple[PortfolioPositionMetric, ...]
