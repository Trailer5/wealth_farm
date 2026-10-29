"""Portfolio plan: migration steps, recorded actions, review nodes and targets in one ledger-recorded Plan.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「计划对象与冲突处理」.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.portfolio_risk import InvestmentPreference
from src.research_contracts import (
    ActionTrigger,
    ActionType,
    Plan,
    ProbabilityInterval,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    ReviewNode,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    SubjectType,
    TargetRange,
    TriggerKind,
    disclosure_deadline,
    settlement_checkpoints,
    to_payload,
)
from src.research_ledger import LedgerEntryType, LedgerScope, ResearchLedgerService
from src.research_ledger.metrics import macro_metric_id
from src.research_visualization import check_plan_html, render_plan_html

from .adjustment_proposal import AdjustmentProposalService

PLAN_SOURCE_PREFIX = "portfolio_plan:"
MIGRATION_HORIZON = ResearchHorizon.MEDIUM_SHORT
QUARTERLY_REVIEW_DAYS = 91
REVIEW_HORIZON_DAYS = 365
TARGET_HOLD_DAYS = 90
_RISK_REDUCING = (ActionType.REDUCE, ActionType.EXIT)
_RISK_ADDING = (ActionType.BUY, ActionType.ADD)
_ACTION_LABELS = {
    ActionType.BUY: "买入",
    ActionType.ADD: "加仓",
    ActionType.REDUCE: "减仓",
    ActionType.EXIT: "清仓",
    ActionType.HOLD: "持有",
}
_METRIC_LABELS = {"portfolio_weight": "占组合比例", "return_1y": "未来一年收益"}


class PlanBlockedError(ValueError):
    """No plan can be assembled until the user decides; ``kind`` tells the caller what to ask."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class PortfolioPlanService:
    def __init__(
        self,
        *,
        adjustment: AdjustmentProposalService,
        ledger: ResearchLedgerService,
        report_root: str | Path,
    ) -> None:
        self.adjustment = adjustment
        self.ledger = ledger
        self.report_root = Path(report_root)

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        candidates: tuple[tuple[str, str], ...] = (),
        record: bool = False,
        allow_remote: bool = True,
        write_report: bool = True,
    ) -> dict[str, Any]:
        proposal = self.adjustment.run(
            user_id=user_id,
            as_of_date=as_of_date,
            cash=cash,
            candidates=candidates,
            allow_remote=allow_remote,
            write_report=False,
        )
        if proposal.get("status") == "conflict":
            raise PlanBlockedError("conflict", "；".join(proposal["summary"]))
        preference = self.adjustment.allocation.require_preference(user_id)
        existing = [
            entry
            for entry in self.ledger.list_entries(
                user_id=user_id, entry_type=LedgerEntryType.ACTION, active_on=as_of_date
            )
            if entry.scope is LedgerScope.PERSONAL and not entry.source_ref.startswith(PLAN_SOURCE_PREFIX)
        ]
        previous_id, previous_version = self._previous(user_id)
        assembled = assemble_plan(
            proposal,
            user_id=user_id,
            as_of_date=as_of_date,
            preference=preference,
            existing=[(entry.entry_id, entry.artifact) for entry in existing],
            review_sources=self._review_sources(
                user_id, as_of_date, {item["asset_id"] for item in proposal["targets"]}
            ),
            previous_version=previous_version,
        )
        plan: Plan = assembled["plan"]
        unchanged = None
        if previous_id is not None:
            previous = self.ledger.get_entry(previous_id, user_id=user_id).artifact
            if previous.plan_id == plan.plan_id:
                plan, unchanged = previous, previous_id
        recorded: list[str] = []
        if record and unchanged is None:
            recorded = self._record(plan, assembled["new_actions"], user_id)
        elif unchanged is not None:
            recorded = [unchanged]
        payload = {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "recorded": bool(recorded),
            "unchanged_from_recorded": unchanged is not None,
            "ledger_entry_ids": recorded,
            "plan": plan.to_dict(),
            "labels": {
                **{item["asset_id"]: item["label"] for item in proposal["targets"]},
                f"portfolio:{user_id}": "整个组合",
            },
            "nodes": assembled["nodes"],
            "proposal_summary": proposal["summary"],
            "return_band": proposal.get("return_band"),
            "before": proposal["before"],
            "after": proposal["after"],
            "gaps": proposal["gaps"],
            "limitations": _limitations(),
        }
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
            html_path, problems = self._write_html(payload)
            payload["html_path"] = str(html_path) if html_path else None
            payload["html_problems"] = problems
        return payload

    def _previous(self, user_id: str) -> tuple[str | None, int]:
        plans = self.ledger.list_entries(user_id=user_id, entry_type=LedgerEntryType.PLAN)
        if not plans:
            return None, 0
        latest = max(plans, key=lambda entry: (entry.artifact.version, entry.recorded_at))
        return latest.entry_id, latest.artifact.version

    def _record(self, plan: Plan, actions: list[SettleableAction], user_id: str) -> list[str]:
        source = f"{PLAN_SOURCE_PREFIX}{plan.plan_id}"
        entries = list(self.ledger.record(actions, scope=LedgerScope.PERSONAL, user_id=user_id, source_ref=source))
        previous_id, _ = self._previous(user_id)
        if previous_id is None:
            entries += self.ledger.record((plan,), scope=LedgerScope.PERSONAL, user_id=user_id, source_ref=source)
        else:
            entries.append(
                self.ledger.record_revision(plan, supersedes_entry_id=previous_id, user_id=user_id, source_ref=source)
            )
        return [entry.entry_id for entry in entries]

    def _review_sources(self, user_id: str, as_of_date: str, assets: set[str]) -> list[tuple[str, str]]:
        """(date, focus) from the user's active theses and directional views on the plan's assets or on markets."""

        result = []
        for entry in self.ledger.list_entries(user_id=user_id, entry_type=LedgerEntryType.THESIS, active_on=as_of_date):
            thesis = entry.artifact
            if thesis.asset_id not in assets:
                continue
            for indicator in thesis.indicators:
                result.append(
                    (
                        disclosure_deadline(indicator.observe_period),
                        f"论点检查：{thesis.summary}（{indicator.label} {indicator.observe_period} 期，运行 thesis-check）",
                    )
                )
        for entry in self.ledger.list_entries(user_id=user_id, entry_type=LedgerEntryType.VIEW, active_on=as_of_date):
            view = entry.artifact
            # Non-directional views have nothing to settle; other assets' views belong to their own research.
            if (
                entry.scope is not LedgerScope.PERSONAL
                or view.claim is None
                or (view.subject_type is SubjectType.ASSET and view.subject_id not in assets)
            ):
                continue
            checkpoints = (
                settlement_checkpoints(view.horizon, view.as_of_date, view.expires_on)
                if view.settlement_rule is not None
                else (view.expires_on,)
            )
            for day in checkpoints:
                result.append((day, f"观点结算：{view.proposition}"))
        return result

    def _write_html(self, payload: dict[str, Any]) -> tuple[Path | None, list[str]]:
        """Write the page, read it back and compare with the plan; a page that disagrees is removed."""

        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"组合计划-{safe_user}-{payload['as_of_date']}.html"
        path.write_text(render_plan_html(payload), encoding="utf-8")
        problems = check_plan_html(path.read_text(encoding="utf-8"), payload["plan"])
        if problems:
            path.unlink()
            return None, problems
        return path, []

    def _write_report(self, payload: dict[str, Any]) -> Path:
        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"组合计划-{safe_user}-{payload['as_of_date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_plan(payload), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return path


def assemble_plan(
    proposal: dict[str, Any],
    *,
    user_id: str,
    as_of_date: str,
    preference: InvestmentPreference,
    existing: list[tuple[str, SettleableAction]],
    review_sources: list[tuple[str, str]],
    previous_version: int,
) -> dict[str, Any]:
    """Build the Plan from an adjustment proposal and the user's recorded actions; no I/O."""

    targets = {item["asset_id"]: item for item in proposal["targets"]}
    labels = {item["asset_id"]: item["label"] for item in proposal["targets"]}
    valuation = proposal.get("buy_valuation")
    steps = proposal["migration"]["steps"]
    conflicts: list[str] = []
    excluded: list[str] = []
    reducing_existing = {
        action.asset_id: entry_id for entry_id, action in existing if action.action_type in _RISK_REDUCING
    }
    new_actions: list[SettleableAction] = []
    nodes: list[dict[str, Any]] = []
    for step in steps:
        last = step["step"] == len(steps)
        for leg in step["legs"]:
            asset_id = leg["asset_id"]
            target = targets[asset_id]
            if leg["action"] == "buy" and asset_id in reducing_existing:
                excluded.append(
                    f"第 {step['step']} 步买入{leg['label']}：账本里已有该标的的减仓或退出动作（{reducing_existing[asset_id]}），风险降低优先，不再加仓"
                )
                continue
            action = _migration_action(
                user_id, as_of_date, step, leg, target, valuation, last=last
            )
            new_actions.append(action)
            nodes.append(_node(action, labels.get(asset_id, asset_id), leg, step["step"], len(steps)))
    planned_sells = {action.asset_id for action in new_actions if action.action_type in _RISK_REDUCING}
    cap = float(preference.max_single_weight)
    kept_existing: list[SettleableAction] = []
    for entry_id, action in existing:
        label = labels.get(action.asset_id, action.asset_id)
        target = targets.get(action.asset_id)
        if action.action_type in _RISK_ADDING and action.asset_id in planned_sells:
            excluded.append(f"{label}的{_ACTION_LABELS[action.action_type]}（{entry_id}）：与组合调整的减仓方向相反，风险降低优先")
            continue
        if (
            action.action_type in _RISK_ADDING
            and target is not None
            and target["asset_type"] == "stock"
            and target["current"] >= cap - 1e-9
        ):
            excluded.append(f"{label}的{_ACTION_LABELS[action.action_type]}（{entry_id}）：该股已达单只个股上限 {cap:.0%}")
            continue
        kept_existing.append(action)
        nodes.append(_node(action, label, None, None, len(steps), source=entry_id))
    shortfall = proposal["migration"]["cash_shortfall"]
    conflicts.append("同一时间窗内先卖后买，买入金额不超过已释放的资金；风险降低类动作（减仓、退出）优先于加仓。")
    if shortfall > 1:
        conflicts.append(f"按计划买入还差约 {shortfall:,.0f} 元：最后一步的买入按比例缩小，需要新增资金或接受更低的目标。")
    if any(item.startswith("第") for item in excluded):
        conflicts.append("账本已有的减仓或退出动作与本次加仓冲突时，保留减仓，删掉对应的买入步骤。")
    if valuation and not valuation["met"]:
        conflicts.append("截止日沪深300 估值分位高于条件，前两步的权益买入可能顺延到最后一步。")
    actions = tuple(new_actions) + tuple(kept_existing)
    review_nodes = _review_nodes(steps, review_sources, as_of_date)
    plan_targets = _targets(proposal, user_id=user_id, steps=steps, as_of_date=as_of_date)
    digest = hashlib.sha256(
        json.dumps(
            to_payload((actions, review_nodes, plan_targets, tuple(conflicts), tuple(excluded))),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:12]
    plan = Plan(
        plan_id=f"plan:{user_id}:{as_of_date}:{digest}",
        user_id=user_id,
        as_of_date=as_of_date,
        version=previous_version + 1,
        actions=actions,
        review_nodes=review_nodes,
        targets=plan_targets,
        conflicts=tuple(conflicts),
        excluded_actions=tuple(excluded),
    )
    return {"plan": plan, "new_actions": new_actions, "nodes": sorted(nodes, key=lambda item: item["date"])}


def _migration_action(
    user_id: str,
    as_of_date: str,
    step: dict[str, Any],
    leg: dict[str, Any],
    target: dict[str, Any],
    valuation: dict[str, Any] | None,
    *,
    last: bool,
) -> SettleableAction:
    buy = leg["action"] == "buy"
    if buy:
        action_type = ActionType.ADD if target["current"] > 0 else ActionType.BUY
    else:
        action_type = ActionType.EXIT if target["target"] <= 0 else ActionType.REDUCE
    start, end = leg["window"]
    if buy and leg["condition"] and valuation and not last:
        trigger = ActionTrigger(
            kind=TriggerKind.CONDITIONAL,
            valid_from=start,
            valid_until=end,
            observability_key=f"metric_below:{valuation['threshold_value']}:{macro_metric_id(valuation['series_id'])}",
            condition=leg["condition"],
        )
    else:
        trigger = ActionTrigger(kind=TriggerKind.SCHEDULED, valid_from=start, valid_until=end, scheduled_on=start)
    quantity = QuantityRule(
        kind=QuantityRuleKind.USER_DEFINED,
        description=(
            (f"{leg['quantity']:,} 股（份），" if leg.get("quantity") else "")
            + f"约 {abs(leg['amount']):,.0f} 元（第 {step['step']} 步）；调整后目标占组合 "
            f"{target['low']:.1%}–{target['high']:.1%}"
        ),
    )
    # Same content keeps the same ID so re-recording is idempotent; changed amounts get a new action.
    digest = hashlib.sha256(
        json.dumps(to_payload((trigger, quantity)), sort_keys=True).encode("utf-8")
    ).hexdigest()[:8]
    return SettleableAction(
        action_id=f"plan-action:{user_id}:{as_of_date}:{step['step']}:{leg['asset_id']}:{action_type.value}:{digest}",
        asset_id=leg["asset_id"],
        action_type=action_type,
        horizon=MIGRATION_HORIZON,
        trigger=trigger,
        quantity_rule=quantity,
        settlement_rule=SettlementRule(method=SettlementMethod.PRICE_RELATIVE, observed_id=leg["asset_id"]),
    )


def _node(
    action: SettleableAction,
    label: str,
    leg: dict[str, Any] | None,
    step: int | None,
    steps: int,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    trigger = action.trigger
    return {
        "date": trigger.scheduled_on or trigger.valid_from,
        "valid_until": trigger.valid_until,
        "kind": trigger.kind.value,
        "asset_id": action.asset_id,
        "label": label,
        "action": _ACTION_LABELS[action.action_type],
        "quantity": action.quantity_rule.description or action.quantity_rule.reference,
        "condition": trigger.condition,
        "step": step,
        "steps": steps,
        "cost": leg["cost"] if leg else None,
        "note": leg.get("note") if leg else None,
        "source": source or "本计划的迁移步骤",
        "action_id": action.action_id,
    }


def _review_nodes(
    steps: list[dict[str, Any]],
    sources: list[tuple[str, str]],
    as_of_date: str,
) -> tuple[ReviewNode, ...]:
    start = date.fromisoformat(as_of_date)
    horizon = (start + timedelta(days=REVIEW_HORIZON_DAYS)).isoformat()
    nodes = [
        (
            step["window_end"],
            f"第 {step['step']} 步结束：核对是否按计划成交、成交价与成本；持仓偏离目标区间超过 2 个百分点时在下一步补做，未执行要写下原因",
        )
        for step in steps
    ]
    for quarter in range(1, REVIEW_HORIZON_DAYS // QUARTERLY_REVIEW_DAYS + 1):
        nodes.append(
            (
                (start + timedelta(days=quarter * QUARTERLY_REVIEW_DAYS)).isoformat(),
                "季度复盘：重新运行组合诊断与调整方案，对照偏好；比较实际与计划的差异，偏离来自市场还是执行，"
                "只按规则调整，不因短期涨跌改变目标",
            )
        )
    nodes.append((horizon, "年度复盘：确认偏好是否仍然成立（期限、可接受回撤、用钱计划），必要时用 record-preference 记录新版本"))
    nodes += [(day, focus) for day, focus in sources if as_of_date <= day <= horizon]
    unique = sorted(set(nodes))
    return tuple(ReviewNode(review_on=day, focus=focus) for day, focus in unique)


def _targets(
    proposal: dict[str, Any],
    *,
    user_id: str,
    steps: list[dict[str, Any]],
    as_of_date: str,
) -> tuple[TargetRange, ...]:
    end = steps[-1]["window_end"] if steps else as_of_date
    hold_until = (date.fromisoformat(end) + timedelta(days=TARGET_HOLD_DAYS)).isoformat()
    result = []
    for item in proposal["targets"]:
        if item["high"] <= item["low"]:
            continue
        result.append(
            TargetRange(
                subject_id=item["asset_id"],
                metric_key="portfolio_weight",
                low=Decimal(str(item["low"])),
                high=Decimal(str(item["high"])),
                window_start=end,
                window_end=hold_until,
                basis="调整方案的目标权重 ±2 个百分点，来自偏好上限、配置区间与风险预算",
            )
        )
    band = proposal.get("return_band")
    if band and band["high"] > band["low"]:
        result.append(
            TargetRange(
                subject_id=f"portfolio:{user_id}",
                metric_key="return_1y",
                low=Decimal(str(band["low"])),
                high=Decimal(str(band["high"])),
                window_start=as_of_date,
                window_end=(date.fromisoformat(as_of_date) + timedelta(days=REVIEW_HORIZON_DAYS)).isoformat(),
                basis=(
                    f"目标权重在 {band['start']} 至 {band['end']} 的滚动一年收益，取 {band['coverage']:.0%} 中间区间；"
                    f"概率区间按约 {band['independent_years']} 个独立年份估计；历史分布，不是预测"
                ),
                probability=ProbabilityInterval(
                    low=Decimal(str(band["probability_low"])),
                    high=Decimal(str(band["probability_high"])),
                ),
            )
        )
    return tuple(result)


def render_plan(payload: dict[str, Any]) -> str:
    plan = payload["plan"]
    lines = [
        f"# 组合计划（截止 {payload['as_of_date']}，第 {plan['version']} 版）",
        "",
        "已入账："
        + (
            ("是（与已入账版本内容相同），" if payload["unchanged_from_recorded"] else "是，")
            + "、".join(payload["ledger_entry_ids"][-1:])
            if payload["recorded"]
            else "否（预览；用户确认后用 --record 入账）"
        ),
        "",
        *[f"- {item}" for item in payload["proposal_summary"]],
        "",
        "## 节点",
        "",
        "| 日期 | 截止 | 类型 | 标的 | 动作 | 数量规则 | 条件 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *[
            f"| {item['date']} | {item['valid_until']} | {'条件' if item['kind'] == 'conditional' else '定期'} | {item['label']} | "
            f"{item['action']} | {item['quantity'] or ''} | {item['condition'] or '按时间执行'} | {item['source']} |"
            for item in payload["nodes"]
        ],
        "",
        "## 目标（区间与依据）",
        "",
        "| 对象 | 指标 | 区间 | 时间窗 | 概率 | 依据 |",
        "| --- | --- | --- | --- | --- | --- |",
        *[
            f"| {payload['labels'].get(item['subject_id'], item['subject_id'])} | {_METRIC_LABELS.get(item['metric_key'], item['metric_key'])} | "
            f"{float(item['low']):.1%}–{float(item['high']):.1%} | "
            f"{item['window_start']}–{item['window_end']} | "
            + (
                f"{float(item['probability']['low']):.0%}–{float(item['probability']['high']):.0%}"
                if item.get("probability")
                else "-"
            )
            + f" | {item['basis']} |"
            for item in plan["targets"]
        ],
        "",
        "## 复盘节点与改进方法",
        "",
        *[f"- {item['review_on']}：{item['focus']}" for item in plan["review_nodes"]],
        "",
        "## 冲突处理",
        "",
        *[f"- {item}" for item in plan["conflicts"]],
        "",
    ]
    if plan["excluded_actions"]:
        lines += ["## 被排除的动作", "", *[f"- {item}" for item in plan["excluded_actions"]], ""]
    if payload["gaps"]:
        lines += ["## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]], ""]
    lines += ["## 口径与局限", "", *[f"- {item}" for item in payload["limitations"]], ""]
    return "\n".join(lines)


def _limitations() -> list[str]:
    return [
        "日期是时间窗，不是精确成交日；有条件节点时在窗口内条件满足才执行（只有前两步的权益买入设估值条件，最后一步不设），其余按时间执行。",
        "数量按截止日价格换算：股票与场内基金按每手 100 股（份）取整，场外基金按金额申购、按份额赎回；成交时价格变化会让金额与目标略有偏差。",
        "目标权重区间是执行纪律，不是收益承诺；一年收益区间是目标权重下的历史回放，样本里重叠窗口很多，概率区间按独立年份估计。",
        "计划只汇总已入账的动作和本次调整的迁移步骤；单标的研究产生的买入条件要先发布最终报告入账，才会进入计划。",
        "重新生成并入账的计划会作为新版本替代旧版本，但旧版本已入账的迁移动作仍会按原规则结算，复盘时以最新版本为准。",
    ]
