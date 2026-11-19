"""Entry timing inside an execution window: how prices moved after past days in the same state.

Research decides what and how much to buy or sell; this only paces one trade within a bounded
window, and every tranche still executes by the window end. Design notes:
docs/modules/6-编排/研究编排模块.md「入场时机」.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any, Sequence

TREND_WINDOW = 60
YEAR = 250
CONFIRM_LOOKBACK = 20
CONFIRM_CAP = 250
EPISODE_GAP = 20
MIN_EPISODES = 5
MIN_DAYS = 40
EDGE = 0.01
SHARE_GAP = 0.08
LONG_HORIZONS = (63, 250)
MATCH_CASCADE = (("trend", "range_position", "year"), ("trend", "range_position"), ("trend",))


def state_at(levels: Sequence[float], i: int) -> dict[str, Any] | None:
    """Trend vs the 60-day mean, place in the 60-day range and the one-year return bucket."""

    if i < YEAR:
        return None
    window = levels[i - TREND_WINDOW + 1:i + 1]
    mean, low, high = sum(window) / TREND_WINDOW, min(window), max(window)
    year_return = levels[i] / levels[i - YEAR] - 1
    if levels[i] <= low * 1.01:
        place = "near_low"
    elif levels[i] >= high * 0.99:
        place = "near_high"
    else:
        place = "middle"
    if year_return < -0.2:
        bucket = "below_-20%"
    elif year_return < 0:
        bucket = "-20%_to_0"
    elif year_return < 0.2:
        bucket = "0_to_20%"
    else:
        bucket = "above_20%"
    return {
        "trend": "above_ma60" if levels[i] >= mean else "below_ma60",
        "range_position": place,
        "year": bucket,
        "year_return": round(year_return, 4),
        "ma60": round(mean, 4),
        "low60": round(low, 4),
        "high60": round(high, 4),
    }


def summarize(values: Sequence[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)

    def quantile(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(p * len(ordered)))]

    return {
        "n": len(ordered),
        "mean": round(statistics.mean(ordered), 4),
        "median": round(quantile(0.5), 4),
        "p10": round(quantile(0.1), 4),
        "p90": round(quantile(0.9), 4),
        "share_negative": round(sum(value < 0 for value in ordered) / len(ordered), 3),
    }


def _episodes(days: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    for day in days:
        if groups and day - groups[-1][-1] <= EPISODE_GAP:
            groups[-1].append(day)
        else:
            groups.append([day])
    return groups


def _confirmation(levels: Sequence[float], days: list[int]) -> dict[str, Any]:
    """Waiting for a close above the prior 20-day high and the 60-day mean: price paid vs buying at once."""

    paid, waited, never = [], [], 0
    last = len(levels) - 1
    for i in days:
        found = None
        for j in range(max(i + 1, TREND_WINDOW), min(last, i + CONFIRM_CAP) + 1):
            mean = sum(levels[j - TREND_WINDOW + 1:j + 1]) / TREND_WINDOW
            if levels[j] > max(levels[j - CONFIRM_LOOKBACK:j]) and levels[j] > mean:
                found = j
                break
        if found is not None:
            paid.append(levels[found] / levels[i] - 1)
            waited.append(found - i)
        elif i + CONFIRM_CAP <= last:
            never += 1
    return {
        "paid_vs_now": summarize(paid),
        "median_trading_days_waited": statistics.median(waited) if waited else None,
        "never_within_250_days": never,
    }


def _recommend(window: dict[str, Any], window_days: int, side: str, as_of_date: str) -> dict[str, Any]:
    state, baseline = window.get("state"), window.get("baseline")
    half = max(1, window_days // 2)
    if not state or not baseline:
        plan, reason = "split_even", "相似状态样本不足，不按状态倾斜，只做时间分散。"
    else:
        gap = state["median"] - baseline["median"]
        lower = state["share_negative"] - baseline["share_negative"]
        falls = gap <= -EDGE or lower >= SHARE_GAP
        rises = gap >= EDGE or lower <= -SHARE_GAP
        tilt = -1 if falls and not rises else 1 if rises and not falls else 0
        if side == "sell":
            tilt = -tilt
        detail = (f"窗口期收益中位数 {state['median']:+.2%}（全部交易日 {baseline['median']:+.2%}），"
                  f"窗口末价格更低的比例 {state['share_negative']:.0%}（全部交易日 {baseline['share_negative']:.0%}）")
        if tilt < 0:
            plan = "defer_part"
            reason = detail + ("；相似状态下窗口内价格偏弱，买入延后一半到窗口末。" if side == "buy"
                               else "；相似状态下窗口内价格偏强，卖出延后一半到窗口末。")
        elif tilt > 0:
            plan = "front_load"
            reason = detail + ("；相似状态下窗口内价格偏强，现在一次买入。" if side == "buy"
                               else "；相似状态下窗口内价格偏弱，现在一次卖出。")
        else:
            plan = "split_even"
            reason = detail + "；与全部交易日差别不大，只做时间分散。"
    tranches = {
        "front_load": [{"share": 1.0, "after_trading_days": 0}],
        "split_even": [{"share": 0.5, "after_trading_days": 0}, {"share": 0.5, "after_trading_days": half}],
        "defer_part": [{"share": 0.5, "after_trading_days": 0}, {"share": 0.5, "after_trading_days": window_days}],
    }[plan]
    start = date.fromisoformat(as_of_date)
    for tranche in tranches:
        tranche["about"] = (start + timedelta(days=round(tranche["after_trading_days"] * 7 / 5))).isoformat()
    return {"plan": plan, "tranches": tranches, "reason": reason,
            "deadline_after_trading_days": window_days}


def analyse(
    rows: Sequence[dict[str, Any]],
    *,
    as_of_date: str,
    window_days: int = 21,
    side: str = "buy",
) -> dict[str, Any]:
    """rows: dated total-return levels up to as_of_date, oldest first."""

    if side not in ("buy", "sell"):
        raise ValueError("side 只能是 buy 或 sell")
    if window_days < 2:
        raise ValueError("window_days 至少 2 个交易日")
    usable = [row for row in rows if row["date"] <= as_of_date and row.get("total_return_level")]
    levels = [float(row["total_return_level"]) for row in usable]
    last = len(levels) - 1
    if last < YEAR + window_days:
        return {"as_of_date": as_of_date, "error": "insufficient_history", "bars": len(levels),
                "recommendation": _recommend({}, window_days, side, as_of_date)}
    states = [state_at(levels, i) for i in range(len(levels))]
    today = states[last]
    matched, days = None, []
    for keys in MATCH_CASCADE:
        candidates = [i for i in range(YEAR, last - window_days + 1)
                      if all(states[i][key] == today[key] for key in keys)]
        if len(candidates) >= MIN_DAYS and len(_episodes(candidates)) >= MIN_EPISODES:
            matched, days = keys, candidates
            break
    every_day = range(YEAR, last + 1)
    forward: dict[str, Any] = {}
    for horizon in (window_days, *LONG_HORIZONS):
        forward[str(horizon)] = {
            "state": summarize([levels[i + horizon] / levels[i] - 1 for i in days if i + horizon <= last]),
            "baseline": summarize([levels[i + horizon] / levels[i] - 1 for i in every_day if i + horizon <= last]),
        }
    episodes = _episodes(days)
    firsts = [group[0] for group in episodes]
    return {
        "as_of_date": as_of_date,
        "last": {"date": usable[last]["date"], "close": usable[last].get("close"), "level": levels[last]},
        "history": {"start": usable[0]["date"], "bars": len(levels)},
        "side": side,
        "window_days": window_days,
        "state": today,
        "matched_on": list(matched) if matched else [],
        "samples": {"days": len(days), "episodes": len(episodes),
                    "episodes_list": [{"start": usable[group[0]]["date"], "end": usable[group[-1]]["date"]}
                                      for group in episodes]},
        "forward": forward,
        "episode_first": {str(horizon): summarize([levels[i + horizon] / levels[i] - 1 for i in firsts if i + horizon <= last])
                          for horizon in (window_days, YEAR)},
        "confirmation_entry": _confirmation(levels, days) if days else None,
        "recommendation": _recommend(forward[str(window_days)] if matched else {}, window_days, side, as_of_date),
        "notes": [
            "状态：总回报指数相对 60 日均值、在 60 日区间的位置（距最低或最高 1% 以内）与近 250 日收益分档；"
            "样本不足时依次放宽为前两项、只看趋势。",
            "相似状态的交易日相互重叠，episodes 才是近似独立的段数；不计交易费用，过去分布不保证未来。",
            "只调整已定交易在窗口内的节奏：所有批次在窗口末前完成，不改变研究给出的目标仓位，也不替代退出线。",
            "confirmation_entry 是“等收盘高于前 20 日最高且站上 60 日均值再买”相对立即买入的价格差，正值表示等确认更贵。",
        ],
    }


class EntryTimingService:
    def __init__(self, *, price_history: Any) -> None:
        self.price_history = price_history

    def run(
        self,
        symbol: str,
        *,
        as_of_date: str,
        market: str | None = None,
        asset_type: str | None = None,
        window_days: int = 21,
        side: str = "buy",
        years: int = 15,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        asset = self.price_history.resolve(symbol, market=market, asset_type=asset_type)
        start = date.fromisoformat(as_of_date).replace(day=1)
        start = start.replace(year=start.year - years).isoformat()
        history = self.price_history.history(asset, start_date=start, end_date=as_of_date, rows=True,
                                             allow_remote=allow_remote)
        payload = analyse(history.get("rows") or [], as_of_date=as_of_date, window_days=window_days, side=side)
        payload["asset"] = history["asset"]
        payload["data"] = {key: history.get(key) for key in ("sources", "stale", "source_conflict_count", "error")}
        return payload
