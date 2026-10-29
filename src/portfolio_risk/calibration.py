"""Stated preference against measured risk and recorded trading behaviour (stage 8 decision 7).

Nothing here changes a preference; the checks only say where the statement and the evidence disagree.
Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「交易流水与偏好校准」.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from .diagnostics import TRADING_DAYS
from .preference import HORIZON_LABELS, InvestmentPreference, PreferenceHorizon
from .trades import TradeRecord, TradeSide

# An asset this far below its one-year high counts as falling when sold.
SELL_DRAWDOWN = 0.10
# A buy this far below the average level of earlier logged buys counts as adding at a loss.
AVERAGING_DOWN = 0.10
SHORT_HOLDING_DAYS = 365
# Measured risk below this share of the stated limit means the stated tolerance is not being used.
UNDER_USE_RATIO = 0.5
MIN_VOLATILITY_DAYS = 60
TOLERANCE = 0.005


def max_drawdown(path: list[tuple[str, float]]) -> dict[str, Any] | None:
    """Largest peak-to-trough fall of a growth path, with its dates."""

    if len(path) < 2:
        return None
    peak_day, peak = path[0]
    worst = (0.0, path[0][0], path[0][0])
    for day, value in path:
        if value > peak:
            peak_day, peak = day, value
        fall = 1 - value / peak
        if fall > worst[0]:
            worst = (fall, peak_day, day)
    return {"drawdown": round(worst[0], 4), "peak": worst[1], "trough": worst[2]}


def drawdown_on(path: list[tuple[str, float]], day: str) -> float | None:
    """Fall from the running peak at the last point on or before ``day``."""

    peak = None
    current = None
    for point_day, value in path:
        if point_day > day:
            break
        peak = value if peak is None else max(peak, value)
        current = value
    if current is None or not peak:
        return None
    return round(1 - current / peak, 4)


def annual_volatility(path: list[tuple[str, float]]) -> float | None:
    returns = [current / previous - 1 for (_, previous), (_, current) in zip(path, path[1:]) if previous > 0]
    if len(returns) < MIN_VOLATILITY_DAYS:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return round(math.sqrt(variance * TRADING_DAYS), 4)


def realized_path(
    quantities: dict[str, float],
    trades: list[TradeRecord],
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    unit_priced: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Time-weighted growth of the recorded holdings since the first logged trade.

    Quantities are rebuilt backwards from today's holdings through the log, trades count at that
    day's close, and cash is left out. Returns None without trades; ``gaps`` lists log
    inconsistencies (negative quantities, missing prices) that stop the path.
    """

    if not trades:
        return None
    ordered = sorted(trades, key=lambda item: item.trade_date)
    start = ordered[0].trade_date
    held = dict(quantities)
    for trade in ordered:
        held[trade.asset_id] = held.get(trade.asset_id, 0.0) - trade.signed_quantity
    gaps = [
        f"{asset_id} 在 {start} 前的数量为 {value:g}（流水卖出多于当前持仓加流水买入）"
        for asset_id, value in held.items()
        if value < -1e-9
    ]
    calendar = sorted(
        {
            day
            for asset_id, series in levels.items()
            if asset_id in held
            for day in series
            if start <= day <= as_of_date
        }
    )
    if gaps or not calendar:
        return {"start": start, "end": as_of_date, "path": [], "gaps": gaps or ["流水期间没有价格"]}
    pending = list(ordered)
    last_price: dict[str, float] = {}
    for asset_id, series in levels.items():
        before = [day for day in series if day < calendar[0]]
        if before:
            last_price[asset_id] = series[max(before)]
    path: list[tuple[str, float]] = []
    previous_value = None
    previous_held: dict[str, float] = {}
    growth = 1.0
    for day in calendar:
        prices = dict(last_price)
        for asset_id, series in levels.items():
            if day in series:
                prices[asset_id] = series[day]
        for asset_id in unit_priced:
            prices[asset_id] = 1.0
        if previous_value:
            value_now = 0.0
            for asset_id, quantity in previous_held.items():
                if quantity <= 0:
                    continue
                if asset_id not in prices:
                    return {"start": start, "end": as_of_date, "path": path, "gaps": [f"{asset_id} 在 {day} 没有价格"]}
                value_now += quantity * prices[asset_id]
            growth *= value_now / previous_value
        while pending and pending[0].trade_date <= day:
            trade = pending.pop(0)
            held[trade.asset_id] = held.get(trade.asset_id, 0.0) + trade.signed_quantity
        value = sum(quantity * prices.get(asset_id, 0.0) for asset_id, quantity in held.items() if quantity > 0)
        if previous_value or value > 0:
            path.append((day, growth))
        previous_value = value if value > 0 else None
        previous_held = dict(held)
        last_price = prices
    return {"start": path[0][0] if path else start, "end": as_of_date, "path": path, "gaps": []}


def trade_behaviours(
    trades: list[TradeRecord],
    levels: dict[str, dict[str, float]],
    *,
    preference: InvestmentPreference,
    first_bought: dict[str, str],
    realized: list[tuple[str, float]] | None,
    labels: dict[str, str],
) -> list[dict[str, Any]]:
    """Rule-based hints on logged trades: selling into a fall, adding at a loss, short holding."""

    stated = float(preference.max_drawdown)
    hints = []
    buys: dict[str, list[tuple[str, float]]] = {}
    for trade in sorted(trades, key=lambda item: item.trade_date):
        series = levels.get(trade.asset_id, {})
        label = labels.get(trade.asset_id, trade.asset_id)
        level = _level_on(series, trade.trade_date)
        base = {"trade_id": trade.trade_id, "trade_date": trade.trade_date, "asset": label, "side": trade.side.value}
        if trade.side is TradeSide.BUY:
            earlier = buys.setdefault(trade.asset_id, [])
            if earlier and level is not None:
                reference = sum(quantity * value for value, quantity in earlier) / sum(quantity for _, quantity in earlier)
                change = level / reference - 1
                if change <= -AVERAGING_DOWN:
                    hints.append(
                        {
                            **base,
                            "hint_id": "averaging_down",
                            "severity": "caution",
                            "title": "亏损时加仓",
                            "evidence": f"买入时价格比流水中此前买入的均价低 {-change:.0%}",
                            "meaning": "加仓理由应来自论点仍成立且估值更有吸引力，而不是摊低成本；加仓后的权重仍受单只上限约束",
                        }
                    )
            if level is not None:
                earlier.append((level, float(trade.quantity)))
            continue
        high = _trailing_high(series, trade.trade_date)
        if level is not None and high:
            fall = 1 - level / high
            if fall >= SELL_DRAWDOWN:
                portfolio_fall = drawdown_on(realized, trade.trade_date) if realized else None
                early = portfolio_fall is not None and portfolio_fall < stated
                hints.append(
                    {
                        **base,
                        "hint_id": "sell_in_drawdown",
                        "severity": "caution" if early else "info",
                        "title": "下跌中卖出",
                        "evidence": f"卖出时距一年内高点 {fall:.0%}"
                        + (
                            f"；当时组合回撤 {portfolio_fall:.0%}，自述可接受 {stated:.0%}"
                            if portfolio_fall is not None
                            else "；流水不足以重建当时的组合回撤"
                        ),
                        "meaning": (
                            "组合回撤还没到自述上限就在下跌中卖出，实际能承受的回撤可能小于自述"
                            if early
                            else "下跌中卖出可能是按计划止损，也可能是恐慌；对照当时是否有已触发的退出条件"
                        ),
                    }
                )
        bought = [day for day, _ in _buy_days(trades, trade)] + (
            [first_bought[trade.asset_id]] if trade.asset_id in first_bought else []
        )
        bought = [day for day in bought if day <= trade.trade_date]
        if bought and preference.horizon is not PreferenceHorizon.UNDER_1Y:
            days = (date.fromisoformat(trade.trade_date) - date.fromisoformat(min(bought))).days
            if days < SHORT_HOLDING_DAYS:
                hints.append(
                    {
                        **base,
                        "hint_id": "short_holding",
                        "severity": "info",
                        "title": "持有期短于自述期限",
                        "evidence": f"自 {min(bought)} 首次买入 {days} 天后卖出，自述期限 {HORIZON_LABELS[preference.horizon]}",
                        "meaning": "频繁的短期卖出与长期限不一致；若是按退出条件执行则无妨，否则说明实际期限比自述短",
                    }
                )
    return hints


def calibration_checks(
    preference: InvestmentPreference,
    *,
    realized: dict[str, Any] | None,
    replay: dict[str, Any],
    worst_stress: dict[str, Any] | None,
    equity_share: float | None,
    behaviours: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare stated drawdown and volatility with measured ones and summarize behaviour hints."""

    stated_drawdown = float(preference.max_drawdown)
    stated_volatility = float(preference.target_volatility)
    realized_path_points = (realized or {}).get("path") or []
    realized_drawdown = max_drawdown(realized_path_points)
    drawdown_measures = [
        item
        for item in (
            realized_drawdown
            and {
                "source": "realized",
                "label": f"按流水重建的实际组合（{realized_path_points[0][0]} 至 {realized_path_points[-1][0]}）",
                "value": realized_drawdown["drawdown"],
                "detail": f"{realized_drawdown['peak']} 高点至 {realized_drawdown['trough']}",
            },
            replay.get("max_drawdown")
            and {
                "source": "replay",
                "label": f"当前持仓按固定权重回放（{replay['start']} 至 {replay['end']}）",
                "value": replay["max_drawdown"]["drawdown"],
                "detail": f"{replay['max_drawdown']['peak']} 高点至 {replay['max_drawdown']['trough']}",
            },
            worst_stress
            and {
                "source": "stress",
                "label": f"当前持仓在最差历史情景（{worst_stress['label']}）",
                "value": round(-worst_stress["portfolio_return"], 4),
                "detail": f"{worst_stress['start']} 至 {worst_stress['end']}",
            },
        )
        if item
    ]
    volatility_measures = [
        item
        for item in (
            annual_volatility(realized_path_points) is not None
            and {
                "source": "realized",
                "label": "按流水重建的实际组合",
                "value": annual_volatility(realized_path_points),
            },
            replay.get("volatility") is not None
            and {"source": "replay", "label": "当前持仓按固定权重回放", "value": replay["volatility"]},
        )
        if item
    ]
    checks = [
        _limit_check("drawdown", "可接受最大回撤", stated_drawdown, drawdown_measures, equity_share),
        _limit_check("volatility", "目标年化波动", stated_volatility, volatility_measures, equity_share),
    ]
    cautions = [item for item in behaviours if item["severity"] == "caution"]
    findings = [item["message"] for item in checks if item["status"] in ("exceeds", "under_used")]
    findings += [f"{item['trade_date']} {item['asset']}：{item['title']}（{item['evidence']}）" for item in cautions]
    consistent = not any(item["status"] == "exceeds" for item in checks) and not cautions
    return {
        "preference_id": preference.preference_id,
        "stated": {"max_drawdown": stated_drawdown, "target_volatility": stated_volatility},
        "checks": checks,
        "behaviours": behaviours,
        "consistent": consistent,
        "findings": findings,
        "has_trade_log": realized is not None,
        "note": "偏好不会被自动修改；用户认可调整后，用其原话经 record-preference 记录新版本",
    }


def _limit_check(
    check_id: str,
    label: str,
    stated: float,
    measures: list[dict[str, Any]],
    equity_share: float | None,
) -> dict[str, Any]:
    if not measures:
        return {"check_id": check_id, "label": label, "stated": stated, "measures": [], "status": "unknown", "message": f"{label}：没有可用的度量"}
    worst = max(measures, key=lambda item: item["value"])
    if worst["value"] > stated + TOLERANCE:
        status = "exceeds"
        message = f"{label}：{worst['label']}为 {worst['value']:.1%}，超过自述的 {stated:.1%}"
        if equity_share:
            fit = equity_share * stated / worst["value"]
            message += (
                f"。两种处理：确认能接受更大的{label[-2:]}（需用户原话，记录新偏好版本）；"
                f"或把权益比例从 {equity_share:.0%} 降到约 {fit:.0%}（按风险与权益比例同比例缩放的粗略估计，"
                "具体目标以配置建议为准）"
            )
    elif worst["value"] < stated * UNDER_USE_RATIO:
        status = "under_used"
        message = f"{label}：各项度量最高 {worst['value']:.1%}，不到自述 {stated:.1%} 的一半，自述的承受力没有被用上"
    else:
        status = "ok"
        message = f"{label}：度量最高 {worst['value']:.1%}，在自述的 {stated:.1%} 以内"
    return {
        "check_id": check_id,
        "label": label,
        "stated": stated,
        "measures": measures,
        "worst_source": worst["source"],
        "status": status,
        "message": message,
    }


def _level_on(series: dict[str, float], day: str) -> float | None:
    before = [item for item in series if item <= day]
    return series[max(before)] if before else None


def _trailing_high(series: dict[str, float], day: str) -> float | None:
    start = (date.fromisoformat(day) - timedelta(days=365)).isoformat()
    window = [value for item, value in series.items() if start < item <= day]
    return max(window) if window else None


def _buy_days(trades: list[TradeRecord], sell: TradeRecord) -> list[tuple[str, float]]:
    return [
        (item.trade_date, float(item.quantity))
        for item in trades
        if item.asset_id == sell.asset_id and item.side is TradeSide.BUY and item.trade_date <= sell.trade_date
    ]
