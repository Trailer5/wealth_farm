"""Total-return price levels from exchange reference closes and fund NAV."""

from __future__ import annotations

from typing import Any, Iterable

PRICE_BASIS_TOTAL_RETURN = "total_return"
PRICE_BASIS_PRICE = "price"
# Published growth rates have 0.01% precision; larger disagreement marks a distribution or split.
_NAV_GROWTH_TOLERANCE = 0.0005


def forward_adjusted_bars(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Scale OHLC so close-to-close ratios equal daily total returns.

    Each record's ``preclose`` is the exchange reference close after
    dividends, bonus shares, splits, or fund share conversion. The last bar
    keeps its traded prices; earlier bars are scaled back from it.
    """

    rows = sorted(
        (
            dict(record)
            for record in records
            if _positive(record.get("close")) is not None
        ),
        key=lambda item: str(item.get("trade_date") or ""),
    )
    if not rows:
        return []
    factor = 1.0
    output: list[dict[str, Any]] = []
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        close = float(row["close"])
        preclose = _positive(row.get("preclose"))
        if index > 0:
            previous_close = float(rows[index - 1]["close"])
            reference = preclose if preclose is not None else previous_close
            row["return_reference"] = (
                "exchange_preclose" if preclose is not None else "previous_close"
            )
        else:
            reference = None
            row["return_reference"] = "window_start"
        row["unadjusted_close"] = close
        row["adjustment_factor"] = factor
        for key in ("open", "high", "low", "close"):
            value = row.get(key)
            if value is not None:
                row[key] = float(value) * factor
        row["price_basis"] = PRICE_BASIS_TOTAL_RETURN
        output.append(row)
        if reference is not None:
            factor *= reference / previous_close
    output.reverse()
    return output


def adjusted_nav_levels(
    records: Iterable[dict[str, Any]],
    *,
    date_key: str = "nav_date",
) -> dict[str, float]:
    """Date to NAV level with distributions reinvested, anchored at the latest unit NAV.

    Day-to-day ratios use unit NAV unless the published daily growth rate
    disagrees, which happens on distribution and split days.
    """

    by_date: dict[str, tuple[float, float | None]] = {}
    for record in records:
        if record.get("estimated"):
            continue
        day = str(record.get(date_key) or "")[:10]
        unit_nav = _positive(record.get("unit_nav"))
        if not day or unit_nav is None or day in by_date:
            continue
        growth = record.get("daily_growth_rate")
        try:
            growth_ratio = (
                1.0 + float(growth) / 100.0 if growth not in (None, "") else None
            )
        except (TypeError, ValueError):
            growth_ratio = None
        by_date[day] = (unit_nav, growth_ratio)
    days = sorted(by_date)
    if not days:
        return {}
    ratios = [1.0]
    for previous, current in zip(days, days[1:]):
        nav_ratio = by_date[current][0] / by_date[previous][0]
        growth_ratio = by_date[current][1]
        if (
            growth_ratio is not None
            and growth_ratio > 0
            and abs(nav_ratio - growth_ratio) > _NAV_GROWTH_TOLERANCE
        ):
            ratios.append(growth_ratio)
        else:
            ratios.append(nav_ratio)
    level = by_date[days[-1]][0]
    levels = {days[-1]: level}
    for index in range(len(days) - 1, 0, -1):
        level /= ratios[index]
        levels[days[index - 1]] = level
    return levels


def _positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
