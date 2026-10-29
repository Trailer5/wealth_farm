"""Discounted-cash-flow arithmetic: forward values, implied growth and justified P/B.

Design notes: docs/modules/3-分析工具/估值与财务分析模块.md, “设计说明”.
"""

from __future__ import annotations

_GROWTH_BOUNDS = (-0.5, 1.0)
_ITERATIONS = 100


def present_value(
    base: float,
    growth: float,
    *,
    discount_rate: float,
    terminal_growth: float,
    years: int,
) -> float:
    """Value today of `base` growing at `growth` for `years`, then at `terminal_growth` forever."""

    if discount_rate <= terminal_growth:
        raise ValueError("discount_rate must exceed terminal_growth")
    value, flow = 0.0, base
    for year in range(1, years + 1):
        flow *= 1 + growth
        value += flow / (1 + discount_rate) ** year
    terminal = flow * (1 + terminal_growth) / (discount_rate - terminal_growth)
    return value + terminal / (1 + discount_rate) ** years


def implied_growth(
    target: float,
    base: float,
    *,
    discount_rate: float,
    terminal_growth: float,
    years: int,
) -> float | None:
    """Annual growth over the explicit period that makes the value equal `target`; None if out of range."""

    if base <= 0 or target <= 0:
        return None
    low, high = _GROWTH_BOUNDS

    def value(growth: float) -> float:
        return present_value(
            base, growth, discount_rate=discount_rate, terminal_growth=terminal_growth, years=years
        )

    if not value(low) <= target <= value(high):
        return None
    for _ in range(_ITERATIONS):
        middle = (low + high) / 2
        if value(middle) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def implied_years(
    target: float,
    base: float,
    growth: float,
    *,
    discount_rate: float,
    terminal_growth: float,
    max_years: int = 30,
) -> int | None:
    """Fewest years at `growth` (then terminal growth) for the value to reach `target`."""

    if base <= 0 or target <= 0 or growth <= terminal_growth:
        return None
    for years in range(0, max_years + 1):
        if present_value(
            base, growth, discount_rate=discount_rate, terminal_growth=terminal_growth, years=years
        ) >= target:
            return years
    return None


def implied_discount_rate(
    target: float,
    base: float,
    growth: float,
    *,
    terminal_growth: float,
    years: int,
) -> float | None:
    """Annual return the price implies if `growth` holds: the rate equating value and `target`."""

    if base <= 0 or target <= 0:
        return None
    low, high = terminal_growth + 1e-4, 1.0

    def value(rate: float) -> float:
        return present_value(base, growth, discount_rate=rate, terminal_growth=terminal_growth, years=years)

    if not value(high) <= target <= value(low):
        return None
    for _ in range(_ITERATIONS):
        middle = (low + high) / 2
        if value(middle) > target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def revenue_value(
    revenue: float,
    growth: float,
    *,
    start_margin: float,
    terminal_margin: float,
    discount_rate: float,
    terminal_growth: float,
    years: int,
) -> float:
    """Value of profits on growing revenue whose margin moves linearly to `terminal_margin`."""

    if discount_rate <= terminal_growth:
        raise ValueError("discount_rate must exceed terminal_growth")
    value, sales = 0.0, revenue
    for year in range(1, years + 1):
        sales *= 1 + growth
        margin = start_margin + (terminal_margin - start_margin) * year / years
        value += sales * margin / (1 + discount_rate) ** year
    terminal = sales * terminal_margin * (1 + terminal_growth) / (discount_rate - terminal_growth)
    return value + terminal / (1 + discount_rate) ** years


def implied_revenue_growth(
    target: float,
    revenue: float,
    *,
    start_margin: float,
    terminal_margin: float,
    discount_rate: float,
    terminal_growth: float,
    years: int,
) -> float | None:
    if revenue <= 0 or target <= 0 or terminal_margin <= 0:
        return None
    low, high = _GROWTH_BOUNDS

    def value(growth: float) -> float:
        return revenue_value(
            revenue,
            growth,
            start_margin=start_margin,
            terminal_margin=terminal_margin,
            discount_rate=discount_rate,
            terminal_growth=terminal_growth,
            years=years,
        )

    if not value(low) <= target <= value(high):
        return None
    for _ in range(_ITERATIONS):
        middle = (low + high) / 2
        if value(middle) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def implied_roe(pb: float, *, discount_rate: float, terminal_growth: float) -> float:
    """Sustainable ROE implied by P/B = (ROE - g) / (r - g)."""

    return terminal_growth + pb * (discount_rate - terminal_growth)


def cagr(start: float | None, end: float | None, years: int) -> float | None:
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return (end / start) ** (1 / years) - 1
