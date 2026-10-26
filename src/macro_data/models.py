"""Macro series query results."""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import MacroSeriesDefinition

AVAILABLE_PUBLISHED = "published"
AVAILABLE_INFERRED = "inferred_conservative"


@dataclass(frozen=True)
class MacroPoint:
    period: str
    period_end: str
    value: float
    unit: str
    available_at: str
    # "published" when a source states the date, otherwise the catalog rule.
    available_at_basis: str
    source: str
    confirmed_by: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "period": self.period,
            "period_end": self.period_end,
            "value": self.value,
            "unit": self.unit,
            "available_at": self.available_at,
            "available_at_basis": self.available_at_basis,
            "source": self.source,
            "confirmed_by": list(self.confirmed_by),
        }


@dataclass(frozen=True)
class MacroSeriesResult:
    definition: MacroSeriesDefinition
    start_date: str
    end_date: str
    as_of_date: str | None
    points: tuple[MacroPoint, ...]
    fetched_remote: bool
    stale: bool
    successful_sources: tuple[str, ...] = ()
    provider_errors: dict[str, str] = field(default_factory=dict)
    conflicts: tuple[dict[str, object], ...] = ()
    last_fetched_at: str | None = None

    @property
    def latest(self) -> MacroPoint | None:
        return self.points[-1] if self.points else None

    def to_dict(self, *, limit: int | None = None) -> dict[str, object]:
        points = self.points if limit is None else self.points[-limit:]
        return {
            "series": self.definition.to_dict(),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "as_of_date": self.as_of_date,
            "point_count": len(self.points),
            "latest": self.latest.to_dict() if self.latest else None,
            "points": [item.to_dict() for item in points],
            "fetched_remote": self.fetched_remote,
            "stale": self.stale,
            "last_fetched_at": self.last_fetched_at,
            "successful_sources": list(self.successful_sources),
            "provider_errors": self.provider_errors,
            "conflicts": list(self.conflicts),
        }
