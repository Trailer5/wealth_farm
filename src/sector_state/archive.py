"""Shenwan industry archive: valuation, relative strength, earnings, crowding and breadth."""

from __future__ import annotations

import bisect
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any

from src.cross_section import (
    DERIVED_BASIS_NOTE,
    CrossSectionService,
    derived_fields,
    shift_quarter,
    single_quarter,
)
from src.data_access import (
    DATASET_INDEX_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.market_analysis import historical_percentile, percentile_bucket, years_before
from src.research_contracts import (
    AssessmentDimension,
    Confidence,
    StateAssessment,
    SubjectType,
)
from src.sector_data import SectorDataService, SwIndustry

SECTOR_ARCHIVE_VERSION = "sector_archive_v1"
_WINDOW_YEARS = 10
# Member-level breadth and level-3 valuation fetch one history per member.
MAX_MEMBERS = 300
_BENCHMARK_INDEX = "000300"
# Calendar days between the latest source date and the cut-off before the archive says so.
_STALE_DAYS = 3

Points = list[tuple[str, float]]


def sector_subject_id(code: str) -> str:
    return f"sector:sw:{code}"


@dataclass(frozen=True)
class SectorArchive:
    code: str
    as_of_date: str
    assessment: StateAssessment
    metrics: dict[str, dict[str, Any]]
    gaps: tuple[str, ...]
    source_errors: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SECTOR_ARCHIVE_VERSION,
            "industry_code": self.code,
            "as_of_date": self.as_of_date,
            "assessment": self.assessment.to_dict(),
            "metrics": self.metrics,
            "gaps": list(self.gaps),
            "source_errors": self.source_errors,
        }


class SectorArchiveService:
    def __init__(
        self,
        *,
        sector_data: SectorDataService,
        cross_section: CrossSectionService,
        data_access: DataAccessService,
    ) -> None:
        self.sector_data = sector_data
        self.cross_section = cross_section
        self.data_access = data_access

    def build(self, as_of_date: str, *, code: str, allow_remote: bool = True) -> SectorArchive:
        industry = self.sector_data.industry(code, allow_remote=allow_remote)
        if industry is None:
            raise KeyError(f"Unknown Shenwan industry: {code}")
        member_list = self.sector_data.members(code, allow_remote=allow_remote)
        members = [m.symbol for m in member_list]
        names = {m.symbol: m.name for m in member_list}
        errors: dict[str, Any] = {}
        gaps: list[str] = []
        member_series: dict[str, list[dict[str, Any]]] = {}
        if len(members) <= MAX_MEMBERS:
            history = self.cross_section.valuation_history(
                members,
                start_date=years_before(as_of_date, _WINDOW_YEARS),
                end_date=as_of_date,
                allow_remote=allow_remote,
            )
            member_series = {k: v for k, v in history["series"].items() if v}
            if history["provider_errors"]:
                errors["member_valuation"] = history["provider_errors"]
        else:
            gaps.append(f"成分 {len(members)} 只超过 {MAX_MEMBERS} 只，未逐只取数：宽度未计算")
        sector_valuation: dict[str, Any] | None = None
        if industry.level < 3:
            sector_valuation = self.sector_data.valuation_history(
                code,
                start_date=years_before(as_of_date, _WINDOW_YEARS),
                end_date=as_of_date,
                allow_remote=allow_remote,
            )
            if sector_valuation["provider_errors"]:
                errors["sector_valuation"] = sector_valuation["provider_errors"]
            points = sector_valuation["points"]
            if points and _days_between(points[-1]["trade_date"], as_of_date) > _STALE_DAYS:
                gaps.append(
                    f"申万行业估值与成交额占比只到 {points[-1]['trade_date']}（截止日 {as_of_date}）：来源尚未发布更新日期"
                )
        index_closes = self._closes(
            AssetInput(asset_id=self.sector_data.index_asset(code).asset_id),
            as_of_date,
            allow_remote,
        )
        market_closes = self._closes(
            AssetInput(raw_symbol=_BENCHMARK_INDEX, asset_type_hint="指数"),
            as_of_date,
            allow_remote,
        )
        if index_closes and _days_between(index_closes[-1][0], as_of_date) > _STALE_DAYS:
            gaps.append(
                f"申万行业指数数据只到 {index_closes[-1][0]}（截止日 {as_of_date}）：来源尚未更新，"
                "相对强弱、估值与拥挤度按该日计算"
            )
        financials = self.cross_section.latest_financials(as_of_date, allow_remote=allow_remote)
        if financials["provider_errors"]:
            errors["financials"] = financials["provider_errors"]
        period_rows: dict[str, dict[str, dict[str, Any]]] = {}

        def rows_for(period: str) -> dict[str, dict[str, Any]]:
            if period not in period_rows:
                result = self.cross_section.report_period(
                    period, as_of_date=as_of_date, allow_remote=allow_remote
                )
                period_rows[period] = {row["symbol"]: row for row in result["rows"]}
            return period_rows[period]

        built = [
            _valuation(industry, sector_valuation, member_series, as_of_date),
            _relative_strength(industry, index_closes, market_closes, as_of_date),
            _earnings(industry, names, financials, rows_for),
            _crowding(industry, sector_valuation, as_of_date),
            _breadth(industry, member_series, as_of_date),
        ]
        dimensions, signals, labels, metrics = [], [], [], {}
        for item in built:
            if isinstance(item, str):
                gaps.append(item)
                continue
            dimension, metric, signal, label = item
            dimensions.append(dimension)
            metrics[dimension.name] = metric
            signals.extend(signal)
            labels.append(label)
        if not dimensions:
            raise ValueError(f"No sector dimension available for {code} as of {as_of_date}")
        gaps.extend(
            (
                "景气与产业链：没有行业景气、价格与产能数据，盈利维度只来自成分股财报汇总",
                "资金流与政策：行业资金流向与政策文本尚未接入",
                "成分：申万当前成分，回溯使用有幸存者偏差",
            )
        )
        confidence = (
            Confidence.HIGH
            if len(dimensions) == len(built)
            else Confidence.MEDIUM
            if len(dimensions) >= 3
            else Confidence.LOW
        )
        label = "；".join(labels)
        payload = {
            "code": code,
            "dimensions": [(d.name, d.value, str(d.historical_percentile)) for d in dimensions],
            "signals": signals,
            "label": label,
            "confidence": confidence.value,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:12]
        assessment = StateAssessment(
            assessment_id=f"sector-archive:{code}:{as_of_date}:{digest}",
            subject_type=SubjectType.SECTOR,
            subject_id=sector_subject_id(code),
            as_of_date=as_of_date,
            dimensions=tuple(dimensions),
            label=f"申万{industry.name}：{label}",
            confidence=confidence,
            change_signals=tuple(signals) or ("没有可比较的前期数据",),
        )
        return SectorArchive(code, as_of_date, assessment, metrics, tuple(gaps), errors)

    def _closes(self, asset: AssetInput, as_of_date: str, allow_remote: bool) -> Points:
        response = self.data_access.get(
            DataAccessRequest(
                asset=asset,
                dataset=DATASET_INDEX_DAILY_BARS,
                start_date=years_before(as_of_date, _WINDOW_YEARS, extra_days=400),
                end_date=as_of_date,
                as_of_date=as_of_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
                provider_timeout_seconds=60,
            )
        )
        return sorted(
            (str(r["trade_date"]), float(r["close"]))
            for r in response.records
            if r.get("trade_date") and r.get("close") and str(r["trade_date"]) <= as_of_date
        )


Built = tuple[AssessmentDimension, dict[str, Any], list[str], str] | str


def _valuation(
    industry: SwIndustry,
    sector_valuation: dict[str, Any] | None,
    member_series: dict[str, list[dict[str, Any]]],
    as_of: str,
) -> Built:
    if sector_valuation is not None:
        pe = [(p["trade_date"], p["pe"]) for p in sector_valuation["points"] if p.get("pe")]
        pb = [(p["trade_date"], p["pb"]) for p in sector_valuation["points"] if p.get("pb")]
        basis = "申万行业指数估值（月末与最新）"
        refs = (f"shenwan_2021:valuation:{industry.code}",)
    else:
        pe = _member_median_series(member_series, "pe_ttm")
        pb = _member_median_series(member_series, "pb")
        basis = "当前成分 PE(TTM) 与 PB 的中位数（月末），亏损成分不计入 PE"
        refs = (f"akshare:stock_value_em:members:{industry.code}",)
    pe_stat = historical_percentile(pe, end_date=as_of, years=_WINDOW_YEARS)
    if pe_stat is None:
        return "估值：行业估值历史不可用"
    pb_stat = historical_percentile(pb, end_date=as_of, years=_WINDOW_YEARS)
    text = (
        f"市盈率 {pe_stat.value:.1f} 倍（{pe_stat.window_end}），历史分位 {pe_stat.percentile:.0%}"
        f"（{pe_stat.window_start} 起 {pe_stat.sample_count} 个样本）"
    )
    if pb_stat:
        text += f"；市净率 {pb_stat.value:.2f} 倍，分位 {pb_stat.percentile:.0%}"
    signals = []
    if len(pe) >= 13:
        signals.append(f"行业市盈率较一年前 {pe_stat.value / pe[-13][1] - 1:+.1%}")
    return (
        AssessmentDimension("估值", text, refs, _decimal(pe_stat.percentile)),
        {
            "basis": basis,
            "pe": pe_stat.to_dict(),
            "pb": pb_stat.to_dict() if pb_stat else None,
        },
        signals,
        f"估值{percentile_bucket(pe_stat.percentile)}（PE 分位 {pe_stat.percentile:.0%}）",
    )


def _relative_strength(
    industry: SwIndustry,
    index: Points,
    market: Points,
    as_of: str,
) -> Built:
    if len(index) < 300 or len(market) < 300:
        return "相对强弱：行业指数或沪深300 日线不足"
    market_by_day = dict(market)
    aligned = [(day, close, market_by_day[day]) for day, close in index if day in market_by_day]
    if len(aligned) < 300:
        return "相对强弱：行业指数与沪深300 共同交易日不足"
    excess_60 = [
        (aligned[i][0], (aligned[i][1] / aligned[i - 60][1]) / (aligned[i][2] / aligned[i - 60][2]) - 1)
        for i in range(60, len(aligned))
    ]
    stat = historical_percentile(excess_60, end_date=as_of, years=_WINDOW_YEARS)
    if stat is None:
        return "相对强弱：历史不足"
    day, close, _ = aligned[-1]
    returns = {
        horizon: close / aligned[-1 - horizon][1] - 1
        for horizon in (20, 60, 250)
        if len(aligned) > horizon
    }
    ma250 = sum(item[1] for item in aligned[-250:]) / 250
    excess_250 = (close / aligned[-251][1]) / (aligned[-1][2] / aligned[-251][2]) - 1
    text = (
        f"行业指数近 20/60/250 日 {returns[20]:+.1%}/{returns[60]:+.1%}/{returns[250]:+.1%}；"
        f"近 60 日相对沪深300 {stat.value:+.1%}（{stat.window_start} 起分位 {stat.percentile:.0%}），"
        f"近 250 日相对 {excess_250:+.1%}；收盘{'高于' if close >= ma250 else '低于'}年线 {close / ma250 - 1:+.1%}"
    )
    trend = "强于" if stat.value > 0 else "弱于"
    market_60 = aligned[-1][2] / aligned[-61][2] - 1
    return (
        AssessmentDimension(
            "相对强弱",
            text,
            (f"shenwan_2021:index:{industry.code}:{day}", f"index_daily_bars:{_BENCHMARK_INDEX}:{day}"),
            _decimal(stat.percentile),
        ),
        {
            "latest_date": day,
            "returns": {str(k): round(v, 6) for k, v in returns.items()},
            "excess_60d": stat.to_dict(),
            "excess_250d": round(excess_250, 6),
            "vs_ma250": round(close / ma250 - 1, 6),
            "market_return_60d": round(market_60, 6),
            "basis": "申万行业指数与沪深300 均为价格指数；相对收益 = (1 + 行业收益) / (1 + 沪深300 收益) − 1",
        },
        [f"近 60 日相对沪深300 {stat.value:+.1%}（行业 {returns[60]:+.1%}，沪深300 {market_60:+.1%}）"],
        f"近 60 日{trend}大盘（行业 {returns[60]:+.0%}、沪深300 {market_60:+.0%}，相对分位 {stat.percentile:.0%}）",
    )


def _earnings(
    industry: SwIndustry,
    names: dict[str, str],
    financials: dict[str, Any],
    rows_for: Callable[[str], dict[str, dict[str, Any]]],
) -> Built:
    members = list(names)
    rows = [row for row in financials["rows"] if row["symbol"] in names]
    if not rows:
        return "盈利：成分股财务截面不可用"
    periods: dict[str, int] = {}
    for row in rows:
        periods[row["report_period"]] = periods.get(row["report_period"], 0) + 1
    period = max(periods, key=lambda key: (periods[key], key))
    # Members that already reported a newer period still count with their row for this period.
    period_rows = rows_for(period)
    same = [period_rows[s] for s in members if s in period_rows]
    newer = {p: n for p, n in periods.items() if p > period}
    reported = {row["symbol"] for row in same}
    absent = [f"{names[s]}（{s}）" for s in members if s not in reported]
    revenue = _aggregate_growth(same, "revenue", "revenue_yoy_percent")
    profit = _aggregate_growth(same, "net_profit", "net_profit_yoy_percent")
    concentration = _profit_concentration(same, names)
    base_period = shift_quarter(period, -4)
    roe, quarter_now, quarter_base = [], 0.0, 0.0
    for row in same:
        symbol = row["symbol"]

        def lookup(day: str, symbol: str = symbol) -> dict[str, Any] | None:
            return rows_for(day).get(symbol)

        derived = derived_fields(row, lookup)
        if derived["roe_ttm_percent"] is not None:
            roe.append(derived["roe_ttm_percent"])
        base_row = lookup(base_period)
        base = single_quarter(base_row, "net_profit", lookup) if base_row else None
        if derived["net_profit_quarter"] is not None and base is not None:
            quarter_now += derived["net_profit_quarter"]
            quarter_base += base
    quarter_growth = quarter_now / quarter_base - 1 if quarter_base > 0 else None
    margin = [row["gross_margin_percent"] for row in same if row.get("gross_margin_percent") is not None]
    losses = sum(1 for row in same if (row.get("net_profit") or 0) < 0)
    parts = [f"{period} 报告期 {len(same)}/{len(members)} 家成分"]
    if revenue is not None:
        parts.append(f"营收合计同比 {revenue:+.1%}")
    if profit is not None:
        parts.append(f"净利润合计同比 {profit:+.1%}")
    if concentration is not None:
        parts.append(
            f"净利润增量的 {concentration['top_share']:.0%} 来自{'、'.join(concentration['top'])}"
            + (
                f"，其余公司合计同比 {concentration['rest_growth']:+.1%}"
                if concentration["rest_growth"] is not None
                else ""
            )
        )
    if quarter_growth is not None:
        parts.append(f"其中单季净利润合计同比 {quarter_growth:+.1%}")
    if roe:
        parts.append(f"ROE(TTM) 中位数 {median(roe):.1f}%（{len(roe)} 家）")
    if margin:
        parts.append(f"毛利率中位数 {median(margin):.1f}%")
    parts.append(f"亏损 {losses} 家")
    if newer:
        parts.append(
            "；".join(f"{p} 报告期已有 {n}/{len(members)} 家披露，未计入" for p, n in sorted(newer.items()))
        )
    if absent:
        parts.append(f"未计入的成分：{'、'.join(absent[:8])}{' 等' if len(absent) > 8 else ''}")
    label = "盈利" + (
        "增长" if (profit or 0) > 0.05 else "下滑" if (profit or 0) < -0.05 else "持平"
    ) + (f"（净利润合计同比 {profit:+.0%}）" if profit is not None else "")
    return (
        AssessmentDimension(
            "盈利",
            "，".join(parts[:1]) + "：" + "，".join(parts[1:]),
            (f"akshare:stock_yjbb_em:{period}",),
        ),
        {
            "report_period": period,
            "members_reported": len(same),
            "members_total": len(members),
            "revenue_yoy": None if revenue is None else round(revenue, 6),
            "net_profit_yoy": None if profit is None else round(profit, 6),
            "net_profit_quarter_yoy": None if quarter_growth is None else round(quarter_growth, 6),
            "roe_ttm_median_percent": round(median(roe), 4) if roe else None,
            "roe_ttm_members": len(roe),
            "gross_margin_median_percent": round(median(margin), 4) if margin else None,
            "loss_making": losses,
            "newer_periods_disclosed": newer,
            "members_not_counted": absent,
            "profit_concentration": concentration,
            "basis": financials["basis_note"] + "；合计同比为累计口径；" + DERIVED_BASIS_NOTE,
        },
        [f"最新单季净利润合计同比 {quarter_growth:+.1%}"] if quarter_growth is not None else [],
        label,
    )


def _crowding(industry: SwIndustry, sector_valuation: dict[str, Any] | None, as_of: str) -> Built:
    if sector_valuation is None:
        return "拥挤度：申万三级行业没有成交额占比来源"
    share = [
        (p["trade_date"], p["amount_share_percent"])
        for p in sector_valuation["points"]
        if p.get("amount_share_percent") is not None
    ]
    stat = historical_percentile(share, end_date=as_of, years=_WINDOW_YEARS)
    if stat is None:
        return "拥挤度：成交额占比历史不可用"
    return (
        AssessmentDimension(
            "拥挤度",
            f"成交额占全市场 {stat.value:.2f}%（{stat.window_end}），历史分位 {stat.percentile:.0%}（{stat.window_start} 起）",
            (f"shenwan_2021:valuation:{industry.code}",),
            _decimal(stat.percentile),
        ),
        {"amount_share_percent": stat.to_dict(), "basis": "申万指数分析：月末与最新一天"},
        [],
        f"交易拥挤度{percentile_bucket(stat.percentile)}",
    )


def _breadth(industry: SwIndustry, member_series: dict[str, list[dict[str, Any]]], as_of: str) -> Built:
    if not member_series:
        return "宽度：成分股价格历史不可用"
    prepared = []
    for rows in member_series.values():
        days = [row["trade_date"] for row in rows if row.get("close")]
        values = [float(row["close"]) for row in rows if row.get("close")]
        sums = [0.0]
        for value in values:
            sums.append(sums[-1] + value)
        prepared.append((days, values, sums))
    month_ends = sorted(_month_ends({str(i): list(zip(p[0], p[1])) for i, p in enumerate(prepared)}))
    history: Points = []
    for day in month_ends:
        above = total = 0
        for days, values, sums in prepared:
            index = bisect.bisect_right(days, day)
            if index < 250:
                continue
            total += 1
            above += values[index - 1] >= (sums[index] - sums[index - 250]) / 250
        if total >= 5:
            history.append((day, above / total))
    stat = historical_percentile(history, end_date=as_of, years=_WINDOW_YEARS)
    if stat is None:
        return "宽度：成分股历史不足一年"
    return (
        AssessmentDimension(
            "宽度",
            f"站上年线的成分占比 {stat.value:.0%}（{stat.window_end}），历史分位 {stat.percentile:.0%}"
            f"（{stat.sample_count} 个月末样本，{stat.window_start} 起）",
            (f"akshare:stock_value_em:members:{industry.code}",),
            _decimal(stat.percentile),
        ),
        {"above_ma250_share": stat.to_dict(), "members_with_history": len(prepared)},
        [],
        f"宽度{percentile_bucket(stat.percentile)}（{stat.value:.0%} 成分在年线上方）",
    )


def _member_median_series(member_series: dict[str, list[dict[str, Any]]], metric: str) -> Points:
    by_day: dict[str, list[float]] = {}
    for symbol_rows in member_series.values():
        for row in _month_end_rows(symbol_rows):
            value = row.get(metric)
            if value is not None and float(value) > 0:
                by_day.setdefault(row["trade_date"][:7], []).append(float(value))
    last_day = {
        row["trade_date"][:7]: row["trade_date"]
        for rows in member_series.values()
        for row in _month_end_rows(rows)
    }
    return [
        (last_day[month], median(values))
        for month, values in sorted(by_day.items())
        if len(values) >= 3
    ]


def _month_end_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_month: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_month[row["trade_date"][:7]] = row
    return [by_month[month] for month in sorted(by_month)]


def _month_ends(closes: dict[str, Points]) -> set[str]:
    by_month: dict[str, str] = {}
    for series in closes.values():
        for day, _ in series:
            if day > by_month.get(day[:7], ""):
                by_month[day[:7]] = day
    return set(by_month.values())


def _profit_concentration(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    """How much of the aggregate profit increase the two largest contributors explain."""

    deltas = {}
    for row in rows:
        value, growth = row.get("net_profit"), row.get("net_profit_yoy_percent")
        if value is None or growth is None or growth <= -100:
            continue
        deltas[row["symbol"]] = value - value / (1 + growth / 100)
    total = sum(deltas.values())
    if len(deltas) < 5 or total <= 0:
        return None
    top = sorted(deltas, key=deltas.get, reverse=True)[:2]  # type: ignore[arg-type]
    rest = [row for row in rows if row["symbol"] not in top]
    return {
        "top": [names.get(symbol, symbol) for symbol in top],
        "top_share": round(sum(deltas[s] for s in top) / total, 4),
        "rest_growth": _aggregate_growth(rest, "net_profit", "net_profit_yoy_percent"),
    }


def _aggregate_growth(rows: list[dict[str, Any]], value_key: str, growth_key: str) -> float | None:
    current = previous = 0.0
    for row in rows:
        value, growth = row.get(value_key), row.get(growth_key)
        if value is None or growth is None or growth <= -100:
            continue
        current += value
        previous += value / (1 + growth / 100)
    if previous <= 0:
        return None
    return current / previous - 1


def _days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))
