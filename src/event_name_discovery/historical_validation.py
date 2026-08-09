"""Pre-outcome preparation for preregistered historical event discovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .historical_universe import build_shenzhen_name_snapshots
from .matcher import MATCHER_VERSION, EventNameMatcher
from .models import (
    EventDirection,
    EventAttentionScope,
    EventPhrase,
    ExternalEventAnchor,
    NameMatchEvidence,
)


@dataclass(frozen=True)
class HistoricalLanguagePrediction:
    event: ExternalEventAnchor
    universe_size: int
    matches: tuple[NameMatchEvidence, ...]
    holdout: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "universe_size": self.universe_size,
            "matches": [item.to_dict() for item in self.matches],
            "holdout": self.holdout,
        }


@dataclass(frozen=True)
class HistoricalPredictionFreeze:
    protocol_ref: str
    events_frozen_at: str
    prepared_at: str
    matcher_version: str
    predictions: tuple[HistoricalLanguagePrediction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_ref": self.protocol_ref,
            "events_frozen_at": self.events_frozen_at,
            "prepared_at": self.prepared_at,
            "matcher_version": self.matcher_version,
            "predictions": [
                item.to_dict() for item in self.predictions
            ],
        }


class HistoricalPredictionPreparer:
    """Match frozen event phrases before any outcome data is loaded."""

    def __init__(self, matcher: EventNameMatcher | None = None) -> None:
        self.matcher = matcher or EventNameMatcher()

    def prepare(
        self,
        *,
        event_manifest: dict[str, Any],
        catalog: list[dict[str, object]],
        name_changes: list[dict[str, object]],
        prepared_at: str,
    ) -> HistoricalPredictionFreeze:
        predictions = []
        for payload in event_manifest["events"]:
            event = _event_from_payload(payload)
            event_date = (
                datetime.fromisoformat(event.first_public_at)
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat()
            )
            assets = build_shenzhen_name_snapshots(
                catalog=catalog,
                name_changes=name_changes,
                event_date=event_date,
            )
            by_asset = self.matcher.match(
                phrases=event.phrases,
                assets=assets,
                event_date=event_date,
            )
            matches = tuple(
                match
                for asset_id in sorted(by_asset)
                for match in by_asset[asset_id]
            )
            predictions.append(
                HistoricalLanguagePrediction(
                    event=event,
                    universe_size=len(assets),
                    matches=matches,
                    holdout=_is_holdout(event.event_id),
                )
            )
        return HistoricalPredictionFreeze(
            protocol_ref=str(event_manifest["selection_protocol"]),
            events_frozen_at=str(event_manifest["frozen_at"]),
            prepared_at=prepared_at,
            matcher_version=MATCHER_VERSION,
            predictions=tuple(predictions),
        )


def _event_from_payload(payload: dict[str, Any]) -> ExternalEventAnchor:
    return ExternalEventAnchor(
        event_id=str(payload["event_id"]),
        title=str(payload["title"]),
        event_kind=str(payload["event_kind"]),
        occurred_at=str(payload["occurred_at"]),
        first_public_at=str(payload["first_public_at"]),
        source="xinhua:annual_top_news",
        source_url=str(payload["source_url"]),
        phrases=tuple(
            EventPhrase(
                phrase_id=str(item["phrase_id"]),
                text=str(item["text"]),
                direction=EventDirection(item["direction"]),
                components=tuple(item["components"]),
                priority=int(item["priority"]),
            )
            for item in payload["phrases"]
        ),
        evidence_refs=(str(payload["source_url"]),),
        attention_scope=EventAttentionScope(
            payload.get("attention_scope", "domestic_event")
        ),
    )


def _is_holdout(event_id: str) -> bool:
    digest = hashlib.sha256(event_id.encode("utf-8")).digest()
    return digest[0] % 5 == 0
