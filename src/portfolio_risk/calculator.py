"""Pure-Python current holding valuation and concentration."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from src.research_state import HoldingPosition

from .models import (
    PortfolioPositionMetric,
    PortfolioSnapshot,
    PortfolioValuation,
)


class PortfolioCalculator:
    """Value one user's non-negative current holdings."""

    def calculate(
        self,
        *,
        user_id: str,
        as_of_date: str,
        holdings: list[HoldingPosition],
        prices: dict[str, float],
    ) -> PortfolioValuation:
        """Return one portfolio snapshot and position metrics."""

        active = [holding for holding in holdings if holding.quantity != 0]
        if not active:
            raise ValueError("Portfolio has no non-zero holdings")
        for holding in active:
            if holding.quantity < 0:
                raise ValueError("Negative holdings are not supported")
            if holding.asset_id not in prices:
                raise KeyError(f"Missing market price for {holding.asset_id}")
            if prices[holding.asset_id] <= 0:
                raise ValueError(
                    f"Market price must be positive for {holding.asset_id}"
                )

        market_values = {
            holding.asset_id: holding.quantity * float(prices[holding.asset_id])
            for holding in active
        }
        total_market_value = sum(market_values.values())
        if total_market_value <= 0:
            raise ValueError("Portfolio market value must be positive")
        all_costs_known = all(
            holding.average_cost is not None
            for holding in active
        )
        cost_values = {
            holding.asset_id: (
                holding.quantity * float(holding.average_cost)
                if holding.average_cost is not None
                else None
            )
            for holding in active
        }
        total_cost_value = (
            sum(float(value) for value in cost_values.values())
            if all_costs_known
            else None
        )
        unrealized_pnl = (
            total_market_value - total_cost_value
            if total_cost_value is not None
            else None
        )
        snapshot_id = _snapshot_id(user_id, as_of_date)
        positions = tuple(
            PortfolioPositionMetric(
                snapshot_id=snapshot_id,
                asset_id=holding.asset_id,
                quantity=holding.quantity,
                market_price=float(prices[holding.asset_id]),
                market_value=market_values[holding.asset_id],
                cost_value=cost_values[holding.asset_id],
                unrealized_pnl=(
                    market_values[holding.asset_id]
                    - float(cost_values[holding.asset_id])
                    if cost_values[holding.asset_id] is not None
                    else None
                ),
                weight=market_values[holding.asset_id] / total_market_value,
            )
            for holding in sorted(active, key=lambda item: item.asset_id)
        )
        weights = [position.weight for position in positions]
        snapshot = PortfolioSnapshot(
            snapshot_id=snapshot_id,
            user_id=user_id,
            as_of_date=as_of_date,
            total_market_value=total_market_value,
            total_cost_value=total_cost_value,
            unrealized_pnl=unrealized_pnl,
            max_weight=max(weights),
            concentration_hhi=sum(weight * weight for weight in weights),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return PortfolioValuation(snapshot=snapshot, positions=positions)


def _snapshot_id(user_id: str, as_of_date: str) -> str:
    return f"portfolio:{uuid5(NAMESPACE_URL, f'{user_id}:{as_of_date}')}"
