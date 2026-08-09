"""Generate user-facing charts for the current SSE market report."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
AS_OF_DATE = "2026-08-07"


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.research_visualization import (
        ChartDataPoint,
        ChartSpec,
        ChartType,
        ReportArtifactService,
    )

    report_service = ReportArtifactService(
        PROJECT_ROOT / "report"
    )

    raw_bars = _fetch_tencent_index_chunks(
        TencentExchangeFundDataSource(),
        "2024-01-01",
        AS_OF_DATE,
    )
    bars = [
        item
        for item in raw_bars
        if _valid_ohlc(item)
    ]
    price_spec = ChartSpec(
        task_id="sse-current-report-2026-08-07",
        asset_id="index:cn_a:sh:000001",
        asset_name="上证指数",
        chart_type=ChartType.PRICE_VOLUME,
        title="上证指数日K线、成交量与均线",
        as_of_date=AS_OF_DATE,
        x_label="交易日期",
        y_label="指数点位",
        points=tuple(
            ChartDataPoint(
                date=item.trade_date,
                values={
                    "open": item.open,
                    "high": item.high,
                    "low": item.low,
                    "close": item.close,
                    "volume": item.volume,
                },
            )
            for item in bars
        ),
        source_datasets=("腾讯财经",),
        unit="点",
        parameters={
            "moving_averages": [60, 200],
            "volume_label": "成交量",
            "as_of_label": "截止",
            "source_label": "来源",
        },
    )

    provider = AkShareDataSource()
    pe_records = provider.get_shanghai_market_pe_history()
    pe_spec = ChartSpec(
        task_id="sse-current-report-2026-08-07",
        asset_id="index:cn_a:sh:000001",
        asset_name="上证指数",
        chart_type=ChartType.VALUATION_HISTORY,
        title="上证市场平均市盈率历史",
        as_of_date=AS_OF_DATE,
        x_label="日期",
        y_label="平均市盈率",
        points=tuple(
            ChartDataPoint(
                date=str(item["trade_date"]),
                values={"平均PE": float(item["market_pe"])},
            )
            for item in pe_records
            if str(item["trade_date"]) <= AS_OF_DATE
        ),
        source_datasets=("AkShare / 乐咕乐股",),
        unit="倍",
        parameters={
            "as_of_label": "截止",
            "source_label": "来源",
        },
    )

    percentile_points = _rolling_pe_percentiles(
        pe_records,
        AS_OF_DATE,
    )
    percentile_spec = ChartSpec(
        task_id="sse-current-report-2026-08-07",
        asset_id="index:cn_a:sh:000001",
        asset_name="上证指数",
        chart_type=ChartType.VALUATION_HISTORY,
        title="上证市场PE四年滚动分位",
        as_of_date=AS_OF_DATE,
        x_label="日期",
        y_label="四年分位",
        points=tuple(
            ChartDataPoint(
                date=trade_date,
                values={"PE四年分位": percentile},
            )
            for trade_date, percentile in percentile_points
        ),
        source_datasets=("AkShare / 乐咕乐股",),
        unit="%",
        parameters={
            "lookback_calendar_days": 1461,
            "minimum_observations": 48,
            "as_of_label": "截止",
            "source_label": "来源",
        },
    )

    yield_records = provider.get_china_ten_year_yield_history(
        "1999-01-01"
    )
    yield_by_date = {
        str(item["trade_date"]): float(
            item["ten_year_yield_percent"]
        )
        for item in yield_records
    }
    erp_points = tuple(
        ChartDataPoint(
            date=str(item["trade_date"]),
            values={
                "股权风险溢价": (
                    100.0 / float(item["market_pe"])
                    - yield_by_date[str(item["trade_date"])]
                )
            },
        )
        for item in pe_records
        if str(item["trade_date"]) in yield_by_date
        and str(item["trade_date"]) <= AS_OF_DATE
    )
    erp_spec = ChartSpec(
        task_id="sse-current-report-2026-08-07",
        asset_id="index:cn_a:sh:000001",
        asset_name="上证指数",
        chart_type=ChartType.VALUATION_HISTORY,
        title="上证市场简化股权风险溢价",
        as_of_date=AS_OF_DATE,
        x_label="日期",
        y_label="盈利收益率减10年国债收益率",
        points=erp_points,
        source_datasets=(
            "AkShare / 乐咕乐股",
            "AkShare / 东方财富",
        ),
        unit="%",
        parameters={
            "as_of_label": "截止",
            "source_label": "来源",
        },
    )

    charts = report_service.render_charts(
        "sse-current-report-2026-08-07",
        {
            "price": price_spec,
            "pe": pe_spec,
            "pe-percentile": percentile_spec,
            "erp": erp_spec,
        },
    )
    aliases = {
        "price": "上证指数K线.png",
        "pe": "上证市场PE历史.png",
        "pe-percentile": "上证市场PE四年分位.png",
        "erp": "上证市场股权风险溢价.png",
    }
    alias_paths = {
        chart.key: report_service.publish_chart_alias(
            chart,
            filename=aliases[chart.key],
        )
        for chart in charts
    }
    print(
        json.dumps(
            {
                chart.key: {
                    "user_png": str(
                        alias_paths[chart.key].relative_to(PROJECT_ROOT)
                    ),
                    "png": str(
                        chart.artifact.png_path.relative_to(PROJECT_ROOT)
                    ),
                    "svg": str(
                        chart.artifact.svg_path.relative_to(PROJECT_ROOT)
                    ),
                    "data": str(
                        chart.artifact.data_path.relative_to(PROJECT_ROOT)
                    ),
                    "metadata": str(
                        chart.artifact.metadata_path.relative_to(
                            PROJECT_ROOT
                        )
                    ),
                }
                for chart in charts
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _fetch_tencent_index_chunks(provider, start_date, end_date):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    by_date = {}
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, date(first_year, 1, 1))
        chunk_end = min(end, date(first_year + 1, 12, 31))
        for bar in provider.get_index_daily_bars(
            "000001",
            chunk_start.isoformat(),
            chunk_end.isoformat(),
            "sh",
        ):
            by_date[bar.trade_date] = bar
    return [by_date[trade_date] for trade_date in sorted(by_date)]


def _valid_ohlc(item) -> bool:
    if any(
        value is None
        for value in (item.open, item.high, item.low, item.close)
    ):
        return False
    return (
        item.low <= item.open <= item.high
        and item.low <= item.close <= item.high
    )


def _rolling_pe_percentiles(pe_records, as_of_date):
    values = [
        (
            date.fromisoformat(str(item["trade_date"])),
            float(item["market_pe"]),
        )
        for item in pe_records
        if str(item["trade_date"]) <= as_of_date
    ]
    result = []
    for current_date, current_value in values:
        start = current_date - timedelta(days=1461)
        window = [
            value
            for trade_date, value in values
            if start <= trade_date <= current_date
        ]
        if len(window) < 48:
            continue
        less = sum(value < current_value for value in window)
        equal = sum(value == current_value for value in window)
        percentile = (
            (less + (equal - 1) / 2) / (len(window) - 1) * 100
        )
        result.append((current_date.isoformat(), percentile))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
