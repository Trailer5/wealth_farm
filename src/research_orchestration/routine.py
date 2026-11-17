"""Manual routine: the periodic upkeep a user (or the Agent) runs by hand, one command.

Settles due ledger checkpoints, tracks forward strategies, lists event views awaiting evidence
(with fresh news per tracked observable), lists debate signposts that are due or whose observable
moved, refreshes the market and macro archives, checks the user's active theses and lists due plan
review nodes. Each step fails on its own: an error lands in
``gaps`` and the remaining steps still run. Nothing here schedules itself; timed runs are
roadmap stage 11.

Design notes: docs/modules/6-编排/问题路由模块.md「手动例行命令」.
"""

from __future__ import annotations

from typing import Any, Callable

from src.data_src.throttle import default_throttle
from src.master_data import MasterDataService
from src.research_ledger import LedgerEntryType, LedgerSettlementService, ResearchLedgerService

from .decision_review import DecisionReviewService
from .debate import debate_next_actions
from .event_watch import watch_next_actions
from .state_archive import StateArchiveRecorder
from .thesis import DEVIATING, INVALIDATED, ThesisService

_CLI = "python -m src.research_orchestration.cli"


class RoutineService:
    def __init__(
        self,
        *,
        settlement: LedgerSettlementService,
        market: StateArchiveRecorder,
        macro: StateArchiveRecorder,
        ledger: ResearchLedgerService,
        thesis: ThesisService,
        decision_review: DecisionReviewService,
        master_data: MasterDataService,
        cooldowns: Callable[[], dict[str, dict[str, Any]]] | None = None,
        strategies: Callable[[str, bool], list[dict[str, Any]]] | None = None,
        events: Callable[[str, str, bool], list[dict[str, Any]]] | None = None,
        guidance: Callable[[str, str], list[dict[str, Any]]] | None = None,
        debates: Callable[[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.settlement = settlement
        self.market = market
        self.macro = macro
        self.ledger = ledger
        self.thesis = thesis
        self.decision_review = decision_review
        self.master_data = master_data
        self.cooldowns = cooldowns or (lambda: default_throttle().cooldowns())
        self.strategies = strategies
        self.events = events
        self.guidance = guidance
        self.debates = debates

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        allow_remote: bool = True,
        archives: bool = True,
    ) -> dict[str, Any]:
        gaps: list[str] = []
        steps: dict[str, Any] = {
            "settlement": _step("结算到期判断", gaps, lambda: self.settlement.settle_due(as_of_date).to_dict()),
        }
        if self.strategies is not None:
            steps["strategies"] = _step("策略前向跟踪", gaps, lambda: self.strategies(as_of_date, allow_remote))
        if self.events is not None:
            steps["events"] = _step("事件核实", gaps, lambda: self.events(user_id, as_of_date, allow_remote))
        if self.guidance is not None:
            steps["guidance"] = _step("推荐仓位复评", gaps, lambda: self.guidance(user_id, as_of_date))
        if self.debates is not None:
            steps["debates"] = _step("辩论观察点", gaps, lambda: self.debates(user_id, as_of_date))
        if archives:
            for name, label, recorder in (("market", "市场档案", self.market), ("macro", "宏观档案", self.macro)):
                steps[name] = _step(
                    label, gaps, lambda recorder=recorder: _archive(recorder.run(as_of_date, allow_remote=allow_remote))
                )
        steps["theses"] = _step("论点检查", gaps, lambda: self._theses(user_id, as_of_date))
        steps["review"] = _step(
            "决策复盘",
            gaps,
            lambda: self.decision_review.run(
                user_id=user_id, as_of_date=as_of_date, calibrate=False, allow_remote=allow_remote, write_report=False
            ),
        )
        cooldowns = self.cooldowns()
        gaps.extend(f"数据来源 {key} 冷却至 {item['until']}：{item['reason']}" for key, item in cooldowns.items())
        return {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "steps": steps,
            "source_cooldowns": cooldowns,
            "summary": _summary(steps),
            "next_actions": _next_actions(steps, user_id, as_of_date),
            "gaps": gaps,
        }

    def _theses(self, user_id: str, as_of_date: str) -> list[dict[str, Any]]:
        entries = self.ledger.list_entries(user_id=user_id, entry_type=LedgerEntryType.THESIS, active_on=as_of_date)
        rows = []
        for asset_id in sorted({entry.artifact.asset_id for entry in entries}):
            asset = self.master_data.get_asset(asset_id)
            name = asset.display_name if asset is not None else asset_id
            for item in self.thesis.check(asset_id, as_of_date=as_of_date, user_id=user_id)["theses"]:
                rows.append(
                    {
                        "asset_id": asset_id,
                        "name": name,
                        "thesis_entry_id": item["thesis_entry_id"],
                        "summary": item["summary"],
                        "status": item["status"],
                        "status_label": item["status_label"],
                    }
                )
        return rows


def _step(label: str, gaps: list[str], call: Callable[[], Any]) -> Any:
    # Failure isolation: remote sources and stale data fail in many ways; one step must not stop the rest.
    try:
        return call()
    except Exception as exc:  # noqa: BLE001
        gaps.append(f"{label}失败：{type(exc).__name__}: {exc}")
        return None


def _archive(payload: dict[str, Any]) -> dict[str, Any]:
    assessment = payload["assessment"]
    return {
        "label": assessment["label"],
        "confidence": assessment["confidence"],
        "change_signals": assessment["change_signals"],
        "ledger_entry_id": payload["ledger_entry_id"],
        "gaps": payload["gaps"],
    }


def _summary(steps: dict[str, Any]) -> list[str]:
    lines = []
    settlement = steps["settlement"]
    if settlement is not None:
        appended = len(settlement["appended_settlement_ids"])
        lines.append(f"结算：新增 {appended} 条结算记录" if appended else "结算：没有新到期的检查点")
    for name, label in (("market", "市场"), ("macro", "宏观")):
        archive = steps.get(name)
        if archive is not None:
            lines.append(f"{label}：{archive['label']}（把握度 {archive['confidence']}）")
    tracked = steps.get("strategies")
    if tracked:
        new = sum(1 for row in tracked if row.get("status") == "recorded")
        failed = sum(1 for row in tracked if row.get("status") == "failed")
        lines.append(f"策略前向跟踪：{len(tracked)} 个策略，新记录 {new} 个信号" + (f"，{failed} 个失败" if failed else ""))
    events = steps.get("events")
    if events:
        due = sum(1 for row in events for item in row["views"] if item["status"] == "due")
        news = sum(len(hit["items"]) for row in events for hit in row["news"])
        lines.append(f"事件核实：{len(events)} 个可观测量，{due} 条观点到期待核实，新闻 {news} 条")
    guidance = steps.get("guidance")
    if guidance:
        lines.append("推荐仓位复评：" + "、".join(str(row["name"] or row["asset_id"]) for row in guidance) + " 已到复评日")
    debates = steps.get("debates")
    if debates and (debates["signposts"] or debates["open_debates"]):
        lines.append(
            f"辩论：{len(debates['signposts'])} 个观察点到期或有新变化，{len(debates['open_debates'])} 场辩论尚未结论"
        )
    theses = steps["theses"]
    if theses is not None:
        flagged = [row for row in theses if row["status"] in (INVALIDATED, DEVIATING)]
        if not theses:
            lines.append("论点：没有有效的论点")
        else:
            lines.append(
                f"论点：{len(theses)} 条，其中 {len(flagged)} 条偏离或失效"
                + ("：" + "、".join(f"{row['name']}{row['status_label']}" for row in flagged) if flagged else "")
            )
    review = steps["review"]
    if review is not None:
        due = review["review_nodes"]["due"]
        lines.append(f"计划复盘节点：{len(due)} 个已到期" if due else "计划复盘节点：没有到期的")
    return lines


def _next_actions(steps: dict[str, Any], user_id: str, as_of_date: str) -> list[dict[str, str]]:
    actions = watch_next_actions(steps.get("events") or [], as_of_date)
    actions.extend(debate_next_actions(steps.get("debates")))
    for row in steps.get("guidance") or ():
        target = f"--asset-id {row['asset_id']}"
        actions.append(
            {
                "command": f"{_CLI} position-guidance --user-id {user_id} {target} --history",
                "reason": f"{row['name']} 的推荐仓位已到复评日 {row['review_on']}：更新研究后记录新一期推荐比例，对比上一期给出加持或减持结论",
            }
        )
    for row in steps.get("strategies") or ():
        if row.get("next_state") == "enabled" and not row.get("next_blockers"):
            actions.append(
                {
                    "command": f"{_CLI} strategy-archive --strategy-id {row['strategy_id']} --transition enabled"
                    f" --reason \"前向跟踪门槛已满足\" --as-of-date {as_of_date}",
                    "reason": f"{row['name']} 的前向跟踪已满足启用门槛",
                }
            )
    for row in steps["theses"] or ():
        if row["status"] in (INVALIDATED, DEVIATING):
            actions.append(
                {
                    "command": f"{_CLI} exit-plan --user-id {user_id} --asset-id {row['asset_id']} --as-of-date {as_of_date}",
                    "reason": f"{row['name']} 的论点{row['status_label']}，复核持有理由并给出退出条件",
                }
            )
    review = steps["review"]
    if review is not None and review["review_nodes"]["due"]:
        actions.append(
            {
                "command": f"{_CLI} decision-review --user-id {user_id} --as-of-date {as_of_date}",
                "reason": "有已到期的计划复盘节点：先复盘执行与结果，需要调整时再生成新计划（portfolio-plan）",
            }
        )
    settlement = steps["settlement"]
    if settlement is not None and settlement["appended_settlement_ids"] and not actions:
        actions.append(
            {
                "command": f"{_CLI} decision-review --user-id {user_id} --as-of-date {as_of_date}",
                "reason": "有新的结算结果，可查看更新后的命中率",
            }
        )
    return actions
