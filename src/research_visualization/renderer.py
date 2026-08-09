"""Matplotlib renderer for deterministic IDE research artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .models import ChartArtifact, ChartSpec, ChartType


matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False


class ChartRenderer:
    def __init__(
        self,
        output_root: str | Path = "temp/research",
    ) -> None:
        self.output_root = Path(output_root)

    def render(self, spec: ChartSpec) -> ChartArtifact:
        payload = _spec_payload(spec)
        canonical_json = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(
            canonical_json.encode("utf-8")
        ).hexdigest()
        output_dir = (
            self.output_root / spec.safe_task_id / "charts"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        base_name = f"{spec.chart_type.value}-{digest[:12]}"
        svg_path = output_dir / f"{base_name}.svg"
        png_path = output_dir / f"{base_name}.png"
        data_path = output_dir / f"{base_name}.data.json"
        metadata_path = output_dir / f"{base_name}.metadata.json"

        fig = self._figure(spec)
        matplotlib.rcParams["svg.hashsalt"] = digest
        fig.savefig(
            svg_path,
            format="svg",
            bbox_inches="tight",
            metadata={
                "Creator": "Wealth Farm",
                "Date": None,
            },
        )
        fig.savefig(
            png_path,
            format="png",
            dpi=150,
            bbox_inches="tight",
            metadata={"Software": "Wealth Farm"},
        )
        plt.close(fig)

        data_path.write_text(
            json.dumps(
                payload["points"],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        metadata = {
            "chart_type": spec.chart_type.value,
            "version": spec.version,
            "task_id": spec.task_id,
            "asset_id": spec.asset_id,
            "asset_name": spec.asset_name,
            "title": spec.title,
            "as_of_date": spec.as_of_date,
            "x_label": spec.x_label,
            "y_label": spec.y_label,
            "unit": spec.unit,
            "currency": spec.currency,
            "price_adjustment": spec.price_adjustment,
            "benchmark_asset_id": spec.benchmark_asset_id,
            "source_datasets": list(spec.source_datasets),
            "parameters": spec.parameters,
            "input_digest": digest,
            "point_count": len(spec.points),
            "files": {
                "svg": svg_path.name,
                "png": png_path.name,
                "data": data_path.name,
            },
        }
        metadata_path.write_text(
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return ChartArtifact(
            chart_type=spec.chart_type,
            input_digest=digest,
            svg_path=svg_path,
            png_path=png_path,
            data_path=data_path,
            metadata_path=metadata_path,
        )

    def _figure(self, spec: ChartSpec):
        renderers = {
            ChartType.PRICE_VOLUME: _price_volume_figure,
            ChartType.CUMULATIVE_RETURN: _line_figure,
            ChartType.DRAWDOWN: _drawdown_figure,
            ChartType.ROLLING_RISK: _line_figure,
            ChartType.VALUATION_HISTORY: _line_figure,
            ChartType.FINANCIAL_TRENDS: (
                _financial_trends_figure
            ),
            ChartType.CATEGORICAL_BAR: _categorical_bar_figure,
        }
        return renderers[spec.chart_type](spec)


def _price_volume_figure(spec: ChartSpec):
    required = {"open", "high", "low", "close", "volume"}
    _require_fields(spec, required)
    fig, (price_axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(12, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )
    x_values = list(range(len(spec.points)))
    closes = [
        _value(point.values.get("close"))
        for point in spec.points
    ]
    for index, point in enumerate(spec.points):
        open_value = _value(point.values.get("open"))
        high = _value(point.values.get("high"))
        low = _value(point.values.get("low"))
        close = _value(point.values.get("close"))
        volume = _value(point.values.get("volume"))
        color = "#c43d3d" if close >= open_value else "#238b57"
        price_axis.vlines(index, low, high, color=color, linewidth=1)
        body_bottom = min(open_value, close)
        body_height = max(abs(close - open_value), 1e-9)
        price_axis.add_patch(
            Rectangle(
                (index - 0.3, body_bottom),
                0.6,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )
        volume_axis.bar(index, volume, color=color, width=0.6)
    for window in spec.parameters.get(
        "moving_averages",
        (5, 20),
    ):
        window = int(window)
        averages = [
            (
                sum(closes[index - window + 1 : index + 1])
                / window
                if index + 1 >= window
                else math.nan
            )
            for index in range(len(closes))
        ]
        price_axis.plot(
            x_values,
            averages,
            linewidth=1.2,
            label=f"MA{window}",
        )
    price_axis.legend(loc="best")
    price_axis.set_ylabel(_axis_label(spec.y_label, spec.unit))
    volume_axis.set_ylabel(
        str(spec.parameters.get("volume_label", "Volume"))
    )
    _apply_dates(volume_axis, spec)
    _apply_title(fig, spec)
    fig.tight_layout()
    return fig


def _line_figure(spec: ChartSpec):
    fig, axis = plt.subplots(figsize=(12, 6))
    fields = sorted(
        {
            field_name
            for point in spec.points
            for field_name, value in point.values.items()
            if value is not None
        }
    )
    if not fields:
        raise ValueError("Chart has no numeric series")
    x_values = list(range(len(spec.points)))
    for field_name in fields:
        axis.plot(
            x_values,
            [
                _optional_value(point.values.get(field_name))
                for point in spec.points
            ],
            linewidth=1.5,
            label=field_name,
        )
    axis.axhline(0, color="#777777", linewidth=0.8)
    axis.set_ylabel(_axis_label(spec.y_label, spec.unit))
    axis.legend(loc="best")
    _apply_dates(axis, spec)
    _apply_title(fig, spec)
    fig.tight_layout()
    return fig


def _drawdown_figure(spec: ChartSpec):
    _require_fields(spec, {"drawdown"})
    fig, axis = plt.subplots(figsize=(12, 5))
    x_values = list(range(len(spec.points)))
    values = [
        _value(point.values.get("drawdown"))
        for point in spec.points
    ]
    axis.plot(x_values, values, color="#7f3c8d", linewidth=1.4)
    axis.fill_between(
        x_values,
        values,
        0,
        color="#7f3c8d",
        alpha=0.2,
    )
    axis.axhline(0, color="#777777", linewidth=0.8)
    axis.set_ylabel(_axis_label(spec.y_label, spec.unit))
    _apply_dates(axis, spec)
    _apply_title(fig, spec)
    fig.tight_layout()
    return fig


def _financial_trends_figure(spec: ChartSpec):
    ratio_fields = {
        "gross_margin",
        "operating_margin",
        "net_margin",
        "roe",
        "roic",
    }
    all_fields = {
        field_name
        for point in spec.points
        for field_name, value in point.values.items()
        if value is not None
    }
    amount_fields = sorted(all_fields - ratio_fields)
    selected_ratios = sorted(all_fields & ratio_fields)
    if not amount_fields and not selected_ratios:
        raise ValueError("Financial chart has no numeric series")
    fig, (amount_axis, ratio_axis) = plt.subplots(
        2,
        1,
        figsize=(12, 8),
        sharex=True,
    )
    x_values = list(range(len(spec.points)))
    for field_name in amount_fields:
        amount_axis.plot(
            x_values,
            [
                _optional_value(point.values.get(field_name))
                for point in spec.points
            ],
            marker="o",
            linewidth=1.4,
            label=field_name,
        )
    for field_name in selected_ratios:
        ratio_axis.plot(
            x_values,
            [
                _optional_value(point.values.get(field_name))
                for point in spec.points
            ],
            marker="o",
            linewidth=1.4,
            label=field_name,
        )
    amount_axis.set_ylabel(
        _axis_label(spec.y_label, spec.currency or spec.unit)
    )
    ratio_axis.set_ylabel("Ratio")
    if amount_fields:
        amount_axis.legend(loc="best")
    if selected_ratios:
        ratio_axis.legend(loc="best")
    _apply_dates(ratio_axis, spec)
    _apply_title(fig, spec)
    fig.tight_layout()
    return fig


def _categorical_bar_figure(spec: ChartSpec):
    fields = sorted(
        {
            field_name
            for point in spec.points
            for field_name, value in point.values.items()
            if value is not None
        }
    )
    if not fields:
        raise ValueError("Categorical chart has no numeric series")
    labels = tuple(
        str(value)
        for value in spec.parameters.get(
            "category_labels",
            [point.date for point in spec.points],
        )
    )
    if len(labels) != len(spec.points):
        raise ValueError(
            "category_labels must match the point count"
        )
    fig, axis = plt.subplots(figsize=(12, 6))
    x_values = list(range(len(spec.points)))
    width = 0.8 / len(fields)
    start_offset = -0.4 + width / 2
    for field_index, field_name in enumerate(fields):
        axis.bar(
            [
                value
                + start_offset
                + field_index * width
                for value in x_values
            ],
            [
                _optional_value(point.values.get(field_name))
                for point in spec.points
            ],
            width=width,
            label=field_name,
        )
    axis.axhline(0, color="#777777", linewidth=0.8)
    axis.set_xticks(x_values)
    axis.set_xticklabels(labels)
    axis.set_xlabel(spec.x_label)
    axis.set_ylabel(_axis_label(spec.y_label, spec.unit))
    axis.legend(loc="best")
    axis.grid(axis="y", alpha=0.2)
    _apply_title(fig, spec)
    fig.tight_layout()
    return fig


def _apply_title(fig, spec: ChartSpec) -> None:
    sources = ", ".join(spec.source_datasets)
    as_of_label = str(spec.parameters.get("as_of_label", "As of"))
    source_label = str(spec.parameters.get("source_label", "Source"))
    fig.suptitle(
        f"{spec.title}\n"
        f"{spec.asset_name} · {as_of_label} {spec.as_of_date} · "
        f"{source_label}: {sources}",
        fontsize=12,
    )


def _apply_dates(axis, spec: ChartSpec) -> None:
    count = len(spec.points)
    step = max(1, count // 8)
    ticks = list(range(0, count, step))
    if ticks[-1] != count - 1:
        if count - 1 - ticks[-1] < max(1, step // 2):
            ticks[-1] = count - 1
        else:
            ticks.append(count - 1)
    axis.set_xticks(ticks)
    axis.set_xticklabels(
        [spec.points[index].date for index in ticks],
        rotation=30,
        ha="right",
    )
    axis.set_xlabel(spec.x_label)
    axis.grid(alpha=0.2)


def _axis_label(label: str, unit: str) -> str:
    return f"{label} ({unit})"


def _require_fields(
    spec: ChartSpec,
    fields: set[str],
) -> None:
    missing = [
        field_name
        for field_name in sorted(fields)
        if all(
            point.values.get(field_name) is None
            for point in spec.points
        )
    ]
    if missing:
        raise ValueError(
            f"Chart requires fields: {', '.join(missing)}"
        )


def _value(value: float | None) -> float:
    if value is None:
        raise ValueError("Required chart value is missing")
    return float(value)


def _optional_value(value: float | None) -> float:
    return float(value) if value is not None else math.nan


def _spec_payload(spec: ChartSpec) -> dict[str, object]:
    return {
        "chart_type": spec.chart_type.value,
        "version": spec.version,
        "task_id": spec.task_id,
        "asset_id": spec.asset_id,
        "asset_name": spec.asset_name,
        "title": spec.title,
        "as_of_date": spec.as_of_date,
        "x_label": spec.x_label,
        "y_label": spec.y_label,
        "unit": spec.unit,
        "currency": spec.currency,
        "price_adjustment": spec.price_adjustment,
        "benchmark_asset_id": spec.benchmark_asset_id,
        "source_datasets": list(spec.source_datasets),
        "parameters": spec.parameters,
        "points": [
            {
                "date": point.date,
                "values": point.values,
            }
            for point in spec.points
        ],
    }
