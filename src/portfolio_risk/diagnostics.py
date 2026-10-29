"""Portfolio diagnosis: exposures, overlap, correlation, risk contribution and historical stress.

Design notes: docs/modules/4-个人数据/组合与风险管理模块.md「组合诊断」.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src.research_contracts import (
    ASSET_CLASS_CASH,
    ASSET_CLASS_EQUITY,
    ASSET_CLASS_LABELS,
    ASSET_CLASS_OVERSEAS,
)

from .preference import FIELD_LABELS, InvestmentPreference

TRADING_DAYS = 252
RISK_WINDOW_DAYS = 3 * 365
COMPARISON_WINDOW_DAYS = 365
MIN_OBSERVATIONS = 120
HIGH_CORRELATION = 0.8
# A series must have a level this close before a window start to replay it.
STRESS_START_TOLERANCE_DAYS = 10
UNCOVERED_INDUSTRY = "未穿透"
# Weights are rounded to 4 places; a limit is only exceeded beyond rounding.
LIMIT_TOLERANCE = 0.0005


@dataclass(frozen=True)
class StressScenario:
    scenario_id: str
    label: str
    start: str
    end: str


# CSI 300 peak-to-trough windows of the main A-share drawdowns since 2015.
STRESS_SCENARIOS = (
    StressScenario("crash_2015", "2015 年 6–8 月股灾", "2015-06-08", "2015-08-26"),
    StressScenario("bear_2018", "2018 年熊市", "2018-01-24", "2019-01-03"),
    StressScenario("covid_2020", "2020 年 2–3 月疫情冲击", "2020-01-14", "2020-03-23"),
    StressScenario("decline_2022", "2022 年下跌", "2022-01-04", "2022-10-31"),
    StressScenario("smallcap_2024", "2024 年 1–2 月小微盘踩踏", "2024-01-02", "2024-02-05"),
)


@dataclass(frozen=True)
class DiagnosisPosition:
    """One holding with fractions of its own value by class, industry and stock."""

    asset_id: str
    label: str
    asset_type: str
    market_value: float
    asset_classes: dict[str, float]
    industries: dict[str, float] = field(default_factory=dict)
    stocks: dict[str, tuple[str, float]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    # CSRC industry shares of an actively managed fund, a cross-check for its uncovered part.
    cross_check: dict[str, float] = field(default_factory=dict)

    def main_class(self) -> str:
        return max(self.asset_classes, key=lambda key: self.asset_classes[key])


def weights_of(positions: list[DiagnosisPosition]) -> dict[str, float]:
    total = sum(item.market_value for item in positions)
    if total <= 0:
        raise ValueError("portfolio market value must be positive")
    return {item.asset_id: item.market_value / total for item in positions}


def exposure_summary(positions: list[DiagnosisPosition]) -> dict[str, Any]:
    weights = weights_of(positions)
    classes: dict[str, float] = {}
    industries: dict[str, float] = {}
    csrc: dict[str, float] = {}
    stocks: dict[str, dict[str, Any]] = {}
    for item in positions:
        weight = weights[item.asset_id]
        for key, share in item.cross_check.items():
            csrc[key] = csrc.get(key, 0.0) + weight * share
        for key, share in item.asset_classes.items():
            classes[key] = classes.get(key, 0.0) + weight * share
        for key, share in item.industries.items():
            industries[key] = industries.get(key, 0.0) + weight * share
        for key, (name, share) in item.stocks.items():
            entry = stocks.setdefault(key, {"key": key, "name": name, "weight": 0.0, "sources": []})
            entry["weight"] += weight * share
            entry["sources"].append({"label": item.label, "weight": round(weight * share, 4)})
    equity = classes.get(ASSET_CLASS_EQUITY, 0.0)
    overlaps = sorted(
        (item for item in stocks.values() if len(item["sources"]) > 1),
        key=lambda item: -item["weight"],
    )
    funds = [item for item in positions if len(item.stocks) > 1]
    shared = []
    for index, left in enumerate(funds):
        for right in funds[index + 1 :]:
            common = sorted(set(left.stocks) & set(right.stocks))
            if common:
                shared.append(
                    {
                        "pair": [left.label, right.label],
                        "common": [left.stocks[key][0] for key in common],
                    }
                )
    return {
        "positions": [
            {
                "asset_id": item.asset_id,
                "label": item.label,
                "asset_type": item.asset_type,
                "market_value": round(item.market_value, 2),
                "weight": round(weights[item.asset_id], 4),
                "main_class": ASSET_CLASS_LABELS.get(item.main_class(), item.main_class()),
                "notes": list(item.notes),
            }
            for item in sorted(positions, key=lambda value: -value.market_value)
        ],
        "total_market_value": round(sum(item.market_value for item in positions), 2),
        "max_weight": round(max(weights.values()), 4),
        "concentration_hhi": round(sum(value * value for value in weights.values()), 4),
        "asset_classes": [
            {"class": key, "label": ASSET_CLASS_LABELS.get(key, key), "weight": round(value, 4)}
            for key, value in sorted(classes.items(), key=lambda pair: -pair[1])
        ],
        "industries": [
            {
                "industry": key,
                "weight": round(value, 4),
                "share_of_equity": round(value / equity, 4) if equity > 0 else None,
            }
            for key, value in sorted(industries.items(), key=lambda pair: -pair[1])
        ],
        "uncovered_equity": round(industries.get(UNCOVERED_INDUSTRY, 0.0), 4),
        "csrc_industries": [
            {"industry": key, "weight": round(value, 4)}
            for key, value in sorted(csrc.items(), key=lambda pair: -pair[1])
            if value >= 0.0005
        ],
        "top_stocks": [
            {**item, "weight": round(item["weight"], 4)}
            for item in sorted(stocks.values(), key=lambda value: -value["weight"])[:10]
        ],
        "overlaps": [{**item, "weight": round(item["weight"], 4)} for item in overlaps],
        "shared_fund_holdings": shared,
    }


def risk_summary(
    weights: dict[str, float],
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    proxy_levels: dict[str, float] | None = None,
    proxy_eligible: frozenset[str] = frozenset(),
    zero_variance: frozenset[str] = frozenset(),
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Three-year daily covariance, Euler risk contributions and a one-year comparison."""

    labels = labels or {}
    main = _covariance_block(
        weights, levels, as_of_date, RISK_WINDOW_DAYS, proxy_levels, proxy_eligible, zero_variance
    )
    recent = _covariance_block(
        weights, levels, as_of_date, COMPARISON_WINDOW_DAYS, proxy_levels, proxy_eligible, zero_variance
    )
    ids = main["ids"]
    cov = main["cov"]
    variance = sum(weights[a] * weights[b] * cov[a][b] for a in ids for b in ids)
    contributions = []
    if variance > 0:
        for asset_id in ids:
            marginal = sum(weights[other] * cov[asset_id][other] for other in ids)
            contributions.append(
                {
                    "asset_id": asset_id,
                    "label": labels.get(asset_id, asset_id),
                    "weight": round(weights[asset_id], 4),
                    "volatility": round(math.sqrt(cov[asset_id][asset_id]), 4),
                    "risk_share": round(weights[asset_id] * marginal / variance, 4),
                }
            )
    correlations = []
    high = []
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            denominator = math.sqrt(cov[left][left] * cov[right][right])
            if denominator <= 0:
                continue
            value = cov[left][right] / denominator
            pair = {
                "pair": [labels.get(left, left), labels.get(right, right)],
                "correlation": round(value, 3),
            }
            correlations.append(pair)
            if value >= HIGH_CORRELATION:
                high.append(pair)
    recent_variance = sum(
        weights[a] * weights[b] * recent["cov"][a][b] for a in recent["ids"] for b in recent["ids"]
    )
    return {
        "window": {"start": main["start"], "end": as_of_date, "trading_days_per_year": TRADING_DAYS},
        "portfolio_volatility": round(math.sqrt(variance), 4) if variance > 0 else None,
        "portfolio_volatility_1y": round(math.sqrt(recent_variance), 4) if recent_variance > 0 else None,
        "risk_contributions": sorted(contributions, key=lambda item: -item["risk_share"]),
        "correlations": sorted(correlations, key=lambda item: -item["correlation"]),
        "high_correlations": high,
        "proxied": [labels.get(item, item) for item in main["proxied"]],
        "excluded": [
            {"label": labels.get(item, item), "weight": round(weights[item], 4)} for item in main["excluded"]
        ],
        "observations": {labels.get(key, key): value for key, value in main["observations"].items()},
    }


def stress_summary(
    weights: dict[str, float],
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    benchmark_levels: dict[str, float],
    labels: dict[str, str] | None = None,
    zero_return: frozenset[str] = frozenset(),
    scenarios: tuple[StressScenario, ...] = STRESS_SCENARIOS,
) -> list[dict[str, Any]]:
    """Replay each window with actual history, else beta to the benchmark over the last three years."""

    labels = labels or {}
    start = (date.fromisoformat(as_of_date) - timedelta(days=RISK_WINDOW_DAYS)).isoformat()
    benchmark_recent = _window(benchmark_levels, start, as_of_date)
    betas = {}
    for asset_id in weights:
        pair = _pair_covariance(_window(levels.get(asset_id, {}), start, as_of_date), benchmark_recent)
        bench_var = _pair_covariance(benchmark_recent, benchmark_recent)
        if pair is not None and bench_var:
            betas[asset_id] = pair / bench_var
    result = []
    for scenario in scenarios:
        benchmark_return = _window_return(benchmark_levels, scenario)
        rows = []
        portfolio = 0.0
        uncovered = 0.0
        for asset_id, weight in weights.items():
            actual = _window_return(levels.get(asset_id, {}), scenario)
            if actual is not None:
                value, method = actual, "history"
            elif asset_id in zero_return:
                value, method = 0.0, "cash"
            elif asset_id in betas and benchmark_return is not None:
                value, method = betas[asset_id] * benchmark_return, "beta_proxy"
            else:
                value, method = None, "unavailable"
            if value is None:
                uncovered += weight
            else:
                portfolio += weight * value
            rows.append(
                {
                    "label": labels.get(asset_id, asset_id),
                    "return": None if value is None else round(value, 4),
                    "method": method,
                    "beta": round(betas[asset_id], 2) if method == "beta_proxy" else None,
                }
            )
        result.append(
            {
                "scenario_id": scenario.scenario_id,
                "label": scenario.label,
                "start": scenario.start,
                "end": scenario.end,
                "benchmark_return": None if benchmark_return is None else round(benchmark_return, 4),
                "portfolio_return": round(portfolio, 4),
                "uncovered_weight": round(uncovered, 4),
                "positions": rows,
            }
        )
    return result


def preference_check(
    exposures: dict[str, Any],
    risk: dict[str, Any],
    stress: list[dict[str, Any]],
    preference: InvestmentPreference,
) -> list[dict[str, Any]]:
    """Each preference limit against the diagnosis; nothing here proposes a change."""

    classes = {item["class"]: item["weight"] for item in exposures["asset_classes"]}
    equity = risk_asset_share(classes)
    checks = [
        _check(
            "equity_weight",
            "权益比例（含跨境）",
            equity,
            low=float(preference.equity_weight_low),
            high=float(preference.equity_weight_high),
        ),
        _check("cash_weight", FIELD_LABELS["min_cash_weight"], classes.get(ASSET_CLASS_CASH, 0.0), low=float(preference.min_cash_weight)),
    ]
    stock_rows = [
        item for item in exposures["top_stocks"] if item["weight"] > float(preference.max_single_weight) + LIMIT_TOLERANCE
    ]
    checks.append(
        {
            "check_id": "single_stock",
            "label": FIELD_LABELS["max_single_weight"],
            "limit": float(preference.max_single_weight),
            "value": max((item["weight"] for item in exposures["top_stocks"]), default=0.0),
            "status": "above" if stock_rows else "ok",
            "detail": "、".join(f"{item['name']} {item['weight']:.1%}" for item in stock_rows),
        }
    )
    industry_rows = [
        item
        for item in exposures["industries"]
        if item["industry"] != UNCOVERED_INDUSTRY and item["weight"] > float(preference.max_industry_weight) + LIMIT_TOLERANCE
    ]
    checks.append(
        {
            "check_id": "single_industry",
            "label": FIELD_LABELS["max_industry_weight"],
            "limit": float(preference.max_industry_weight),
            "value": max(
                (item["weight"] for item in exposures["industries"] if item["industry"] != UNCOVERED_INDUSTRY),
                default=0.0,
            ),
            "status": "above" if industry_rows else "ok",
            "detail": "、".join(f"{item['industry']} {item['weight']:.1%}" for item in industry_rows),
        }
    )
    volatility = risk.get("portfolio_volatility")
    checks.append(
        _check("volatility", FIELD_LABELS["target_volatility"], volatility, high=float(preference.target_volatility))
        if volatility is not None
        else {"check_id": "volatility", "label": FIELD_LABELS["target_volatility"], "status": "unknown"}
    )
    covered = [item for item in stress if item["uncovered_weight"] < 0.05]
    worst = min(covered, key=lambda item: item["portfolio_return"], default=None)
    checks.append(
        {
            "check_id": "stress_loss",
            "label": FIELD_LABELS["max_drawdown"],
            "limit": float(preference.max_drawdown),
            "value": None if worst is None else -worst["portfolio_return"],
            "status": (
                "unknown"
                if worst is None
                else "above"
                if -worst["portfolio_return"] > float(preference.max_drawdown)
                else "ok"
            ),
            "detail": "" if worst is None else f"最差历史情景：{worst['label']}",
        }
    )
    return checks


def rolling_return_band(
    weights: dict[str, float],
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    benchmark_levels: dict[str, float],
    zero_return: frozenset[str] = frozenset(),
    horizon_days: int = TRADING_DAYS,
    coverage: float = 0.8,
) -> dict[str, Any] | None:
    """Historical range of the fixed-weight portfolio's one-year return and how often it held.

    Days an asset has no price use beta times the benchmark return; the probability band is a
    Wilson interval on the number of non-overlapping years, not on the overlapping windows.
    """

    path = fixed_weight_path(
        weights, levels, as_of_date=as_of_date, benchmark_levels=benchmark_levels, zero_return=zero_return
    )
    if len(path) <= horizon_days * 2:
        return None
    growth = [value for _, value in path]
    yearly = sorted(growth[index + horizon_days] / growth[index] - 1 for index in range(len(growth) - horizon_days))
    tail = (1 - coverage) / 2
    low = yearly[int(tail * (len(yearly) - 1))]
    high = yearly[int((1 - tail) * (len(yearly) - 1))]
    independent = (len(path) - 1) // horizon_days
    probability_low, probability_high = _wilson(coverage, independent)
    return {
        "low": round(low, 4),
        "high": round(high, 4),
        "coverage": coverage,
        "probability_low": round(probability_low, 2),
        "probability_high": round(probability_high, 2),
        "windows": len(yearly),
        "independent_years": independent,
        "start": path[0][0],
        "end": path[-1][0],
        "yearly": yearly,
    }


def fixed_weight_path(
    weights: dict[str, float],
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    benchmark_levels: dict[str, float],
    zero_return: frozenset[str] = frozenset(),
    start: str | None = None,
) -> list[tuple[str, float]]:
    """Daily growth of the fixed-weight portfolio on the benchmark's trading days, rebalanced daily.

    Days an asset has no price use its three-year beta times the benchmark return.
    """

    days = sorted(day for day in benchmark_levels if day <= as_of_date and (start is None or day >= start))
    if not days:
        return []
    window_start = (date.fromisoformat(as_of_date) - timedelta(days=RISK_WINDOW_DAYS)).isoformat()
    recent = _window(benchmark_levels, window_start, as_of_date)
    bench_var = _pair_covariance(recent, recent) or 0.0
    betas = {}
    for asset_id in weights:
        pair = _pair_covariance(_window(levels.get(asset_id, {}), window_start, as_of_date), recent)
        betas[asset_id] = (pair / bench_var) if pair is not None and bench_var else (0.0 if asset_id in zero_return else 1.0)
    path = [(days[0], 1.0)]
    for previous, current in zip(days, days[1:]):
        bench = benchmark_levels[current] / benchmark_levels[previous] - 1
        value = 0.0
        for asset_id, weight in weights.items():
            series = levels.get(asset_id, {})
            if asset_id in zero_return:
                continue
            if previous in series and current in series:
                value += weight * (series[current] / series[previous] - 1)
            else:
                value += weight * betas[asset_id] * bench
        path.append((current, path[-1][1] * (1 + value)))
    return path


def _wilson(share: float, count: int, z: float = 1.645) -> tuple[float, float]:
    if count <= 0:
        return 0.0, 1.0
    denominator = 1 + z * z / count
    centre = (share + z * z / (2 * count)) / denominator
    spread = z * math.sqrt(share * (1 - share) / count + z * z / (4 * count * count)) / denominator
    return max(centre - spread, 0.0), min(centre + spread, 1.0)


def risk_asset_share(classes: dict[str, float]) -> float:
    """Equity share as the preference counts it: domestic equity plus cross-border funds."""

    return classes.get(ASSET_CLASS_EQUITY, 0.0) + classes.get(ASSET_CLASS_OVERSEAS, 0.0)


def covariance_matrix(
    levels: dict[str, dict[str, float]],
    *,
    as_of_date: str,
    window_days: int = RISK_WINDOW_DAYS,
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Annualized pairwise covariance of the series with enough history in the window."""

    block = _covariance_block(
        {key: 1.0 for key in levels}, levels, as_of_date, window_days, None, frozenset(), frozenset()
    )
    return block["ids"], block["cov"]


def _check(
    check_id: str,
    label: str,
    value: float,
    *,
    low: float | None = None,
    high: float | None = None,
) -> dict[str, Any]:
    if low is not None and value < low:
        status = "below"
    elif high is not None and value > high:
        status = "above"
    else:
        status = "ok"
    return {
        "check_id": check_id,
        "label": label,
        "value": round(value, 4),
        "low": low,
        "high": high,
        "status": status,
    }


def _covariance_block(
    weights: dict[str, float],
    levels: dict[str, dict[str, float]],
    as_of_date: str,
    window_days: int,
    proxy_levels: dict[str, float] | None,
    proxy_eligible: frozenset[str],
    zero_variance: frozenset[str],
) -> dict[str, Any]:
    start = (date.fromisoformat(as_of_date) - timedelta(days=window_days)).isoformat()
    series: dict[str, dict[str, float]] = {}
    proxied, excluded, observations = [], [], {}
    for asset_id in weights:
        window = _window(levels.get(asset_id, {}), start, as_of_date)
        observations[asset_id] = max(len(window) - 1, 0)
        if len(window) > MIN_OBSERVATIONS:
            series[asset_id] = window
        elif asset_id in zero_variance:
            series[asset_id] = {}
        elif proxy_levels and asset_id in proxy_eligible:
            series[asset_id] = _window(proxy_levels, start, as_of_date)
            proxied.append(asset_id)
        else:
            excluded.append(asset_id)
    ids = list(series)
    cov: dict[str, dict[str, float]] = {item: {} for item in ids}
    for index, left in enumerate(ids):
        for right in ids[index:]:
            value = _pair_covariance(series[left], series[right]) or 0.0
            cov[left][right] = cov[right][left] = value * TRADING_DAYS
    return {
        "start": start,
        "ids": ids,
        "cov": cov,
        "proxied": proxied,
        "excluded": excluded,
        "observations": observations,
    }


def _window(levels: dict[str, float], start: str, end: str) -> dict[str, float]:
    return {day: value for day, value in levels.items() if start <= day <= end}


def _pair_covariance(left: dict[str, float], right: dict[str, float]) -> float | None:
    days = sorted(set(left) & set(right))
    if len(days) <= MIN_OBSERVATIONS:
        return None
    a = [left[current] / left[previous] - 1 for previous, current in zip(days, days[1:])]
    b = [right[current] / right[previous] - 1 for previous, current in zip(days, days[1:])]
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (len(a) - 1)


def _window_return(levels: dict[str, float], scenario: StressScenario) -> float | None:
    tolerance = (date.fromisoformat(scenario.start) - timedelta(days=STRESS_START_TOLERANCE_DAYS)).isoformat()
    end_tolerance = (date.fromisoformat(scenario.end) - timedelta(days=STRESS_START_TOLERANCE_DAYS)).isoformat()
    start = [day for day in levels if tolerance <= day <= scenario.start]
    end = [day for day in levels if end_tolerance <= day <= scenario.end]
    if not start or not end:
        return None
    return levels[max(end)] / levels[max(start)] - 1
