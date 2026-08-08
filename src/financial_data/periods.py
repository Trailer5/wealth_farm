"""Financial reporting-period helpers."""

from __future__ import annotations

from datetime import date

from .models import (
    DATASET_STOCK_INCOME_STATEMENTS,
    FinancialPeriodKind,
    FinancialPeriodSummary,
    FinancialResearchBundle,
    TTMInputSet,
)


def infer_period_kind(report_period: str) -> FinancialPeriodKind:
    parsed = date.fromisoformat(report_period)
    month_day = (parsed.month, parsed.day)
    mapping = {
        (3, 31): FinancialPeriodKind.Q1,
        (6, 30): FinancialPeriodKind.HALF_YEAR,
        (9, 30): FinancialPeriodKind.Q3,
        (12, 31): FinancialPeriodKind.ANNUAL,
    }
    kind = mapping.get(month_day)
    if kind is None:
        raise ValueError(
            f"Unsupported financial report period: {report_period}"
        )
    return kind


def ttm_input_periods(report_period: str) -> TTMInputSet:
    parsed = date.fromisoformat(report_period)
    kind = infer_period_kind(report_period)
    if kind is FinancialPeriodKind.ANNUAL:
        return TTMInputSet(
            target_period=report_period,
            current_cumulative_period=report_period,
            previous_annual_period=report_period,
            prior_comparable_period=None,
        )
    return TTMInputSet(
        target_period=report_period,
        current_cumulative_period=report_period,
        previous_annual_period=(
            date(parsed.year - 1, 12, 31).isoformat()
        ),
        prior_comparable_period=(
            date(
                parsed.year - 1,
                parsed.month,
                parsed.day,
            ).isoformat()
        ),
    )


def summarize_financial_periods(
    bundle: FinancialResearchBundle,
) -> FinancialPeriodSummary:
    periods = tuple(
        sorted(
            {
                str(record["report_period"])
                for record in bundle.records_by_dataset.get(
                    DATASET_STOCK_INCOME_STATEMENTS,
                    (),
                )
                if str(record.get("published_at") or "")
                <= bundle.as_of_date
            }
        )
    )
    annual = [
        period
        for period in periods
        if infer_period_kind(period)
        is FinancialPeriodKind.ANNUAL
    ]
    interim = [
        period
        for period in periods
        if infer_period_kind(period)
        is not FinancialPeriodKind.ANNUAL
    ]
    latest = periods[-1] if periods else None
    return FinancialPeriodSummary(
        available_periods=periods,
        latest_annual_period=(annual[-1] if annual else None),
        latest_interim_period=(
            interim[-1] if interim else None
        ),
        latest_ttm_inputs=(
            ttm_input_periods(latest) if latest else None
        ),
    )
