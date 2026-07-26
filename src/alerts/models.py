"""Local alert rules and generated reminders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RULE_PRICE_ABOVE = "price_above"
RULE_PRICE_BELOW = "price_below"
RULE_EVENT_TYPE = "event_type"
RULE_MAX_WEIGHT_ABOVE = "max_weight_above"

ALERT_PENDING = "pending"
ALERT_ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class AlertRule:
    rule_id: str
    user_id: str
    rule_type: str
    config: dict[str, Any] = field(default_factory=dict)
    asset_id: str | None = None
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class Alert:
    alert_id: str
    rule_id: str
    user_id: str
    title: str
    message: str
    triggered_at: str
    dedupe_key: str
    status: str = ALERT_PENDING
    asset_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    acknowledged_at: str | None = None
    created_at: str = ""
