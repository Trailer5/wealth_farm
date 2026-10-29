"""Static IDE research chart generation."""

from .builders import (
    build_factor_series_spec,
    build_financial_trends_spec,
    build_price_volume_spec,
    build_valuation_history_spec,
    chart_types_for_profile,
)
from .models import (
    CHART_VERSION,
    ChartArtifact,
    ChartDataPoint,
    ChartSpec,
    ChartType,
)
from .renderer import ChartRenderer
from .plan_html import check_plan_html, render_plan_html
from .reporting import (
    PublishedReport,
    ReportArtifactService,
    ReportChartArtifact,
)

__all__ = [
    "CHART_VERSION",
    "ChartArtifact",
    "ChartDataPoint",
    "ChartRenderer",
    "ChartSpec",
    "ChartType",
    "PublishedReport",
    "ReportArtifactService",
    "ReportChartArtifact",
    "build_factor_series_spec",
    "build_financial_trends_spec",
    "build_price_volume_spec",
    "build_valuation_history_spec",
    "chart_types_for_profile",
    "check_plan_html",
    "render_plan_html",
]
