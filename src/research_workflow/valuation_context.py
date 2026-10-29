"""Implied expectations for stock research runs; failures become non-blocking gaps."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.financial_data import FinancialResearchBundle
from src.fundamental_analysis import (
    DividendHistoryService,
    FundamentalMetricResult,
    FundamentalMetricSet,
    ValuationModelService,
    dividends_by_year,
    metric_history,
    trailing_dividends,
)
from src.master_data import Asset
from src.research_contracts import ResearchTaskSpec

from .execution_models import ResearchExecutionGap, ResearchExecutionOptions


def collect_dividends(
    service: DividendHistoryService | None,
    *,
    asset: Asset,
    as_of_date: str,
    price: Decimal | None,
    config: ResearchExecutionOptions,
) -> tuple[dict[str, Any] | None, tuple[ResearchExecutionGap, ...]]:
    """Trailing cash dividends from implemented distributions; design notes in 估值与财务分析模块.md「股息率口径」."""

    if service is None:
        return None, ()
    events, error = service.events(
        asset.standard_symbol,
        allow_remote=config.allow_remote,
        timeout_seconds=config.provider_timeout_seconds,
    )
    if events is None:
        return None, (
            ResearchExecutionGap(
                "fundamental_analysis",
                "dividend_history_unavailable",
                f"分红记录不可用，股息率未计算：{error or '来源没有返回数据'}",
                False,
            ),
        )
    ttm, paid = trailing_dividends(events, as_of_date)
    by_year = dividends_by_year(events, as_of_date)
    result = {
        "ttm_per_share": str(ttm),
        "ttm_events": [item.to_dict() for item in paid],
        "by_year": {year: str(value) for year, value in list(by_year.items())[-5:]},
        "price": None if price is None else str(price),
        "ttm_yield": None if not price else str((ttm / price).quantize(Decimal("0.0001"))),
        "source": "新浪财经分红记录（已实施方案，派息按除息日归属）",
    }
    gaps = ()
    if error:
        gaps = (
            ResearchExecutionGap("fundamental_analysis", "dividend_history_stale", f"分红记录刷新失败，使用缓存：{error}", False),
        )
    return result, gaps


def collect_valuation_model(
    service: ValuationModelService | None,
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    fundamental: FundamentalMetricSet | None,
    config: ResearchExecutionOptions,
) -> tuple[dict[str, Any] | None, tuple[ResearchExecutionGap, ...]]:
    if service is None or fundamental is None:
        return None, ()
    try:
        return (
            service.evaluate_company(
                snapshot=fundamental.snapshot,
                asset=asset,
                profile_id=task.profile_id,
                as_of_date=task.as_of_date,
                allow_remote=config.allow_remote,
            ),
            (),
        )
    except Exception as exc:  # noqa: BLE001 - reported as a gap
        return None, (_gap(exc),)


def collect_bank_valuation_model(
    service: ValuationModelService | None,
    *,
    as_of_date: str,
    metrics: tuple[FundamentalMetricResult, ...],
    indicator_records: list[dict[str, Any]],
    allow_remote: bool,
    **overrides: float | None,
) -> tuple[dict[str, Any] | None, tuple[ResearchExecutionGap, ...]]:
    by_name = {item.metric_name: item for item in metrics}
    if service is None or "pb" not in by_name or by_name["pb"].value is None:
        return None, ()
    report_period = by_name["pb"].report_period
    latest: dict[str, tuple[str, float]] = {}
    for record in indicator_records:
        period = str(record.get("report_period") or "")
        published = str(record.get("published_at") or "")
        value = record.get("normalized_value")
        if (
            record.get("field_name") == "roe"
            and value is not None
            and published
            and published <= as_of_date
            and (period.endswith("-12-31") or period == report_period)
            and published >= latest.get(period, ("", 0.0))[0]
        ):
            latest[period] = (published, float(value))
    history = [(period, value) for period, (_, value) in sorted(latest.items()) if period.endswith("-12-31")]
    current = latest.get(report_period)
    roe = None
    if current is not None:
        months = int(report_period[5:7])
        roe = current[1] * 12 / months
    try:
        result = service.evaluate_bank(
            pb=float(by_name["pb"].value),
            roe=roe,
            roe_history=history,
            as_of_date=as_of_date,
            report_period=report_period,
            allow_remote=allow_remote,
            **overrides,
        )
    except Exception as exc:  # noqa: BLE001 - reported as a gap
        return None, (_gap(exc),)
    if current is not None and not report_period.endswith("-12-31"):
        result["notes"].append(f"最新 ROE 为 {report_period} 累计值按 12/{int(report_period[5:7])} 年化，季节性会让年化值偏离全年")
    return result, ()


GROWTH_TREND_METRICS = ("revenue_growth", "gross_margin", "rd_intensity", "cash_runway_years")
GROWTH_TREND_PERIODS = 8


def collect_growth_trend(financials: FinancialResearchBundle) -> dict[str, Any]:
    """Last report periods of growth, margin, R&D intensity and runway, each as first published."""

    series = {metric: {item.period_end: item for item in metric_history(financials, metric)} for metric in GROWTH_TREND_METRICS}
    periods = sorted({period for values in series.values() for period in values})[-GROWTH_TREND_PERIODS:]
    return {
        "metrics": list(GROWTH_TREND_METRICS),
        "periods": [
            {
                "period_end": period,
                **{
                    metric: (None if period not in series[metric] else str(series[metric][period].value))
                    for metric in GROWTH_TREND_METRICS
                },
            }
            for period in periods
        ],
        "note": "每期按该期首次披露时可见的财报计算，比率与增速为 TTM 口径",
    }


def _gap(exc: Exception) -> ResearchExecutionGap:
    return ResearchExecutionGap(
        "fundamental_analysis",
        "valuation_model_unavailable",
        f"{type(exc).__name__}: {exc}",
        False,
    )
