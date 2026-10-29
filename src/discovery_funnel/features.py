"""Point-in-time member features from report-period financials and daily valuations."""

from __future__ import annotations

import bisect
from collections import deque
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from src.cross_section import derived_fields, shift_quarter, ttm

FINANCIAL_L1 = frozenset({"银行", "非银金融"})
CYCLICAL_L1 = frozenset({"煤炭", "石油石化", "钢铁", "有色金属"})
# Own-history valuation percentiles need at least three years of month-ends.
MIN_PE_SAMPLES = 36
DRAWDOWN_WINDOW_DAYS = 750
# A valuation older than this on the cut-off date is treated as missing (suspension).
MAX_VALUATION_LAG_DAYS = 15


def annualized_roe(roe_percent: float | None, period: str) -> float | None:
    """Fallback when TTM inputs are missing: scale year-to-date ROE to a full year."""

    if roe_percent is None:
        return None
    return roe_percent * 12 / int(period[5:7])


class Financials:
    """All loaded report-period rows; lookups only see rows announced by a date."""

    def __init__(self, rows_by_period: dict[str, list[dict[str, Any]]]) -> None:
        self.periods = sorted(rows_by_period, reverse=True)
        self.rows = {
            period: {row["symbol"]: row for row in rows if row.get("announced_on")}
            for period, rows in rows_by_period.items()
        }

    def row(self, symbol: str, period: str, on: str) -> dict[str, Any] | None:
        row = self.rows.get(period, {}).get(symbol)
        return row if row is not None and row["announced_on"] <= on else None

    def latest(self, symbol: str, on: str) -> dict[str, Any] | None:
        for period in self.periods:
            if period >= on:
                continue
            row = self.row(symbol, period, on)
            if row is not None:
                return row
        return None


@dataclass(frozen=True)
class MemberFeatures:
    symbol: str
    name: str
    industry_l1: str | None
    report_period: str | None = None
    roe: float | None = None
    roe_prev_year: float | None = None
    roe_basis: str | None = None
    debt_ratio: float | None = None
    operating_cash_flow: float | None = None
    cash_flow_basis: str | None = None
    net_profit: float | None = None
    net_profit_yoy: float | None = None
    net_profit_prev_year: float | None = None
    net_profit_quarter: float | None = None
    net_profit_quarter_yoy: float | None = None
    net_profit_quarter_yoy_prev: float | None = None
    revenue_yoy: float | None = None
    revenue_quarter_yoy: float | None = None
    net_margin: float | None = None
    gross_margin: float | None = None
    valuation_date: str | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pe_samples: int = 0
    drawdown_3y: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: round(value, 4) if isinstance(value, float) else value
            for key, value in asdict(self).items()
        }


def financial_fields(financials: Financials, symbol: str, on: str) -> dict[str, Any]:
    current = financials.latest(symbol, on)
    if current is None:
        return {}
    period = current["report_period"]

    def lookup(day: str) -> dict[str, Any] | None:
        return financials.row(symbol, day, on)

    derived = derived_fields(current, lookup)
    prev_quarter = lookup(shift_quarter(period, -1))
    prev_year = lookup(shift_quarter(period, -4))
    roe, roe_basis = derived["roe_ttm_percent"], "ttm"
    roe_prev = ttm(prev_year, "roe_percent", lookup) if prev_year else None
    if roe is None:
        roe, roe_basis = annualized_roe(current.get("roe_percent"), period), "annualized_ytd"
        roe_prev = annualized_roe((prev_year or {}).get("roe_percent"), shift_quarter(period, -4))
    cash_flow, cash_flow_basis = derived["operating_cash_flow_ttm"], "ttm"
    if cash_flow is None:
        cash_flow, cash_flow_basis = current.get("operating_cash_flow"), "ytd"
    return {
        "report_period": period,
        "roe": roe,
        "roe_prev_year": roe_prev,
        "roe_basis": roe_basis if roe is not None else None,
        "debt_ratio": current.get("debt_ratio_percent"),
        "operating_cash_flow": cash_flow,
        "cash_flow_basis": cash_flow_basis if cash_flow is not None else None,
        "net_profit": current.get("net_profit"),
        "net_profit_yoy": current.get("net_profit_yoy_percent"),
        "net_profit_prev_year": (prev_year or {}).get("net_profit"),
        "net_profit_quarter": derived["net_profit_quarter"],
        "net_profit_quarter_yoy": derived["net_profit_quarter_yoy_percent"],
        "net_profit_quarter_yoy_prev": (
            derived_fields(prev_quarter, lookup)["net_profit_quarter_yoy_percent"]
            if prev_quarter
            else None
        ),
        "revenue_yoy": current.get("revenue_yoy_percent"),
        "revenue_quarter_yoy": derived["revenue_quarter_yoy_percent"],
        "net_margin": (
            current["net_profit"] / current["revenue"]
            if current.get("net_profit") is not None and current.get("revenue")
            else None
        ),
        "gross_margin": current.get("gross_margin_percent"),
    }


class ValuationSeries:
    """One stock's daily Eastmoney valuation, prepared for repeated as-of lookups."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        rows = sorted(rows, key=lambda row: row["trade_date"])
        self.days = [row["trade_date"] for row in rows]
        self.pe = [row.get("pe_ttm") for row in rows]
        self.pb = [row.get("pb") for row in rows]
        self.cap = [row.get("market_cap") for row in rows]
        self.cap_high = _rolling_max(self.cap, DRAWDOWN_WINDOW_DAYS)
        by_month: dict[str, int] = {}
        for index, day in enumerate(self.days):
            by_month[day[:7]] = index
        month_ends = sorted(by_month.values())
        self.month_days = [self.days[i] for i in month_ends]
        self.month_pe = [self.pe[i] for i in month_ends]

    def index_on(self, on: str) -> int | None:
        index = bisect.bisect_right(self.days, on) - 1
        if index < 0:
            return None
        lag = (date.fromisoformat(on) - date.fromisoformat(self.days[index])).days
        return index if lag <= MAX_VALUATION_LAG_DAYS else None

    def cap_on(self, on: str) -> float | None:
        index = self.index_on(on)
        return None if index is None else self.cap[index]

    def fields(self, on: str) -> dict[str, Any]:
        index = self.index_on(on)
        if index is None:
            return {}
        pe = self.pe[index]
        result: dict[str, Any] = {
            "valuation_date": self.days[index],
            "pe_ttm": pe,
            "pb": self.pb[index],
        }
        months = bisect.bisect_right(self.month_days, self.days[index])
        history = [value for value in self.month_pe[:months] if value is not None and value > 0]
        result["pe_samples"] = len(history)
        if pe is not None and pe > 0 and len(history) >= MIN_PE_SAMPLES:
            result["pe_percentile"] = sum(1 for value in history if value <= pe) / len(history)
        cap, high = self.cap[index], self.cap_high[index]
        if cap and high and index >= 250:
            result["drawdown_3y"] = cap / high - 1
        return result


def build_features(
    *,
    symbol: str,
    name: str,
    industry_l1: str | None,
    financials: Financials,
    on: str,
    valuation: ValuationSeries | None = None,
    snapshot_row: dict[str, Any] | None = None,
) -> MemberFeatures:
    fields = financial_fields(financials, symbol, on)
    if valuation is not None:
        fields.update(valuation.fields(on))
    elif snapshot_row is not None:
        fields.update(
            {
                "valuation_date": snapshot_row.get("quote_date"),
                "pe_ttm": snapshot_row.get("pe_ttm"),
                "pb": snapshot_row.get("pb"),
            }
        )
    return MemberFeatures(symbol=symbol, name=name, industry_l1=industry_l1, **fields)


def one_off_suspicion(features: MemberFeatures) -> str | None:
    """Profit that cannot come from operations; the cross-section has no recurring-profit figure."""

    if features.net_margin is not None and features.net_margin > 0.5:
        return f"净利润是营收的 {features.net_margin:.0%}：利润很可能来自非经常性损益"
    if features.net_profit_yoy is not None and features.net_profit_yoy > 300:
        return f"累计净利润同比 {features.net_profit_yoy:+.0f}%：可能来自非经常性损益或极低基数"
    return None


def exclusion(features: MemberFeatures) -> str | None:
    if "ST" in features.name.upper() or "退" in features.name:
        return "ST 或退市整理"
    if features.industry_l1 in FINANCIAL_L1:
        return "金融机构：只排除并标注，不参与排序"
    if features.industry_l1 in CYCLICAL_L1:
        return "周期资源：只排除并标注，不参与排序"
    if features.report_period is None:
        return "截止日前没有已公告的财务截面"
    return None


def _rolling_max(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = []
    queue: deque[int] = deque()
    for index, value in enumerate(values):
        if value is not None:
            while queue and values[queue[-1]] <= value:  # type: ignore[operator]
                queue.pop()
            queue.append(index)
        while queue and queue[0] <= index - window:
            queue.popleft()
        result.append(values[queue[0]] if queue else None)
    return result
