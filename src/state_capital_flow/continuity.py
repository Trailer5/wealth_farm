"""Evidence-tiered ETF share-unit continuity assessment."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Sequence
from urllib.parse import urlparse

from .models import ETFDailyObservation, SourceTier


ContinuityStatus = Literal[
    "official_confirmed",
    "aggregator_checked",
    "blocked",
    "uncovered",
]


@dataclass(frozen=True)
class ETFShareUnitEvent:
    asset_id: str
    event_date: str
    factor: Decimal
    source_tier: SourceTier
    source: str
    source_url: str

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        _parse_date(self.event_date, "event_date")
        if not isinstance(self.factor, Decimal):
            raise TypeError("factor must be Decimal")
        if not self.factor.is_finite() or self.factor <= 0:
            raise ValueError("factor must be finite and greater than zero")
        _require_source(self.source_tier)
        _require_text(self.source, "source")
        _require_url(self.source_url, "source_url")


@dataclass(frozen=True)
class ETFShareUnitCoverage:
    asset_id: str
    coverage_start: str
    coverage_end: str
    source_tier: SourceTier
    source: str
    source_url: str
    events: tuple[ETFShareUnitEvent, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        start = _parse_date(self.coverage_start, "coverage_start")
        end = _parse_date(self.coverage_end, "coverage_end")
        if end < start:
            raise ValueError("coverage_end must not precede coverage_start")
        _require_source(self.source_tier)
        _require_text(self.source, "source")
        _require_url(self.source_url, "source_url")
        events = tuple(self.events)
        object.__setattr__(self, "events", events)
        for event in events:
            if not isinstance(event, ETFShareUnitEvent):
                raise TypeError("events must contain ETFShareUnitEvent values")
            if event.asset_id != self.asset_id:
                raise ValueError("event asset_id must match coverage asset_id")
            event_date = _parse_date(event.event_date, "event_date")
            if not start <= event_date <= end:
                raise ValueError("events must fall within the coverage interval")


@dataclass(frozen=True)
class ETFShareContinuityAssessment:
    asset_id: str
    start_date: str
    end_date: str
    status: ContinuityStatus
    event_count: int
    source_tier: SourceTier | None

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        start = _parse_date(self.start_date, "start_date")
        end = _parse_date(self.end_date, "end_date")
        if end < start:
            raise ValueError("end_date must not precede start_date")
        if self.status not in {
            "official_confirmed",
            "aggregator_checked",
            "blocked",
            "uncovered",
        }:
            raise ValueError("unsupported continuity status")
        if (
            isinstance(self.event_count, bool)
            or not isinstance(self.event_count, int)
            or self.event_count < 0
        ):
            raise ValueError("event_count must be a non-negative integer")
        if self.status == "official_confirmed" and (
            self.source_tier != "official" or self.event_count != 0
        ):
            raise ValueError("official confirmation requires no events")
        if self.status == "aggregator_checked" and (
            self.source_tier != "aggregator" or self.event_count != 0
        ):
            raise ValueError("aggregator check requires no events")
        if self.status == "blocked" and (
            self.source_tier not in {"official", "aggregator"}
            or self.event_count == 0
        ):
            raise ValueError("blocked assessment requires event evidence")
        if self.status == "uncovered" and (
            self.source_tier is not None or self.event_count != 0
        ):
            raise ValueError("uncovered assessment cannot claim evidence")


class ETFShareContinuityService:
    def assess(
        self,
        asset_id: str,
        start: str,
        end: str,
        coverages: Sequence[ETFShareUnitCoverage],
    ) -> ETFShareContinuityAssessment:
        _require_text(asset_id, "asset_id")
        start_date = _parse_date(start, "start")
        end_date = _parse_date(end, "end")
        if end_date < start_date:
            raise ValueError("end must not precede start")
        relevant = [
            coverage
            for coverage in coverages
            if coverage.asset_id == asset_id
        ]
        events = {
            (
                event.event_date,
                event.factor,
                event.source_tier,
                event.source,
                event.source_url,
            ): event
            for coverage in relevant
            for event in coverage.events
            if start_date <= _parse_date(event.event_date, "event_date") <= end_date
        }
        if events:
            source_tier: SourceTier = (
                "official"
                if any(event.source_tier == "official" for event in events.values())
                else "aggregator"
            )
            return ETFShareContinuityAssessment(
                asset_id=asset_id,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                status="blocked",
                event_count=len(events),
                source_tier=source_tier,
            )

        official = [
            coverage for coverage in relevant if coverage.source_tier == "official"
        ]
        if _fully_covers(official, start_date, end_date):
            return ETFShareContinuityAssessment(
                asset_id=asset_id,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                status="official_confirmed",
                event_count=0,
                source_tier="official",
            )
        aggregator = [
            coverage for coverage in relevant if coverage.source_tier == "aggregator"
        ]
        if _fully_covers(aggregator, start_date, end_date):
            return ETFShareContinuityAssessment(
                asset_id=asset_id,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                status="aggregator_checked",
                event_count=0,
                source_tier="aggregator",
            )
        return ETFShareContinuityAssessment(
            asset_id=asset_id,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            status="uncovered",
            event_count=0,
            source_tier=None,
        )

    def apply_to_observation(
        self,
        observation: ETFDailyObservation,
        assessment: ETFShareContinuityAssessment,
    ) -> ETFDailyObservation:
        return apply_to_observation(observation, assessment)


def apply_to_observation(
    observation: ETFDailyObservation,
    assessment: ETFShareContinuityAssessment,
) -> ETFDailyObservation:
    if not isinstance(observation, ETFDailyObservation):
        raise TypeError("observation must be ETFDailyObservation")
    if not isinstance(assessment, ETFShareContinuityAssessment):
        raise TypeError("assessment must be ETFShareContinuityAssessment")
    if observation.instrument_asset_id != assessment.asset_id:
        raise ValueError("observation and assessment asset_ids must match")
    trade_date = _parse_date(observation.trade_date, "trade_date")
    if not (
        _parse_date(assessment.start_date, "start_date")
        <= trade_date
        <= _parse_date(assessment.end_date, "end_date")
    ):
        raise ValueError("observation trade_date must be within assessment")
    return replace(
        observation,
        share_unit_continuity_confirmed=(
            assessment.status == "official_confirmed"
        ),
    )


def _fully_covers(
    coverages: Sequence[ETFShareUnitCoverage],
    start: date,
    end: date,
) -> bool:
    intervals = sorted(
        (
            _parse_date(item.coverage_start, "coverage_start"),
            _parse_date(item.coverage_end, "coverage_end"),
        )
        for item in coverages
    )
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_end < cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = interval_end + timedelta(days=1)
        if cursor > end:
            return True
    return cursor > end


def _require_source(value: str) -> None:
    if value not in {"official", "aggregator"}:
        raise ValueError("source_tier must be official or aggregator")


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_url(value: str, field_name: str) -> None:
    _require_text(value, field_name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc
