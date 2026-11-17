"""Event intelligence: dated global news, filings and rules, and evidence-tracked observables.

Design notes: docs/modules/2-世界状态/事件情报与观测模块.md.
"""

from .models import (
    KIND_EVENT,
    KIND_VALUE,
    OBS_PREFIX,
    IntelItem,
    Observable,
    Observation,
    intel_item_id,
    watch_query,
)
from .service import SOURCES, EventIntelService, is_observable_metric
from .sqlite_repository import SQLiteEventIntelRepository
from .text import html_to_text

__all__ = [
    "KIND_EVENT",
    "KIND_VALUE",
    "OBS_PREFIX",
    "SOURCES",
    "EventIntelService",
    "IntelItem",
    "Observable",
    "Observation",
    "SQLiteEventIntelRepository",
    "html_to_text",
    "intel_item_id",
    "is_observable_metric",
    "watch_query",
]
