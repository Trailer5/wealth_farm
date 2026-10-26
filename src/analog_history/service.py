"""Analog history: what followed when a market metric sat in a similar percentile band."""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from functools import partial
from statistics import mean, median
from typing import Any

from src.data_access import DATASET_INDEX_DAILY_BARS, AssetInput, DataAccessRequest
from src.data_src.fan_out import fan_out
from src.macro_data import MacroDataService
from src.market_analysis import value_on_or_before

Points = list[tuple[str, float]]

HISTORY_START = "2005-01-01"
_INDEX_SYMBOL = "000300"
_DEFAULT_HORIZONS = (20, 60, 120, 250)
_DEFAULT_HALF_BAND = 0.1
_MIN_INDEPENDENT_SAMPLES = 8
_MARKET_DEFAULTS = ("csi300_pe_ttm", "equity_bond_spread", "csi300_return_60d")


@dataclass(frozen=True)
class AnalogMetric:
    metric_id: str
    name: str
    frequency: str
    description: str
    # Trailing observations needed before a point-in-time percentile is meaningful.
    min_window: int
    window_years: int = 10


ANALOG_METRICS: tuple[AnalogMetric, ...] = (
    AnalogMetric("csi300_pe_ttm", "沪深300 滚动市盈率", "monthly", "乐咕乐股月末值", 36),
    AnalogMetric("csi300_pb", "沪深300 市净率", "monthly", "乐咕乐股月末值", 36),
    AnalogMetric(
        "equity_bond_spread",
        "股债利差（沪深300 盈利收益率 − 10 年国债）",
        "monthly",
        "每个市盈率观测日对应当日或之前最近的 10 年国债收益率",
        36,
    ),
    AnalogMetric("cgb_10y", "10 年国债收益率", "daily", "东方财富与中债", 500),
    AnalogMetric("csi300_return_60d", "沪深300 近 60 个交易日涨跌幅", "daily", "价格指数", 500),
    AnalogMetric("csi300_volatility_20d", "沪深300 20 日年化已实现波动率", "daily", "价格指数", 500),
    AnalogMetric("margin_change_20d", "两融余额近 20 个交易日变化率", "daily", "沪深合计", 500),
)
_METRICS = {item.metric_id: item for item in ANALOG_METRICS}
# Series each metric needs besides the index closes used for forward returns.
_INPUTS = {
    "csi300_pe_ttm": ("csi300_pe_ttm",),
    "csi300_pb": ("csi300_pb",),
    "equity_bond_spread": ("csi300_pe_ttm", "cgb_10y"),
    "cgb_10y": ("cgb_10y",),
    "csi300_return_60d": (),
    "csi300_volatility_20d": (),
    "margin_change_20d": ("margin_balance_total",),
}


def analog_metric(metric_id: str) -> AnalogMetric:
    try:
        return _METRICS[metric_id]
    except KeyError:
        raise ValueError(
            f"Unknown analog metric {metric_id}; known: {', '.join(_METRICS)}"
        ) from None


class AnalogHistoryService:
    def __init__(self, *, macro_data: MacroDataService, data_access: Any) -> None:
        self.macro_data = macro_data
        self.data_access = data_access

    def query(
        self,
        metric_id: str,
        *,
        as_of_date: str,
        band: tuple[float, float] | None = None,
        horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Forward CSI 300 returns after past dates in the metric's percentile band."""

        metric = analog_metric(metric_id)
        date.fromisoformat(as_of_date)
        if not horizons or any(item <= 0 for item in horizons):
            raise ValueError("horizons must be positive trading-day counts")
        calls: dict[str, Callable[[], Any]] = {
            name: partial(
                self.macro_data.get,
                name,
                start_date=HISTORY_START,
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
            )
            for name in _INPUTS[metric_id]
        }
        calls["index"] = partial(self._index_closes, as_of_date, allow_remote)
        outcomes = fan_out(calls)
        errors = {
            name: f"{type(outcome).__name__}: {outcome}"
            for name, outcome in outcomes.items()
            if isinstance(outcome, Exception)
        }
        if errors:
            return {
                "status": "data_unavailable",
                "metric": _metric_dict(metric),
                "as_of_date": as_of_date,
                "errors": errors,
            }
        series = {
            name: [(item.period_end, item.value) for item in outcome.points]
            for name, outcome in outcomes.items()
            if name != "index"
        }
        closes: Points = outcomes["index"]
        values = _metric_series(metric_id, series, closes)
        return analog_distribution(
            metric,
            values,
            closes,
            as_of_date=as_of_date,
            band=band,
            horizons=horizons,
        )

    def market_defaults(self, as_of_date: str) -> dict[str, Any]:
        """Analogs used by the market brief: valuation, equity-bond spread and trend."""

        return {
            metric_id: self.query(metric_id, as_of_date=as_of_date, horizons=(60, 250))
            for metric_id in _MARKET_DEFAULTS
        }

    def _index_closes(self, as_of_date: str, allow_remote: bool) -> Points:
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(raw_symbol=_INDEX_SYMBOL, asset_type_hint="指数"),
                dataset=DATASET_INDEX_DAILY_BARS,
                start_date=HISTORY_START,
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=90,
            )
        )
        # A bar dated today may be an intraday snapshot rather than a close.
        last_day = min(as_of_date, (date.today() - timedelta(days=1)).isoformat())
        return sorted(
            (str(record["trade_date"]), float(record["close"]))
            for record in response.records
            if record.get("trade_date") and record.get("close")
            and str(record["trade_date"]) <= last_day
        )


def analog_distribution(
    metric: AnalogMetric,
    values: Points,
    closes: Points,
    *,
    as_of_date: str,
    band: tuple[float, float] | None,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    values = [(day, value) for day, value in values if day <= as_of_date]
    percentiles = trailing_percentiles(values, metric.window_years, metric.min_window)
    if not percentiles:
        return {
            "status": "insufficient_history",
            "metric": _metric_dict(metric),
            "as_of_date": as_of_date,
            "note": f"需要至少 {metric.min_window} 个历史观测才能计算时点分位",
        }
    current_day, current_percentile = percentiles[-1]
    current_value = dict(values)[current_day]
    low, high = band or (
        max(0.0, current_percentile - _DEFAULT_HALF_BAND),
        min(1.0, current_percentile + _DEFAULT_HALF_BAND),
    )
    if not 0 <= low < high <= 1:
        raise ValueError("band must satisfy 0 <= low < high <= 1")
    trade_days = [day for day, _ in closes]
    in_band = [day for day, value in percentiles if low <= value <= high]
    all_days = [day for day, _ in percentiles]
    results = []
    for horizon in horizons:
        conditional = _forward_samples(in_band, closes, trade_days, horizon, as_of_date)
        baseline = _forward_samples(all_days, closes, trade_days, horizon, as_of_date)
        independent = _non_overlapping(conditional, horizon)
        sufficient = len(independent) >= _MIN_INDEPENDENT_SAMPLES
        results.append(
            {
                "horizon_trading_days": horizon,
                "status": "ok" if sufficient else "insufficient_samples",
                "conditional": {
                    "all_samples": _stats(conditional),
                    "non_overlapping": _stats(independent),
                    "non_overlapping_entry_dates": [item[0] for item in independent][-30:],
                },
                "baseline_all_dates": {
                    "all_samples": _stats(baseline),
                    "non_overlapping": _stats(_non_overlapping(baseline, horizon)),
                },
                "note": (
                    None
                    if sufficient
                    else f"不重叠样本少于 {_MIN_INDEPENDENT_SAMPLES} 个，只能作为参考，不能据此下结论"
                ),
            }
        )
    return {
        "status": "ok",
        "metric": _metric_dict(metric),
        "as_of_date": as_of_date,
        "current": {
            "date": current_day,
            "value": current_value,
            "percentile": round(current_percentile, 4),
        },
        "band": {"low": round(low, 4), "high": round(high, 4), "default": band is None},
        "history": {
            "first_percentile_date": percentiles[0][0],
            "observations_in_band": len(in_band),
            "observations_with_percentile": len(percentiles),
            "index_first_date": trade_days[0] if trade_days else None,
        },
        "horizons": results,
        "method": (
            "每个历史观测日只用当日及之前不超过 "
            f"{metric.window_years} 年的观测计算分位（时点一致）；分位落在区间内的观测日，"
            "从其后第一个交易日的沪深300收盘起算 N 个交易日涨跌幅（价格指数，不含分红）；"
            "前瞻窗口必须在截止日前结束。全部样本相互重叠，不重叠样本按入场日至少间隔 N 个交易日贪心选取，"
            "是有效样本数；baseline 为所有有分位的观测日，用于比较条件分布与无条件分布。"
        ),
    }


def trailing_percentiles(
    values: Points,
    window_years: int,
    min_window: int,
) -> Points:
    """Point-in-time percentile of each value within its trailing window."""

    output = []
    window: list[float] = []
    start = 0
    for index, (day, value) in enumerate(values):
        bisect.insort(window, value)
        cutoff = (date.fromisoformat(day) - timedelta(days=365 * window_years)).isoformat()
        while values[start][0] < cutoff:
            window.pop(bisect.bisect_left(window, values[start][1]))
            start += 1
        if len(window) >= min_window:
            output.append((day, bisect.bisect_right(window, value) / len(window)))
    return output


def _forward_samples(
    days: list[str],
    closes: Points,
    trade_days: list[str],
    horizon: int,
    as_of_date: str,
) -> list[tuple[str, int, float]]:
    samples = []
    for day in days:
        entry = bisect.bisect_right(trade_days, day)
        exit_index = entry + horizon
        if exit_index >= len(closes) or closes[exit_index][0] > as_of_date:
            continue
        samples.append(
            (closes[entry][0], entry, closes[exit_index][1] / closes[entry][1] - 1)
        )
    return samples


def _non_overlapping(
    samples: list[tuple[str, int, float]],
    horizon: int,
) -> list[tuple[str, int, float]]:
    chosen = []
    last_entry = None
    for sample in sorted(samples, key=lambda item: item[1]):
        if last_entry is None or sample[1] >= last_entry + horizon:
            chosen.append(sample)
            last_entry = sample[1]
    return chosen


def _stats(samples: list[tuple[str, int, float]]) -> dict[str, Any]:
    returns = sorted(item[2] for item in samples)
    if not returns:
        return {"count": 0}
    return {
        "count": len(returns),
        "mean": round(mean(returns), 4),
        "median": round(median(returns), 4),
        "p10": round(_quantile(returns, 0.1), 4),
        "p25": round(_quantile(returns, 0.25), 4),
        "p75": round(_quantile(returns, 0.75), 4),
        "p90": round(_quantile(returns, 0.9), 4),
        "share_positive": round(sum(1 for item in returns if item > 0) / len(returns), 4),
        "first_entry": min(item[0] for item in samples),
        "last_entry": max(item[0] for item in samples),
    }


def _quantile(ordered: list[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _metric_series(metric_id: str, series: dict[str, Points], closes: Points) -> Points:
    if metric_id in {"csi300_pe_ttm", "csi300_pb", "cgb_10y"}:
        return series[metric_id]
    if metric_id == "equity_bond_spread":
        yields = series["cgb_10y"]
        output = []
        for day, pe in series["csi300_pe_ttm"]:
            bond = value_on_or_before(yields, day)
            if bond is not None and pe > 0:
                output.append((day, 100 / pe - bond))
        return output
    if metric_id == "csi300_return_60d":
        return [
            (closes[index][0], closes[index][1] / closes[index - 60][1] - 1)
            for index in range(60, len(closes))
        ]
    if metric_id == "csi300_volatility_20d":
        logs = [
            math.log(closes[index][1] / closes[index - 1][1])
            for index in range(1, len(closes))
        ]
        output = []
        for index in range(20, len(logs) + 1):
            window = logs[index - 20 : index]
            average = sum(window) / 20
            variance = sum((value - average) ** 2 for value in window) / 19
            output.append((closes[index][0], math.sqrt(variance * 252)))
        return output
    balance = series["margin_balance_total"]
    return [
        (balance[index][0], balance[index][1] / balance[index - 20][1] - 1)
        for index in range(20, len(balance))
    ]


def _metric_dict(metric: AnalogMetric) -> dict[str, Any]:
    return {
        "metric_id": metric.metric_id,
        "name": metric.name,
        "frequency": metric.frequency,
        "description": metric.description,
        "window_years": metric.window_years,
    }
