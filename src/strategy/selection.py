"""Stock selection rules on point-in-time fundamentals (stage 9 slices 3 and 4).

Candidates carry the latest annual report announced by the rebalance date and the unadjusted close
of that date. Design notes: docs/modules/3-分析工具/低频回测模块.md「时点股票池与选股回测」.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

FACTOR_SELECTION_DEFAULTS = {
    "min_roe": "10",
    "max_debt_ratio": "60",
    "min_profit_growth": "0",
    "top_n": "10",
    "rank_by": "earnings_yield",
}
# Ranking factor -> candidate field; yields must be positive to rank (loss makers and negative book are dropped).
_RANK_FIELDS = {
    "earnings_yield": "earnings_yield",
    "book_yield": "book_yield",
    "roe": "roe_percent",
    "profit_growth": "net_profit_yoy_percent",
}


def factor_selection_weights(
    candidates: Sequence[Mapping[str, Any]], parameters: Mapping[str, str]
) -> tuple[dict[str, float], dict[str, int]]:
    """Quality screen (annual ROE, debt ratio, profit growth), then the top N by the ranking factor, equal weights.

    Candidates: asset_id, roe_percent, debt_ratio_percent, net_profit_yoy_percent, earnings_yield, book_yield.
    Returns weights and how many candidates fell out at each step.
    """

    params = {**FACTOR_SELECTION_DEFAULTS, **parameters}
    min_roe, max_debt = float(params["min_roe"]), float(params["max_debt_ratio"])
    min_growth, top_n = float(params["min_profit_growth"]), int(params["top_n"])
    rank_field = _RANK_FIELDS[params["rank_by"]]
    counts = {"candidates": len(candidates), "no_data": 0, "failed_quality": 0, "no_earnings": 0, "selected": 0}
    passed = []
    for item in candidates:
        values = (item.get("roe_percent"), item.get("debt_ratio_percent"), item.get("net_profit_yoy_percent"))
        if any(value is None for value in values) or item.get(rank_field) is None:
            counts["no_data"] += 1
            continue
        roe, debt, growth = values
        if roe < min_roe or debt > max_debt or growth < min_growth:
            counts["failed_quality"] += 1
            continue
        if rank_field.endswith("_yield") and item[rank_field] <= 0:
            counts["no_earnings"] += 1
            continue
        passed.append(item)
    chosen = sorted(passed, key=lambda item: (-item[rank_field], item["asset_id"]))[:top_n]
    counts["selected"] = len(chosen)
    return ({item["asset_id"]: 1 / len(chosen) for item in chosen} if chosen else {}), counts
