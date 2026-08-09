"""Generate investment-committee charts from validated SSE research runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.research_visualization import (
        ChartDataPoint,
        ChartSpec,
        ChartType,
        ReportArtifactService,
    )

    common = {
        "task_id": "sse-investment-committee-2026-08-07",
        "asset_id": "index:cn_a:sh:000001",
        "asset_name": "上证指数研究",
        "as_of_date": "2026-08-07",
        "source_datasets": (
            "实际ETF含成本回测",
            "跨指数扩展窗口验证",
        ),
    }
    display_parameters = {
        "as_of_label": "截止",
        "source_label": "来源",
    }
    specs = {
        "etf-schemes": ChartSpec(
            **common,
            chart_type=ChartType.CATEGORICAL_BAR,
            title="实际ETF各仓位方案累计收益",
            x_label="方案",
            y_label="累计收益",
            unit="%",
            points=_points(
                (
                    {"510210": 107.10, "510760": 26.72},
                    {"510210": 67.48, "510760": 22.28},
                    {"510210": 36.05, "510760": -11.87},
                    {"510210": 9.60, "510760": -7.85},
                ),
                ChartDataPoint,
            ),
            parameters={
                **display_parameters,
                "category_labels": [
                    "买入持有",
                    "目标波动",
                    "回撤治理",
                    "择时倾斜",
                ],
            },
        ),
        "panel-oos": ChartSpec(
            **common,
            chart_type=ChartType.CATEGORICAL_BAR,
            title="跨指数中长期分数与未来一年收益相关性",
            x_label="指数",
            y_label="Spearman相关",
            unit="相关系数",
            points=_points(
                (
                    {
                        "连续全样本": 0.2819,
                        "样本外中位": 0.5352,
                        "回顾性留出": 0.7239,
                    },
                    {
                        "连续全样本": 0.3354,
                        "样本外中位": 0.6644,
                        "回顾性留出": 0.5942,
                    },
                    {
                        "连续全样本": 0.1383,
                        "样本外中位": 0.6171,
                        "回顾性留出": 0.2776,
                    },
                ),
                ChartDataPoint,
            ),
            parameters={
                **display_parameters,
                "category_labels": [
                    "上证50",
                    "沪深300",
                    "中证500",
                ],
            },
        ),
        "sse-quantiles": ChartSpec(
            **common,
            chart_type=ChartType.CATEGORICAL_BAR,
            title="上证中长期分数五档未来一年平均收益",
            x_label="风险分档",
            y_label="平均收益",
            unit="%",
            points=_points(
                (
                    {"未来一年平均收益": -10.10},
                    {"未来一年平均收益": -0.02},
                    {"未来一年平均收益": 4.19},
                    {"未来一年平均收益": 9.24},
                    {"未来一年平均收益": 17.55},
                ),
                ChartDataPoint,
            ),
            parameters={
                **display_parameters,
                "category_labels": [
                    "Q1最高风险",
                    "Q2",
                    "Q3",
                    "Q4",
                    "Q5最低风险",
                ],
            },
        ),
        "defensive-drawdown": ChartSpec(
            **common,
            chart_type=ChartType.CATEGORICAL_BAR,
            title="510760防御方案最大回撤",
            x_label="方案",
            y_label="最大回撤",
            unit="%",
            points=_points(
                (
                    {"最大回撤": -19.98},
                    {"最大回撤": -18.10},
                    {"最大回撤": -16.99},
                    {"最大回撤": -15.27},
                    {"最大回撤": -13.43},
                ),
                ChartDataPoint,
            ),
            parameters={
                **display_parameters,
                "category_labels": [
                    "买入持有",
                    "波动10%+现金",
                    "波动10%+短融",
                    "波动8%+短融",
                    "波动7%+短融",
                ],
            },
        ),
    }
    service = ReportArtifactService(PROJECT_ROOT / "report")
    charts = service.render_charts(
        "sse-investment-committee-2026-08-07",
        specs,
    )
    aliases = {
        "etf-schemes": "上证ETF方案收益对比.png",
        "panel-oos": "跨指数样本外相关.png",
        "sse-quantiles": "上证中长期五档收益.png",
        "defensive-drawdown": "510760防御方案回撤.png",
    }
    outputs = {
        chart.key: str(
            service.publish_chart_alias(
                chart,
                filename=aliases[chart.key],
            ).relative_to(PROJECT_ROOT)
        )
        for chart in charts
    }
    print(json.dumps(outputs, ensure_ascii=True, indent=2))
    return 0


def _points(values, point_type):
    return tuple(
        point_type(
            date=f"2026-01-{index:02d}",
            values=dict(item),
        )
        for index, item in enumerate(values, start=1)
    )


if __name__ == "__main__":
    raise SystemExit(main())
