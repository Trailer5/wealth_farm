"""Target equity range and risk-budget reference weights for a user's portfolio.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「配置与风险预算」.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from src.master_data import ASSET_TYPE_EXCHANGE_FUND, ASSET_TYPE_INDEX, ASSET_TYPE_STOCK, MasterDataService
from src.portfolio_risk import (
    InvestmentPreference,
    PortfolioRiskService,
    class_risk,
    covariance_matrix,
    effective_cash_floor,
    equity_range,
    exposure_summary,
    risk_asset_share,
    weights_of,
    within_risk_assets,
)
from src.research_contracts import (
    ASSET_CLASS_BOND,
    ASSET_CLASS_CASH,
    ASSET_CLASS_COMMODITY,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_LABELS,
    ASSET_CLASS_OVERSEAS,
)
from src.user_space import UserContext

from .portfolio_diagnosis import PortfolioDiagnosisService

# Representative series per class; documented in allocation_risk_budget.md.
CLASS_PROXIES = {
    ASSET_CLASS_BOND: ("511010", ASSET_TYPE_EXCHANGE_FUND, "国债ETF"),
    ASSET_CLASS_COMMODITY: ("518880", ASSET_TYPE_EXCHANGE_FUND, "黄金ETF"),
    ASSET_CLASS_OVERSEAS: ("513100", ASSET_TYPE_EXCHANGE_FUND, "纳斯达克100ETF"),
}
_RISK_CLASSES = (ASSET_CLASS_EQUITY, ASSET_CLASS_OVERSEAS)
_LIMIT_LABELS = {
    "preference": "偏好的权益上限",
    "volatility": "目标波动",
    "drawdown": "可接受最大回撤（按历史最差情景）",
    "cash_floor": "现金底线",
}


class AllocationProposalService:
    def __init__(
        self,
        *,
        diagnosis: PortfolioDiagnosisService,
        portfolio_risk: PortfolioRiskService,
        master_data: MasterDataService,
        identity_bootstrap: Callable[..., Any],
        report_root: str | Path,
    ) -> None:
        self.diagnosis = diagnosis
        self.portfolio_risk = portfolio_risk
        self.master_data = master_data
        self.identity_bootstrap = identity_bootstrap
        self.report_root = Path(report_root)

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        allow_remote: bool = True,
        write_report: bool = True,
    ) -> dict[str, Any]:
        preference = self.require_preference(user_id)
        state = self.diagnosis.collect(user_id=user_id, as_of_date=as_of_date, cash=cash, allow_remote=allow_remote)
        payload = self.evaluate(state, preference=preference, user_id=user_id, as_of_date=as_of_date, allow_remote=allow_remote)
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
        return payload

    def require_preference(self, user_id: str) -> InvestmentPreference:
        preference = self.portfolio_risk.latest_preference(UserContext(user_id=user_id))
        if preference is None:
            raise ValueError("用户还没有记录投资偏好；先用 record-preference 记录后再给配置建议")
        return preference

    def evaluate(
        self,
        state: dict[str, Any],
        *,
        preference: InvestmentPreference,
        user_id: str,
        as_of_date: str,
        allow_remote: bool,
    ) -> dict[str, Any]:
        """Allocation for positions already collected by the diagnosis service."""

        positions, gaps = state["positions"], list(state["gaps"])
        exposures = exposure_summary(positions)
        classes = {item["class"]: item["weight"] for item in exposures["asset_classes"]}
        prices = self.diagnosis.price_source(allow_remote)
        proxies: dict[str, dict[str, float]] = {ASSET_CLASS_CASH: {}}
        if state["benchmark"]:
            proxies[ASSET_CLASS_EQUITY] = state["benchmark"]
        else:
            gaps.append("沪深300 指数不可得，无法估计权益波动")
        for name, (symbol, asset_type, label) in CLASS_PROXIES.items():
            asset = self.resolve_asset(symbol, asset_type)
            if asset is None:
                gaps.append(f"{label}（{symbol}）未登记，{ASSET_CLASS_LABELS[name]}不参与估计")
                continue
            try:
                proxies[name] = self.diagnosis.levels(prices, asset, as_of_date, gaps)
            except ValueError as exc:
                gaps.append(f"{label} 价格不可得：{exc}")
        risk = class_risk(proxies, as_of_date=as_of_date)
        defensive = {key: value for key, value in classes.items() if key not in _RISK_CLASSES}
        if not defensive:
            gaps.append("组合没有防御类资产，按现金估算防御部分")
        floor, floor_note = effective_cash_floor(
            preference, total_value=exposures["total_market_value"], as_of_date=as_of_date
        )
        target = equity_range(preference, risk, defensive_mix=defensive, cash_floor=floor)
        current = risk_asset_share(classes)
        weights = weights_of(positions)
        risky_positions = [
            {
                "asset_id": item.asset_id,
                "label": item.label,
                "single_stock": item.asset_type == ASSET_TYPE_STOCK,
                "risk_weight": weights[item.asset_id]
                * sum(item.asset_classes.get(name, 0.0) for name in _RISK_CLASSES),
            }
            for item in positions
            if any(item.asset_classes.get(name, 0.0) > 0 for name in _RISK_CLASSES)
        ]
        _, cov = covariance_matrix(
            {item["asset_id"]: state["levels"].get(item["asset_id"], {}) for item in risky_positions},
            as_of_date=as_of_date,
        )
        within = within_risk_assets(risky_positions, cov, target_share=target["middle"], preference=preference)
        total = exposures["total_market_value"]
        direction = (
            "raise" if current < target["low"] - 0.01 else "lower" if current > target["high"] + 0.01 else "keep"
        )
        payload = {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "preference_id": preference.preference_id,
            "current_risk_share": round(current, 4),
            "target": target,
            "direction": direction,
            "amount_to_target": {
                "low": round((target["low"] - current) * total, 2),
                "high": round((target["high"] - current) * total, 2),
            },
            "within_risk_assets": within,
            "class_volatility": {
                ASSET_CLASS_LABELS.get(name, name): round(risk["cov"][name][name] ** 0.5, 4) for name in risk["classes"]
            },
            "summary": _summary(current, target, direction, within, total),
            "conflict": _conflict(target, preference) if target["conflict"] else None,
            "cash_floor": round(floor, 4),
            "gaps": gaps,
            "limitations": _limitations(),
        }
        if floor_note:
            payload["summary"].append(floor_note)
        return payload

    def resolve_asset(self, symbol: str, asset_type: str):
        matches = self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
        if not matches:
            try:
                self.identity_bootstrap(symbol, asset_type=asset_type)
            except Exception:  # noqa: BLE001 - reported as a gap by the caller
                return None
            matches = self.master_data.find_assets(standard_symbol=symbol, asset_type=asset_type)
        return matches[0] if matches else None

    def _write_report(self, payload: dict[str, Any]) -> Path:
        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"配置建议-{safe_user}-{payload['as_of_date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_allocation(payload), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return path


def render_allocation(payload: dict[str, Any]) -> str:
    target = payload["target"]
    lines = [
        f"# 配置与风险预算（截止 {payload['as_of_date']}）",
        "",
        *[f"- {item}" for item in payload["summary"]],
        "",
        "## 权益比例的上限从哪里来",
        "",
        "| 约束 | 允许的最高权益比例 | 是否起作用 |",
        "| --- | ---: | --- |",
        *[
            f"| {_LIMIT_LABELS[name]} | {value:.1%} | {'是' if name in target['binding'] else ''} |"
            for name, value in target["limits"].items()
        ],
        "",
        f"防御部分按当前构成估算：{'、'.join(f'{ASSET_CLASS_LABELS.get(k, k)} {v:.0%}' for k, v in target['defensive_mix'].items())}。"
        f"权益取上限 {target['high']:.0%} 时，估算组合波动 {target['at_high']['volatility']:.1%}，"
        f"历史最差情景损失 {target['at_high']['worst_loss']:.1%}（{target['at_high']['worst_scenario'] or '-'}）。",
        "",
        "## 目标区间中点的风险预算",
        "",
        "| 大类 | 权重 | 风险占比 |",
        "| --- | ---: | ---: |",
        *[f"| {item['label']} | {item['weight']:.1%} | {item['risk_share']:.1%} |" for item in target["risk_budget"]],
        "",
        f"## 权益内部（按目标中点 {target['middle']:.0%} 换算）",
        "",
        "| 持仓 | 当前结构 | 风险均衡参照 | 中点下占组合（当前结构） | 中点下占组合（风险均衡） | 超过单只上限 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
        *[
            f"| {item['label']} | {item['current_share']:.1%} | "
            f"{'-' if item['balanced_share'] is None else format(item['balanced_share'], '.1%')} | "
            f"{item['at_target_current']:.1%} | "
            f"{'-' if item['at_target_balanced'] is None else format(item['at_target_balanced'], '.1%')} | "
            f"{'当前结构' if item['over_single_cap'] else ''}"
            f"{'、' if item['over_single_cap'] and item['balanced_over_single_cap'] else ''}"
            f"{'风险均衡' if item['balanced_over_single_cap'] else ''} |"
            for item in payload["within_risk_assets"]
        ],
        "",
    ]
    if payload["gaps"]:
        lines += ["## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]], ""]
    lines += ["## 口径与局限", "", *[f"- {item}" for item in payload["limitations"]], ""]
    return "\n".join(lines)


def _summary(
    current: float,
    target: dict[str, Any],
    direction: str,
    within: list[dict[str, Any]],
    total: float,
) -> list[str]:
    binding = "、".join(_LIMIT_LABELS[name] for name in target["binding"])
    lines = [
        f"当前权益（含跨境）占 {current:.1%}；按偏好与风险预算，目标区间 {target['low']:.0%}–{target['high']:.0%}，上限由{binding}决定。"
    ]
    if target["conflict"]:
        low = target["at_preference_low"]
        lines.append(
            f"冲突：{binding}只允许权益到 {target['high']:.0%}，低于偏好下限 {low['equity']:.0%}。需要用户二选一："
            f"守住{binding}，权益最多 {target['high']:.0%}；或权益到 {low['equity']:.0%}，接受约 {low['volatility']:.1%} 的年化波动和"
            f"历史最差情景约 {low['worst_loss']:.1%} 的损失（{low['worst_scenario'] or '-'}）。用户选定前不生成调整方案。"
        )
        return lines
    if direction == "raise":
        lines.append(
            f"需要提高权益：约 {(target['low'] - current) * total:,.0f}–{(target['high'] - current) * total:,.0f} 元转入权益。"
        )
    elif direction == "lower":
        lines.append(
            f"需要降低权益：约 {(current - target['high']) * total:,.0f}–{(current - target['low']) * total:,.0f} 元转出权益。"
        )
    else:
        lines.append("权益比例已在目标区间内，调整重点在权益内部结构。")
    over = [item["label"] for item in within if item["over_single_cap"]]
    if over:
        lines.append(f"按当前结构放大到目标中点后，{'、'.join(over)} 会超过单只个股上限，加仓应落在其他持仓或新的分散构件上。")
    return lines


def _conflict(target: dict[str, Any], preference: InvestmentPreference) -> dict[str, Any]:
    """The two choices the user has when a risk limit caps equity below the preferred lower bound."""

    low = target["at_preference_low"]
    return {
        "binding": [_LIMIT_LABELS[name] for name in target["binding"]],
        "keep_limits": {
            "equity_high": target["high"],
            "volatility": target["at_high"]["volatility"],
            "worst_loss": target["at_high"]["worst_loss"],
        },
        "keep_equity": {
            "equity_low": low["equity"],
            "volatility": low["volatility"],
            "worst_loss": low["worst_loss"],
            "worst_scenario": low["worst_scenario"],
            "needs": {
                "max_drawdown": max(low["worst_loss"], float(preference.max_drawdown)),
                "target_volatility": max(low["volatility"], float(preference.target_volatility)),
            },
        },
    }


def _limitations() -> list[str]:
    return [
        "权益用沪深300 指数、债券用国债ETF（511010）、黄金用黄金ETF（518880）、跨境用纳斯达克100ETF（513100）代表，波动与相关性取近 3 年日收益；用户持有的具体基金与代表指数不同。",
        "回撤约束用代表指数在五段历史情景中的跌幅计算，是历史重演，不是未来跌幅的上限。",
        "风险均衡参照是等风险贡献权重（来源见 docs/evidence/strategy_sources/allocation_risk_budget.md），只作对照，不是目标权重；不预测收益，两个波动相近但前景不同的持仓会得到相近的参照。",
        "配置区间只说明方向和幅度；具体换入换出、分几步和触发条件由调整方案给出。",
    ]
