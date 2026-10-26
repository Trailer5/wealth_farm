"""A-share market archive: a structured state assessment from market-wide series."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from typing import Any

from src.data_access import (
    DATASET_INDEX_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
)
from src.data_src.fan_out import fan_out
from src.macro_data import MacroDataService, MacroSeriesResult
from src.research_contracts import (
    AssessmentDimension,
    Confidence,
    StateAssessment,
    SubjectType,
)

MARKET_SUBJECT_ID = "market:cn_a"
MARKET_ARCHIVE_VERSION = "market_archive_v1"
_LONG_WINDOW_YEARS = 10
_BREADTH_WINDOW_YEARS = 3
_STALE_DAYS = 10
_SERIES_YEARS = {
    "csi300_pe_ttm": _LONG_WINDOW_YEARS,
    "csi300_pb": _LONG_WINDOW_YEARS,
    "csi500_pe_ttm": _LONG_WINDOW_YEARS,
    "cgb_10y": _LONG_WINDOW_YEARS,
    "shibor_3m": 1,
    "lpr_1y": 1,
    "new_high_60": _BREADTH_WINDOW_YEARS,
    "new_low_60": _BREADTH_WINDOW_YEARS,
    "margin_balance_total": _LONG_WINDOW_YEARS,
}
_BENCHMARK_INDEX = "000300"

Points = list[tuple[str, float]]


@dataclass(frozen=True)
class PercentileStat:
    value: float
    percentile: float
    sample_count: int
    window_start: str
    window_end: str

    def to_dict(self) -> dict[str, object]:
        return {
            "value": round(self.value, 6),
            "percentile": round(self.percentile, 4),
            "sample_count": self.sample_count,
            "window_start": self.window_start,
            "window_end": self.window_end,
        }


def historical_percentile(
    points: Points,
    *,
    end_date: str,
    years: int,
) -> PercentileStat | None:
    """Share of observations in the trailing window at or below the latest value."""

    window_start = _years_before(end_date, years)
    window = [(day, value) for day, value in points if window_start <= day <= end_date]
    if len(window) < 2:
        return None
    latest = window[-1][1]
    below = sum(1 for _, value in window if value <= latest)
    return PercentileStat(
        value=latest,
        percentile=below / len(window),
        sample_count=len(window),
        window_start=window[0][0],
        window_end=window[-1][0],
    )


@dataclass(frozen=True)
class MarketArchive:
    as_of_date: str
    assessment: StateAssessment
    metrics: dict[str, dict[str, Any]]
    gaps: tuple[str, ...]
    source_errors: dict[str, dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MARKET_ARCHIVE_VERSION,
            "as_of_date": self.as_of_date,
            "assessment": self.assessment.to_dict(),
            "metrics": self.metrics,
            "gaps": list(self.gaps),
            "source_errors": self.source_errors,
        }


class MarketArchiveService:
    """Build the A-share market state as of a date from point-in-time series."""

    def __init__(
        self,
        *,
        macro_data: MacroDataService,
        data_access: Any,
    ) -> None:
        self.macro_data = macro_data
        self.data_access = data_access

    def build(
        self,
        as_of_date: str,
        *,
        allow_remote: bool = True,
    ) -> MarketArchive:
        date.fromisoformat(as_of_date)
        calls: dict[str, Callable[[], Any]] = {
            series_id: partial(
                self.macro_data.get,
                series_id,
                start_date=_years_before(as_of_date, years, extra_days=40),
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
            )
            for series_id, years in _SERIES_YEARS.items()
        }
        calls["index_bars"] = partial(self._index_closes, as_of_date, allow_remote)
        outcomes = fan_out(calls)
        series: dict[str, Points] = {}
        errors: dict[str, dict[str, str]] = {}
        for name, outcome in outcomes.items():
            if isinstance(outcome, Exception):
                errors[name] = {"error": f"{type(outcome).__name__}: {outcome}"}
                continue
            if isinstance(outcome, MacroSeriesResult):
                if outcome.provider_errors:
                    errors[name] = dict(outcome.provider_errors)
                series[name] = [(item.period_end, item.value) for item in outcome.points]
            else:
                series[name] = outcome
        builders = (
            _valuation,
            _equity_bond_spread,
            _rates_liquidity,
            _trend,
            _volatility,
            _breadth,
            _margin,
        )
        dimensions = []
        signals = []
        labels = []
        metrics: dict[str, dict[str, Any]] = {}
        gaps = []
        for builder in builders:
            built = builder(series, as_of_date)
            if isinstance(built, str):
                gaps.append(built)
                continue
            dimension, metric, signal, label = built
            dimensions.append(dimension)
            metrics[dimension.name] = metric
            signals.extend(signal)
            labels.append(label)
        gaps.extend(
            (
                "资金流：北向资金已不再按日披露，ETF 申赎与主力资金尚未接入",
                "情绪：期权隐含波动率、换手与新开户数据尚未接入",
            )
        )
        if not dimensions:
            raise ValueError(f"No market dimension available as of {as_of_date}")
        stale = [
            name
            for name, metric in metrics.items()
            if (
                date.fromisoformat(as_of_date)
                - date.fromisoformat(str(metric["latest_date"]))
            ).days
            > _STALE_DAYS
        ]
        for name in stale:
            gaps.append(f"{name}：最新数据早于截止日 {_STALE_DAYS} 天以上")
        confidence = (
            Confidence.HIGH
            if len(dimensions) == len(builders) and not stale
            else Confidence.MEDIUM
            if len(dimensions) >= 4
            else Confidence.LOW
        )
        payload = {
            "dimensions": [
                (item.name, item.value, str(item.historical_percentile))
                for item in dimensions
            ],
            "signals": signals,
            "label": "；".join(labels),
            "confidence": confidence.value,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        assessment = StateAssessment(
            assessment_id=f"market-archive:{as_of_date}:{digest}",
            subject_type=SubjectType.MARKET,
            subject_id=MARKET_SUBJECT_ID,
            as_of_date=as_of_date,
            dimensions=tuple(dimensions),
            label="；".join(labels),
            confidence=confidence,
            change_signals=tuple(signals) or ("没有可比较的前期数据",),
        )
        return MarketArchive(
            as_of_date=as_of_date,
            assessment=assessment,
            metrics=metrics,
            gaps=tuple(gaps),
            source_errors=errors,
        )

    def _index_closes(self, as_of_date: str, allow_remote: bool) -> Points:
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(raw_symbol=_BENCHMARK_INDEX, asset_type_hint="指数"),
                dataset=DATASET_INDEX_DAILY_BARS,
                start_date=_years_before(as_of_date, _LONG_WINDOW_YEARS, extra_days=400),
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=60,
            )
        )
        points = []
        # A bar dated today may be an intraday snapshot rather than a close.
        last_day = min(as_of_date, (date.today() - timedelta(days=1)).isoformat())
        for record in response.records:
            day = str(record.get("trade_date") or "")
            close = record.get("close")
            if day and day <= last_day and close:
                points.append((day, float(close)))
        return sorted(points)


Built = tuple[AssessmentDimension, dict[str, Any], list[str], str] | str


def _valuation(series: dict[str, Points], as_of: str) -> Built:
    pe = series.get("csi300_pe_ttm") or []
    pe_stat = historical_percentile(pe, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if pe_stat is None:
        return "估值：沪深300 滚动市盈率历史不可用"
    pb_stat = historical_percentile(
        series.get("csi300_pb") or [], end_date=as_of, years=_LONG_WINDOW_YEARS
    )
    mid_stat = historical_percentile(
        series.get("csi500_pe_ttm") or [], end_date=as_of, years=_LONG_WINDOW_YEARS
    )
    parts = [
        f"沪深300 滚动市盈率 {pe_stat.value:.2f} 倍（{pe_stat.window_end}），"
        f"近 10 年分位 {_pct(pe_stat.percentile)}（{pe_stat.sample_count} 个观测）"
    ]
    if pb_stat:
        parts.append(f"市净率 {pb_stat.value:.2f} 倍，分位 {_pct(pb_stat.percentile)}")
    if mid_stat:
        parts.append(
            f"中证500 滚动市盈率 {mid_stat.value:.2f} 倍，分位 {_pct(mid_stat.percentile)}"
        )
    signals = []
    previous = _value_on_or_before(pe, _days_before(pe_stat.window_end, 30))
    if previous:
        signals.append(f"沪深300 滚动市盈率较一个月前 {pe_stat.value / previous - 1:+.1%}")
    return (
        AssessmentDimension(
            name="估值",
            value="；".join(parts),
            evidence_refs=_refs(("csi300_pe_ttm", pe_stat), ("csi300_pb", pb_stat), ("csi500_pe_ttm", mid_stat)),
            historical_percentile=_decimal(pe_stat.percentile),
        ),
        {
            "latest_date": pe_stat.window_end,
            "csi300_pe_ttm": pe_stat.to_dict(),
            "csi300_pb": pb_stat.to_dict() if pb_stat else None,
            "csi500_pe_ttm": mid_stat.to_dict() if mid_stat else None,
            "source": "乐咕乐股（AkShare stock_index_pe_lg / stock_index_pb_lg），历史为月末值",
        },
        signals,
        f"估值{_bucket(pe_stat.percentile)}",
    )


def _equity_bond_spread(series: dict[str, Points], as_of: str) -> Built:
    pe = series.get("csi300_pe_ttm") or []
    yields = series.get("cgb_10y") or []
    spread = []
    for day, value in pe:
        bond = _value_on_or_before(yields, day)
        if bond is not None and value > 0:
            spread.append((day, 100 / value - bond))
    stat = historical_percentile(spread, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if stat is None:
        return "股债性价比：市盈率或 10 年国债收益率历史不可用"
    pe_latest = _value_on_or_before(pe, stat.window_end) or 0
    bond_latest = _value_on_or_before(yields, stat.window_end) or 0
    previous = _value_on_or_before(spread, _days_before(stat.window_end, 30))
    signals = (
        [f"股债利差较一个月前 {stat.value - previous:+.2f} 个百分点"]
        if previous is not None
        else []
    )
    return (
        AssessmentDimension(
            name="股债性价比",
            value=(
                f"沪深300 盈利收益率 {100 / pe_latest:.2f}% − 10 年国债 {bond_latest:.2f}% "
                f"= {stat.value:.2f} 个百分点（{stat.window_end}），近 10 年分位 "
                f"{_pct(stat.percentile)}（{stat.sample_count} 个观测；分位越高，股票相对债券越便宜）"
            ),
            evidence_refs=_refs(("csi300_pe_ttm", stat), ("cgb_10y", stat)),
            historical_percentile=_decimal(stat.percentile),
        ),
        {
            "latest_date": stat.window_end,
            "spread_percentage_points": stat.to_dict(),
            "earnings_yield_percent": round(100 / pe_latest, 4),
            "cgb_10y_percent": bond_latest,
        },
        signals,
        f"股票相对债券吸引力{_bucket(stat.percentile)}",
    )


def _rates_liquidity(series: dict[str, Points], as_of: str) -> Built:
    yields = series.get("cgb_10y") or []
    stat = historical_percentile(yields, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if stat is None:
        return "利率与流动性：10 年国债收益率历史不可用"
    parts = [
        f"10 年国债 {stat.value:.2f}%（{stat.window_end}），近 10 年分位 {_pct(stat.percentile)}"
    ]
    signals = []
    earlier = _value_on_or_before(yields, _days_before(stat.window_end, 91))
    if earlier is not None:
        change = (stat.value - earlier) * 100
        parts[0] += f"，近 3 个月 {change:+.0f}bp"
        signals.append(f"10 年国债收益率近 3 个月 {change:+.0f}bp")
    shibor = series.get("shibor_3m") or []
    metric: dict[str, Any] = {"latest_date": stat.window_end, "cgb_10y": stat.to_dict()}
    if shibor:
        day, value = shibor[-1]
        earlier_shibor = _value_on_or_before(shibor, _days_before(day, 91))
        text = f"3 个月 Shibor {value:.2f}%"
        if earlier_shibor is not None:
            text += f"（近 3 个月 {(value - earlier_shibor) * 100:+.0f}bp）"
        parts.append(text)
        metric["shibor_3m"] = {"date": day, "value": value}
    lpr = series.get("lpr_1y") or []
    if lpr:
        parts.append(f"1 年期 LPR {lpr[-1][1]:.2f}%（{lpr[-1][0]}）")
        metric["lpr_1y"] = {"date": lpr[-1][0], "value": lpr[-1][1]}
    return (
        AssessmentDimension(
            name="利率与流动性",
            value="；".join(parts),
            evidence_refs=_refs(("cgb_10y", stat)) + tuple(
                f"macro_series:{name}:{series[name][-1][0]}"
                for name in ("shibor_3m", "lpr_1y")
                if series.get(name)
            ),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        signals,
        f"利率{_bucket(stat.percentile)}",
    )


def _trend(series: dict[str, Points], as_of: str) -> Built:
    closes = series.get("index_bars") or []
    if len(closes) < 260:
        return "趋势：沪深300 指数日线不足 260 个交易日"
    returns_60 = [
        (closes[index][0], closes[index][1] / closes[index - 60][1] - 1)
        for index in range(60, len(closes))
    ]
    stat = historical_percentile(returns_60, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if stat is None:
        return "趋势：沪深300 60 日收益历史不足"
    day, close = closes[-1]
    ma250 = sum(value for _, value in closes[-250:]) / 250
    return_20 = close / closes[-21][1] - 1
    return (
        AssessmentDimension(
            name="趋势",
            value=(
                f"沪深300 {close:.2f}（{day}），近 20 个交易日 {return_20:+.1%}，"
                f"近 60 个交易日 {stat.value:+.1%}（近 10 年分位 {_pct(stat.percentile)}），"
                f"较 250 日均线 {close / ma250 - 1:+.1%}"
            ),
            evidence_refs=(f"index_daily_bars:{_BENCHMARK_INDEX}:{day}",),
            historical_percentile=_decimal(stat.percentile),
        ),
        {
            "latest_date": day,
            "close": close,
            "return_20d": round(return_20, 6),
            "return_60d": stat.to_dict(),
            "deviation_from_ma250": round(close / ma250 - 1, 6),
            "basis": "价格指数，不含分红",
        },
        [f"沪深300 近 20 个交易日 {return_20:+.1%}"],
        "趋势" + ("向上" if close > ma250 and return_20 > 0 else "向下" if close < ma250 and return_20 < 0 else "震荡"),
    )


def _volatility(series: dict[str, Points], as_of: str) -> Built:
    closes = series.get("index_bars") or []
    if len(closes) < 80:
        return "波动：沪深300 指数日线不足"
    daily = [
        (closes[index][0], math.log(closes[index][1] / closes[index - 1][1]))
        for index in range(1, len(closes))
    ]
    vol = []
    for index in range(20, len(daily) + 1):
        window = [value for _, value in daily[index - 20 : index]]
        mean = sum(window) / 20
        variance = sum((value - mean) ** 2 for value in window) / 19
        vol.append((daily[index - 1][0], math.sqrt(variance * 252)))
    stat = historical_percentile(vol, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if stat is None:
        return "波动：已实现波动率历史不足"
    previous = _value_on_or_before(vol, _days_before(stat.window_end, 30))
    signals = (
        [f"沪深300 20 日年化波动率较一个月前 {(stat.value - previous) * 100:+.1f} 个百分点"]
        if previous is not None
        else []
    )
    return (
        AssessmentDimension(
            name="波动",
            value=(
                f"沪深300 近 20 个交易日年化波动率 {stat.value:.1%}（{stat.window_end}），"
                f"近 10 年分位 {_pct(stat.percentile)}"
            ),
            evidence_refs=(f"index_daily_bars:{_BENCHMARK_INDEX}:{stat.window_end}",),
            historical_percentile=_decimal(stat.percentile),
        ),
        {"latest_date": stat.window_end, "realized_vol_20d": stat.to_dict()},
        signals,
        f"波动{_bucket(stat.percentile)}",
    )


def _breadth(series: dict[str, Points], as_of: str) -> Built:
    highs = dict(series.get("new_high_60") or [])
    lows = dict(series.get("new_low_60") or [])
    days = sorted(set(highs) & set(lows))
    net = [(day, highs[day] - lows[day]) for day in days]
    averaged = [
        (net[index][0], sum(value for _, value in net[index - 19 : index + 1]) / 20)
        for index in range(19, len(net))
    ]
    stat = historical_percentile(averaged, end_date=as_of, years=_BREADTH_WINDOW_YEARS)
    if stat is None:
        return "宽度：新高新低家数历史不足"
    day = stat.window_end
    previous = _value_on_or_before(averaged, _days_before(day, 28))
    signals = (
        [f"近 20 日新高减新低平均值较前一月 {stat.value - previous:+.0f} 家"]
        if previous is not None
        else []
    )
    return (
        AssessmentDimension(
            name="宽度",
            value=(
                f"全 A 股 60 日新高 {highs[day]:.0f} 家、新低 {lows[day]:.0f} 家（{day}）；"
                f"近 20 个交易日新高减新低平均 {stat.value:+.0f} 家，"
                f"近 {stat.sample_count} 个交易日分位 {_pct(stat.percentile)}（来源只有约两年历史）"
            ),
            evidence_refs=_refs(("new_high_60", stat), ("new_low_60", stat)),
            historical_percentile=_decimal(stat.percentile),
        ),
        {
            "latest_date": day,
            "new_high_60": highs[day],
            "new_low_60": lows[day],
            "net_new_high_20d_mean": stat.to_dict(),
        },
        signals,
        f"宽度{_bucket(stat.percentile)}",
    )


def _margin(series: dict[str, Points], as_of: str) -> Built:
    balance = series.get("margin_balance_total") or []
    changes = [
        (balance[index][0], balance[index][1] / balance[index - 20][1] - 1)
        for index in range(20, len(balance))
    ]
    stat = historical_percentile(changes, end_date=as_of, years=_LONG_WINDOW_YEARS)
    if stat is None:
        return "两融：融资融券余额历史不可用"
    day, latest = balance[-1]
    return (
        AssessmentDimension(
            name="两融",
            value=(
                f"沪深两融余额 {latest:,.0f} 亿元（{day}），近 20 个交易日 {stat.value:+.1%}，"
                f"该变化在近 10 年分位 {_pct(stat.percentile)}"
            ),
            evidence_refs=_refs(("margin_balance_total", stat)),
            historical_percentile=_decimal(stat.percentile),
        ),
        {
            "latest_date": day,
            "balance_100m_yuan": round(latest, 2),
            "change_20d": stat.to_dict(),
        },
        [f"两融余额近 20 个交易日 {stat.value:+.1%}"],
        "融资" + ("扩张" if stat.value > 0 else "收缩"),
    )


def _refs(*items: tuple[str, PercentileStat | None]) -> tuple[str, ...]:
    return tuple(
        f"macro_series:{name}:{stat.window_end}" for name, stat in items if stat is not None
    )


def _value_on_or_before(points: Points, day: str) -> float | None:
    index = bisect.bisect_right(points, (day, math.inf)) - 1
    return points[index][1] if index >= 0 else None


def _bucket(percentile: float) -> str:
    for limit, text in ((0.2, "低"), (0.4, "偏低"), (0.6, "中等"), (0.8, "偏高")):
        if percentile < limit:
            return text
    return "高"


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _years_before(day: str, years: int, *, extra_days: int = 0) -> str:
    return (date.fromisoformat(day) - timedelta(days=365 * years + extra_days)).isoformat()


def _days_before(day: str, days: int) -> str:
    return (date.fromisoformat(day) - timedelta(days=days)).isoformat()


# Shared with other state archives (macro).
value_on_or_before = _value_on_or_before
percentile_bucket = _bucket
years_before = _years_before
days_before = _days_before
