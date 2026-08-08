"""Deterministic title rules for official disclosure events."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from src.data_src.models import DisclosureDocument

from .models import (
    EVENT_BUYBACK,
    EVENT_DIVIDEND,
    EVENT_EARNINGS_FORECAST,
    EVENT_FINANCIAL_REPORT,
    EVENT_HOLDING_CHANGE,
    EVENT_LOCK_UP_RELEASE,
    EVENT_LITIGATION,
    EVENT_MANAGEMENT_CHANGE,
    EVENT_OTHER,
    EVENT_REGULATORY,
    ResearchEvent,
)


RULE_VERSION = "title_rules_v1"

KEYWORDS_BY_EVENT = {
    EVENT_FINANCIAL_REPORT: (
        "年度报告",
        "半年度报告",
        "季度报告",
        "业绩快报",
    ),
    EVENT_EARNINGS_FORECAST: ("业绩预告",),
    EVENT_DIVIDEND: ("分红", "派息", "权益分派", "利润分配"),
    EVENT_BUYBACK: ("回购",),
    EVENT_HOLDING_CHANGE: (
        "增持",
        "减持",
        "持股变动",
        "权益变动",
    ),
    EVENT_LOCK_UP_RELEASE: (
        "解除限售",
        "限售股上市流通",
        "限售股份上市流通",
        "解禁",
    ),
    EVENT_REGULATORY: ("处罚", "问询函", "监管函", "警示函"),
    EVENT_LITIGATION: ("诉讼", "仲裁"),
}

MANAGEMENT_SUBJECTS = ("董事", "监事", "高级管理人员", "高管", "基金经理")
MANAGEMENT_ACTIONS = ("辞职", "聘任", "变更", "任免", "离任")


class DisclosureEventExtractor:
    """Extract zero or more events from one disclosure title."""

    def extract(
        self,
        asset_id: str,
        document: DisclosureDocument,
        *,
        include_other: bool = False,
    ) -> list[ResearchEvent]:
        """Return deterministic events with source evidence."""

        event_types = [
            event_type
            for event_type, keywords in KEYWORDS_BY_EVENT.items()
            if any(keyword in document.title for keyword in keywords)
        ]
        if (
            any(keyword in document.title for keyword in MANAGEMENT_SUBJECTS)
            and any(keyword in document.title for keyword in MANAGEMENT_ACTIONS)
        ):
            event_types.append(EVENT_MANAGEMENT_CHANGE)
        event_types = list(dict.fromkeys(event_types))
        if not event_types and include_other:
            event_types = [EVENT_OTHER]

        return [
            ResearchEvent(
                event_id=_event_id(
                    document.source,
                    document.document_id,
                    event_type,
                ),
                asset_id=asset_id,
                event_type=event_type,
                event_date=document.announcement_date,
                title=document.title,
                summary=document.title,
                source=document.source,
                source_record_id=document.document_id,
                source_url=document.file_url or document.page_url,
                confidence=0.4 if event_type == EVENT_OTHER else 0.95,
                extraction_method="title_rule",
                rule_version=RULE_VERSION,
                raw=dict(document.raw),
            )
            for event_type in event_types
        ]


def _event_id(source: str, source_record_id: str, event_type: str) -> str:
    value = f"{source}:{source_record_id}:{event_type}"
    return f"event:{uuid5(NAMESPACE_URL, value)}"
