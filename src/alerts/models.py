"""Local alert rules and generated reminders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RULE_PRICE_ABOVE = "price_above"
RULE_PRICE_BELOW = "price_below"
RULE_EVENT_TYPE = "event_type"
RULE_MAX_WEIGHT_ABOVE = "max_weight_above"
RULE_REVIEW_DATE = "review_date"

ALERT_PENDING = "pending"
ALERT_ACKNOWLEDGED = "acknowledged"


@dataclass(frozen=True)
class AlertRuleProvenance:
    """Bind a local rule back to one reviewed research condition."""

    workflow_run_id: str
    final_report_id: str
    monitoring_plan_id: str
    monitoring_condition_id: str
    condition_source: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.workflow_run_id, "workflow_run_id"),
            (self.final_report_id, "final_report_id"),
            (self.monitoring_plan_id, "monitoring_plan_id"),
            (
                self.monitoring_condition_id,
                "monitoring_condition_id",
            ),
            (self.condition_source, "condition_source"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in self.evidence_refs
        ):
            raise ValueError("evidence_refs must contain non-empty strings")

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "final_report_id": self.final_report_id,
            "monitoring_plan_id": self.monitoring_plan_id,
            "monitoring_condition_id": self.monitoring_condition_id,
            "condition_source": self.condition_source,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, object],
    ) -> "AlertRuleProvenance":
        evidence_refs = payload.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            raise ValueError("evidence_refs must be a list")
        return cls(
            workflow_run_id=str(payload["workflow_run_id"]),
            final_report_id=str(payload["final_report_id"]),
            monitoring_plan_id=str(payload["monitoring_plan_id"]),
            monitoring_condition_id=str(
                payload["monitoring_condition_id"]
            ),
            condition_source=str(payload["condition_source"]),
            evidence_refs=tuple(str(item) for item in evidence_refs),
        )


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
    provenance: AlertRuleProvenance | None = None


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
