"""Decision review and preference calibration for one user (stage 8 slice 7).

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「交易流水与偏好校准」 and
docs/modules/5-研究产物/观点账本模块.md「决策复盘」.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.master_data import MasterDataService
from src.portfolio_risk import (
    PortfolioRiskService,
    TradeRecord,
    TradeSide,
    annual_volatility,
    calibration_checks,
    exposure_summary,
    fixed_weight_path,
    max_drawdown,
    realized_path,
    risk_asset_share,
    stress_summary,
    trade_behaviours,
    weights_of,
)
from src.portfolio_risk.diagnostics import RISK_WINDOW_DAYS
from src.research_contracts import ASSET_CLASS_CASH
from src.research_ledger import ResearchLedgerService, due_review_nodes, summarize_decisions
from src.research_ledger.decision_review import ACTION_LABELS, SOURCE_LABELS
from src.user_space import UserContext

from .portfolio_diagnosis import PortfolioDiagnosisService

_BUY_ACTIONS = frozenset({"buy", "add"})
# Development sample judgements in the ledger; never part of a user's record unless asked for.
DEV_SEED_PREFIX = "dev-seed:"
_SELL_ACTIONS = frozenset({"reduce", "exit"})
_EXECUTION_LABELS = {
    "executed": "已执行",
    "not_executed": "未执行",
    "not_triggered": "条件未出现",
    "open": "仍在窗口内",
}
_RESULT_LABELS = {"met": "命中", "missed": "未命中", "not_directional": "无方向"}
_STATUS_LABELS = {
    "settled_view": "已结算观点",
    "settled_action": "已结算动作",
    "pending": "未到检查点",
    "not_triggered": "条件未出现",
    "not_settleable": "不可结算",
    "data_insufficient": "数据不足",
    "superseded": "已被修订",
}


class DecisionReviewService:
    def __init__(
        self,
        *,
        ledger: ResearchLedgerService,
        diagnosis: PortfolioDiagnosisService,
        portfolio_risk: PortfolioRiskService,
        master_data: MasterDataService,
        report_root: str | Path,
    ) -> None:
        self.ledger = ledger
        self.diagnosis = diagnosis
        self.portfolio_risk = portfolio_risk
        self.master_data = master_data
        self.report_root = Path(report_root)

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        calibrate: bool = True,
        allow_remote: bool = True,
        write_report: bool = True,
        include_dev_seed: bool = False,
    ) -> dict[str, Any]:
        context = UserContext(user_id=user_id)
        entries = self.ledger.track_record(user_id=user_id)["entries"]
        seeded = [item for item in entries if item["source_ref"].startswith(DEV_SEED_PREFIX)]
        if not include_dev_seed:
            entries = [item for item in entries if not item["source_ref"].startswith(DEV_SEED_PREFIX)]
        labels = self._labels({item["subject_id"] for item in entries})
        personal = [item for item in entries if item["scope"] == "personal"]
        trades = self.portfolio_risk.list_trades(context)
        for trade in trades:
            labels.setdefault(trade.asset_id, self._labels({trade.asset_id}).get(trade.asset_id, trade.asset_id))
        payload: dict[str, Any] = {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "decisions": summarize_decisions(entries, as_of_date=as_of_date, labels=labels),
            "review_nodes": due_review_nodes(personal, as_of_date=as_of_date),
            "execution": _execution(personal, trades, as_of_date, labels),
            "trade_count": len(trades),
            "dev_seed": {"entries": len(seeded), "included": include_dev_seed},
            "gaps": [],
        }
        if calibrate:
            payload["calibration"] = self._calibrate(
                context, as_of_date, cash, allow_remote, trades, labels, payload["gaps"]
            )
        payload["summary"] = _summary(payload)
        payload["limitations"] = _limitations()
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
        return payload

    def _calibrate(
        self,
        context: UserContext,
        as_of_date: str,
        cash: float | None,
        allow_remote: bool,
        trades: list[TradeRecord],
        labels: dict[str, str],
        gaps: list[str],
    ) -> dict[str, Any] | None:
        preference = self.portfolio_risk.latest_preference(context)
        if preference is None:
            gaps.append("没有已记录的投资偏好，无法做偏好校准；先用 record-preference 记录")
            return None
        try:
            state = self.diagnosis.collect(
                user_id=context.user_id, as_of_date=as_of_date, cash=cash, allow_remote=allow_remote
            )
        except (KeyError, ValueError, RuntimeError) as exc:
            gaps.append(f"组合取数失败，无法做偏好校准：{exc}")
            return None
        positions, levels, benchmark = state["positions"], dict(state["levels"]), state["benchmark"] or {}
        gaps.extend(state["gaps"])
        labels.update({item.asset_id: item.label for item in positions})
        weights = weights_of(positions)
        cash_like = frozenset(item.asset_id for item in positions if item.main_class() == ASSET_CLASS_CASH)
        start = (date.fromisoformat(as_of_date) - timedelta(days=RISK_WINDOW_DAYS)).isoformat()
        path = fixed_weight_path(
            weights, levels, as_of_date=as_of_date, benchmark_levels=benchmark, zero_return=cash_like, start=start
        )
        replay = (
            {
                "start": path[0][0],
                "end": path[-1][0],
                "max_drawdown": max_drawdown(path),
                "volatility": annual_volatility(path),
            }
            if path
            else {}
        )
        if not path:
            gaps.append("沪深300 指数不可用，无法回放当前持仓的历史回撤")
        stress = stress_summary(
            weights, levels, as_of_date=as_of_date, benchmark_levels=benchmark, labels=labels, zero_return=cash_like
        )
        worst = min(
            (item for item in stress if item["uncovered_weight"] < 0.05),
            key=lambda item: item["portfolio_return"],
            default=None,
        )
        classes = {item["class"]: item["weight"] for item in exposure_summary(positions)["asset_classes"]}
        holdings = [
            item for item in self.portfolio_risk.research_state.list_holdings(context) if item.quantity > 0
        ]
        realized = None
        if trades:
            missing = {item.asset_id for item in trades} - set(levels)
            prices = self.diagnosis.price_source(allow_remote) if missing else None
            for asset_id in missing:
                asset = self.master_data.get_asset(asset_id)
                try:
                    levels[asset_id] = self.diagnosis.levels(prices, asset, as_of_date, gaps) if asset else {}
                except ValueError as exc:
                    levels[asset_id] = {}
                    gaps.append(str(exc))
            realized = realized_path(
                {item.asset_id: float(item.quantity) for item in holdings},
                trades,
                levels,
                as_of_date=as_of_date,
                unit_priced=frozenset(key for key in cash_like if not levels.get(key)),
            )
            gaps.extend(f"交易流水：{item}" for item in realized["gaps"])
        behaviours = trade_behaviours(
            trades,
            levels,
            preference=preference,
            first_bought={item.asset_id: item.first_bought_on for item in holdings if item.first_bought_on},
            realized=(realized or {}).get("path") or None,
            labels=labels,
        )
        result = calibration_checks(
            preference,
            realized=realized,
            replay=replay,
            worst_stress=worst,
            equity_share=risk_asset_share(classes),
            behaviours=behaviours,
        )
        result["replay"] = replay
        if realized is not None:
            points = realized["path"]
            result["realized"] = {
                "start": points[0][0] if points else realized["start"],
                "end": points[-1][0] if points else realized["end"],
                "max_drawdown": max_drawdown(points),
                "volatility": annual_volatility(points),
            }
        return result

    def _labels(self, subject_ids: set[str]) -> dict[str, str]:
        labels = {}
        for subject_id in subject_ids:
            asset = self.master_data.get_asset(subject_id) if subject_id.startswith("asset:") else None
            if asset is not None:
                labels[subject_id] = f"{asset.display_name}（{asset.standard_symbol}）"
        return labels

    def _write_report(self, payload: dict[str, Any]) -> Path:
        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"决策复盘-{safe_user}-{payload['as_of_date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_decision_review(payload), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return path


def _execution(
    entries: list[dict[str, Any]],
    trades: list[TradeRecord],
    as_of_date: str,
    labels: dict[str, str],
) -> dict[str, Any] | None:
    """Match the user's recorded actions with logged trades in their windows; None without a trade log."""

    if not trades:
        return None
    rows = []
    used: set[str] = set()
    for entry in entries:
        if entry["entry_type"] != "action" or entry["superseded"]:
            continue
        artifact = entry["artifact"]
        trigger = artifact["trigger"]
        if trigger["valid_from"] > as_of_date:
            continue
        side = (
            TradeSide.BUY
            if artifact["action_type"] in _BUY_ACTIONS
            else TradeSide.SELL
            if artifact["action_type"] in _SELL_ACTIONS
            else None
        )
        if side is None:
            continue
        matched = [
            item
            for item in trades
            if item.asset_id == entry["subject_id"]
            and item.side is side
            and trigger["valid_from"] <= item.trade_date <= trigger["valid_until"]
        ]
        not_triggered = any("未触发" in (item.get("note") or "") for item in entry["settlements"])
        if matched:
            status = "executed"
            used.update(item.trade_id for item in matched)
        elif not_triggered:
            status = "not_triggered"
        elif trigger["valid_until"] < as_of_date:
            status = "not_executed"
        else:
            status = "open"
        rows.append(
            {
                "entry_id": entry["entry_id"],
                "asset": labels.get(entry["subject_id"], entry["subject_id"]),
                "action_type": artifact["action_type"],
                "window": [trigger["valid_from"], trigger["valid_until"]],
                "status": status,
                "trades": [item.trade_id for item in matched],
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "actions": rows,
        "counts": counts,
        "unplanned_trades": [
            {
                "trade_id": item.trade_id,
                "trade_date": item.trade_date,
                "asset": labels.get(item.asset_id, item.asset_id),
                "side": item.side.value,
            }
            for item in trades
            if item.trade_id not in used
        ],
    }


def _summary(payload: dict[str, Any]) -> list[str]:
    lines = []
    seed = payload["dev_seed"]
    if seed["entries"]:
        lines.append(
            f"已包含 {seed['entries']} 条开发样例（不是真实判断）"
            if seed["included"]
            else f"账本中有 {seed['entries']} 条开发样例，未计入"
        )
    views = payload["decisions"]["views"]["overall"]
    actions = payload["decisions"]["actions"]["overall"]
    for name, stats in (("观点", views), ("动作", actions)):
        if not stats["directional"]:
            lines.append(f"{name}：还没有已结算的方向性判断")
            continue
        text = (
            f"{name}：已结算 {stats['directional']} 条，命中 {stats['met']} 条（{stats['hit_rate']:.0%}，"
            f"90% 区间 {stats['hit_rate_interval'][0]:.0%}–{stats['hit_rate_interval'][1]:.0%}）"
        )
        if stats["small_sample"]:
            text += "，样本太少，不作解读"
        if "timing_contribution_mean" in stats:
            text += f"；择时贡献平均 {stats['timing_contribution_mean']:+.2%}（{stats['timing_contribution_positive']}/{stats['timing_contribution_count']} 条为正）"
        lines.append(text)
    due = payload["review_nodes"]["due"]
    if due:
        lines.append(f"已到期的计划复盘节点 {len(due)} 个，最早 {due[0]['review_on']}")
    execution = payload["execution"]
    if execution is None:
        lines.append("没有交易流水：无法判断计划动作是否执行，也无法重建实际组合的回撤")
    else:
        counts = execution["counts"]
        lines.append(
            "计划动作执行："
            + ("、".join(f"{_EXECUTION_LABELS[key]} {value}" for key, value in counts.items()) or "没有到期的动作")
            + f"；计划外交易 {len(execution['unplanned_trades'])} 笔"
        )
    calibration = payload.get("calibration")
    if calibration:
        lines.append(
            "偏好与表现、行为一致"
            if calibration["consistent"]
            else "偏好与表现或行为不一致：" + "；".join(calibration["findings"])
        )
    return lines


def _limitations() -> list[str]:
    return [
        "命中率按每条判断最近一个已结算的检查点；还没到期的判断是中途结果，后续检查点可能改变结论",
        "择时贡献是“执行动作”减“不动”：买入的不动是持有现金（收益记 0，不计货币基金利息），卖出的不动是继续持有；"
        "带替代标的的动作以替代标的为不动。每条动作等权，没有按金额加权",
        "命中按各判断自己的结算规则（有基准时看超额），择时贡献看绝对收益，所以买入可能跑赢基准算命中、但择时贡献为负",
        "同组少于 5 条时只列出，不解读；被修订的判断只计新版本",
        "回放是当前持仓按固定权重的假设历史，不是用户的实际经历；实际回撤需要交易流水，且只覆盖流水开始之后",
        "流水重建不含现金，按收盘价成交；流水与当前持仓不一致时不重建",
        "行为提示是规则提示，不是结论；偏好不会被自动修改",
    ]


def render_decision_review(payload: dict[str, Any]) -> str:
    decisions = payload["decisions"]
    lines = [
        f"# 决策复盘（截止 {payload['as_of_date']}）",
        "",
        "## 要点",
        "",
        *[f"- {item}" for item in payload["summary"]],
        "",
        "## 判断战绩",
        "",
        "| 类别 | 分组 | 已结算 | 命中 | 命中率 | 90% 区间 | 自述概率 | 择时贡献（平均） |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for kind, name in (("views", "观点"), ("actions", "动作")):
        groups = decisions[kind]
        lines.append(_stats_row(name, "全部", groups["overall"]))
        for dimension, rows in groups.items():
            if dimension == "overall":
                continue
            for row in rows:
                lines.append(_stats_row(name, row["group"], row))
    lines += [
        "",
        "条目状态："
        + "、".join(f"{_STATUS_LABELS.get(key, key)} {value}" for key, value in sorted(decisions["status_counts"].items())),
        "",
    ]
    if decisions["action_items"]:
        lines += [
            "## 动作明细",
            "",
            "| 标的 | 动作 | 来源 | 检查点 | 结果 | 标的收益 | 超额 | 择时贡献 |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: |",
        ]
        for row in decisions["action_items"]:
            lines.append(
                f"| {row['subject']} | {ACTION_LABELS.get(row['action_type'], row['action_type'])} | "
                f"{SOURCE_LABELS.get(row['source'], row['source'])} | {row['checkpoint_date']}"
                f"{'（中途）' if row['interim'] else ''} | {_RESULT_LABELS.get(row['result'], row['result'])} | "
                f"{_pct(row['asset_return'])} | {_pct(row['excess_return'], signed=True)} | "
                f"{_pct(row['timing_contribution'], signed=True)} |"
            )
        lines.append("")
    if decisions["view_items"]:
        lines += ["## 观点明细", "", "| 对象 | 来源 | 检查点 | 结果 | 超额或收益 | 命题 |", "| --- | --- | --- | --- | ---: | --- |"]
        for row in decisions["view_items"]:
            measure = row["excess_return"] if row["excess_return"] is not None else row["asset_return"]
            lines.append(
                f"| {row['subject']} | {SOURCE_LABELS.get(row['source'], row['source'])} | "
                f"{row['checkpoint_date']}{'（中途）' if row['interim'] else ''} | "
                f"{_RESULT_LABELS.get(row['result'], row['result'])} | {_pct(measure, signed=True)} | "
                f"{(row.get('proposition') or '').replace('|', '/')} |"
            )
        lines.append("")
    reviews = payload["review_nodes"]
    lines += ["## 计划复盘节点", ""]
    if not reviews["due"] and not reviews["upcoming"]:
        lines.append("没有已入账的组合计划。")
    for row in reviews["due"]:
        lines.append(f"- 已到期 {row['review_on']}：{row['focus']}")
    for row in reviews["upcoming"]:
        lines.append(f"- 即将到期 {row['review_on']}：{row['focus']}")
    lines += ["", "## 计划执行", ""]
    execution = payload["execution"]
    if execution is None:
        lines.append("没有交易流水（record-trade），无法判断执行情况。")
    else:
        for row in execution["actions"]:
            lines.append(
                f"- {row['asset']} {row['action_type']}（{row['window'][0]} 至 {row['window'][1]}）：{_EXECUTION_LABELS[row['status']]}"
            )
        for row in execution["unplanned_trades"]:
            lines.append(f"- 计划外交易：{row['trade_date']} {row['asset']} {'买入' if row['side'] == 'buy' else '卖出'}")
    lines += ["", "## 偏好校准", ""]
    calibration = payload.get("calibration")
    if calibration is None:
        lines.append("未做偏好校准（见数据缺口）。")
    else:
        lines += [f"- {item['message']}" for item in calibration["checks"]]
        for check in calibration["checks"]:
            for measure in check["measures"]:
                lines.append(
                    f"  - {check['label']}·{measure['label']}：{measure['value']:.1%}"
                    + (f"（{measure['detail']}）" if measure.get("detail") else "")
                )
        if calibration["behaviours"]:
            lines += ["", "行为提示：", ""]
            for item in calibration["behaviours"]:
                lines.append(f"- {item['trade_date']} {item['asset']} {item['title']}：{item['evidence']}。{item['meaning']}")
        lines += ["", calibration["note"]]
    if payload["gaps"]:
        lines += ["", "## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]]]
    lines += ["", "## 口径与局限", "", *[f"- {item}" for item in payload["limitations"]], ""]
    return "\n".join(lines)


def _stats_row(kind: str, group: str, stats: dict[str, Any]) -> str:
    interval = stats.get("hit_rate_interval")
    return (
        f"| {kind} | {group} | {stats['settled']} | {stats['met']} | {_pct(stats['hit_rate'])}"
        f"{'（样本少）' if stats['directional'] and stats['small_sample'] else ''} | "
        f"{'—' if not interval else f'{interval[0]:.0%}–{interval[1]:.0%}'} | {_pct(stats.get('stated_probability_mean'))} | "
        f"{_pct(stats.get('timing_contribution_mean'), signed=True)} |"
    )


def _pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    return f"{value:+.1%}" if signed else f"{value:.1%}"
