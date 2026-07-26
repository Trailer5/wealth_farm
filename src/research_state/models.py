"""Models for user-owned manual research state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Watchlist:
    watchlist_id: str
    user_id: str
    name: str
    description: str = ""
    is_default: bool = False
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class WatchlistItem:
    watchlist_id: str
    asset_id: str
    reason: str = ""
    tags: tuple[str, ...] = ()
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class HoldingPosition:
    user_id: str
    asset_id: str
    quantity: float
    average_cost: float | None = None
    currency: str = "CNY"
    note: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class AnalysisReport:
    report_id: str
    user_id: str
    asset_id: str
    title: str
    summary: str
    content_markdown: str
    status: str = "draft"
    created_at: str = ""
    updated_at: str = ""
