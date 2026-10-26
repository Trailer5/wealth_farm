"""Build chart specifications from research-domain records."""

from __future__ import annotations

from collections import defaultdict

from src.factor_engineering import FactorValue
from src.fundamental_analysis import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalMetricResult,
    FundamentalSnapshot,
    MetricStatus,
)
from src.master_data import Asset
from src.research_contracts import (
    PROFILE_EXCHANGE_FUND,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchTaskSpec,
)

from .models import ChartDataPoint, ChartSpec, ChartType


def chart_types_for_profile(
    profile_id: str,
) -> tuple[ChartType, ...]:
    if profile_id == PROFILE_ORDINARY_COMPANY:
        return (
            ChartType.PRICE_VOLUME,
            ChartType.CUMULATIVE_RETURN,
            ChartType.DRAWDOWN,
            ChartType.ROLLING_RISK,
            ChartType.VALUATION_HISTORY,
            ChartType.FINANCIAL_TRENDS,
        )
    if profile_id == PROFILE_EXCHANGE_FUND:
        return (
            ChartType.PRICE_VOLUME,
            ChartType.CUMULATIVE_RETURN,
            ChartType.DRAWDOWN,
            ChartType.ROLLING_RISK,
        )
    if profile_id == PROFILE_OPEN_FUND:
        return (
            ChartType.CUMULATIVE_RETURN,
            ChartType.DRAWDOWN,
            ChartType.ROLLING_RISK,
        )
    return (
        ChartType.PRICE_VOLUME,
        ChartType.CUMULATIVE_RETURN,
        ChartType.DRAWDOWN,
        ChartType.ROLLING_RISK,
    )


def build_price_volume_spec(
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    records: list[dict[str, object]],
    source_dataset: str,
    price_adjustment: str,
    moving_averages: tuple[int, ...] = (5, 20),
) -> ChartSpec:
    points = []
    for record in sorted(
        records,
        key=lambda item: str(item.get("trade_date", "")),
    ):
        trade_date = str(record.get("trade_date", ""))
        if not trade_date or trade_date > task.as_of_date:
            continue
        points.append(
            ChartDataPoint(
                date=trade_date,
                values={
                    field_name: _float_or_none(
                        record.get(field_name)
                    )
                    for field_name in (
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                    )
                },
            )
        )
    return ChartSpec(
        task_id=task.task_id,
        asset_id=asset.asset_id,
        asset_name=asset.display_name or asset.standard_symbol,
        chart_type=ChartType.PRICE_VOLUME,
        title="Price, Volume, and Moving Averages",
        as_of_date=task.as_of_date,
        x_label="Trade date",
        y_label="Price",
        points=tuple(points),
        source_datasets=(source_dataset,),
        unit="price",
        currency="CNY",
        price_adjustment=price_adjustment,
        parameters={
            "moving_averages": list(moving_averages),
        },
    )


def build_factor_series_spec(
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    chart_type: ChartType,
    values: list[FactorValue],
    factor_names: tuple[str, ...],
    source_dataset: str,
    unit: str = "ratio",
    benchmark_asset_id: str | None = None,
) -> ChartSpec:
    if chart_type not in {
        ChartType.CUMULATIVE_RETURN,
        ChartType.DRAWDOWN,
        ChartType.ROLLING_RISK,
    }:
        raise ValueError(
            f"Unsupported factor chart type: {chart_type.value}"
        )
    by_date: dict[str, dict[str, float | None]] = defaultdict(dict)
    aliases = {"drawdown_series": "drawdown"}
    for value in values:
        if (
            value.factor_name not in factor_names
            or value.as_of_date > task.as_of_date
        ):
            continue
        by_date[value.as_of_date][
            aliases.get(value.factor_name, value.factor_name)
        ] = value.value
    title_by_type = {
        ChartType.CUMULATIVE_RETURN: "Cumulative Return",
        ChartType.DRAWDOWN: "Drawdown",
        ChartType.ROLLING_RISK: "Rolling Risk Metrics",
    }
    ordered_dates = sorted(by_date)
    if not ordered_dates:
        raise ValueError("Factor chart has no matching observations")
    input_start_date = ordered_dates[0]
    input_end_date = ordered_dates[-1]
    title = (
        f"{title_by_type[chart_type]} "
        f"({input_start_date} to {input_end_date})"
    )
    return ChartSpec(
        task_id=task.task_id,
        asset_id=asset.asset_id,
        asset_name=asset.display_name or asset.standard_symbol,
        chart_type=chart_type,
        title=title,
        as_of_date=task.as_of_date,
        x_label="Date",
        y_label=title_by_type[chart_type],
        points=tuple(
            ChartDataPoint(date=trade_date, values=by_date[trade_date])
            for trade_date in ordered_dates
        ),
        source_datasets=(source_dataset,),
        unit=unit,
        benchmark_asset_id=benchmark_asset_id,
        parameters={
            "factor_names": list(factor_names),
            "input_start_date": input_start_date,
            "input_end_date": input_end_date,
        },
    )


def build_valuation_history_spec(
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    results: list[FundamentalMetricResult],
    metric_names: tuple[str, ...] = (
        "pe_ttm",
        "pb",
        "ps_ttm",
    ),
) -> ChartSpec:
    by_date: dict[str, dict[str, float | None]] = defaultdict(dict)
    versions: set[str] = set()
    for result in results:
        if (
            result.metric_name not in metric_names
            or result.as_of_date > task.as_of_date
            or result.status is not MetricStatus.AVAILABLE
            or result.value is None
        ):
            continue
        by_date[result.as_of_date][result.metric_name] = float(
            result.value
        )
        versions.add(result.calculation_version)
    statistics = {}
    for metric_name in metric_names:
        observations = [
            (value_date, values[metric_name])
            for value_date, values in sorted(by_date.items())
            if metric_name in values
            and values[metric_name] is not None
        ]
        if observations:
            current_date, current_value = observations[-1]
            series_values = [value for _, value in observations]
            statistics[metric_name] = {
                "minimum": min(series_values),
                "maximum": max(series_values),
                "current": current_value,
                "current_date": current_date,
                "current_percentile": (
                    sum(
                        value <= current_value
                        for value in series_values
                    )
                    / len(series_values)
                ),
                "observation_count": len(series_values),
            }
    return ChartSpec(
        task_id=task.task_id,
        asset_id=asset.asset_id,
        asset_name=asset.display_name or asset.standard_symbol,
        chart_type=ChartType.VALUATION_HISTORY,
        title="Valuation History",
        as_of_date=task.as_of_date,
        x_label="As-of date",
        y_label="Valuation multiple",
        points=tuple(
            ChartDataPoint(date=value_date, values=by_date[value_date])
            for value_date in sorted(by_date)
        ),
        source_datasets=tuple(sorted(versions))
        or (FUNDAMENTAL_CALCULATION_VERSION,),
        unit="multiple",
        currency="CNY",
        parameters={
            "metric_names": list(metric_names),
            "history_statistics": statistics,
        },
    )


def build_financial_trends_spec(
    *,
    task: ResearchTaskSpec,
    asset: Asset,
    snapshots: list[FundamentalSnapshot],
    metric_results: list[FundamentalMetricResult] | None = None,
    field_names: tuple[str, ...] = (
        "revenue",
        "earnings",
        "operating_cash_flow",
        "free_cash_flow",
        "gross_margin",
        "operating_margin",
        "net_margin",
        "roe",
        "roic",
    ),
) -> ChartSpec:
    metrics_by_period: dict[str, dict[str, float]] = defaultdict(dict)
    for result in metric_results or []:
        if (
            result.status is MetricStatus.AVAILABLE
            and result.value is not None
            and result.as_of_date <= task.as_of_date
        ):
            metrics_by_period[result.report_period][
                result.metric_name
            ] = float(result.value)
    points = []
    for snapshot in sorted(
        snapshots,
        key=lambda item: item.report_period,
    ):
        if snapshot.as_of_date > task.as_of_date:
            continue
        values = {
            field_name: (
                float(snapshot.fields[field_name])
                if field_name in snapshot.fields
                else metrics_by_period.get(
                    snapshot.report_period,
                    {},
                ).get(field_name)
            )
            for field_name in field_names
        }
        if any(value is not None for value in values.values()):
            points.append(
                ChartDataPoint(
                    date=snapshot.report_period,
                    values=values,
                )
            )
    return ChartSpec(
        task_id=task.task_id,
        asset_id=asset.asset_id,
        asset_name=asset.display_name or asset.standard_symbol,
        chart_type=ChartType.FINANCIAL_TRENDS,
        title="Financial Trends",
        as_of_date=task.as_of_date,
        x_label="Report period",
        y_label="Financial value",
        points=tuple(points),
        source_datasets=("financial_research_bundle",),
        unit="mixed",
        currency="CNY",
        parameters={"field_names": list(field_names)},
    )


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
