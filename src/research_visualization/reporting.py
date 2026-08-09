"""Reusable publishing infrastructure for user-facing research reports."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from .models import ChartArtifact, ChartSpec
from .renderer import ChartRenderer


@dataclass(frozen=True)
class ReportChartArtifact:
    key: str
    artifact: ChartArtifact
    markdown_path: str


@dataclass(frozen=True)
class PublishedReport:
    report_path: Path
    charts: tuple[ReportChartArtifact, ...]


class ReportArtifactService:
    """Write user reports to report/ while retaining chart audit artifacts."""

    def __init__(self, report_root: str | Path = "report") -> None:
        self.report_root = Path(report_root)
        self.chart_renderer = ChartRenderer(
            self.report_root / "assets"
        )

    def render_charts(
        self,
        report_slug: str,
        specs: Mapping[str, ChartSpec],
    ) -> tuple[ReportChartArtifact, ...]:
        safe_slug = _safe_name(report_slug, "report_slug")
        if not specs:
            raise ValueError("specs must not be empty")
        results = []
        for key in sorted(specs):
            safe_key = _safe_name(key, "chart key")
            spec = replace(
                specs[key],
                task_id=f"{safe_slug}-{safe_key}",
            )
            artifact = self.chart_renderer.render(spec)
            markdown_path = artifact.png_path.relative_to(
                self.report_root
            ).as_posix()
            results.append(
                ReportChartArtifact(
                    key=key,
                    artifact=artifact,
                    markdown_path=markdown_path,
                )
            )
        return tuple(results)

    def publish_markdown(
        self,
        *,
        filename: str,
        content: str,
        charts: tuple[ReportChartArtifact, ...] = (),
    ) -> PublishedReport:
        safe_filename = _safe_markdown_filename(filename)
        if not content.strip():
            raise ValueError("content must not be blank")
        self.report_root.mkdir(parents=True, exist_ok=True)
        report_path = self.report_root / safe_filename
        report_path.write_text(
            content.rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return PublishedReport(
            report_path=report_path,
            charts=tuple(charts),
        )

    def publish_chart_alias(
        self,
        chart: ReportChartArtifact,
        *,
        filename: str,
    ) -> Path:
        safe_filename = _safe_image_filename(filename)
        self.report_root.mkdir(parents=True, exist_ok=True)
        output_path = self.report_root / safe_filename
        shutil.copyfile(chart.artifact.png_path, output_path)
        return output_path


def _safe_name(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    safe = safe.strip("-.")
    if not safe or safe != value:
        raise ValueError(
            f"{field_name} must be a safe ASCII slug"
        )
    return safe


def _safe_markdown_filename(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("filename must not be blank")
    path = Path(value)
    if (
        path.name != value
        or path.suffix.lower() != ".md"
        or value in {".md", "..md"}
    ):
        raise ValueError(
            "filename must be one Markdown filename without directories"
        )
    return value


def _safe_image_filename(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("filename must not be blank")
    path = Path(value)
    if path.name != value or path.suffix.lower() != ".png":
        raise ValueError(
            "filename must be one PNG filename without directories"
        )
    return value
