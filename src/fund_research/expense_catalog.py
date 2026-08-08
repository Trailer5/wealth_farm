"""Reviewed official ETF expense schedules."""

from decimal import Decimal

from .models import ETFExpenseSchedule


def official_etf_expense_schedule(
    *,
    asset_id: str,
    standard_symbol: str,
) -> ETFExpenseSchedule:
    schedules = {
        "518880": {
            "effective_date": "2026-03-17",
            "management": "0.0050",
            "custody": "0.0010",
            "comprehensive": "0.0067",
            "source": "sse:fund_product_summary",
            "url": (
                "http://www.sse.com.cn/disclosure/fund/"
                "announcement/c/new/2026-03-17/"
                "518880_20260317_QR4M.pdf"
            ),
        },
        "510300": {
            "effective_date": "2024-11-22",
            "management": "0.0015",
            "custody": "0.0005",
            "comprehensive": "0.0020",
            "source": "sse:fund_fee_amendment",
            "url": (
                "https://www.sse.com.cn/disclosure/fund/"
                "announcement/c/new/2024-11-20/"
                "510300_20241120_FOBE.pdf"
            ),
        },
    }
    item = schedules.get(standard_symbol)
    if item is None:
        raise KeyError(
            f"No reviewed ETF expense schedule: {standard_symbol}"
        )
    return ETFExpenseSchedule(
        asset_id=asset_id,
        effective_date=item["effective_date"],
        management_fee_rate=Decimal(item["management"]),
        custody_fee_rate=Decimal(item["custody"]),
        comprehensive_expense_ratio=Decimal(
            item["comprehensive"]
        ),
        source=item["source"],
        source_url=item["url"],
    )
