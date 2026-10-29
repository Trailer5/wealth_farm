"""Forward tracking of archived strategies (stage 9 slice 8).

Each run takes the strategy's target weights at the latest close and records them as scheduled, settleable
actions in the objective ledger (source `strategy:<id>:<version>:<signal date>`); existing settlement rules settle
them. Signal days that fell between two runs are written to the archive and never recreated. The forward record
(settled signals, hit-rate interval, forward excess over the simple benchmark) feeds the gate to "enabled".

Design notes: docs/modules/3-分析工具/策略规则模块.md「前向跟踪」.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.backtest.target_weights import TargetSignal
from src.research_contracts import (
    ActionTrigger,
    ActionType,
    QuantityRule,
    QuantityRuleKind,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
)
from src.research_ledger import LedgerEntryType, LedgerScope, ResearchLedgerService
from src.research_ledger.decision_review import summarize_decisions
from src.strategy import STATE_LABELS, LifecycleState, StrategyArchive

from .strategy_archive import StrategyArchiveError, StrategyArchiveService
from .strategy_backtest import SELECTION_TEMPLATE, StrategyBacktestService

TRACKED_STATES = (LifecycleState.FORWARD_TRACKING, LifecycleState.ENABLED)
# Calendar days of history loaded before the run date; covers the longest template warm-up.
HISTORY_DAYS = 500


class StrategyTrackService:
    def __init__(
        self, *, backtest: StrategyBacktestService, archives: StrategyArchiveService, ledger: ResearchLedgerService
    ) -> None:
        self.backtest = backtest
        self.archives = archives
        self.ledger = ledger

    def track_all(self, *, as_of_date: str, allow_remote: bool = True) -> list[dict[str, Any]]:
        """Track every strategy in forward tracking or enabled; one failure does not stop the others."""

        results = []
        for archive, state, _ in self.backtest.repository.list_latest():
            if state not in TRACKED_STATES:
                continue
            try:
                results.append(self.track(archive.strategy_id, as_of_date=as_of_date, allow_remote=allow_remote))
            except (StrategyArchiveError, ValueError, OSError, RuntimeError) as exc:
                results.append({"strategy_id": archive.strategy_id, "status": "failed", "error": str(exc)})
        return results

    def track(
        self, strategy_id: str, *, as_of_date: str, version: str | None = None, allow_remote: bool = True
    ) -> dict[str, Any]:
        archive, state, parameters = self.backtest.resolve(strategy_id, as_of_date=as_of_date, version=version)
        if state not in TRACKED_STATES:
            raise StrategyArchiveError(
                f"{archive.name} 处于“{STATE_LABELS[state]}”；只有“前向跟踪”或“启用”的策略生成前向信号，"
                "先用 strategy-evaluate 完整验证再迁移到 forward_tracking"
            )
        since = self._tracking_since(archive)
        gaps: list[str] = []
        start = (date.fromisoformat(as_of_date) - timedelta(days=HISTORY_DAYS)).isoformat()
        prepared = self.backtest.prepare(
            archive, parameters, start=start, end=as_of_date, allow_remote=allow_remote, gaps=gaps
        )
        calendar = sorted({bar.trade_date for bar in prepared.bars if bar.trade_date <= as_of_date})
        signal_date = calendar[-1]
        recorded = self.backtest.repository.forward_signals(archive.strategy_id, archive.version)
        last = recorded[-1] if recorded else None
        status, missed, entry_ids = "recorded", [], []
        current = [s for s in prepared.inputs.signals if s.as_of_date <= signal_date]
        if signal_date < since:
            status = "no_data_since_tracking"
        elif last is not None and last["signal_date"] >= signal_date:
            status = "already_recorded"
        else:
            if not current:
                raise StrategyArchiveError(f"{signal_date} 之前没有信号（历史不足以计算参数窗口）")
            target = current[-1]
            window_start = last["signal_date"] if last else since
            missed = [s.as_of_date for s in current if window_start < s.as_of_date < signal_date]
            if missed:
                self.backtest.repository.record_event(
                    archive,
                    event_type="forward_gap",
                    from_state=None,
                    to_state=None,
                    on_date=as_of_date,
                    reason=f"{window_start} 到 {signal_date} 之间有 {len(missed)} 个信号日没有运行跟踪，未补造",
                    detail={"missed_signal_dates": missed},
                )
            entry_ids = self._record_actions(archive, prepared, target, signal_date, last)
            self.backtest.repository.record_forward_signal(
                archive,
                signal_date=signal_date,
                weights=dict(target.weights),
                reason=target.reason,
                entry_ids=entry_ids,
                recorded_on=as_of_date,
            )
        forward = self._forward_status(archive, prepared, since, as_of_date)
        self.backtest.repository.record_forward_status(archive, as_of_date=as_of_date, payload=forward)
        shown = self.archives.show(archive.strategy_id, as_of_date, archive.version)
        signals = self.backtest.repository.forward_signals(archive.strategy_id, archive.version)
        weights = _named(signals[-1]["weights"], prepared) if signals else {}
        held = {symbol: w for symbol, w in weights.items() if w > 0}
        return {
            "strategy_id": archive.strategy_id,
            "version": archive.version,
            "name": archive.name,
            "status": status,
            "signal_date": signal_date,
            "rule_signal_date": current[-1].as_of_date if current else None,
            "rule_reason": current[-1].reason if current else None,
            "target_weights": weights,
            "position": (
                "、".join(f"{symbol} {w:.0%}" for symbol, w in held.items()) if held else "全部现金"
            ) if signals else None,
            "ledger_entries": entry_ids,
            "missed_signal_dates": missed,
            "signals_recorded": len(signals),
            "forward": forward,
            "lifecycle_state": shown["state"],
            "state_label": shown["state_label"],
            "next_state": shown["next_state"],
            "next_blockers": shown["next_blockers"],
            "gaps": gaps,
        }

    def _record_actions(
        self, archive: StrategyArchive, prepared: Any, target: TargetSignal, signal_date: str, last: dict | None
    ) -> list[str]:
        """Held assets as buys; dropped holdings, and for fixed universes every asset not held, as exits against the
        largest holding (or against nothing, i.e. cash)."""

        weights = {asset: w for asset, w in target.weights.items() if w > 0}
        previous = {asset: w for asset, w in (last["weights"] if last else {}).items() if w > 0}
        top = max(weights, key=weights.get) if weights else None
        exits = set(previous) - set(weights)
        if archive.template_id != SELECTION_TEMPLATE:
            exits |= set(prepared.inputs.assets) - set(weights)
        horizon = archive.horizons[0]
        trigger = ActionTrigger(TriggerKind.SCHEDULED, signal_date, signal_date, scheduled_on=signal_date)
        actions = []
        for asset_id in sorted(weights):
            actions.append(
                SettleableAction(
                    action_id=f"strategy:{archive.strategy_id}:{archive.version}:{signal_date}:{asset_id}",
                    asset_id=asset_id,
                    action_type=ActionType.BUY,
                    horizon=horizon,
                    trigger=trigger,
                    quantity_rule=QuantityRule(QuantityRuleKind.USER_DEFINED, description=f"目标权重 {weights[asset_id]:.1%}"),
                    settlement_rule=SettlementRule(SettlementMethod.PRICE_RELATIVE, asset_id),
                )
            )
        for asset_id in sorted(exits):
            alternative = top if top != asset_id else None
            actions.append(
                SettleableAction(
                    action_id=f"strategy:{archive.strategy_id}:{archive.version}:{signal_date}:{asset_id}",
                    asset_id=asset_id,
                    action_type=ActionType.EXIT,
                    horizon=horizon,
                    trigger=trigger,
                    quantity_rule=QuantityRule(QuantityRuleKind.USER_DEFINED, description="目标权重 0"),
                    settlement_rule=SettlementRule(SettlementMethod.PRICE_RELATIVE, asset_id, alternative_id=alternative),
                )
            )
        entries = self.ledger.record(
            tuple(actions),
            scope=LedgerScope.OBJECTIVE,
            user_id=None,
            source_ref=f"strategy:{archive.strategy_id}:{archive.version}:{signal_date}",
        )
        return [entry.entry_id for entry in entries]

    def _forward_status(self, archive: StrategyArchive, prepared: Any, since: str, as_of_date: str) -> dict[str, Any]:
        prefix = f"strategy:{archive.strategy_id}:{archive.version}:"
        entries = [
            entry
            for entry in self.ledger.track_record(user_id=None, entry_type=LedgerEntryType.ACTION)["entries"]
            if entry["source_ref"].startswith(prefix)
        ]
        overall = summarize_decisions(entries, as_of_date=as_of_date)["actions"]["overall"]
        interval = overall.get("hit_rate_interval")
        settled = int(overall.get("directional") or 0)
        signals = self.backtest.repository.forward_signals(archive.strategy_id, archive.version)
        excess = self._forward_excess(prepared, signals, as_of_date)
        return {
            "tracking_since": since,
            "signals_recorded": len(signals),
            "settled_signals": settled,
            "hit_rate": overall.get("hit_rate"),
            "hit_rate_interval": interval,
            # Not worse than a coin flip: the upper end of the 90% interval reaches one half; None until something settles.
            "hit_rate_not_worse": interval[1] >= 0.5 if interval else None,
            **excess,
            "excess_not_worse": excess["excess_return"] >= 0 if excess.get("excess_return") is not None else None,
        }

    def _forward_excess(self, prepared: Any, signals: list[dict[str, Any]], as_of_date: str) -> dict[str, Any]:
        """Replay only the recorded signals from the day after the first one, against the simple benchmark."""

        if not signals:
            return {"forward_return": None, "benchmark_return": None, "excess_return": None}
        calendar = sorted({bar.trade_date for bar in prepared.bars if signals[0]["signal_date"] < bar.trade_date <= as_of_date})
        if len(calendar) < 2:
            return {"forward_return": None, "benchmark_return": None, "excess_return": None, "forward_days": len(calendar)}
        start = calendar[0]
        recorded = [TargetSignal(item["signal_date"], item["weights"], item["reason"]) for item in signals]
        strategy = prepared.run(recorded, start=start, end=as_of_date).metrics()
        label, benchmark_signals = self.backtest.simple_benchmark(prepared, start)
        benchmark = prepared.run(benchmark_signals, start=start, end=as_of_date).metrics()
        return {
            "forward_days": len(calendar),
            "forward_return": strategy.get("total_return"),
            "benchmark": label,
            "benchmark_return": benchmark.get("total_return"),
            "excess_return": round(strategy["total_return"] - benchmark["total_return"], 4),
        }

    def _tracking_since(self, archive: StrategyArchive) -> str:
        moves = [
            event["on_date"]
            for event in self.backtest.repository.events(archive.strategy_id)
            if event["version"] == archive.version
            and event["event_type"] == "transition"
            and event["to_state"] == LifecycleState.FORWARD_TRACKING.value
        ]
        if not moves:
            raise StrategyArchiveError(f"{archive.name} 没有进入前向跟踪的迁移记录")
        return moves[-1]


def _named(weights: dict[str, float], prepared: Any) -> dict[str, float]:
    assets = prepared.inputs.assets
    return {assets[a]["symbol"] if a in assets else a: round(w, 4) for a, w in weights.items()}
