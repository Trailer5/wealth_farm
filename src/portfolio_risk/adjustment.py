"""Adjustment from current weights to the allocation target, and a stepwise migration path.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「调整方案与迁移路径」.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.research_contracts import ASSET_CLASS_BOND

from .preference import InvestmentPreference

RANGE_TOLERANCE = 0.02
MIN_TRADE_WEIGHT = 0.005
MIGRATION_STEPS = 3
STEP_INTERVAL_DAYS = 14
SELL_STEPS = 2


@dataclass(frozen=True)
class AdjustmentLine:
    """One holding or candidate; fractions are of the line's own value."""

    asset_id: str
    label: str
    asset_type: str
    current_weight: float
    risk_fraction: float
    industries: dict[str, float] = field(default_factory=dict)
    cash_like: bool = False
    candidate: bool = False
    main_class: str = ""

    @property
    def single_stock(self) -> bool:
        return self.asset_type == "stock"

    @property
    def risk_line(self) -> bool:
        return self.risk_fraction >= 0.5


def target_weights(
    lines: list[AdjustmentLine],
    *,
    preference: InvestmentPreference,
    equity_low: float,
    equity_high: float,
    receivers: list[str],
    stock_caps: dict[str, float] | None = None,
    cash_floor: float | None = None,
    substitute: bool = False,
) -> dict[str, dict[str, Any]]:
    """Cap single stocks and industries, bring equity into range, let defensive lines absorb the rest.

    ``stock_caps`` lowers a stock's own cap when funds already hold it, so the look-through total stays within the limit.
    ``substitute`` (named candidates) gives each candidate at least one equal equity slot, funded by the existing equity lines.
    """

    weights = {line.asset_id: line.current_weight for line in lines}
    reasons: dict[str, list[str]] = {line.asset_id: [] for line in lines}
    by_id = {line.asset_id: line for line in lines}
    cap = float(preference.max_single_weight)
    caps = {line.asset_id: (stock_caps or {}).get(line.asset_id, cap) for line in lines}
    industry_cap = float(preference.max_industry_weight)
    floor = float(preference.min_cash_weight) if cash_floor is None else cash_floor
    for line in lines:
        if line.single_stock and weights[line.asset_id] > caps[line.asset_id]:
            weights[line.asset_id] = caps[line.asset_id]
            reasons[line.asset_id].append(
                f"单只个股上限 {cap:.0%}"
                + ("（扣除基金里已持有的部分）" if caps[line.asset_id] < cap else "")
            )
    notes: list[str] = []
    for industry in sorted({key for line in lines for key in line.industries}):
        exposure = sum(weights[line.asset_id] * line.industries.get(industry, 0.0) for line in lines)
        if exposure <= industry_cap + 1e-9:
            continue
        stocks = [line for line in lines if line.single_stock and line.industries.get(industry, 0.0) > 0]
        movable = sum(weights[line.asset_id] * line.industries[industry] for line in stocks)
        excess = exposure - industry_cap
        if movable <= 0:
            notes.append(f"{industry} {exposure:.1%} 超过行业上限，主要来自基金，未在本方案中调整")
            continue
        for line in stocks:
            share = weights[line.asset_id] * line.industries[industry] / movable
            weights[line.asset_id] = max(weights[line.asset_id] - min(excess, movable) * share, 0.0)
            reasons[line.asset_id].append(f"{industry}行业上限 {industry_cap:.0%}")
        if excess > movable:
            notes.append(f"{industry} 减掉全部个股后仍超过行业上限")
    current_equity = sum(line.current_weight * line.risk_fraction for line in lines)
    target_equity = min(max(current_equity, equity_low), equity_high)
    equity = sum(weights[line.asset_id] * line.risk_fraction for line in lines)
    gap = target_equity - equity
    if gap > 1e-9:
        _fill(gap, [by_id[item] for item in receivers if item in by_id], weights, reasons, caps)
    elif gap < -1e-9:
        risky = [line for line in lines if line.risk_line and weights[line.asset_id] > 0]
        total = sum(weights[line.asset_id] * line.risk_fraction for line in risky)
        for line in risky:
            share = weights[line.asset_id] * line.risk_fraction / total
            weights[line.asset_id] -= -gap * share / line.risk_fraction
            reasons[line.asset_id].append(f"权益高于目标上限 {equity_high:.0%}")
    if substitute:
        _substitute(
            [by_id[item] for item in receivers if item in by_id and by_id[item].candidate],
            lines,
            weights,
            reasons,
            caps,
            target_equity,
            industry_cap,
        )
    _absorb_in_defensive(lines, weights, reasons, floor, notes)
    _ensure_cash_floor(lines, weights, reasons, floor, notes)
    for line in lines:
        if abs(weights[line.asset_id] - line.current_weight) < MIN_TRADE_WEIGHT and not line.candidate:
            if weights[line.asset_id] != line.current_weight:
                reasons[line.asset_id] = [f"变化小于 {MIN_TRADE_WEIGHT:.1%}，不调整"]
            weights[line.asset_id] = line.current_weight
    residual = 1.0 - sum(weights.values())
    sink = next((line for line in lines if line.asset_type == "cash"), None) or max(
        (line for line in lines if line.cash_like), key=lambda line: weights[line.asset_id], default=None
    )
    if sink is not None and abs(residual) > 1e-9:
        weights[sink.asset_id] = max(weights[sink.asset_id] + residual, 0.0)
    result = {}
    for line in lines:
        target = weights[line.asset_id]
        high_bound = caps[line.asset_id] if line.single_stock else 1.0
        result[line.asset_id] = {
            "label": line.label,
            "asset_type": line.asset_type,
            "candidate": line.candidate,
            "current": round(line.current_weight, 4),
            "target": round(target, 4),
            "low": round(max(target - RANGE_TOLERANCE, 0.0), 4) if target > 0 else 0.0,
            "high": round(min(target + RANGE_TOLERANCE, high_bound), 4) if target > 0 else 0.0,
            "change": round(target - line.current_weight, 4),
            "reasons": reasons[line.asset_id],
        }
    result["_summary"] = {
        "current_equity": round(current_equity, 4),
        "target_equity": round(target_equity, 4),
        "notes": notes,
    }
    return result


def migration_steps(
    changes: list[dict[str, Any]],
    *,
    as_of_date: str,
    starting_cash: float,
    buy_condition: str,
    steps: int = MIGRATION_STEPS,
    interval_days: int = STEP_INTERVAL_DAYS,
) -> dict[str, Any]:
    """Sells over the first two steps (later if a fee tier ends soon), buys over all steps within released cash.

    Each change: asset_id, label, amount (signed yuan), cost_rate, risk (bool), earliest (date or None), and for
    exchange-traded lines ``price`` and ``lot``: their legs are whole lots (a full exit may end with an odd lot).
    """

    start = date.fromisoformat(as_of_date)
    windows = [
        (
            (start + timedelta(days=index * interval_days + 1)).isoformat(),
            (start + timedelta(days=(index + 1) * interval_days)).isoformat(),
        )
        for index in range(steps)
    ]
    plan: list[list[dict[str, Any]]] = [[] for _ in range(steps)]
    sells = [item for item in changes if item["amount"] < 0]
    buys = [item for item in changes if item["amount"] > 0]
    for item in sells:
        first = 0
        if item.get("earliest"):
            first = next(
                (index for index, (_, end) in enumerate(windows) if item["earliest"] <= end),
                steps - 1,
            )
        span = list(range(first, min(max(first + SELL_STEPS, first + 1), steps)))
        if item.get("lot"):
            quantity = round(-item["amount"] / item["price"])
            parts = [part * item["lot"] for part in _split(quantity // item["lot"], len(span))]
            parts[-1] += quantity % item["lot"]
            amounts = [-part * item["price"] for part in parts]
        else:
            amounts = [item["amount"] / len(span)] * len(span)
        for index, amount in zip(span, amounts):
            if abs(amount) < 1e-9:
                continue
            window = windows[index]
            if item.get("earliest") and item["earliest"] > window[0]:
                window = (item["earliest"], window[1])
            plan[index].append(_leg(item, amount, window, None))
    cash = starting_cash
    pending = {item["asset_id"]: item["amount"] for item in buys}
    shares = {
        item["asset_id"]: _split(round(item["amount"] / item["price"] / item["lot"]), steps)
        for item in buys
        if item.get("lot")
    }
    shortfall = 0.0
    for index in range(steps):
        cash += -sum(leg["amount"] for leg in plan[index] if leg["amount"] < 0)
        wanted = {}
        for item in buys:
            key = item["asset_id"]
            if key in shares:
                unit = item["price"] * item["lot"]
                lots = round(pending[key] / unit) if index == steps - 1 else min(shares[key][index], round(pending[key] / unit))
                wanted[key] = lots * unit
            else:
                wanted[key] = min(pending[key] if index == steps - 1 else item["amount"] / steps, pending[key])
        total_wanted = sum(wanted.values())
        scale = min(1.0, cash / total_wanted) if total_wanted > 0 else 0.0
        for item in buys:
            key = item["asset_id"]
            amount = wanted[key] * scale
            if key in shares:
                unit = item["price"] * item["lot"]
                amount = math.floor(amount / unit + 1e-9) * unit
            if amount <= 0:
                continue
            pending[key] -= amount
            cash -= amount
            plan[index].append(
                _leg(item, amount, windows[index], buy_condition if item.get("risk") else None)
            )
        if index == steps - 1:
            shortfall = sum(value for value in pending.values() if value > 1e-6)
    return {
        "steps": [
            {
                "step": index + 1,
                "window_start": windows[index][0],
                "window_end": windows[index][1],
                "legs": legs,
                "sell_amount": round(-sum(leg["amount"] for leg in legs if leg["amount"] < 0), 2),
                "buy_amount": round(sum(leg["amount"] for leg in legs if leg["amount"] > 0), 2),
                "cost": round(sum(leg["cost"] for leg in legs), 2),
            }
            for index, legs in enumerate(plan)
        ],
        "total_cost": round(sum(leg["cost"] for legs in plan for leg in legs), 2),
        "cash_shortfall": round(shortfall, 2),
    }


def _leg(item: dict[str, Any], amount: float, window: tuple[str, str], condition: str | None) -> dict[str, Any]:
    return {
        "asset_id": item["asset_id"],
        "label": item["label"],
        "action": "sell" if amount < 0 else "buy",
        "amount": round(amount, 2),
        "quantity": round(abs(amount) / item["price"]) if item.get("lot") else None,
        # A broker charges at least min_commission per order, whatever the size.
        "cost": round(
            abs(amount) * item["cost_rate"]
            + max(item.get("min_commission", 0.0) - abs(amount) * item.get("commission_rate", 0.0), 0.0),
            2,
        ),
        "condition": condition,
        "note": item.get("note"),
        "window": list(window),
    }


def _split(units: int, parts: int) -> list[int]:
    """Whole units over ``parts`` steps, earlier steps taking the remainder."""

    base, extra = divmod(max(int(units), 0), parts)
    return [base + (1 if index < extra else 0) for index in range(parts)]


def _fill(
    gap: float,
    receivers: list[AdjustmentLine],
    weights: dict[str, float],
    reasons: dict[str, list[str]],
    caps: dict[str, float],
) -> None:
    remaining = gap
    active = [line for line in receivers if line.risk_fraction > 0]
    while remaining > 1e-9 and active:
        share = remaining / len(active)
        remaining = 0.0
        still = []
        for line in active:
            add = share / line.risk_fraction
            if line.single_stock:
                room = max(caps[line.asset_id] - weights[line.asset_id], 0.0)
                if add > room:
                    remaining += (add - room) * line.risk_fraction
                    add = room
                else:
                    still.append(line)
            else:
                still.append(line)
            if add > 0:
                weights[line.asset_id] += add
                if "补足权益至目标区间" not in reasons[line.asset_id]:
                    reasons[line.asset_id].append("补足权益至目标区间")
        active = still if remaining > 1e-9 else []


def _absorb_in_defensive(
    lines: list[AdjustmentLine],
    weights: dict[str, float],
    reasons: dict[str, list[str]],
    cash_floor: float,
    notes: list[str],
) -> None:
    released = 1.0 - sum(weights.values())
    defensive = [line for line in lines if not line.risk_line]
    if abs(released) < 1e-9:
        return
    cash_lines = [line for line in defensive if line.cash_like]
    if released > 0:
        cash_now = sum(weights[line.asset_id] for line in cash_lines)
        to_cash = min(max(cash_floor - cash_now, 0.0), released)
        if to_cash > 0 and cash_lines:
            _spread(to_cash, cash_lines, weights, reasons, "补足现金底线")
            released -= to_cash
        # Released money goes to bonds and cash only; gold and commodities can swing as much as equity.
        # Bond funds named as candidates take all of it, in equal parts.
        named_bonds = [line for line in defensive if line.candidate and line.main_class == ASSET_CLASS_BOND]
        pool = [
            line
            for line in defensive
            if weights[line.asset_id] > 0 and (line.cash_like or line.main_class == ASSET_CLASS_BOND)
        ] or cash_lines
        if released > 1e-9 and named_bonds:
            for line in named_bonds:
                weights[line.asset_id] += released / len(named_bonds)
                reasons[line.asset_id].append("承接减持释放的资金")
        elif released > 1e-9 and pool:
            _spread(released, pool, weights, reasons, "承接减持释放的资金")
            if not any(line.main_class == ASSET_CLASS_BOND for line in pool):
                notes.append("组合没有债券类持仓，减持释放的资金计入现金类；需要债券时把债券基金作为候选传入")
        elif released > 1e-9:
            notes.append(f"减持释放的 {released:.1%} 没有现金或债券类持仓承接，计为现金")
        return
    need = -released
    for group, label in (
        (cash_lines, "用现金买入"),
        ([line for line in defensive if not line.cash_like], "卖出防御类资产补足买入资金"),
    ):
        available = sum(weights[line.asset_id] for line in group)
        if group == cash_lines:
            available = max(available - cash_floor, 0.0)
        take = min(available, need)
        if take > 0:
            _spread(-take, group, weights, reasons, label)
            need -= take
    if need > 1e-9:
        notes.append(f"现有防御类资产不足以支付买入，缺口约占组合 {need:.1%}，需要新增资金或降低目标")
        for line in lines:
            if line.risk_line and weights[line.asset_id] > line.current_weight:
                weights[line.asset_id] -= need * (weights[line.asset_id] - line.current_weight) / max(
                    sum(
                        weights[item.asset_id] - item.current_weight
                        for item in lines
                        if item.risk_line and weights[item.asset_id] > item.current_weight
                    ),
                    1e-12,
                )


def _substitute(
    candidates: list[AdjustmentLine],
    lines: list[AdjustmentLine],
    weights: dict[str, float],
    reasons: dict[str, list[str]],
    caps: dict[str, float],
    target_equity: float,
    industry_cap: float,
) -> None:
    """Top each named candidate up to one equal equity slot, taken pro rata from the existing equity lines."""

    candidates = [line for line in candidates if line.risk_fraction > 0]
    existing = [line for line in lines if line.risk_line and not line.candidate and weights[line.asset_id] > 0]
    if not candidates or not existing:
        return
    slot = target_equity / (len(existing) + len(candidates))
    adds = {}
    for line in candidates:
        add = max(slot - weights[line.asset_id] * line.risk_fraction, 0.0) / line.risk_fraction
        if line.single_stock:
            add = min(add, max(caps[line.asset_id] - weights[line.asset_id], 0.0))
        for industry, share in line.industries.items():
            if share <= 0:
                continue
            others = sum(weights[item.asset_id] * item.industries.get(industry, 0.0) for item in lines)
            others += sum(adds.get(item.asset_id, 0.0) * item.industries.get(industry, 0.0) for item in candidates)
            add = min(add, max(industry_cap - others, 0.0) / share)
        if add > 1e-9:
            adds[line.asset_id] = add
    needed = sum(adds[key] * next(item for item in candidates if item.asset_id == key).risk_fraction for key in adds)
    available = sum(weights[line.asset_id] * line.risk_fraction for line in existing)
    if needed <= 1e-9 or available <= 0:
        return
    scale = min(1.0, available / needed)
    for line in candidates:
        if line.asset_id in adds:
            weights[line.asset_id] += adds[line.asset_id] * scale
            reasons[line.asset_id].append("点名候选：按多一个等权席位，从现有权益持仓等比例腾出额度")
    for line in existing:
        share = weights[line.asset_id] * line.risk_fraction / available
        weights[line.asset_id] -= needed * scale * share / line.risk_fraction
        reasons[line.asset_id].append("为点名候选腾出权益额度")


def _ensure_cash_floor(
    lines: list[AdjustmentLine],
    weights: dict[str, float],
    reasons: dict[str, list[str]],
    cash_floor: float,
    notes: list[str],
) -> None:
    """Raise cash-like lines to the floor, selling other defensive lines first, then equity lines pro rata."""

    cash_lines = [line for line in lines if line.cash_like]
    shortfall = cash_floor - sum(weights[line.asset_id] for line in cash_lines)
    if shortfall <= 1e-9:
        return
    if not cash_lines:
        notes.append(f"没有现金类持仓，现金底线 {cash_floor:.0%} 无法在本方案中体现")
        return
    for group in (
        [line for line in lines if not line.risk_line and not line.cash_like],
        [line for line in lines if line.risk_line],
    ):
        available = sum(weights[line.asset_id] for line in group)
        take = min(available, shortfall)
        if take <= 1e-9:
            continue
        _spread(-take, group, weights, reasons, f"补足现金底线 {cash_floor:.0%}")
        _spread(take, cash_lines, weights, reasons, f"补足现金底线 {cash_floor:.0%}")
        shortfall -= take
        if shortfall <= 1e-9:
            return


def round_to_lots(
    targets: dict[str, dict[str, Any]],
    *,
    total: float,
    quantities: dict[str, float],
    prices: dict[str, float],
    lots: dict[str, int],
    sink: str | None,
) -> list[str]:
    """Move each exchange-traded line to a whole number of lots (a full exit may be an odd lot); the rest goes to ``sink``."""

    notes = []
    drift = 0.0
    for asset_id, lot in lots.items():
        row = targets.get(asset_id)
        price = prices.get(asset_id)
        if row is None or not price or abs(row["change"]) < 1e-12:
            continue
        held = quantities.get(asset_id, 0.0)
        wanted = row["target"] * total / price
        change = wanted - held
        if row["target"] <= 0:
            new_quantity = 0.0
        else:
            moved = math.floor(abs(change) / lot + 0.5) * lot
            new_quantity = max(held + math.copysign(moved, change), 0.0)
        new_target = new_quantity * price / total
        shift = new_target - row["target"]
        drift += shift
        traded = new_quantity - held
        if abs(traded) < 1e-9:
            row["reasons"] = [f"变化约 {abs(change):,.0f} 份，不足一手（{lot} 份），不交易"]
        elif abs(traded - change) >= 1:
            row["reasons"].append(f"按每手 {lot} 份取整：约 {abs(change):,.0f} 份 → {abs(traded):,.0f} 份")
        row["target"] = round(new_target, 4)
        row["change"] = round(new_target - row["current"], 4)
        row["low"] = round(max(row["low"] + shift, 0.0), 4) if new_target > 0 else 0.0
        row["high"] = round(max(row["high"] + shift, 0.0), 4) if new_target > 0 else 0.0
        row["quantity_change"] = round(traded, 2)
        row["lot"] = lot
        row["price"] = price
    if abs(drift) > 1e-9:
        if sink is not None and sink in targets:
            row = targets[sink]
            row["target"] = round(max(row["target"] - drift, 0.0), 4)
            row["change"] = round(row["target"] - row["current"], 4)
            row["low"] = round(max(row["target"] - RANGE_TOLERANCE, 0.0), 4)
            row["high"] = round(row["target"] + RANGE_TOLERANCE, 4)
            notes.append(f"按整手取整后的差额（约占组合 {abs(drift):.1%}）计入{row['label']}")
        else:
            notes.append(f"按整手取整后的差额约占组合 {abs(drift):.1%}，没有现金类持仓承接")
    return notes


def _spread(
    amount: float,
    lines: list[AdjustmentLine],
    weights: dict[str, float],
    reasons: dict[str, list[str]],
    reason: str,
) -> None:
    total = sum(weights[line.asset_id] for line in lines)
    for line in lines:
        share = weights[line.asset_id] / total if total > 0 else 1 / len(lines)
        weights[line.asset_id] = max(weights[line.asset_id] + amount * share, 0.0)
        if reason not in reasons[line.asset_id]:
            reasons[line.asset_id].append(reason)
