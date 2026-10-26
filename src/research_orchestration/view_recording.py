"""Record a falsifiable view written by the Agent, checked to be settleable."""

from __future__ import annotations

from typing import Any

from src.master_data import MasterDataService
from src.research_contracts import SettlementMethod, View, ViewOrigin
from src.research_ledger import (
    LedgerScope,
    MacroMetricSeriesSource,
    ResearchLedgerService,
    settlement_schedule,
)


class ViewRecorder:
    def __init__(
        self,
        *,
        ledger: ResearchLedgerService,
        metrics: MacroMetricSeriesSource,
        master_data: MasterDataService,
    ) -> None:
        self.ledger = ledger
        self.metrics = metrics
        self.master_data = master_data

    def record(
        self,
        view: View,
        *,
        supersedes_entry_id: str | None = None,
    ) -> dict[str, Any]:
        """User views are personal, system views objective; revisions keep the scope."""

        rule = view.settlement_rule
        schedule = settlement_schedule(view)
        if rule is not None and schedule.unsettleable_reason is not None:
            raise ValueError(schedule.unsettleable_reason)
        if (
            rule is not None
            and rule.method is SettlementMethod.METRIC_OBSERVATION
            and not self.metrics.known(rule.observed_id)
        ):
            raise ValueError(
                f"未知指标 {rule.observed_id}；可用指标见 cli macro，ID 写作 macro:<series_id>"
            )
        if rule is not None and rule.method is SettlementMethod.PRICE_RELATIVE:
            for asset_id in (rule.observed_id, rule.benchmark_id, rule.alternative_id):
                if asset_id is not None and self.master_data.get_asset(asset_id) is None:
                    raise ValueError(
                        f"未知标的 {asset_id}；用 cli asset --symbol <代码> 查 asset_id"
                    )
        user_id = view.user_id if view.origin is ViewOrigin.USER else None
        source_ref = f"agent-view:{view.view_id}"
        if supersedes_entry_id is not None:
            entry = self.ledger.record_revision(
                view,
                supersedes_entry_id=supersedes_entry_id,
                user_id=user_id,
                source_ref=source_ref,
            )
        else:
            (entry,) = self.ledger.record(
                (view,),
                scope=(
                    LedgerScope.PERSONAL
                    if user_id is not None
                    else LedgerScope.OBJECTIVE
                ),
                user_id=user_id,
                source_ref=source_ref,
            )
        return {
            "ledger_entry_id": entry.entry_id,
            "scope": entry.scope.value,
            "supersedes_entry_id": supersedes_entry_id,
            "settleable": rule is not None,
            "checkpoints": list(schedule.checkpoints),
        }
