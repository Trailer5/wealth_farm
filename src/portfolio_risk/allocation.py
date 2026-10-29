"""Two-layer allocation: equity range from the risk budget, then risk-balanced reference weights.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「配置与风险预算」;
sources: docs/evidence/strategy_sources/allocation_risk_budget.md.
"""

from __future__ import annotations

import math
from typing import Any

from src.research_contracts import (
    ASSET_CLASS_CASH,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_LABELS,
    ASSET_CLASS_OVERSEAS,
)

from .diagnostics import STRESS_SCENARIOS, StressScenario, _window_return, covariance_matrix
from .preference import InvestmentPreference

GRID_STEP = 0.005
ERC_ITERATIONS = 2000
ERC_TOLERANCE = 1e-9
RISK_CLASSES = frozenset({ASSET_CLASS_EQUITY, ASSET_CLASS_OVERSEAS})


def class_risk(
    proxies: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    scenarios: tuple[StressScenario, ...] = STRESS_SCENARIOS,
) -> dict[str, Any]:
    """Covariance of the class proxies and each proxy's return in every historical scenario."""

    ids, cov = covariance_matrix(proxies, as_of_date=as_of_date)
    for name in proxies:
        if name not in ids and name == ASSET_CLASS_CASH:
            ids.append(name)
    for name in ids:
        cov.setdefault(name, {})
        for other in ids:
            cov[name].setdefault(other, 0.0)
    return {
        "classes": ids,
        "cov": cov,
        "scenario_returns": {
            scenario.scenario_id: {
                name: (0.0 if name == ASSET_CLASS_CASH else _window_return(proxies[name], scenario))
                for name in ids
            }
            for scenario in scenarios
        },
        "scenario_labels": {scenario.scenario_id: scenario.label for scenario in scenarios},
    }


def equity_range(
    preference: InvestmentPreference,
    risk: dict[str, Any],
    *,
    defensive_mix: dict[str, float],
    risky_class: str = ASSET_CLASS_EQUITY,
    cash_floor: float | None = None,
) -> dict[str, Any]:
    """Largest equity share each limit allows, intersected with the preferred range."""

    mix = {key: value for key, value in defensive_mix.items() if key in risk["classes"] and value > 0}
    total = sum(mix.values())
    mix = {key: value / total for key, value in mix.items()} if total > 0 else {ASSET_CLASS_CASH: 1.0}
    cov = risk["cov"]

    def volatility(share: float) -> float:
        weights = {name: (1 - share) * value for name, value in mix.items()}
        weights[risky_class] = weights.get(risky_class, 0.0) + share
        variance = sum(weights[a] * weights[b] * cov[a][b] for a in weights for b in weights)
        return math.sqrt(max(variance, 0.0))

    def worst_loss(share: float) -> tuple[float, str]:
        worst = (0.0, "")
        for scenario_id, returns in risk["scenario_returns"].items():
            if returns.get(risky_class) is None or any(returns.get(name) is None for name in mix):
                continue
            value = share * returns[risky_class] + (1 - share) * sum(
                weight * returns[name] for name, weight in mix.items()
            )
            if -value > worst[0]:
                worst = (-value, risk["scenario_labels"][scenario_id])
        return worst

    grid = [round(index * GRID_STEP, 6) for index in range(int(1 / GRID_STEP) + 1)]
    target = float(preference.target_volatility)
    max_dd = float(preference.max_drawdown)
    by_volatility = max((share for share in grid if volatility(share) <= target), default=0.0)
    by_drawdown = max((share for share in grid if worst_loss(share)[0] <= max_dd), default=0.0)
    limits = {
        "preference": float(preference.equity_weight_high),
        "volatility": by_volatility,
        "drawdown": by_drawdown,
        "cash_floor": 1 - (float(preference.min_cash_weight) if cash_floor is None else cash_floor),
    }
    high = min(limits.values())
    low = float(preference.equity_weight_low)
    binding = [name for name, value in limits.items() if abs(value - high) < 1e-9]
    conflict = high < low
    middle = high if conflict else (low + high) / 2
    return {
        "low": round(min(low, high), 4),
        "high": round(high, 4),
        "middle": round(middle, 4),
        "limits": {key: round(value, 4) for key, value in limits.items()},
        "binding": binding,
        "conflict": conflict,
        "defensive_mix": {key: round(value, 4) for key, value in mix.items()},
        "at_high": {
            "volatility": round(volatility(high), 4),
            "worst_loss": round(worst_loss(high)[0], 4),
            "worst_scenario": worst_loss(high)[1],
        },
        # What keeping the preferred lower bound would cost, for the user to weigh against the binding limit.
        "at_preference_low": {
            "equity": round(low, 4),
            "volatility": round(volatility(low), 4),
            "worst_loss": round(worst_loss(low)[0], 4),
            "worst_scenario": worst_loss(low)[1],
        },
        "risk_budget": _class_risk_shares(middle, mix, cov, risky_class),
    }


def erc_weights(ids: list[str], cov: dict[str, dict[str, float]]) -> dict[str, float]:
    """Equal risk contribution weights by multiplicative updates."""

    usable = [item for item in ids if cov[item][item] > 0]
    if not usable:
        return {}
    weights = {item: 1 / math.sqrt(cov[item][item]) for item in usable}
    scale = sum(weights.values())
    weights = {key: value / scale for key, value in weights.items()}
    for _ in range(ERC_ITERATIONS):
        marginal = {a: sum(cov[a][b] * weights[b] for b in usable) for a in usable}
        contributions = {a: weights[a] * marginal[a] for a in usable}
        variance = sum(contributions.values())
        target = variance / len(usable)
        updated = {a: weights[a] * math.sqrt(target / contributions[a]) for a in usable}
        scale = sum(updated.values())
        updated = {key: value / scale for key, value in updated.items()}
        if max(abs(updated[a] - weights[a]) for a in usable) < ERC_TOLERANCE:
            return updated
        weights = updated
    return weights


def within_risk_assets(
    positions: list[dict[str, Any]],
    cov: dict[str, dict[str, float]],
    *,
    target_share: float,
    preference: InvestmentPreference,
) -> list[dict[str, Any]]:
    """Current and risk-balanced split of the risk assets, scaled to the target share."""

    ids = [item["asset_id"] for item in positions if item["asset_id"] in cov]
    current_total = sum(item["risk_weight"] for item in positions)
    erc = erc_weights(ids, cov)
    cap = float(preference.max_single_weight)
    rows = []
    for item in positions:
        current = item["risk_weight"] / current_total if current_total > 0 else 0.0
        balanced = erc.get(item["asset_id"])
        row = {
            "asset_id": item["asset_id"],
            "label": item["label"],
            "single_stock": item["single_stock"],
            "current_share": round(current, 4),
            "balanced_share": None if balanced is None else round(balanced, 4),
            "at_target_current": round(current * target_share, 4),
            "at_target_balanced": None if balanced is None else round(balanced * target_share, 4),
        }
        row["over_single_cap"] = bool(
            item["single_stock"] and current * target_share > cap
        )
        row["balanced_over_single_cap"] = bool(
            item["single_stock"] and balanced is not None and balanced * target_share > cap
        )
        rows.append(row)
    return rows


def _class_risk_shares(
    share: float,
    mix: dict[str, float],
    cov: dict[str, dict[str, float]],
    risky_class: str,
) -> list[dict[str, Any]]:
    weights = {name: (1 - share) * value for name, value in mix.items()}
    weights[risky_class] = weights.get(risky_class, 0.0) + share
    variance = sum(weights[a] * weights[b] * cov[a][b] for a in weights for b in weights)
    if variance <= 0:
        return []
    return [
        {
            "class": name,
            "label": ASSET_CLASS_LABELS.get(name, name),
            "weight": round(weight, 4),
            "risk_share": round(weight * sum(cov[name][other] * weights[other] for other in weights) / variance, 4) + 0.0,
        }
        for name, weight in sorted(weights.items(), key=lambda pair: -pair[1])
    ]
