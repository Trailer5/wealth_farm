"""Run forward settlement over every due ledger checkpoint."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from src.research_contracts import SettlementMethod, settlement_checkpoints

from .models import ClaimResult, Settlement, SettlementStatus, new_settlement
from .settlement import (
    MetricSeriesSource,
    PriceDataUnavailable,
    PricePoint,
    PriceSeriesSource,
    SettlementSchedule,
    settle_checkpoint,
    settlement_schedule,
    trigger_date,
)
from .sqlite_repository import SQLiteResearchLedgerRepository

_FINAL_STATUSES = frozenset(
    {SettlementStatus.SETTLED, SettlementStatus.NOT_SETTLEABLE}
)
# Covers a quarterly series plus its release lag, so the reference is found.
_METRIC_LOOKBACK_DAYS = 400


@dataclass(frozen=True)
class SettlementRunSummary:
    as_of_date: str
    appended: tuple[Settlement, ...]
    status_counts: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of_date": self.as_of_date,
            "appended_settlement_ids": [
                item.settlement_id for item in self.appended
            ],
            "status_counts": dict(self.status_counts),
        }


class LedgerSettlementService:
    def __init__(
        self,
        *,
        repository: SQLiteResearchLedgerRepository,
        prices: PriceSeriesSource,
        metrics: MetricSeriesSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.prices = prices
        self.metrics = metrics
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.repository.initialize()

    def settle_due(
        self,
        as_of_date: str,
        *,
        recompute: bool = False,
    ) -> SettlementRunSummary:
        """Settle due checkpoints; recompute appends results of revised data."""

        computed_at = self.clock().isoformat()
        cache: dict[
            tuple[str, str, str],
            tuple[PricePoint, ...] | PriceDataUnavailable,
        ] = {}
        appended = []
        for entry in self.repository.all_entries():
            artifact = entry.artifact
            schedule = settlement_schedule(artifact)
            if schedule is None:
                continue
            if schedule.condition is not None and schedule.unsettleable_reason is None:
                appended.extend(
                    self._settle_conditional(
                        entry.entry_id, artifact, schedule, as_of_date, recompute, computed_at, cache
                    )
                )
                continue
            done = {
                item.checkpoint_date
                for item in self.repository.list_settlements(entry.entry_id)
                if item.status in _FINAL_STATUSES
            }
            due = tuple(
                item
                for item in schedule.checkpoints
                if item <= as_of_date and (recompute or item not in done)
            )
            if not due:
                continue
            prices = {}
            rule = schedule.rule
            if (
                schedule.unsettleable_reason is None
                and rule is not None
                and rule.method is SettlementMethod.METRIC_OBSERVATION
            ):
                prices[rule.observed_id] = self._metric_series(
                    cache,
                    rule.observed_id,
                    schedule.start,
                    due[-1],
                )
            elif schedule.unsettleable_reason is None and rule is not None:
                for asset_id in (
                    rule.observed_id,
                    rule.benchmark_id,
                    rule.alternative_id,
                ):
                    if asset_id is not None:
                        prices[asset_id] = self._series(
                            cache,
                            asset_id,
                            schedule.start,
                            due[-1],
                        )
            for checkpoint in due:
                stored, inserted = self.repository.append_settlement(
                    settle_checkpoint(
                        entry_id=entry.entry_id,
                        artifact=artifact,
                        schedule=schedule,
                        checkpoint=checkpoint,
                        prices=prices,
                        computed_at=computed_at,
                    )
                )
                if inserted:
                    appended.append(stored)
        return SettlementRunSummary(
            as_of_date=as_of_date,
            appended=tuple(appended),
            status_counts=dict(
                Counter(item.status.value for item in appended)
            ),
        )

    def _settle_conditional(
        self,
        entry_id: str,
        artifact,
        schedule: SettlementSchedule,
        as_of_date: str,
        recompute: bool,
        computed_at: str,
        cache: dict,
    ) -> list[Settlement]:
        """Find when the condition was first observed, then settle from that date like a scheduled action."""

        condition = schedule.condition
        valid_until = schedule.valid_until
        window_end = min(as_of_date, valid_until)
        if window_end <= schedule.start:
            return []
        series = (
            self._series(cache, condition.observed_id, schedule.start, window_end)
            if condition.on_price
            else self._metric_series(cache, condition.observed_id, schedule.start, window_end)
        )
        if isinstance(series, PriceDataUnavailable):
            return []
        triggered_on = trigger_date(condition, series, schedule.start, valid_until)
        done = {
            item.checkpoint_date
            for item in self.repository.list_settlements(entry_id)
            if item.status in _FINAL_STATUSES
        }
        if triggered_on is None:
            if as_of_date < valid_until or (valid_until in done and not recompute):
                return []
            stored, inserted = self.repository.append_settlement(
                new_settlement(
                    entry_id=entry_id,
                    checkpoint_date=valid_until,
                    status=SettlementStatus.NOT_SETTLEABLE,
                    claim_result=ClaimResult.UNKNOWN,
                    computed_at=computed_at,
                    note=f"条件在 {schedule.start} 至 {valid_until} 内未出现，动作未触发",
                )
            )
            return [stored] if inserted else []
        effective = SettlementSchedule(
            start=triggered_on,
            checkpoints=settlement_checkpoints(artifact.horizon, triggered_on),
            rule=schedule.rule,
        )
        due = tuple(
            item
            for item in effective.checkpoints
            if item <= as_of_date and (recompute or item not in done)
        )
        if not due:
            return []
        rule = schedule.rule
        prices = {
            asset_id: self._series(cache, asset_id, triggered_on, due[-1])
            for asset_id in (rule.observed_id, rule.benchmark_id, rule.alternative_id)
            if asset_id is not None
        }
        appended = []
        for checkpoint in due:
            stored, inserted = self.repository.append_settlement(
                settle_checkpoint(
                    entry_id=entry_id,
                    artifact=artifact,
                    schedule=effective,
                    checkpoint=checkpoint,
                    prices=prices,
                    computed_at=computed_at,
                    note=f"条件于 {triggered_on} 首次出现（{artifact.trigger.observability_key}），按触发日起算",
                )
            )
            if inserted:
                appended.append(stored)
        return appended

    def _series(
        self,
        cache: dict,
        asset_id: str,
        start: str,
        end: str,
    ) -> tuple[PricePoint, ...] | PriceDataUnavailable:
        key = (asset_id, start, end)
        if key not in cache:
            try:
                cache[key] = self.prices.series(asset_id, start, end)
            except PriceDataUnavailable as exc:
                cache[key] = exc
        return cache[key]

    def _metric_series(self, cache: dict, metric_id: str, start: str, end: str):
        key = ("metric", metric_id, start, end)
        if key not in cache:
            if self.metrics is None:
                cache[key] = PriceDataUnavailable("指标数据源未接入结算服务")
            else:
                lookback = (
                    date.fromisoformat(start)
                    - timedelta(days=_METRIC_LOOKBACK_DAYS)
                ).isoformat()
                try:
                    cache[key] = self.metrics.series(metric_id, lookback, end)
                except PriceDataUnavailable as exc:
                    cache[key] = exc
        return cache[key]
