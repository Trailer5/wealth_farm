"""Recommended position size for one holding the user tracks only as a share of assets (e.g. employer stock).

The user does not report quantities; each period the Agent records a recommended weight range for the
asset, and the service compares it with the previous recommendation to state the conclusion: add,
reduce, exit or hold. Every recommendation is a personal settleable action, so later settlements show
whether add and reduce calls beat the benchmark. Design notes: docs/modules/6-编排/研究编排模块.md
「单一持仓推荐仓位」.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from src.master_data import MasterDataService
from src.research_contracts import (
    ActionTrigger,
    ActionType,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
)
from src.research_ledger import LedgerEntryType, LedgerScope, ResearchLedgerService

SOURCE_PREFIX = "position-guidance:"
# Changes smaller than one percentage point keep the previous conclusion's position.
MIN_CHANGE = Decimal("0.01")
_LABELS = {
    ActionType.ADD: "加持",
    ActionType.REDUCE: "减持",
    ActionType.EXIT: "清仓",
    ActionType.HOLD: "维持",
}


class PositionGuidanceService:
    def __init__(self, *, ledger: ResearchLedgerService, master_data: MasterDataService) -> None:
        self.ledger = ledger
        self.master_data = master_data

    def record(
        self,
        *,
        user_id: str,
        asset_id: str,
        as_of_date: str,
        target: Decimal,
        low: Decimal,
        high: Decimal,
        review_on: str,
        basis: str,
        benchmark_id: str | None = None,
        refs: tuple[str, ...] = (),
        current: Decimal | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Compare with the latest earlier recommendation, then record this one unless dry_run.

        Without an earlier recommendation, ``current`` (the user's actual share now) is the baseline, so a
        first recommendation can open or close a position. Recording again on the same date revises that
        day's entry.
        """

        if not Decimal(0) <= low <= target <= high <= Decimal(1):
            raise ValueError("需要 0 ≤ 下限 ≤ 目标 ≤ 上限 ≤ 1（比例用小数，5% 写 0.05）")
        if current is not None and not Decimal(0) <= current <= Decimal(1):
            raise ValueError("--current 是当前实际占比，用 0–1 的小数")
        if not basis.strip():
            raise ValueError("--basis 写明推荐比例的依据")
        if review_on <= as_of_date:
            raise ValueError("--review-on 必须晚于截止日")
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise ValueError(f"未知标的 {asset_id}")
        rows = self.history(user_id=user_id, asset_id=asset_id)
        previous = next((item for item in reversed(rows) if item["as_of_date"] < as_of_date), None)
        same_day = next((item for item in rows if item["as_of_date"] == as_of_date), None)
        baseline = previous or (None if current is None else {"target": str(current), "as_of_date": None})
        action_type = _conclusion(baseline, target)
        details = {
            "target": str(target),
            "low": str(low),
            "high": str(high),
            "previous_target": previous["target"] if previous else None,
            "previous_as_of_date": previous["as_of_date"] if previous else None,
            "current": None if current is None else str(current),
            "basis": basis.strip(),
        }
        revision = 1 + sum(1 for item in self._all_entries(user_id, asset_id) if item["as_of_date"] == as_of_date)
        action = SettleableAction(
            action_id=f"guidance:{user_id}:{asset.standard_symbol}:{as_of_date}" + (f":r{revision}" if same_day else ""),
            asset_id=asset_id,
            action_type=action_type,
            horizon=ResearchHorizon.MEDIUM_SHORT,
            trigger=ActionTrigger(
                kind=TriggerKind.SCHEDULED,
                valid_from=as_of_date,
                valid_until=review_on,
                scheduled_on=as_of_date,
            ),
            quantity_rule=QuantityRule(
                kind=QuantityRuleKind.USER_DEFINED,
                description=json.dumps(details, ensure_ascii=False, sort_keys=True),
            ),
            settlement_rule=SettlementRule(SettlementMethod.PRICE_RELATIVE, asset_id, benchmark_id=benchmark_id),
            invalidation_refs=tuple(refs),
        )
        entry_id = None
        source_ref = f"{SOURCE_PREFIX}{asset_id}:{as_of_date}"
        if not dry_run and same_day:
            entry_id = self.ledger.record_revision(
                action,
                supersedes_entry_id=same_day["ledger_entry_id"],
                user_id=user_id,
                source_ref=f"{source_ref}:r{revision}",
            ).entry_id
        elif not dry_run:
            (entry,) = self.ledger.record(
                (action,),
                scope=LedgerScope.PERSONAL,
                user_id=user_id,
                source_ref=source_ref,
            )
            entry_id = entry.entry_id
        return {
            "asset": {"asset_id": asset_id, "symbol": asset.standard_symbol, "name": asset.display_name},
            "as_of_date": as_of_date,
            "recommended": {"target": str(target), "low": str(low), "high": str(high)},
            "previous": previous,
            "current": None if current is None else str(current),
            "revises": same_day["ledger_entry_id"] if same_day else None,
            "conclusion": action_type.value,
            "conclusion_label": _label(previous, current, action_type),
            "execution": _execution(low, target, high),
            "review_on": review_on,
            "ledger_entry_id": entry_id,
            "recorded": not dry_run,
        }

    def history(self, *, user_id: str, asset_id: str) -> list[dict[str, Any]]:
        """Recommendations in force, one per date (a same-day revision replaces the earlier entry)."""

        superseded = self.ledger.repository.superseded_entry_ids()
        return [item for item in self._all_entries(user_id, asset_id) if item["ledger_entry_id"] not in superseded]

    def _all_entries(self, user_id: str, asset_id: str) -> list[dict[str, Any]]:
        rows = []
        record = self.ledger.track_record(user_id=user_id, entry_type=LedgerEntryType.ACTION, subject_id=asset_id)
        for entry in record["entries"]:
            if not entry["source_ref"].startswith(SOURCE_PREFIX):
                continue
            artifact = entry["artifact"]
            details = json.loads(artifact["quantity_rule"]["description"])
            settled = [item for item in entry["settlements"] if item["status"] == "settled"]
            rows.append(
                {
                    "ledger_entry_id": entry["entry_id"],
                    "as_of_date": entry["as_of_date"],
                    "review_on": artifact["trigger"]["valid_until"],
                    "target": details["target"],
                    "low": details["low"],
                    "high": details["high"],
                    "conclusion": artifact["action_type"],
                    "basis": details["basis"],
                    "latest_settlement": settled[-1] if settled else None,
                }
            )
        return sorted(rows, key=lambda item: item["as_of_date"])

    def due(self, *, user_id: str, as_of_date: str) -> list[dict[str, Any]]:
        """Assets whose latest recommendation has reached its review date."""

        latest: dict[str, dict[str, Any]] = {}
        superseded = self.ledger.repository.superseded_entry_ids()
        record = self.ledger.track_record(user_id=user_id, entry_type=LedgerEntryType.ACTION)
        for entry in record["entries"]:
            if entry["entry_id"] in superseded:
                continue
            if entry["source_ref"].startswith(SOURCE_PREFIX) and entry["as_of_date"] <= as_of_date:
                current = latest.get(entry["subject_id"])
                if current is None or entry["as_of_date"] > current["as_of_date"]:
                    latest[entry["subject_id"]] = entry
        rows = []
        for asset_id, entry in sorted(latest.items()):
            review_on = entry["artifact"]["trigger"]["valid_until"]
            if review_on <= as_of_date:
                asset = self.master_data.get_asset(asset_id)
                rows.append(
                    {
                        "asset_id": asset_id,
                        "symbol": asset.standard_symbol if asset else None,
                        "name": asset.display_name if asset else None,
                        "market": asset.market if asset else None,
                        "last_as_of_date": entry["as_of_date"],
                        "review_on": review_on,
                    }
                )
        return rows


def _conclusion(previous: dict[str, Any] | None, target: Decimal) -> ActionType:
    if previous is None:
        return ActionType.HOLD
    change = target - Decimal(previous["target"])
    if target == 0 and Decimal(previous["target"]) > 0:
        return ActionType.EXIT
    if change >= MIN_CHANGE:
        return ActionType.ADD
    if change <= -MIN_CHANGE:
        return ActionType.REDUCE
    return ActionType.HOLD


def _label(previous: dict[str, Any] | None, current: Decimal | None, action_type: ActionType) -> str:
    if previous is not None:
        return _LABELS[action_type]
    if current is None:
        return "首期基准"
    if current == 0 and action_type is ActionType.ADD:
        return "首期：建仓"
    return f"首期：{_LABELS[action_type]}（对照当前实际占比）"


def _execution(low: Decimal, target: Decimal, high: Decimal) -> list[str]:
    def pct(value: Decimal) -> str:
        return f"{value * 100:.1f}%"

    return [
        f"实际占比高于 {pct(high)}：在可交易时卖出到约 {pct(target)}",
        f"实际占比在 {pct(low)}–{pct(high)}：不动",
        f"实际占比低于 {pct(low)}：结论为加持时买到约 {pct(target)}，否则不动",
    ]
