"""Raw statement fields over recent quarters: leading indicators such as contract liabilities and inventory.

The fundamental packs use derived metrics; the balance-sheet and income items that lead them (contract
liabilities, inventory, payables, other income, fair-value gains) are stored by their source labels.
This reads them from the local store, turns cumulative income and cash-flow items into single quarters,
and gives year-on-year and quarter-on-quarter changes. Design notes: docs/modules/3-分析工具/估值与财务分析模块.md
「原始字段与领先指标」.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.financial_data import SQLiteFinancialDataRepository

DEFAULT_FIELDS = (
    "TOTAL_OPERATE_INCOME",
    "CONTRACT_LIAB",
    "INVENTORY",
    "ACCOUNTS_PAYABLE",
    "OTHER_INCOME",
    "FAIRVALUE_CHANGE_INCOME",
)
DATASETS = (
    "stock_balance_sheets",
    "stock_income_statements",
    "stock_cash_flow_statements",
)
_QUARTER_ORDER = {"03-31": 1, "06-30": 2, "09-30": 3, "12-31": 4}


def available_fields(repository: SQLiteFinancialDataRepository, *, asset_id: str, as_of_date: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for dataset in DATASETS:
        points = repository.list_points(
            asset_id=asset_id, dataset=dataset, start_date="1990-01-01", end_date=as_of_date, as_of_date=as_of_date
        )
        result[dataset] = sorted({point.field_label for point in points})
    return result


def field_series(
    repository: SQLiteFinancialDataRepository,
    *,
    asset_id: str,
    fields: tuple[str, ...],
    as_of_date: str,
    quarters: int = 8,
) -> dict[str, Any]:
    """Latest ``quarters`` periods of each field, known on ``as_of_date``."""

    wanted = set(fields)
    found: dict[str, dict[str, Any]] = {}
    for dataset in DATASETS:
        for point in repository.list_points(
            asset_id=asset_id, dataset=dataset, start_date="1990-01-01", end_date=as_of_date, as_of_date=as_of_date
        ):
            if point.field_label not in wanted:
                continue
            entry = found.setdefault(
                point.field_label,
                {"dataset": dataset, "scope": point.value_scope.value, "unit": point.unit, "values": {}},
            )
            current = entry["values"].get(point.report_period)
            if current is None or point.published_at >= current[1]:
                entry["values"][point.report_period] = (point.normalized_value, point.published_at)
    series = {}
    for label in fields:
        entry = found.get(label)
        if entry is None:
            series[label] = {"missing": True}
            continue
        values = {period: value for period, (value, _) in entry["values"].items()}
        cumulative = entry["scope"] == "cumulative"
        quarter_values = _single_quarters(values) if cumulative else values
        periods = sorted(quarter_values)[-quarters:]
        rows = []
        for period in periods:
            value = quarter_values[period]
            last_year = quarter_values.get(f"{int(period[:4]) - 1}{period[4:]}")
            previous = _previous_quarter(period)
            row = {
                "period": period,
                "value": _number(value),
                "yoy": _ratio(value, last_year),
                "published_at": entry["values"][period][1] if period in entry["values"] else None,
            }
            if not cumulative and previous in quarter_values:
                row["qoq_change"] = _number(value - quarter_values[previous])
            rows.append(row)
        series[label] = {
            "dataset": entry["dataset"],
            "basis": "single_quarter" if cumulative else "period_end",
            "unit": entry["unit"],
            "rows": rows,
        }
    return {"asset_id": asset_id, "as_of_date": as_of_date, "fields": series}


def _single_quarters(cumulative: dict[str, Decimal]) -> dict[str, Decimal]:
    """Q1 as reported; later quarters minus the same year's previous cumulative value."""

    result = {}
    for period, value in cumulative.items():
        order = _QUARTER_ORDER.get(period[5:])
        if order is None:
            continue
        if order == 1:
            result[period] = value
            continue
        previous = _previous_quarter(period)
        if previous in cumulative:
            result[period] = value - cumulative[previous]
    return result


def _previous_quarter(period: str) -> str:
    year, suffix = int(period[:4]), period[5:]
    order = _QUARTER_ORDER[suffix]
    if order == 1:
        return f"{year - 1}-12-31"
    return f"{year}-{[key for key, item in _QUARTER_ORDER.items() if item == order - 1][0]}"


def _ratio(value: Decimal, base: Decimal | None) -> float | None:
    if base is None or base == 0:
        return None
    return round(float((value - base) / abs(base)), 4)


def _number(value: Decimal) -> float:
    return round(float(value), 2)
