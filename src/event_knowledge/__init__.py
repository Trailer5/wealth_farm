"""Deterministic source-backed event extraction."""

from .extractor import DisclosureEventExtractor, RULE_VERSION
from .models import (
    EVENT_BUYBACK,
    EVENT_DIVIDEND,
    EVENT_EARNINGS_FORECAST,
    EVENT_FINANCIAL_REPORT,
    EVENT_HOLDING_CHANGE,
    EVENT_LITIGATION,
    EVENT_LOCK_UP_RELEASE,
    EVENT_MANAGEMENT_CHANGE,
    EVENT_OTHER,
    EVENT_REGULATORY,
    ResearchEvent,
)
from .service import EventKnowledgeService
from .sqlite_repository import SQLiteEventRepository

__all__ = [
    "EVENT_BUYBACK",
    "EVENT_DIVIDEND",
    "EVENT_EARNINGS_FORECAST",
    "EVENT_FINANCIAL_REPORT",
    "EVENT_HOLDING_CHANGE",
    "EVENT_LITIGATION",
    "EVENT_LOCK_UP_RELEASE",
    "EVENT_MANAGEMENT_CHANGE",
    "EVENT_OTHER",
    "EVENT_REGULATORY",
    "ResearchEvent",
    "DisclosureEventExtractor",
    "RULE_VERSION",
    "EventKnowledgeService",
    "SQLiteEventRepository",
]
