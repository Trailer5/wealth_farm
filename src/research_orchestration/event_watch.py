"""Event views awaiting evidence: which obs: judgements are open, due or decided, and what news arrived.

The routine runs this after settlement so the Agent sees, per tracked observable, the views that
depend on it, its latest recorded value, and headlines published since then for its watch queries.
Recording a new observation is the Agent's job (record-observation), never automatic.
Design notes: docs/modules/2-世界状态/事件情报与观测模块.md「例行核实」.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.event_intel import OBS_PREFIX, EventIntelService
from src.research_contracts import SettlementMethod, View
from src.research_ledger import LedgerEntryType, ResearchLedgerService, settlement_schedule

_CLI = "python -m src.research_orchestration.cli"
UPCOMING_DAYS = 14
_NEWS_LOOKBACK_DAYS = 30


class ObservableWatchService:
    def __init__(self, *, ledger: ResearchLedgerService, intel: EventIntelService) -> None:
        self.ledger = ledger
        self.intel = intel

    def run(
        self,
        *,
        user_id: str | None,
        as_of_date: str,
        allow_remote: bool = True,
        signposted: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """``signposted``: observables that debate signposts watch; they get news even without dependent views."""

        views: dict[str, list[dict[str, Any]]] = {observable_id: [] for observable_id in signposted}
        for entry in self.ledger.track_record(user_id=user_id, entry_type=LedgerEntryType.VIEW)["entries"]:
            rule = entry["artifact"].get("settlement_rule") or {}
            observed = str(rule.get("observed_id") or "")
            if (
                entry["superseded"]
                or rule.get("method") != SettlementMethod.METRIC_OBSERVATION.value
                or not observed.startswith(OBS_PREFIX)
                or entry["as_of_date"] > as_of_date
            ):
                continue
            view = View.from_dict(entry["artifact"])
            final = settlement_schedule(view).checkpoints[-1]
            settled = [item for item in entry["settlements"] if item["status"] == "settled"]
            if settled:
                status = "decided"
            elif final <= as_of_date:
                status = "due"
            elif final <= (date.fromisoformat(as_of_date) + timedelta(days=UPCOMING_DAYS)).isoformat():
                status = "upcoming"
            else:
                status = "open"
            views.setdefault(observed, []).append(
                {
                    "entry_id": entry["entry_id"],
                    "proposition": view.proposition,
                    "claim": entry["artifact"].get("claim"),
                    "final_checkpoint": final,
                    "status": status,
                    "result": settled[-1]["claim_result"] if settled else None,
                }
            )
        rows = []
        for observable_id in sorted(views):
            observable = self.intel.observable(observable_id)
            if observable is None:
                continue
            history = self.intel.observations(observable_id)
            latest = history[-1] if history else None
            active = [item for item in views[observable_id] if item["status"] != "decided"]
            news: list[dict[str, Any]] = []
            if (active or observable_id in signposted) and observable.watch_queries:
                floor = (date.fromisoformat(as_of_date) - timedelta(days=_NEWS_LOOKBACK_DAYS)).isoformat()
                since = max(latest.period_end, floor) if latest is not None else floor
                news = self.intel.watch(observable, since=since, until=as_of_date, allow_remote=allow_remote)
            rows.append(
                {
                    "observable_id": observable_id,
                    "title": observable.title,
                    "kind": observable.kind,
                    "criteria": observable.criteria,
                    "latest_observation": latest.to_dict() if latest is not None else None,
                    "views": views[observable_id],
                    "signposted": observable_id in signposted,
                    "news": news,
                }
            )
        return rows


def watch_next_actions(rows: list[dict[str, Any]], as_of_date: str) -> list[dict[str, str]]:
    actions = []
    for row in rows or ():
        due = [item for item in row["views"] if item["status"] == "due"]
        fresh = sum(len(hit["items"]) for hit in row["news"])
        if due:
            actions.append(
                {
                    "command": f"{_CLI} record-observation --json <path>",
                    "reason": f"{row['title']}（{row['observable_id']}）有 {len(due)} 条观点已到期：用 intel 核实到期日状态，"
                    f"按判定标准记录 period_end={due[0]['final_checkpoint']} 的观测后再运行 settle-ledger",
                }
            )
        elif fresh:
            actions.append(
                {
                    "command": f"{_CLI} record-observation --json <path>",
                    "reason": f"{row['title']}（{row['observable_id']}）自上次观测以来有 {fresh} 条新闻：核实是否满足判定标准，"
                    "满足则记录观测（证据引用 intel 条目），不满足不必记录",
                }
            )
    return actions
