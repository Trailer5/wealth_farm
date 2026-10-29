"""Reported versus recurring (扣非) profit from a financial bundle."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.financial_data import FinancialResearchBundle

from .assembler import FundamentalSnapshotAssembler, FundamentalSnapshotError


def recurring_profit_summary(bundle: FinancialResearchBundle) -> dict[str, Any] | None:
    """TTM parent net profit, recurring profit and the non-recurring share; None without 扣非 data."""

    try:
        snapshot = FundamentalSnapshotAssembler().assemble(bundle, market_price=Decimal("1"))
    except FundamentalSnapshotError:
        return None
    earnings = snapshot.fields.get("earnings")
    recurring = snapshot.fields.get("recurring_earnings")
    if earnings is None or recurring is None:
        return None
    return {
        "report_period": snapshot.report_period,
        "basis": snapshot.flow_basis,
        "earnings": float(earnings),
        "recurring_earnings": float(recurring),
        "non_recurring_share": float((earnings - recurring) / earnings) if earnings > 0 else None,
    }


def growth_profile_signals(bundle: FinancialResearchBundle) -> dict[str, Any] | None:
    """Latest TTM parent net profit and TTM revenue growth, the inputs of the high-growth profile rule."""

    try:
        snapshot = FundamentalSnapshotAssembler().assemble(bundle, market_price=Decimal("1"))
    except FundamentalSnapshotError:
        return None
    revenue = snapshot.fields.get("revenue")
    prior = snapshot.prior_fields.get("revenue")
    return {
        "report_period": snapshot.report_period,
        "earnings_ttm": snapshot.fields.get("earnings"),
        "revenue_ttm": revenue,
        "revenue_growth": (revenue / prior - 1) if revenue is not None and prior and prior > 0 else None,
    }
