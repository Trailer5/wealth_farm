"""Models for user-owned manual research state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    # User-stated date of the first purchase; None when not told.
    first_bought_on: str | None = None


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


@dataclass(frozen=True)
class ExplorationSessionRecord:
    session_id: str
    user_id: str
    workflow_run_id: str
    handoff_id: str
    decision_brief_id: str | None
    asset_id: str
    status: str
    payload: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ExplorationLedgerRecord:
    entry_id: str
    session_id: str
    sequence_no: int
    entry_kind: str
    payload: dict[str, Any]
    recorded_at: str
