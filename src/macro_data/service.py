"""Point-in-time macro series merged from several sources fetched at once."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from functools import partial

from src.data_src.fan_out import fan_out
from src.data_src.models import MacroObservation
from src.data_src.process_executor import run_provider_task

from .catalog import MACRO_SERIES, MacroSeriesDefinition, macro_series
from .models import (
    AVAILABLE_INFERRED,
    AVAILABLE_PUBLISHED,
    MacroPoint,
    MacroSeriesResult,
)
from .sqlite_repository import SQLiteMacroDataRepository

Fetcher = Callable[[str, str, str, str, float], list[MacroObservation]]
_GRACE_SECONDS = 10.0


def _fetch_in_process(
    source: str,
    series_id: str,
    start_date: str,
    end_date: str,
    timeout_seconds: float,
) -> list[MacroObservation]:
    return run_provider_task(
        "src.data_src.macro_sources",
        "fetch_macro_observations",
        {
            "source": source,
            "series_id": series_id,
            "start_date": start_date,
            "end_date": end_date,
        },
        timeout_seconds=timeout_seconds,
    )


class MacroDataService:
    def __init__(
        self,
        repository: SQLiteMacroDataRepository,
        *,
        fetcher: Fetcher = _fetch_in_process,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher
        self.clock = clock
        self.repository.initialize()

    def catalog(self) -> tuple[MacroSeriesDefinition, ...]:
        return MACRO_SERIES

    def get(
        self,
        series_id: str,
        *,
        start_date: str,
        end_date: str,
        as_of_date: str | None = None,
        allow_remote: bool = True,
        max_age_seconds: int | None = 86400,
        provider_timeout_seconds: float = 60.0,
    ) -> MacroSeriesResult:
        """Series values, visible only once published as of ``as_of_date``."""

        definition = macro_series(series_id)
        if date.fromisoformat(start_date) > date.fromisoformat(end_date):
            raise ValueError("start_date must not be after end_date")
        last_fetched = self.repository.last_successful_fetch(
            series_id,
            start_date,
            end_date,
            max_age_seconds=max_age_seconds,
        )
        fetched_remote = False
        successful: tuple[str, ...] = ()
        errors: dict[str, str] = {}
        if last_fetched is None and allow_remote:
            fetched_remote = True
            successful, errors = self._fetch(
                definition,
                start_date,
                end_date,
                provider_timeout_seconds,
            )
            if successful:
                last_fetched = self.repository.last_successful_fetch(
                    series_id,
                    start_date,
                    end_date,
                    max_age_seconds=None,
                )
        observations = self.repository.list_observations(
            series_id,
            start_date,
            end_date,
        )
        points, conflicts = _merge(definition, observations)
        if as_of_date is not None:
            points = [
                item
                for item in points
                if item.available_at <= as_of_date and item.period_end <= as_of_date
            ]
        return MacroSeriesResult(
            definition=definition,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            points=tuple(points),
            fetched_remote=fetched_remote,
            stale=last_fetched is None,
            successful_sources=successful,
            provider_errors=errors,
            conflicts=tuple(conflicts),
            last_fetched_at=last_fetched,
        )

    def _fetch(
        self,
        definition: MacroSeriesDefinition,
        start_date: str,
        end_date: str,
        timeout_seconds: float,
    ) -> tuple[tuple[str, ...], dict[str, str]]:
        outcomes = fan_out(
            {
                source: partial(
                    self._fetch_and_store,
                    source,
                    definition.series_id,
                    start_date,
                    end_date,
                    timeout_seconds,
                )
                for source in definition.sources
            },
            accept=bool,
            grace_seconds=_GRACE_SECONDS,
        )
        successful = []
        errors = {}
        for source, outcome in outcomes.items():
            if isinstance(outcome, Exception):
                errors[source] = f"{type(outcome).__name__}: {outcome}"
            elif not outcome:
                errors[source] = "no observations in range"
            else:
                successful.append(source)
        return tuple(successful), errors

    def _fetch_and_store(
        self,
        source: str,
        series_id: str,
        start_date: str,
        end_date: str,
        timeout_seconds: float,
    ) -> int:
        """Store and log inside the worker so late sources still land in the cache."""

        error = None
        count = 0
        try:
            observations = self.fetcher(
                source,
                series_id,
                start_date,
                end_date,
                timeout_seconds,
            )
            count = self.repository.upsert(list(observations), self.clock().isoformat())
            if not count:
                error = "no observations in range"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.repository.log_fetch(
                series_id=series_id,
                source=source,
                start_date=start_date,
                end_date=end_date,
                fetched_at=self.clock().isoformat(),
                record_count=count,
                error=error,
            )
        return count


def _merge(
    definition: MacroSeriesDefinition,
    observations: list[MacroObservation],
) -> tuple[list[MacroPoint], list[dict[str, object]]]:
    """Value from the highest-priority source; earliest agreeing publication date."""

    rank = {source: index for index, source in enumerate(definition.sources)}
    by_period: dict[str, list[MacroObservation]] = {}
    for item in observations:
        by_period.setdefault(item.period, []).append(item)
    points = []
    conflicts = []
    for period, candidates in by_period.items():
        candidates.sort(key=lambda item: rank.get(_source_name(item.source), len(rank)))
        chosen = candidates[0]
        agreeing = [item for item in candidates if _agrees(item.value, chosen.value)]
        disagreeing = [item for item in candidates if item not in agreeing]
        if disagreeing:
            conflicts.append(
                {
                    "period": period,
                    "values": {item.source: item.value for item in candidates},
                }
            )
        published = sorted(item.published_at for item in agreeing if item.published_at)
        if published:
            available_at, basis = published[0], AVAILABLE_PUBLISHED
        else:
            available_at = definition.release_rule.available_at(chosen.period_end)
            basis = AVAILABLE_INFERRED
        points.append(
            MacroPoint(
                period=period,
                period_end=chosen.period_end,
                value=chosen.value,
                unit=chosen.unit,
                available_at=available_at,
                available_at_basis=basis,
                source=chosen.source,
                confirmed_by=tuple(
                    sorted({item.source for item in agreeing if item is not chosen})
                ),
            )
        )
    points.sort(key=lambda item: (item.period_end, item.period))
    return points, conflicts


def _agrees(value: float, reference: float) -> bool:
    # Published figures are rounded to 0.01 or 0.1; amounts to 0.5%.
    return abs(value - reference) <= max(0.051, abs(reference) * 0.005)


def _source_name(source: str) -> str:
    return source.split(":", 1)[0]
