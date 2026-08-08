"""Static IDE research chart contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any


CHART_VERSION = "research_chart_v1"


class ChartType(str, Enum):
    PRICE_VOLUME = "price_volume"
    CUMULATIVE_RETURN = "cumulative_return"
    DRAWDOWN = "drawdown"
    ROLLING_RISK = "rolling_risk"
    VALUATION_HISTORY = "valuation_history"
    FINANCIAL_TRENDS = "financial_trends"


@dataclass(frozen=True)
class ChartDataPoint:
    date: str
    values: dict[str, float | None]

    def __post_init__(self) -> None:
        date.fromisoformat(self.date)
        if not self.values:
            raise ValueError("ChartDataPoint values must not be empty")


@dataclass(frozen=True)
class ChartSpec:
    task_id: str
    asset_id: str
    asset_name: str
    chart_type: ChartType
    title: str
    as_of_date: str
    x_label: str
    y_label: str
    points: tuple[ChartDataPoint, ...]
    source_datasets: tuple[str, ...]
    unit: str
    currency: str | None = None
    price_adjustment: str | None = None
    benchmark_asset_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    version: str = CHART_VERSION

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.task_id, "task_id"),
            (self.asset_id, "asset_id"),
            (self.asset_name, "asset_name"),
            (self.title, "title"),
            (self.x_label, "x_label"),
            (self.y_label, "y_label"),
            (self.unit, "unit"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be blank")
        date.fromisoformat(self.as_of_date)
        if not self.points:
            raise ValueError("ChartSpec points must not be empty")
        if not self.source_datasets:
            raise ValueError(
                "source_datasets must not be empty"
            )
        point_dates = [point.date for point in self.points]
        if point_dates != sorted(point_dates):
            raise ValueError("Chart points must be date-sorted")
        if len(set(point_dates)) != len(point_dates):
            raise ValueError(
                "Chart points must not contain duplicate dates"
            )
        if any(
            point.date > self.as_of_date
            for point in self.points
        ):
            raise ValueError(
                "Chart data must not exceed as_of_date"
            )

    @property
    def safe_task_id(self) -> str:
        value = re.sub(r"[^A-Za-z0-9_.-]+", "-", self.task_id)
        value = value.strip("-.")
        if not value:
            raise ValueError("task_id has no safe path characters")
        return value


@dataclass(frozen=True)
class ChartArtifact:
    chart_type: ChartType
    input_digest: str
    svg_path: Path
    png_path: Path
    data_path: Path
    metadata_path: Path
