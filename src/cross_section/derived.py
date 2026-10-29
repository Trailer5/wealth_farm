"""Seasonality-free figures derived from consecutive year-to-date report periods.

Report-period figures are cumulative from January. Trailing-twelve-month (TTM) values
add the last annual report and remove the same period a year earlier; single-quarter
values subtract the previous period of the same fiscal year.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

RowLookup = Callable[[str], dict[str, Any] | None]

DERIVED_BASIS_NOTE = (
    "TTM = 本期累计 + 上一年报 − 去年同期累计；ROE(TTM) 按同式由加权 ROE 近似；"
    "单季 = 本期累计 − 同年上一期累计；去年同季为负或为零时单季同比不计算"
)


def shift_quarter(period: str, quarters: int) -> str:
    year, month = int(period[:4]), int(period[5:7])
    year, quarter = divmod(year * 4 + month // 3 - 1 + quarters, 4)
    month = (quarter + 1) * 3
    return date(year, month, 31 if month in (3, 12) else 30).isoformat()


def ttm(row: dict[str, Any], key: str, lookup: RowLookup) -> float | None:
    period = row["report_period"]
    value = row.get(key)
    if value is None:
        return None
    if period[5:7] == "12":
        return value
    annual = lookup(f"{int(period[:4]) - 1}-12-31")
    same = lookup(shift_quarter(period, -4))
    if annual is None or same is None or annual.get(key) is None or same.get(key) is None:
        return None
    return value + annual[key] - same[key]


def single_quarter(row: dict[str, Any], key: str, lookup: RowLookup) -> float | None:
    value = row.get(key)
    if value is None or row["report_period"][5:7] == "03":
        return value
    previous = lookup(shift_quarter(row["report_period"], -1))
    if previous is None or previous.get(key) is None:
        return None
    return value - previous[key]


def single_quarter_yoy(row: dict[str, Any], key: str, lookup: RowLookup) -> float | None:
    """Percent change of the single quarter against the same quarter a year earlier."""

    current = single_quarter(row, key, lookup)
    base_row = lookup(shift_quarter(row["report_period"], -4))
    base = None if base_row is None else single_quarter(base_row, key, lookup)
    if current is None or base is None or base <= 0:
        return None
    return (current / base - 1) * 100


def derived_fields(row: dict[str, Any], lookup: RowLookup) -> dict[str, Any]:
    return {
        "roe_ttm_percent": ttm(row, "roe_percent", lookup),
        "net_profit_ttm": ttm(row, "net_profit", lookup),
        "revenue_ttm": ttm(row, "revenue", lookup),
        "operating_cash_flow_ttm": ttm(row, "operating_cash_flow", lookup),
        "net_profit_quarter": single_quarter(row, "net_profit", lookup),
        "net_profit_quarter_yoy_percent": single_quarter_yoy(row, "net_profit", lookup),
        "revenue_quarter_yoy_percent": single_quarter_yoy(row, "revenue", lookup),
    }
