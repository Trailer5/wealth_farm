"""China macro archive: growth, inflation, money, credit and policy-rate state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Any

from src.data_src.fan_out import fan_out
from src.macro_data import MacroDataService, MacroPoint
from src.market_analysis import (
    days_before,
    historical_percentile,
    percentile_bucket,
    value_on_or_before,
    years_before,
)
from src.research_contracts import (
    AssessmentDimension,
    Confidence,
    StateAssessment,
    SubjectType,
)

MACRO_SUBJECT_ID = "macro:cn"
MACRO_ARCHIVE_VERSION = "macro_archive_v1"
_WINDOW_YEARS = 10
# Monthly data is published in the following month; older means the source lags.
_STALE_DAYS = 75
_SERIES = (
    "pmi_manufacturing",
    "pmi_non_manufacturing",
    "gdp_yoy_ytd",
    "cpi_yoy",
    "ppi_yoy",
    "m2_yoy",
    "m1_yoy",
    "new_rmb_loans",
    "social_financing_flow",
    "shibor_3m",
    "cgb_10y",
    "lpr_1y",
    "lpr_5y",
)

Points = list[tuple[str, float]]


@dataclass(frozen=True)
class MacroArchive:
    as_of_date: str
    assessment: StateAssessment
    metrics: dict[str, dict[str, Any]]
    gaps: tuple[str, ...]
    source_errors: dict[str, dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MACRO_ARCHIVE_VERSION,
            "as_of_date": self.as_of_date,
            "assessment": self.assessment.to_dict(),
            "metrics": self.metrics,
            "gaps": list(self.gaps),
            "source_errors": self.source_errors,
        }


@dataclass(frozen=True)
class _Series:
    points: Points
    periods: dict[str, str]

    def period(self, period_end: str) -> str:
        return self.periods.get(period_end, period_end)


class MacroArchiveService:
    """Build the macro state as of a date from point-in-time macro series."""

    def __init__(self, *, macro_data: MacroDataService) -> None:
        self.macro_data = macro_data

    def build(self, as_of_date: str, *, allow_remote: bool = True) -> MacroArchive:
        date.fromisoformat(as_of_date)
        outcomes = fan_out(
            {
                series_id: partial(
                    self.macro_data.get,
                    series_id,
                    start_date=years_before(as_of_date, _WINDOW_YEARS + 2),
                    end_date=as_of_date,
                    as_of_date=as_of_date,
                    allow_remote=allow_remote,
                )
                for series_id in _SERIES
            }
        )
        series: dict[str, _Series] = {}
        errors: dict[str, dict[str, str]] = {}
        for name, outcome in outcomes.items():
            if isinstance(outcome, Exception):
                errors[name] = {"error": f"{type(outcome).__name__}: {outcome}"}
                continue
            if outcome.provider_errors:
                errors[name] = dict(outcome.provider_errors)
            series[name] = _to_series(outcome.points)
        builders = (_growth, _inflation, _money, _credit, _policy)
        dimensions, signals, labels, gaps = [], [], [], []
        metrics: dict[str, dict[str, Any]] = {}
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
        if not dimensions:
            raise ValueError(f"No macro dimension available as of {as_of_date}")
        stale = _stale_series(series, as_of_date)
        gaps.extend(
            f"{name}：最新期 {latest}，距截止日超过 {_STALE_DAYS} 天，来源更新滞后"
            for name, latest in stale
        )
        gaps.extend(
            (
                "政策：央行、政治局与国常会表述未结构化，政策判断需 Agent 阅读官方原文",
                "外部环境：海外利率、汇率与出口数据尚未接入",
                "工业增加值、社零、固定资产投资月度数据尚未接入",
            )
        )
        core_stale = [name for name, _ in stale if name != "social_financing_flow"]
        confidence = (
            Confidence.HIGH
            if len(dimensions) == len(builders) and not core_stale
            else Confidence.MEDIUM
            if len(dimensions) >= 3
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
            assessment_id=f"macro-archive:{as_of_date}:{digest}",
            subject_type=SubjectType.MACRO,
            subject_id=MACRO_SUBJECT_ID,
            as_of_date=as_of_date,
            dimensions=tuple(dimensions),
            label="；".join(labels),
            confidence=confidence,
            change_signals=tuple(signals) or ("没有可比较的前期数据",),
        )
        return MacroArchive(
            as_of_date=as_of_date,
            assessment=assessment,
            metrics=metrics,
            gaps=tuple(gaps),
            source_errors=errors,
        )


Built = tuple[AssessmentDimension, dict[str, Any], list[str], str] | str


def _growth(series: dict[str, _Series], as_of: str) -> Built:
    pmi = series.get("pmi_manufacturing")
    stat = historical_percentile(pmi.points, end_date=as_of, years=_WINDOW_YEARS) if pmi else None
    if stat is None:
        return "增长：制造业 PMI 不可用"
    recent = [value for _, value in pmi.points[-3:]]
    average = sum(recent) / len(recent)
    parts = [
        f"制造业 PMI {stat.value:.1f}（{pmi.period(stat.window_end)}），近 3 个月均值 "
        f"{average:.1f}，近 10 年分位 {_pct(stat.percentile)}"
    ]
    metric: dict[str, Any] = {
        "latest_date": stat.window_end,
        "pmi_manufacturing": stat.to_dict(),
        "pmi_manufacturing_3m_mean": round(average, 3),
    }
    refs = [f"macro_series:pmi_manufacturing:{pmi.period(stat.window_end)}"]
    non_manufacturing = series.get("pmi_non_manufacturing")
    if non_manufacturing and non_manufacturing.points:
        day, value = non_manufacturing.points[-1]
        parts.append(f"非制造业商务活动指数 {value:.1f}")
        metric["pmi_non_manufacturing"] = {"period": non_manufacturing.period(day), "value": value}
        refs.append(f"macro_series:pmi_non_manufacturing:{non_manufacturing.period(day)}")
    gdp = series.get("gdp_yoy_ytd")
    if gdp and gdp.points:
        day, value = gdp.points[-1]
        parts.append(f"GDP 累计同比 {value:.1f}%（{gdp.period(day)}）")
        metric["gdp_yoy_ytd"] = {"period": gdp.period(day), "value": value}
        refs.append(f"macro_series:gdp_yoy_ytd:{gdp.period(day)}")
    signals = []
    if len(pmi.points) >= 4:
        signals.append(f"制造业 PMI 较 3 个月前 {stat.value - pmi.points[-4][1]:+.1f}")
    return (
        AssessmentDimension(
            name="增长",
            value="；".join(parts),
            evidence_refs=tuple(refs),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        signals,
        "制造业景气" + ("扩张" if average >= 50 else "偏弱") + f"（PMI 近 3 个月均值 {average:.1f}）",
    )


def _inflation(series: dict[str, _Series], as_of: str) -> Built:
    cpi = series.get("cpi_yoy")
    stat = historical_percentile(cpi.points, end_date=as_of, years=_WINDOW_YEARS) if cpi else None
    if stat is None:
        return "通胀：CPI 不可用"
    parts = [
        f"CPI 同比 {stat.value:+.1f}%（{cpi.period(stat.window_end)}），近 10 年分位 "
        f"{_pct(stat.percentile)}"
    ]
    refs = [f"macro_series:cpi_yoy:{cpi.period(stat.window_end)}"]
    metric: dict[str, Any] = {"latest_date": stat.window_end, "cpi_yoy": stat.to_dict()}
    signals = []
    if len(cpi.points) >= 4:
        signals.append(f"CPI 同比较 3 个月前 {stat.value - cpi.points[-4][1]:+.1f} 个百分点")
    ppi = series.get("ppi_yoy")
    if ppi and ppi.points:
        day, value = ppi.points[-1]
        ppi_stat = historical_percentile(ppi.points, end_date=as_of, years=_WINDOW_YEARS)
        text = f"PPI 同比 {value:+.1f}%（{ppi.period(day)}）"
        if ppi_stat:
            text += f"，分位 {_pct(ppi_stat.percentile)}"
            metric["ppi_yoy"] = ppi_stat.to_dict()
        parts.append(text)
        refs.append(f"macro_series:ppi_yoy:{ppi.period(day)}")
        if len(ppi.points) >= 4:
            earlier = ppi.points[-4][1]
            if (earlier < 0) != (value < 0):
                signals.append(f"PPI 同比由 {earlier:+.1f}% 变为 {value:+.1f}%，近 3 个月转向")
            else:
                signals.append(f"PPI 同比较 3 个月前 {value - earlier:+.1f} 个百分点")
    return (
        AssessmentDimension(
            name="通胀",
            value="；".join(parts),
            evidence_refs=tuple(refs),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        signals,
        f"通胀{percentile_bucket(stat.percentile)}",
    )


def _money(series: dict[str, _Series], as_of: str) -> Built:
    m1 = series.get("m1_yoy")
    m2 = series.get("m2_yoy")
    if not m1 or not m2:
        return "货币与流动性：M1、M2 不可用"
    m2_values = dict(m2.points)
    gap = [(day, value - m2_values[day]) for day, value in m1.points if day in m2_values]
    stat = historical_percentile(gap, end_date=as_of, years=_WINDOW_YEARS)
    if stat is None:
        return "货币与流动性：M1−M2 剪刀差历史不足"
    day = stat.window_end
    parts = [
        f"M2 同比 {m2_values[day]:.1f}%、M1 同比 {dict(m1.points)[day]:.1f}%（{m2.period(day)}），"
        f"M1−M2 剪刀差 {stat.value:+.1f} 个百分点，近 10 年分位 {_pct(stat.percentile)}"
    ]
    refs = [f"macro_series:m1_yoy:{m1.period(day)}", f"macro_series:m2_yoy:{m2.period(day)}"]
    metric: dict[str, Any] = {"latest_date": day, "m1_minus_m2": stat.to_dict()}
    signals = []
    if len(gap) >= 4:
        signals.append(f"M1−M2 剪刀差较 3 个月前 {stat.value - gap[-4][1]:+.1f} 个百分点")
    shibor = series.get("shibor_3m")
    if shibor and shibor.points:
        shibor_day, value = shibor.points[-1]
        parts.append(f"3 个月 Shibor {value:.2f}%（{shibor_day}）")
        metric["shibor_3m"] = {"date": shibor_day, "value": value}
        refs.append(f"macro_series:shibor_3m:{shibor_day}")
    yields = series.get("cgb_10y")
    if yields and yields.points:
        yield_day, value = yields.points[-1]
        earlier = value_on_or_before(yields.points, days_before(yield_day, 91))
        text = f"10 年国债 {value:.2f}%"
        if earlier is not None:
            text += f"（近 3 个月 {(value - earlier) * 100:+.0f}bp）"
        parts.append(text)
        metric["cgb_10y"] = {"date": yield_day, "value": value}
        refs.append(f"macro_series:cgb_10y:{yield_day}")
    return (
        AssessmentDimension(
            name="货币与流动性",
            value="；".join(parts),
            evidence_refs=tuple(refs),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        signals,
        f"货币活化程度{percentile_bucket(stat.percentile)}",
    )


def _credit(series: dict[str, _Series], as_of: str) -> Built:
    loans = series.get("new_rmb_loans")
    growth = _ttm_growth(loans.points) if loans else []
    stat = historical_percentile(growth, end_date=as_of, years=_WINDOW_YEARS)
    if stat is None:
        return "信用：新增人民币贷款历史不足 24 个月"
    day = stat.window_end
    ttm = sum(value for _, value in loans.points[-12:])
    parts = [
        f"近 12 个月新增人民币贷款 {ttm:,.0f} 亿元（截至 {loans.period(day)}），较上一个 12 个月 "
        f"{stat.value:+.1%}，近 10 年分位 {_pct(stat.percentile)}"
    ]
    refs = [f"macro_series:new_rmb_loans:{loans.period(day)}"]
    metric: dict[str, Any] = {
        "latest_date": day,
        "new_rmb_loans_ttm_100m_yuan": round(ttm, 1),
        "new_rmb_loans_ttm_growth": stat.to_dict(),
    }
    financing = series.get("social_financing_flow")
    if financing and financing.points:
        financing_day, value = financing.points[-1]
        parts.append(
            f"社融增量最新期 {financing.period(financing_day)} 为 {value:,.0f} 亿元（来源滞后，仅供参考）"
        )
        metric["social_financing_flow"] = {"period": financing.period(financing_day), "value": value}
        refs.append(f"macro_series:social_financing_flow:{financing.period(financing_day)}")
    previous = value_on_or_before(growth, days_before(day, 85))
    signals = (
        [f"近 12 个月新增贷款同比增速较 3 个月前 {(stat.value - previous) * 100:+.1f} 个百分点"]
        if previous is not None
        else []
    )
    return (
        AssessmentDimension(
            name="信用",
            value="；".join(parts),
            evidence_refs=tuple(refs),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        signals,
        "信贷" + ("同比扩张" if stat.value > 0 else "同比收缩"),
    )


def _policy(series: dict[str, _Series], as_of: str) -> Built:
    lpr = series.get("lpr_1y")
    stat = historical_percentile(lpr.points, end_date=as_of, years=_WINDOW_YEARS) if lpr else None
    if stat is None:
        return "政策：LPR 不可用"
    day = stat.window_end
    year_ago = value_on_or_before(lpr.points, days_before(day, 365))
    change = (stat.value - year_ago) * 100 if year_ago is not None else None
    parts = [f"1 年期 LPR {stat.value:.2f}%（{day}）"]
    if change is not None:
        parts[0] += f"，近 12 个月 {change:+.0f}bp"
    refs = [f"macro_series:lpr_1y:{day}"]
    metric: dict[str, Any] = {"latest_date": day, "lpr_1y": stat.to_dict(), "lpr_1y_change_12m_bp": change}
    five_year = series.get("lpr_5y")
    if five_year and five_year.points:
        five_day, value = five_year.points[-1]
        parts.append(f"5 年期以上 LPR {value:.2f}%")
        metric["lpr_5y"] = {"date": five_day, "value": value}
        refs.append(f"macro_series:lpr_5y:{five_day}")
    parts.append("政策文本未结构化，宽松或收紧的判断需结合央行与政治局会议原文")
    direction = (
        "未知" if change is None else "下调" if change < 0 else "上调" if change > 0 else "持平"
    )
    return (
        AssessmentDimension(
            name="政策利率",
            value="；".join(parts),
            evidence_refs=tuple(refs),
            historical_percentile=_decimal(stat.percentile),
        ),
        metric,
        [f"1 年期 LPR 近 12 个月{direction}" + (f" {abs(change):.0f}bp" if change else "")],
        f"政策利率近一年{direction}",
    )


def _to_series(points: tuple[MacroPoint, ...]) -> _Series:
    return _Series(
        points=[(item.period_end, item.value) for item in points],
        periods={item.period_end: item.period for item in points},
    )


def _ttm_growth(points: Points) -> Points:
    """Trailing-12-month sum versus the previous 12 months, on consecutive months."""

    output = []
    for index in range(23, len(points)):
        window = points[index - 23 : index + 1]
        months = [day[:7] for day, _ in window]
        if len(set(months)) != 24 or not _consecutive(months):
            continue
        previous = sum(value for _, value in window[:12])
        current = sum(value for _, value in window[12:])
        if previous > 0:
            output.append((points[index][0], current / previous - 1))
    return output


def _consecutive(months: list[str]) -> bool:
    ordinals = [int(item[:4]) * 12 + int(item[5:7]) for item in months]
    return all(later - earlier == 1 for earlier, later in zip(ordinals, ordinals[1:]))


def _stale_series(series: dict[str, _Series], as_of: str) -> list[tuple[str, str]]:
    stale = []
    for name in ("pmi_manufacturing", "cpi_yoy", "ppi_yoy", "m2_yoy", "new_rmb_loans", "social_financing_flow"):
        item = series.get(name)
        if item and item.points:
            latest = item.points[-1][0]
            if (date.fromisoformat(as_of) - date.fromisoformat(latest)).days > _STALE_DAYS:
                stale.append((name, item.period(latest)))
    return stale


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))
