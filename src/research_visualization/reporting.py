"""Reusable publishing infrastructure for user-facing research reports."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from pathlib import PurePosixPath
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
        if "完整决策报告" in safe_filename:
            raise ValueError(
                "final decision filenames require reviewed publication"
            )
        return self._write_markdown(
            filename=safe_filename,
            content=content,
            charts=charts,
        )

    def _publish_reviewed_final_markdown(
        self,
        *,
        filename: str,
        content: str,
    ) -> PublishedReport:
        """Private sink used only after final-report review passes."""
        safe_filename = _safe_markdown_filename(filename)
        if "完整决策报告" not in safe_filename:
            raise ValueError(
                "reviewed final publication requires final filename"
            )
        return self._write_markdown(
            filename=safe_filename,
            content=content,
            charts=(),
        )

    def _write_markdown(
        self,
        *,
        filename: str,
        content: str,
        charts: tuple[ReportChartArtifact, ...],
    ) -> PublishedReport:
        if not content.strip():
            raise ValueError("content must not be blank")
        self.report_root.mkdir(parents=True, exist_ok=True)
        published_charts = tuple(
            self._publish_chart(chart) for chart in charts
        )
        report_path = self.report_root / filename
        report_path.write_text(
            content.rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return PublishedReport(
            report_path=report_path,
            charts=published_charts,
        )

    def prepare_existing_charts(
        self,
        *,
        report_filename: str,
        artifacts: tuple[ChartArtifact, ...],
    ) -> tuple[ReportChartArtifact, ...]:
        safe_filename = _safe_markdown_filename(report_filename)
        report_slug = Path(safe_filename).stem
        counts: dict[str, int] = {}
        prepared = []
        for artifact in artifacts:
            base_key = artifact.chart_type.value
            counts[base_key] = counts.get(base_key, 0) + 1
            chart_key = (
                base_key
                if counts[base_key] == 1
                else f"{base_key}-{counts[base_key]}"
            )
            directory = PurePosixPath(
                "assets",
                f"{report_slug}-{chart_key}",
                "charts",
            )
            prepared.append(
                ReportChartArtifact(
                    key=chart_key,
                    artifact=artifact,
                    markdown_path=(directory / "chart.png").as_posix(),
                )
            )
        return tuple(prepared)

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

    def _publish_chart(
        self,
        chart: ReportChartArtifact,
    ) -> ReportChartArtifact:
        relative = PurePosixPath(chart.markdown_path)
        if (
            relative.is_absolute()
            or ":" in chart.markdown_path
            or ".." in relative.parts
            or relative.suffix.lower() != ".png"
        ):
            raise ValueError(
                "chart markdown_path must be a relative PNG path"
            )
        report_root = self.report_root.resolve()
        png_path = self.report_root.joinpath(
            *relative.parts
        ).resolve()
        try:
            png_path.relative_to(report_root)
        except ValueError as exc:
            raise ValueError(
                "chart markdown_path escapes report root"
            ) from exc
        if chart.artifact.png_path.resolve() == png_path.resolve():
            return chart
        chart_root = png_path.parent
        chart_root.mkdir(parents=True, exist_ok=True)
        published = ChartArtifact(
            chart_type=chart.artifact.chart_type,
            input_digest=chart.artifact.input_digest,
            png_path=png_path,
            svg_path=chart_root / "chart.svg",
            data_path=chart_root / "data.json",
            metadata_path=chart_root / "metadata.json",
        )
        for source, destination in (
            (chart.artifact.png_path, published.png_path),
            (chart.artifact.svg_path, published.svg_path),
            (chart.artifact.data_path, published.data_path),
            (chart.artifact.metadata_path, published.metadata_path),
        ):
            shutil.copyfile(source, destination)
        return replace(chart, artifact=published)


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
        or re.search(r'[<>:"/\\|?*]', value)
    ):
        raise ValueError(
            "filename must be one Markdown filename without directories"
        )
    return value


def _safe_image_filename(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("filename must not be blank")
    path = Path(value)
    if (
        path.name != value
        or path.suffix.lower() != ".png"
        or re.search(r'[<>:"/\\|?*]', value)
    ):
        raise ValueError(
            "filename must be one PNG filename without directories"
        )
    return value
