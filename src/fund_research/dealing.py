"""Open-fund dealing costs, fee drag and current-manager tenure performance.

Design notes: docs/modules/3-分析工具/基金与ETF研究数据模块.md「场外基金时点与仓位事实」.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.data_src.models import FundManagerTerm, OpenFundDealingInfo

ROUND_TRIP_HOLDING_DAYS = (7, 30, 180, 365, 730, 1095)
FEE_DRAG_YEARS = (1, 3, 5, 10)
# A term counts as continuous with the next one when the gap is at most this many days (weekends, page rounding).
_CONTINUITY_DAYS = 7
_ONE = Decimal("1")


def ongoing_fee_rate(info: OpenFundDealingInfo) -> Decimal | None:
    if info.management_fee_rate is None:
        return None
    return sum(
        (rate for rate in (info.management_fee_rate, info.custody_fee_rate, info.sales_service_fee_rate) if rate),
        Decimal("0"),
    )


def purchase_fee_rate(
    info: OpenFundDealingInfo,
    *,
    amount: float | None = None,
    discounted: bool = True,
) -> Decimal | None:
    """Rate of the tier holding ``amount``; the smallest tier when amount is unknown. None for fixed-fee tiers."""

    tiers = info.purchase_fees
    if not tiers:
        return None
    tier = tiers[0]
    if amount is not None:
        tier = next(
            (
                item
                for item in tiers
                if (item.min_amount is None or amount >= item.min_amount)
                and (item.max_amount is None or amount < item.max_amount)
            ),
            tier,
        )
    return tier.discounted_rate if discounted else tier.rate


def redemption_fee_rate(info: OpenFundDealingInfo, holding_days: int) -> Decimal | None:
    for tier in info.redemption_fees:
        if holding_days >= tier.min_days and (tier.max_days is None or holding_days < tier.max_days):
            return tier.rate
    return None


def round_trip_cost(purchase_rate: Decimal, redemption_rate: Decimal) -> Decimal:
    """Share of the amount paid that is lost to fees: front-end fees are charged on the net amount."""

    return _ONE - (_ONE - redemption_rate) / (_ONE + purchase_rate)


def fee_free_from_days(info: OpenFundDealingInfo) -> int | None:
    return next((tier.min_days for tier in info.redemption_fees if tier.rate == 0), None)


def dealing_summary(info: OpenFundDealingInfo) -> dict[str, object]:
    purchase = purchase_fee_rate(info)
    ongoing = ongoing_fee_rate(info)
    round_trips = []
    if purchase is not None:
        for days in ROUND_TRIP_HOLDING_DAYS:
            redemption = redemption_fee_rate(info, days)
            if redemption is not None:
                round_trips.append(
                    {
                        "holding_days": days,
                        "purchase_rate": str(purchase),
                        "redemption_rate": str(redemption),
                        "round_trip_cost": str(round_trip_cost(purchase, redemption).quantize(Decimal("0.0001"))),
                    }
                )
    drag = []
    if ongoing is not None:
        for years in FEE_DRAG_YEARS:
            ongoing_drag = _ONE - (_ONE - ongoing) ** years
            redemption = redemption_fee_rate(info, years * 365)
            one_off = (
                round_trip_cost(purchase, redemption)
                if purchase is not None and redemption is not None
                else None
            )
            total = None if one_off is None else _ONE - (_ONE - one_off) * (_ONE - ongoing) ** years
            drag.append(
                {
                    "years": years,
                    "ongoing_drag": str(ongoing_drag.quantize(Decimal("0.0001"))),
                    "total_drag": None if total is None else str(total.quantize(Decimal("0.0001"))),
                }
            )
    return {
        "fetched_on": info.fetched_on,
        "purchase_status": info.purchase_status,
        "redemption_status": info.redemption_status,
        "min_purchase_amount": info.min_purchase_amount,
        "daily_purchase_limit": info.daily_purchase_limit,
        "purchase_confirm_days": info.purchase_confirm_days,
        "redemption_confirm_days": info.redemption_confirm_days,
        "purchase_rate_original": None if not info.purchase_fees or info.purchase_fees[0].rate is None else str(info.purchase_fees[0].rate),
        "purchase_rate_discounted": None if purchase is None else str(purchase),
        "redemption_fees": [
            {"period_text": tier.period_text, "min_days": tier.min_days, "max_days": tier.max_days, "rate": str(tier.rate)}
            for tier in info.redemption_fees
        ],
        "fee_free_from_days": fee_free_from_days(info),
        "ongoing_fee_rate": None if ongoing is None else str(ongoing),
        "round_trips": round_trips,
        "fee_drag": drag,
        "source_urls": list(info.source_urls),
        "parse_issues": list(info.parse_issues),
    }


def manager_tenure(
    terms: tuple[FundManagerTerm, ...],
    nav_points: list[tuple[str, float]],
    *,
    as_of_date: str,
) -> dict[str, object] | None:
    """Performance under the lineup in charge on ``as_of_date`` and each current manager's continuous run."""

    ordered = sorted((term for term in terms if term.start_date <= as_of_date), key=lambda t: t.start_date, reverse=True)
    if not ordered or (ordered[0].end_date is not None and ordered[0].end_date < as_of_date):
        return None
    current = ordered[0]
    periods = [("最近一次经理变动以来", current.start_date)]
    for name in current.managers:
        start = current.start_date
        for newer, older in zip(ordered, ordered[1:]):
            if name not in older.managers or older.end_date is None:
                break
            gap = (date.fromisoformat(newer.start_date) - date.fromisoformat(older.end_date)).days
            if gap > _CONTINUITY_DAYS:
                break
            start = older.start_date
        if start < current.start_date:
            periods.append((f"{name} 连续管理", start))
    results = []
    for label, start in periods:
        window = [(day, value) for day, value in nav_points if start <= day <= as_of_date and value > 0]
        if len(window) < 2:
            continue
        first, last = window[0], window[-1]
        years = (date.fromisoformat(last[0]) - date.fromisoformat(first[0])).days / 365.25
        total = last[1] / first[1] - 1
        peak, drawdown = window[0][1], 0.0
        for _, value in window:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1)
        results.append(
            {
                "label": label,
                "start_date": start,
                "first_nav_date": first[0],
                "end_date": last[0],
                "years": round(years, 2),
                "total_return": round(total, 6),
                "annualized_return": round((1 + total) ** (1 / years) - 1, 6) if years >= 1 else None,
                "max_drawdown": round(drawdown, 6),
            }
        )
    return {
        "current_managers": list(current.managers),
        "lineup_start": current.start_date,
        "periods": results,
    }
