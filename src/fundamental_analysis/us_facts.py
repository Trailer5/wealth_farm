"""Trailing-twelve-month values and annual history from SEC XBRL company facts (pure functions).

Each fact in companyfacts carries start/end dates (durations) or an end date (instants), the form and
the filed date. Values are taken only from filings made on or before the as-of date. Trailing twelve
months are rebuilt as last fiscal year + current year-to-date − prior-year year-to-date, because 10-Qs
report year-to-date (and three-month) figures but no fourth quarter. Design notes:
docs/modules/3-分析工具/估值与财务分析模块.md「美股估值」.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# First concept present wins; companies tag revenue differently across years.
CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "eps_diluted": ("EarningsPerShareDiluted",),
}
INSTANTS = {
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "short_investments": ("ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
    "debt": ("LongTermDebt", "LongTermDebtNoncurrent"),
    "debt_current": ("LongTermDebtCurrent", "DebtCurrent"),
}
SHARES = ("EntityCommonStockSharesOutstanding",)


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _entries(facts: dict[str, Any], taxonomy: str, names: tuple[str, ...], as_of_date: str) -> list[dict[str, Any]]:
    for name in names:
        concept = facts.get(taxonomy, {}).get(name)
        if not concept:
            continue
        units = concept.get("units") or {}
        rows = next(iter(units.values()), [])
        rows = [row for row in rows if str(row.get("filed", "9999")) <= as_of_date and row.get("form") in ("10-K", "10-Q", "10-K/A", "10-Q/A")]
        if rows:
            return rows
    return []


def _durations(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    """Latest filed value per (start, end)."""

    latest: dict[tuple[str, str], tuple[str, float]] = {}
    for row in rows:
        if "start" not in row:
            continue
        key = (row["start"], row["end"])
        if key not in latest or row["filed"] >= latest[key][0]:
            latest[key] = (row["filed"], float(row["val"]))
    return {key: value for key, (_, value) in latest.items()}


def ttm(facts: dict[str, Any], names: tuple[str, ...], as_of_date: str) -> dict[str, Any] | None:
    durations = _durations(_entries(facts, "us-gaap", names, as_of_date))
    years = sorted((key for key in durations if 350 <= _days(*key) <= 380), key=lambda key: key[1])
    if not years:
        return None
    fy = years[-1]
    ytd = [key for key in durations if key[0] > fy[1] and key[1] > fy[1] and 80 <= _days(*key) <= 290]
    if not ytd:
        return {"value": durations[fy], "period_end": fy[1], "basis": "fiscal_year"}
    current = max(ytd, key=lambda key: (key[1], _days(*key)))
    span = _days(*current)
    prior = [
        key for key in durations
        if abs(_days(*key) - span) <= 7 and abs(_days(key[1], current[1]) - 365) <= 10 and key[1] <= fy[1]
    ]
    if not prior:
        return {"value": durations[fy], "period_end": fy[1], "basis": "fiscal_year"}
    value = durations[fy] + durations[current] - durations[max(prior, key=lambda key: key[1])]
    return {"value": value, "period_end": current[1], "basis": "ttm"}


def annual(facts: dict[str, Any], names: tuple[str, ...], as_of_date: str) -> list[dict[str, Any]]:
    durations = _durations(_entries(facts, "us-gaap", names, as_of_date))
    rows = [(key[1], value) for key, value in durations.items() if 350 <= _days(*key) <= 380]
    return [{"fiscal_year_end": end, "value": value} for end, value in sorted(rows)]


def instant(facts: dict[str, Any], taxonomy: str, names: tuple[str, ...], as_of_date: str) -> dict[str, Any] | None:
    rows = [row for row in _entries(facts, taxonomy, names, as_of_date) if "start" not in row]
    if not rows:
        return None
    row = max(rows, key=lambda item: (item["end"], item["filed"]))
    return {"value": float(row["val"]), "period_end": row["end"]}


def shares_outstanding(facts: dict[str, Any], as_of_date: str) -> dict[str, Any] | None:
    for name in SHARES:
        concept = facts.get("dei", {}).get(name)
        if not concept:
            continue
        rows = [row for row in next(iter((concept.get("units") or {}).values()), []) if str(row.get("filed", "9999")) <= as_of_date]
        if rows:
            row = max(rows, key=lambda item: (item["end"], item["filed"]))
            return {"value": float(row["val"]), "period_end": row["end"]}
    return None


def summarize(facts: dict[str, Any], as_of_date: str) -> dict[str, Any]:
    """TTM flows, latest balances, shares and annual history known on the date."""

    flows = {name: ttm(facts, names, as_of_date) for name, names in CONCEPTS.items() if name != "eps_diluted"}
    balances = {name: instant(facts, "us-gaap", names, as_of_date) for name, names in INSTANTS.items()}
    history = {name: annual(facts, CONCEPTS[name], as_of_date) for name in ("revenue", "net_income")}
    return {"ttm": flows, "balances": balances, "shares": shares_outstanding(facts, as_of_date), "annual": history}
