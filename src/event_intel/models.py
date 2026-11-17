"""Dated intelligence items and Agent-tracked observables (events and unstructured values).

Design notes: docs/modules/2-世界状态/事件情报与观测模块.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

OBS_PREFIX = "obs:"
KIND_EVENT = "event"
KIND_VALUE = "value"
OBSERVABLE_KINDS = (KIND_EVENT, KIND_VALUE)
_OBSERVABLE_ID = re.compile(r"^obs:[a-z0-9][a-z0-9_.\-]{2,80}$")
# Evidence is a stored item (intel:<id>) or a stored search whose empty result is the evidence (search:<id>).
_EVIDENCE_REF = re.compile(r"^(intel:[0-9a-f]{16}|search:\d+)$")
WATCH_LANGUAGES = ("en", "zh")


def intel_item_id(url: str) -> str:
    return "intel:" + hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class IntelItem:
    item_id: str
    source: str
    kind: str
    title: str
    url: str
    published_on: str
    published_at: str | None = None
    publisher: str | None = None
    summary: str | None = None
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "item_id": self.item_id,
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "published_on": self.published_on,
            "publisher": self.publisher,
            "language": self.language,
        }
        if self.published_at:
            payload["published_at"] = self.published_at
        if self.summary:
            payload["summary"] = self.summary
        extra = {key: value for key, value in self.raw.items() if value not in (None, "", [])}
        if extra:
            payload["extra"] = extra
        return payload


@dataclass(frozen=True)
class Observable:
    """Something the Agent tracks by evidence: an event (0 not yet / 1 happened) or a value per period."""

    observable_id: str
    title: str
    kind: str
    unit: str
    criteria: str
    created_on: str
    watch_queries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _OBSERVABLE_ID.match(self.observable_id):
            raise ValueError("observable_id 形如 obs:<小写字母数字与 _.->，3–80 个字符")
        for name in ("title", "unit", "criteria"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} 不能为空")
        if self.kind not in OBSERVABLE_KINDS:
            raise ValueError(f"kind 只能是 {'、'.join(OBSERVABLE_KINDS)}")
        date.fromisoformat(self.created_on)
        for query in self.watch_queries:
            watch_query(query)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observable_id": self.observable_id,
            "title": self.title,
            "kind": self.kind,
            "unit": self.unit,
            "criteria": self.criteria,
            "created_on": self.created_on,
            "watch_queries": list(self.watch_queries),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Observable":
        return cls(
            observable_id=str(payload.get("observable_id") or ""),
            title=str(payload.get("title") or ""),
            kind=str(payload.get("kind") or ""),
            unit=str(payload.get("unit") or ""),
            criteria=str(payload.get("criteria") or ""),
            created_on=str(payload.get("created_on") or ""),
            watch_queries=tuple(payload.get("watch_queries") or ()),
        )


def watch_query(text: str) -> tuple[str, str]:
    """"en:<query>" or "zh:<query>"; a query without a prefix searches English news."""

    value = str(text or "").strip()
    language, sep, rest = value.partition(":")
    if sep and language in WATCH_LANGUAGES:
        value = rest.strip()
    else:
        language = "en"
    if not value:
        raise ValueError("watch_queries 里有空检索词")
    return language, value


@dataclass(frozen=True)
class Observation:
    """One evidence-backed value: what held at ``period_end``, publicly knowable from ``available_at``."""

    observable_id: str
    period_end: str
    value: Decimal
    available_at: str
    evidence_refs: tuple[str, ...]
    note: str
    recorded_at: str

    def __post_init__(self) -> None:
        period = date.fromisoformat(self.period_end)
        available = date.fromisoformat(self.available_at)
        recorded = datetime.fromisoformat(self.recorded_at).date()
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("value 必须是有限的小数")
        if period > available:
            raise ValueError("period_end 不能晚于 available_at：一期结束前不可能知道它的值")
        if available > recorded:
            raise ValueError("available_at 不能晚于记录日：不能记录尚未公开的信息")
        if not self.evidence_refs or any(not _EVIDENCE_REF.match(item) for item in self.evidence_refs):
            raise ValueError("evidence_refs 至少一条，每条是 intel:<条目> 或 search:<检索编号>")
        if not str(self.note or "").strip():
            raise ValueError("note 写明判断依据")

    @property
    def observation_id(self) -> str:
        digest = hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8"))
        return "observation-" + digest.hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "observable_id": self.observable_id,
            "period_end": self.period_end,
            "value": str(self.value),
            "available_at": self.available_at,
            "evidence_refs": list(self.evidence_refs),
            "note": self.note,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Observation":
        try:
            value = Decimal(str(payload.get("value")))
        except InvalidOperation as exc:
            raise ValueError("value 必须是数字字符串") from exc
        return cls(
            observable_id=str(payload.get("observable_id") or ""),
            period_end=str(payload.get("period_end") or ""),
            value=value,
            available_at=str(payload.get("available_at") or ""),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
            note=str(payload.get("note") or ""),
            recorded_at=str(payload.get("recorded_at") or ""),
        )
