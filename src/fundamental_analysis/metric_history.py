"""Per-report-period company metrics as first published, for thesis tracking and settlement.

Design notes: docs/modules/3-分析工具/估值与财务分析模块.md, “按期财务指标序列”.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.financial_data import (
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FinancialResearchBundle,
)

from .assembler import FundamentalSnapshotAssembler, FundamentalSnapshotError
from .calculator import FundamentalMetricCalculator
from .models import MetricStatus

# Metrics that do not depend on the share price, so a report period has one value.
COMPUTED_METRICS: dict[str, str] = {
    "roe": "ROE（TTM，平均权益）",
    "roe_recurring": "扣非 ROE（TTM）",
    "roa": "ROA（TTM）",
    "roic": "ROIC（TTM）",
    "gross_margin": "毛利率（TTM）",
    "operating_margin": "营业利润率（TTM）",
    "net_margin": "净利率（TTM）",
    "non_recurring_share": "非经常性损益占归母净利润（TTM）",
    "revenue_growth": "营收同比（TTM）",
    "net_profit_growth": "归母净利润同比（TTM）",
    "recurring_profit_growth": "扣非净利润同比（TTM）",
    "operating_cash_flow_growth": "经营现金流同比（TTM）",
    "free_cash_flow_growth": "自由现金流同比（TTM）",
    "debt_to_assets": "资产负债率",
    "interest_coverage": "利息保障倍数（TTM）",
    "rd_intensity": "研发费用率（TTM）",
    "cash_runway_years": "现金跑道（年，货币资金 ÷ TTM 自由现金流出）",
}
SNAPSHOT_FIELDS: dict[str, str] = {
    "revenue": "营业收入（TTM，元）",
    "earnings": "归母净利润（TTM，元）",
    "recurring_earnings": "扣非归母净利润（TTM，元）",
    "operating_cash_flow": "经营现金流（TTM，元）",
    "free_cash_flow": "自由现金流（TTM，元）",
}
# Raw provider indicators (e.g. bank capital ratios), reported as disclosed: often year-to-date.
INDICATOR_PREFIX = "indicator."


@dataclass(frozen=True)
class MetricObservation:
    period_end: str
    available_at: str
    value: Decimal


def metric_label(metric: str) -> str | None:
    if metric.startswith(INDICATOR_PREFIX) and len(metric) > len(INDICATOR_PREFIX):
        return f"披露指标 {metric[len(INDICATOR_PREFIX):]}（按披露口径，常为年初累计）"
    return COMPUTED_METRICS.get(metric) or SNAPSHOT_FIELDS.get(metric)


def format_metric_value(metric: str, value: Decimal) -> str:
    if metric in SNAPSHOT_FIELDS:
        return f"{value / Decimal(100_000_000):.2f} 亿元"
    if metric == "interest_coverage":
        return f"{value:.2f} 倍"
    if metric == "cash_runway_years":
        return f"{value:.1f} 年"
    if metric.startswith(INDICATOR_PREFIX):
        name = metric[len(INDICATOR_PREFIX):]
        if name in _RATIO_INDICATORS or name.endswith(("_ratio", "_margin", "_rate", "_coverage")):
            return f"{value * 100:.2f}%"
        return format(value, "f")
    return f"{value * 100:.2f}%"


# Disclosed indicators normalized to ratio units; anything else (per-share amounts) is shown as disclosed.
_RATIO_INDICATORS = frozenset({"roe", "roa", "capital_adequacy", "core_tier1_capital_adequacy"})
# Balance-sheet style disclosures measured at period end, so any quarter compares with any other.
_PERIOD_END_INDICATORS = frozenset(
    {
        "non_performing_loan_ratio",
        "capital_adequacy",
        "core_tier1_capital_adequacy",
        "provision_coverage",
        "book_value_per_share",
    }
)


def is_period_end_indicator(metric: str) -> bool:
    return metric.startswith(INDICATOR_PREFIX) and metric[len(INDICATOR_PREFIX):] in _PERIOD_END_INDICATORS


def metric_history(bundle: FinancialResearchBundle, metric: str) -> tuple[MetricObservation, ...]:
    """One value per report period, computed from the data published when that period first came out."""

    if metric_label(metric) is None:
        raise ValueError(f"不支持的财务指标 {metric}")
    if metric.startswith(INDICATOR_PREFIX):
        return _indicator_history(bundle, metric[len(INDICATOR_PREFIX):])
    first_published: dict[str, str] = {}
    for record in bundle.records_by_dataset.get(DATASET_STOCK_INCOME_STATEMENTS, ()):
        period = str(record.get("report_period") or "")
        published = str(record.get("published_at") or "")
        if period and published and published <= bundle.as_of_date:
            first_published[period] = min(published, first_published.get(period, published))
    assembler = FundamentalSnapshotAssembler()
    calculator = FundamentalMetricCalculator()
    observations = []
    for period in sorted(first_published):
        published = first_published[period]
        try:
            snapshot = assembler.assemble(
                bundle.filtered_as_of(published),
                market_price=Decimal("1"),
                target_report_period=period,
            )
        except FundamentalSnapshotError:
            continue
        if metric in SNAPSHOT_FIELDS:
            value = snapshot.fields.get(metric)
        else:
            result = calculator.calculate(snapshot).by_name().get(metric)
            value = result.value if result is not None and result.status is MetricStatus.AVAILABLE else None
        if value is not None:
            observations.append(MetricObservation(period, published, value))
    return tuple(observations)


def _indicator_history(bundle: FinancialResearchBundle, field_name: str) -> tuple[MetricObservation, ...]:
    first: dict[str, tuple[str, int, Decimal]] = {}
    for record in bundle.records_by_dataset.get(DATASET_STOCK_FINANCIAL_INDICATORS, ()):
        period = str(record.get("report_period") or "")
        published = str(record.get("published_at") or "")
        value = record.get("normalized_value")
        if (
            record.get("field_name") != field_name
            or not period
            or not published
            or published > bundle.as_of_date
            or value is None
        ):
            continue
        candidate = (published, int(record.get("revision") or 1), Decimal(str(value)))
        if period not in first or candidate[:2] < first[period][:2]:
            first[period] = candidate
    return tuple(
        MetricObservation(period, published, value)
        for period, (published, _, value) in sorted(first.items())
    )
