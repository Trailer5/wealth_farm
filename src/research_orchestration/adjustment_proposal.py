"""Adjustment proposal: target weight ranges, a stepwise migration path, costs, and the adjusted portfolio's risk.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「调整方案与迁移路径」.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.fund_research import OpenFundDecisionFactsService, purchase_fee_rate, redemption_fee_rate
from src.macro_data import MacroDataService
from src.market_analysis.archive import historical_percentile
from src.master_data import ASSET_TYPE_EXCHANGE_FUND, ASSET_TYPE_OPEN_FUND, ASSET_TYPE_STOCK, Asset
from src.portfolio_risk import (
    UNCOVERED_INDUSTRY,
    AdjustmentLine,
    DiagnosisPosition,
    InvestmentPreference,
    exposure_summary,
    migration_steps,
    preference_check,
    risk_asset_share,
    risk_summary,
    rolling_return_band,
    round_to_lots,
    stress_summary,
    target_weights,
    weights_of,
)
from src.research_contracts import ASSET_CLASS_CASH, ASSET_CLASS_EQUITY, ASSET_CLASS_OVERSEAS
from src.user_space import UserContext

from .allocation_proposal import AllocationProposalService
from .portfolio_diagnosis import CASH_ASSET_ID, PortfolioDiagnosisService

# Broad building blocks offered when the user names no candidates; not stock picks.
DEFAULT_BUILDING_BLOCKS = (
    ("510500", ASSET_TYPE_EXCHANGE_FUND),
    ("510880", ASSET_TYPE_EXCHANGE_FUND),
    ("510300", ASSET_TYPE_EXCHANGE_FUND),
)
MAX_DEFAULT_BLOCKS = 2
# Commission 0.025% each way; stamp duty 0.05% on stock sales (since 2023-08-28).
STOCK_BUY_COST = 0.00025
STOCK_SELL_COST = 0.00075
EXCHANGE_FUND_COST = 0.00025
# Minimum commission per order and the board lot of A-share stocks and exchange funds.
MIN_COMMISSION = 5.0
BOARD_LOT = 100
UNKNOWN_HOLDING_DAYS = 730
FEE_WAIT_DAYS = 60
BUY_PE_PERCENTILE = 0.8
PE_SERIES = "csi300_pe_ttm"
PE_YEARS = 10


class AdjustmentProposalService:
    def __init__(
        self,
        *,
        diagnosis: PortfolioDiagnosisService,
        allocation: AllocationProposalService,
        fund_facts: OpenFundDecisionFactsService,
        macro_data: MacroDataService,
        report_root: str | Path,
    ) -> None:
        self.diagnosis = diagnosis
        self.allocation = allocation
        self.fund_facts = fund_facts
        self.macro_data = macro_data
        self.report_root = Path(report_root)

    def run(
        self,
        *,
        user_id: str,
        as_of_date: str,
        cash: float | None = None,
        candidates: tuple[tuple[str, str], ...] = (),
        allow_remote: bool = True,
        write_report: bool = True,
    ) -> dict[str, Any]:
        preference = self.allocation.require_preference(user_id)
        state = self.diagnosis.collect(user_id=user_id, as_of_date=as_of_date, cash=cash, allow_remote=allow_remote)
        allocation = self.allocation.evaluate(
            state, preference=preference, user_id=user_id, as_of_date=as_of_date, allow_remote=allow_remote
        )
        gaps = list(allocation["gaps"])
        positions: list[DiagnosisPosition] = list(state["positions"])
        levels = dict(state["levels"])
        if allocation["target"]["conflict"]:
            return self._conflict_payload(user_id, as_of_date, preference, allocation, state, gaps, write_report)
        held = {item.asset_id for item in positions}
        prices = self.diagnosis.price_source(allow_remote)
        candidate_assets = self._candidates(candidates, held, gaps)
        candidate_ids = []
        for asset in candidate_assets:
            try:
                levels[asset.asset_id] = self.diagnosis.levels(prices, asset, as_of_date, gaps)
            except ValueError as exc:
                gaps.append(f"候选 {asset.display_name or asset.standard_symbol} 价格不可得，未纳入：{exc}")
                continue
            positions.append(
                self.diagnosis.position(
                    asset,
                    market_value=0.0,
                    as_of_date=as_of_date,
                    industry_map=state["industry_map"],
                    allow_remote=allow_remote,
                    gaps=gaps,
                )
            )
            candidate_ids.append(asset.asset_id)
        weights = weights_of(positions)
        lines = [_line(item, weights[item.asset_id], item.asset_id in candidate_ids) for item in positions]
        receivers = [
            line.asset_id
            for line in lines
            if line.risk_line and (line.candidate or (not line.single_stock and not candidates))
        ]
        target = allocation["target"]
        common = {
            "preference": preference,
            "equity_low": target["low"],
            "equity_high": target["high"],
            "receivers": receivers,
            "cash_floor": allocation["cash_floor"],
            "substitute": bool(candidate_ids) and bool(candidates),
        }
        targets = target_weights(lines, **common)
        stock_caps = _look_through_caps(positions, targets, float(preference.max_single_weight))
        if stock_caps:
            targets = target_weights(lines, **common, stock_caps=stock_caps)
        summary = targets.pop("_summary")
        total = sum(item.market_value for item in positions)
        holdings = {
            item.asset_id: item
            for item in self.allocation.portfolio_risk.research_state.list_holdings(UserContext(user_id=user_id))
        }
        assets = {item.asset_id: self.diagnosis.master_data.get_asset(item.asset_id) for item in positions}
        last_prices = {key: value[1] for key, value in state["latest"].items()}
        last_prices.update({key: levels[key][max(levels[key])] for key in candidate_ids})
        lots = {
            item.asset_id: BOARD_LOT
            for item in positions
            if item.asset_type in (ASSET_TYPE_STOCK, ASSET_TYPE_EXCHANGE_FUND)
        }
        summary["notes"] += round_to_lots(
            targets,
            total=total,
            quantities={key: float(item.quantity) for key, item in holdings.items()},
            prices=last_prices,
            lots=lots,
            sink=_cash_sink(lines, targets),
        )
        changes = []
        for line in lines:
            row = targets[line.asset_id]
            if line.asset_id == CASH_ASSET_ID or abs(row["change"]) < 1e-9:
                continue
            amount = row["change"] * total
            if line.asset_id in lots and "quantity_change" in row:
                amount = row["quantity_change"] * last_prices[line.asset_id]
            rate, earliest, note = self._cost(assets[line.asset_id], amount, holdings.get(line.asset_id), as_of_date, allow_remote)
            row["cost_rate"] = rate
            change = {
                "asset_id": line.asset_id,
                "label": line.label,
                "amount": amount,
                "cost_rate": rate,
                "risk": line.risk_line,
                "earliest": earliest,
                "note": note,
            }
            if line.asset_id in lots and "quantity_change" in row:
                change.update(
                    price=last_prices[line.asset_id],
                    lot=lots[line.asset_id],
                    commission_rate=STOCK_BUY_COST if line.asset_type == ASSET_TYPE_STOCK else EXCHANGE_FUND_COST,
                    min_commission=MIN_COMMISSION,
                )
            changes.append(change)
        cash_line = targets.get(CASH_ASSET_ID)
        starting_cash = max(cash_line["current"] - cash_line["target"], 0.0) * total if cash_line else 0.0
        condition, valuation = self._buy_condition(as_of_date, allow_remote, gaps)
        migration = migration_steps(changes, as_of_date=as_of_date, starting_cash=starting_cash, buy_condition=condition)
        if migration["cash_shortfall"] > 1:
            gaps.append(f"按计划买入还差约 {migration['cash_shortfall']:,.0f} 元资金，需要新增资金或降低目标")
        after_positions = [
            replace(item, market_value=targets[item.asset_id]["target"] * total)
            for item in positions
            if targets[item.asset_id]["target"] > 0
        ]
        before = _profile(positions, levels, state["benchmark"], as_of_date, preference)
        after = _profile(after_positions, levels, state["benchmark"], as_of_date, preference)
        band = rolling_return_band(
            weights_of(after_positions),
            levels,
            as_of_date=as_of_date,
            benchmark_levels=state["benchmark"] or {},
            zero_return=frozenset(item.asset_id for item in after_positions if item.main_class() == ASSET_CLASS_CASH),
        )
        if band is not None:
            yearly = band.pop("yearly")
            band["share_below_max_drawdown"] = round(
                sum(1 for value in yearly if value < -float(preference.max_drawdown)) / len(yearly), 4
            )
        payload = {
            "status": "ok",
            "user_id": user_id,
            "as_of_date": as_of_date,
            "preference_id": preference.preference_id,
            "allocation": allocation,
            "targets": [
                {"asset_id": key, **value}
                for key, value in sorted(targets.items(), key=lambda pair: -pair[1]["target"])
                if value["target"] > 0 or value["current"] > 0
            ],
            "equity": summary,
            "notes": summary["notes"],
            "migration": migration,
            "buy_valuation": valuation,
            "before": before,
            "after": after,
            "return_band": band,
            "after_weights": {key: value["target"] for key, value in targets.items() if value["target"] > 0},
            "candidates": [targets[item] | {"asset_id": item} for item in candidate_ids],
            "total_market_value": round(total, 2),
            "gaps": gaps,
            "limitations": _limitations(),
        }
        payload["summary"] = _summary(payload)
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
        return payload

    def _conflict_payload(
        self,
        user_id: str,
        as_of_date: str,
        preference: InvestmentPreference,
        allocation: dict[str, Any],
        state: dict[str, Any],
        gaps: list[str],
        write_report: bool,
    ) -> dict[str, Any]:
        """No migration while the preferred equity range and a risk limit disagree; the user picks one."""

        conflict = allocation["conflict"]
        keep, equity = conflict["keep_limits"], conflict["keep_equity"]
        binding = "、".join(conflict["binding"])
        payload = {
            "status": "conflict",
            "user_id": user_id,
            "as_of_date": as_of_date,
            "preference_id": preference.preference_id,
            "allocation": allocation,
            "conflict": conflict,
            "before": _profile(state["positions"], state["levels"], state["benchmark"], as_of_date, preference),
            "targets": [],
            "migration": None,
            "summary": [
                *allocation["summary"],
                "调整方案未生成：偏好的权益区间与风险约束冲突，需要用户先选一条路，系统不替用户选。",
                f"选项一：守住{binding}，权益最多约 {keep['equity_high']:.0%}（按代表指数估算的历史最差情景损失约 {keep['worst_loss']:.1%}），"
                f"相应把偏好的权益下限降到这一水平。",
                f"选项二：权益保持在 {equity['equity_low']:.0%} 以上，需要能接受约 {equity['worst_loss']:.1%} 的历史最差情景损失"
                f"（{equity['worst_scenario'] or '-'}）和约 {equity['volatility']:.1%} 的年化波动，相应提高可接受最大回撤与目标波动。",
                "用户选定后，按其原话用 record-preference 记录新版本，再重新生成调整方案。",
            ],
            "gaps": gaps,
            "limitations": _limitations(),
        }
        if write_report:
            payload["report_path"] = str(self._write_report(payload))
        return payload

    def _candidates(
        self,
        candidates: tuple[tuple[str, str], ...],
        held: set[str],
        gaps: list[str],
    ) -> list[Asset]:
        result = []
        requested = candidates or DEFAULT_BUILDING_BLOCKS
        for symbol, asset_type in requested:
            asset = self.allocation.resolve_asset(symbol, asset_type)
            if asset is None:
                gaps.append(f"候选 {symbol}（{asset_type}）无法识别")
                continue
            if asset.asset_id in held or asset in result:
                continue
            result.append(asset)
            if not candidates and len(result) >= MAX_DEFAULT_BLOCKS:
                break
        return result

    def _cost(
        self,
        asset: Asset | None,
        amount: float,
        holding: Any,
        as_of_date: str,
        allow_remote: bool,
    ) -> tuple[float, str | None, str | None]:
        if asset is None:
            return 0.0, None, None
        if asset.asset_type == ASSET_TYPE_STOCK:
            return (STOCK_BUY_COST if amount > 0 else STOCK_SELL_COST), None, None
        if asset.asset_type == ASSET_TYPE_EXCHANGE_FUND:
            return EXCHANGE_FUND_COST, None, None
        if asset.asset_type != ASSET_TYPE_OPEN_FUND:
            return 0.0, None, None
        info, _ = self.fund_facts.dealing_info(asset, allow_remote=allow_remote)
        if info is None:
            return 0.0, None, "申赎费率不可得，成本按 0 计"
        if "货币" in (info.fund_type or ""):
            return 0.0, None, None
        if amount > 0:
            rate = purchase_fee_rate(info, amount=amount)
            return float(rate or 0), None, None if rate is not None else "申购费为固定金额或不可得"
        bought = getattr(holding, "first_bought_on", None) if holding else None
        if not bought:
            rate = redemption_fee_rate(info, UNKNOWN_HOLDING_DAYS)
            return float(rate or 0), None, "买入日未知，赎回费按持有满 2 年计，持有不足时更高"
        days = (date.fromisoformat(as_of_date) - date.fromisoformat(bought)).days
        rate = float(redemption_fee_rate(info, days) or 0)
        cheaper = next(
            (
                tier
                for tier in info.redemption_fees
                if tier.min_days > days and float(tier.rate) < rate and tier.min_days - days <= FEE_WAIT_DAYS
            ),
            None,
        )
        if cheaper is None:
            return rate, None, None
        earliest = (date.fromisoformat(bought) + timedelta(days=cheaper.min_days)).isoformat()
        return (
            float(cheaper.rate),
            earliest,
            f"持有满 {cheaper.min_days} 天（{earliest}）后赎回费从 {rate:.2%} 降到 {float(cheaper.rate):.2%}，赎回排在此后",
        )

    def _buy_condition(self, as_of_date: str, allow_remote: bool, gaps: list[str]) -> tuple[str, dict[str, Any] | None]:
        start = f"{int(as_of_date[:4]) - PE_YEARS - 1}-01-01"
        try:
            series = self.macro_data.get(
                PE_SERIES, start_date=start, end_date=as_of_date, as_of_date=as_of_date, allow_remote=allow_remote
            )
            stat = historical_percentile(
                [(item.period_end, item.value) for item in series.points], end_date=as_of_date, years=PE_YEARS
            )
        except Exception as exc:  # noqa: BLE001 - buys then run on time only
            gaps.append(f"沪深300 估值分位不可得，买入只按时间执行：{type(exc).__name__}: {exc}")
            stat = None
        if stat is None:
            return "按时间窗口执行（估值分位不可得）", None
        window_start = f"{int(as_of_date[:4]) - PE_YEARS}{as_of_date[4:]}"
        values = sorted(item.value for item in series.points if window_start <= item.period_end <= as_of_date)
        threshold = values[int(BUY_PE_PERCENTILE * (len(values) - 1))]
        met = stat.percentile <= BUY_PE_PERCENTILE
        condition = (
            f"沪深300 市盈率（TTM）近 {PE_YEARS} 年分位不高于 {BUY_PE_PERCENTILE:.0%}"
            f"（截止日为 {stat.percentile:.0%}，{'满足' if met else '不满足'}）；不满足时该步权益买入顺延到下一步，最后一步照常执行"
        )
        return condition, {**stat.to_dict(), "series_id": PE_SERIES, "met": met, "threshold_value": threshold}

    def _write_report(self, payload: dict[str, Any]) -> Path:
        safe_user = re.sub(r"[^\w-]+", "_", payload["user_id"])
        path = self.report_root / f"调整方案-{safe_user}-{payload['as_of_date']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_adjustment(payload), encoding="utf-8")
        path.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        return path


def _look_through_caps(
    positions: list[DiagnosisPosition],
    targets: dict[str, dict[str, Any]],
    cap: float,
) -> dict[str, float]:
    """Direct caps for stocks that funds also hold at the target weights."""

    caps = {}
    for stock in positions:
        if stock.asset_type != ASSET_TYPE_STOCK:
            continue
        (key,) = stock.stocks or {"": None}
        indirect = sum(
            targets[item.asset_id]["target"] * item.stocks[key][1]
            for item in positions
            if item.asset_id != stock.asset_id and key in item.stocks
        )
        if indirect > 0:
            caps[stock.asset_id] = max(cap - indirect, 0.0)
    return caps


def _line(position: DiagnosisPosition, weight: float, candidate: bool) -> AdjustmentLine:
    return AdjustmentLine(
        asset_id=position.asset_id,
        label=position.label,
        asset_type=position.asset_type,
        current_weight=weight,
        risk_fraction=sum(position.asset_classes.get(name, 0.0) for name in (ASSET_CLASS_EQUITY, ASSET_CLASS_OVERSEAS)),
        industries={key: value for key, value in position.industries.items() if key != UNCOVERED_INDUSTRY},
        cash_like=position.main_class() == ASSET_CLASS_CASH,
        candidate=candidate,
        main_class=position.main_class(),
    )


def _cash_sink(lines: list[AdjustmentLine], targets: dict[str, dict[str, Any]]) -> str | None:
    """Where rounding differences go: the stated cash, else the largest cash-like holding."""

    if CASH_ASSET_ID in targets:
        return CASH_ASSET_ID
    cash_like = [line for line in lines if line.cash_like]
    return max(cash_like, key=lambda line: targets[line.asset_id]["target"]).asset_id if cash_like else None


def _profile(
    positions: list[DiagnosisPosition],
    levels: dict[str, dict[str, float]],
    benchmark: dict[str, float] | None,
    as_of_date: str,
    preference: InvestmentPreference,
) -> dict[str, Any]:
    weights = weights_of(positions)
    labels = {item.asset_id: item.label for item in positions}
    cash_like = frozenset(item.asset_id for item in positions if item.main_class() == ASSET_CLASS_CASH)
    exposures = exposure_summary(positions)
    risk = risk_summary(
        weights,
        levels,
        as_of_date=as_of_date,
        proxy_levels=benchmark,
        proxy_eligible=frozenset(item.asset_id for item in positions if item.main_class() == ASSET_CLASS_EQUITY),
        zero_variance=cash_like,
        labels=labels,
    )
    stress = stress_summary(
        weights, levels, as_of_date=as_of_date, benchmark_levels=benchmark or {}, labels=labels, zero_return=cash_like
    )
    classes = {item["class"]: item["weight"] for item in exposures["asset_classes"]}
    industries = [item for item in exposures["industries"] if item["industry"] != UNCOVERED_INDUSTRY]
    return {
        "equity": round(risk_asset_share(classes), 4),
        "asset_classes": exposures["asset_classes"],
        "industries": industries[:10],
        "max_stock": exposures["top_stocks"][0] if exposures["top_stocks"] else None,
        "max_industry": industries[0] if industries else None,
        "volatility": risk["portfolio_volatility"],
        "risk_contributions": risk["risk_contributions"],
        "stress": [
            {
                "label": item["label"],
                "portfolio_return": item["portfolio_return"],
                "benchmark_return": item["benchmark_return"],
                "uncovered_weight": item["uncovered_weight"],
            }
            for item in stress
        ],
        "preference_checks": preference_check(exposures, risk, stress, preference),
    }


def _summary(payload: dict[str, Any]) -> list[str]:
    before, after = payload["before"], payload["after"]
    lines = [
        f"权益（含跨境）{before['equity']:.1%} → {after['equity']:.1%}；组合波动 "
        f"{_pct(before['volatility'])} → {_pct(after['volatility'])}；最大单只个股 "
        f"{_name_pct(before['max_stock'])} → {_name_pct(after['max_stock'])}；最大行业 "
        f"{_name_pct(before['max_industry'], 'industry')} → {_name_pct(after['max_industry'], 'industry')}。"
    ]
    worst_before = min(before["stress"], key=lambda item: item["portfolio_return"], default=None)
    worst_after = min(after["stress"], key=lambda item: item["portfolio_return"], default=None)
    if worst_before and worst_after:
        lines.append(
            f"最差历史情景：调整前 {worst_before['label']} {worst_before['portfolio_return']:.1%}，"
            f"调整后 {worst_after['label']} {worst_after['portfolio_return']:.1%}。"
        )
    worse = [
        f"{left['label']} {left['portfolio_return']:.1%}→{right['portfolio_return']:.1%}"
        for left, right in zip(before["stress"], after["stress"])
        if right["portfolio_return"] < left["portfolio_return"] - 0.01
    ]
    if worse:
        lines.append("注意：调整后在这些历史情景中损失更大：" + "；".join(worse) + "。分散降低了单只与行业集中，不保证每段历史都更抗跌。")
    sells = [item for item in payload["targets"] if item["change"] < 0]
    buys = [item for item in payload["targets"] if item["change"] > 0]
    if sells:
        lines.append("减少：" + "、".join(f"{item['label']} {item['current']:.0%}→{item['low']:.0%}–{item['high']:.0%}" for item in sells))
    if buys:
        lines.append("增加：" + "、".join(f"{item['label']} {item['current']:.0%}→{item['low']:.0%}–{item['high']:.0%}" for item in buys))
    migration = payload["migration"]
    lines.append(
        f"分 {len(migration['steps'])} 步执行，预计交易成本约 {migration['total_cost']:,.0f} 元。"
    )
    lines += payload["notes"]
    return lines


def render_adjustment(payload: dict[str, Any]) -> str:
    if payload.get("status") == "conflict":
        lines = [
            f"# 调整方案与迁移路径（截止 {payload['as_of_date']}）",
            "",
            "## 需要先选择",
            "",
            *[f"- {item}" for item in payload["summary"]],
            "",
            "## 现状",
            "",
            f"权益（含跨境）{payload['before']['equity']:.1%}，组合年化波动 {_pct(payload['before']['volatility'])}；",
            *[
                f"- 情景：{item['label']} {item['portfolio_return']:.1%}"
                for item in payload["before"]["stress"]
            ],
            "",
        ]
        if payload["gaps"]:
            lines += ["## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]], ""]
        return "\n".join(lines)
    before, after = payload["before"], payload["after"]
    before_checks = {item["check_id"]: item for item in before["preference_checks"]}
    lines = [
        f"# 调整方案与迁移路径（截止 {payload['as_of_date']}）",
        "",
        *[f"- {item}" for item in payload["summary"]],
        "",
        "## 目标区间",
        "",
        "| 持仓或候选 | 当前 | 目标区间 | 变化 | 原因 |",
        "| --- | ---: | --- | ---: | --- |",
        *[
            f"| {item['label']}{'（候选）' if item['candidate'] else ''} | {item['current']:.1%} | "
            f"{item['low']:.1%}–{item['high']:.1%} | {item['change']:+.1%} | {'；'.join(item['reasons'])} |"
            for item in payload["targets"]
        ],
        "",
        f"目标区间是目标权重 ±2 个百分点；金额按总市值 {payload['total_market_value']:,.0f} 元（含用户说明的未投资现金）换算；"
        "股票与场内基金按每手 100 股（份）取整，场外基金按金额申购、按份额赎回。",
        "",
        "## 迁移路径",
        "",
    ]
    for step in payload["migration"]["steps"]:
        lines += [
            f"### 第 {step['step']} 步（{step['window_start']} 至 {step['window_end']}）",
            "",
            f"卖出 {step['sell_amount']:,.0f} 元，买入 {step['buy_amount']:,.0f} 元，预计成本 {step['cost']:,.0f} 元。",
            "",
            "| 动作 | 标的 | 金额 | 数量 | 条件 | 说明 |",
            "| --- | --- | ---: | ---: | --- | --- |",
            *[
                f"| {'卖出' if leg['action'] == 'sell' else '买入'} | {leg['label']} | {abs(leg['amount']):,.0f} | "
                f"{'按金额' if leg.get('quantity') is None else format(leg['quantity'], ',')} | "
                f"{leg['condition'] or '按时间执行'} | {leg['note'] or ''} |"
                for leg in step["legs"]
            ],
            "",
        ]
    lines += [
        "## 调整前后对比",
        "",
        "| 项目 | 调整前 | 调整后 |",
        "| --- | ---: | ---: |",
        f"| 权益（含跨境） | {before['equity']:.1%} | {after['equity']:.1%} |",
        f"| 组合年化波动（近 3 年） | {_pct(before['volatility'])} | {_pct(after['volatility'])} |",
        f"| 最大单只个股 | {_name_pct(before['max_stock'])} | {_name_pct(after['max_stock'])} |",
        f"| 最大行业 | {_name_pct(before['max_industry'], 'industry')} | {_name_pct(after['max_industry'], 'industry')} |",
        *[
            f"| 情景：{left['label']} | {left['portfolio_return']:.1%} | {right['portfolio_return']:.1%} |"
            for left, right in zip(before["stress"], after["stress"])
        ],
        "",
        "调整后与偏好对照："
        + "；".join(
            f"{item['label']}" + _check_text(item, before_checks.get(item["check_id"]))
            for item in after["preference_checks"]
        )
        + "。",
        "",
    ]
    band = payload.get("return_band")
    if band:
        lines += [
            f"按目标权重回放 {band['start']} 至 {band['end']}：滚动一年收益有 {band['coverage']:.0%} 落在 "
            f"{band['low']:.1%} 到 {band['high']:.1%} 之间（{band['windows']} 个重叠窗口，约 {band['independent_years']} 个独立年份，"
            f"按独立年份估计这一比例的区间为 {band['probability_low']:.0%}–{band['probability_high']:.0%}）；"
            f"一年亏损超过可接受最大回撤的窗口占 {band['share_below_max_drawdown']:.1%}。历史分布，不是预测。",
            "",
        ]
    if payload["gaps"]:
        lines += ["## 数据缺口", "", *[f"- {item}" for item in payload["gaps"]], ""]
    lines += ["## 口径与局限", "", *[f"- {item}" for item in payload["limitations"]], ""]
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _check_text(after: dict[str, Any], before: dict[str, Any] | None) -> str:
    if after["status"] == "ok":
        return "符合"
    if after["status"] not in ("above", "below"):
        return "无法判断"
    return "仍不符" if before is not None and before["status"] in ("above", "below") else "不符（调整前符合）"


def _name_pct(item: dict[str, Any] | None, key: str = "name") -> str:
    return "-" if item is None else f"{item[key]} {item['weight']:.1%}"


def _limitations() -> list[str]:
    return [
        "调整只依据偏好、风险预算和集中度上限，不预测收益；增加的部分只给到用户点名的候选、已持有的基金，或默认的宽基 ETF 构件（中证500、红利、沪深300），不是选股结论；点名候选在权益已在区间内时按“多一个等权席位”腾出额度，这是机械规则，不代表对候选的看法。",
        "个股超过单只上限先减到上限；行业超过上限时只减该行业的个股，基金带来的行业集中只提示不调整；减持释放的资金只进债券与现金类，黄金与商品不加仓。",
        "迁移默认分 3 步、每步 14 天：减持在前两步完成（场外基金临近赎回费降档时排在降档之后），买入分三步并受已释放资金约束；权益买入以沪深300 估值分位为条件，不满足顺延，最后一步照常执行。股票与场内基金按每手 100 股（份）取整，差额计入现金。",
        "交易成本按佣金 0.025%（每笔至少 5 元）、股票卖出印花税 0.05%、场外基金申购费（优惠费率）与按持有天数的赎回费估算，不含冲击成本与税费以外的其他费用。",
        "调整后的波动、行业与压力情景按目标权重和各持仓的历史重放计算，是历史重演，不是对未来的预测。",
    ]
