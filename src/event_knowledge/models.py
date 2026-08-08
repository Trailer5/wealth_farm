"""Structured research events extracted from source records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EVENT_FINANCIAL_REPORT = "financial_report"
EVENT_EARNINGS_FORECAST = "earnings_forecast"
EVENT_DIVIDEND = "dividend"
EVENT_BUYBACK = "buyback"
EVENT_HOLDING_CHANGE = "holding_change"
EVENT_LOCK_UP_RELEASE = "lock_up_release"
EVENT_REGULATORY = "regulatory"
EVENT_LITIGATION = "litigation"
EVENT_MANAGEMENT_CHANGE = "management_change"
EVENT_OTHER = "other"


@dataclass(frozen=True)
class ResearchEvent:
    """One source-backed event associated with a canonical asset."""

    event_id: str
    asset_id: str
    event_type: str
    event_date: str
    title: str
    summary: str
    source: str
    source_record_id: str
    source_url: str | None
    confidence: float
    extraction_method: str
    rule_version: str
    raw: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
