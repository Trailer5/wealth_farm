"""Build a weighted benchmark series on common observation dates."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import (
    BenchmarkComponentKind,
    CompositeBenchmarkDefinition,
)


def build_composite_benchmark_records(
    *,
    asset_records: list[dict[str, Any]],
    definition: CompositeBenchmarkDefinition,
    index_records: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return asset and synthetic benchmark closes on identical dates."""

    asset_closes = _closes_by_date(asset_records)
    component_closes = {}
    for component in definition.components:
        if component.kind is BenchmarkComponentKind.CASH_RATE:
            continue
        if component.index_asset_id is None:
            raise ValueError(
                f"Unresolved benchmark component: {component.label}"
            )
        component_closes[component.index_asset_id] = _closes_by_date(
            index_records.get(component.index_asset_id, [])
        )
    common_dates = set(asset_closes)
    for closes in component_closes.values():
        common_dates &= set(closes)
    ordered_dates = sorted(common_dates)
    if len(ordered_dates) < 2:
        raise ValueError(
            "Composite benchmark requires at least two common observations"
        )

    filtered_asset = [
        {
            "trade_date": value_date,
            "close": asset_closes[value_date],
        }
        for value_date in ordered_dates
    ]
    synthetic_close = 100.0
    benchmark_records = [
        {
            "trade_date": ordered_dates[0],
            "close": synthetic_close,
        }
    ]
    for previous, current in zip(ordered_dates, ordered_dates[1:]):
        elapsed_days = (
            date.fromisoformat(current) - date.fromisoformat(previous)
        ).days
        weighted_return = 0.0
        for component in definition.components:
            if component.kind is BenchmarkComponentKind.CASH_RATE:
                assert component.annual_rate is not None
                component_return = (
                    float(component.annual_rate)
                    * elapsed_days
                    / 365.0
                )
            else:
                assert component.index_asset_id is not None
                closes = component_closes[component.index_asset_id]
                component_return = (
                    closes[current] / closes[previous] - 1.0
                )
            weighted_return += float(component.weight) * component_return
        if weighted_return <= -1:
            raise ValueError(
                f"Composite benchmark return is invalid on {current}"
            )
        synthetic_close *= 1.0 + weighted_return
        benchmark_records.append(
            {
                "trade_date": current,
                "close": synthetic_close,
            }
        )
    return filtered_asset, benchmark_records


def _closes_by_date(
    records: list[dict[str, Any]],
) -> dict[str, float]:
    closes: dict[str, float] = {}
    for record in records:
        value_date = str(
            record.get("trade_date")
            or record.get("nav_date")
            or ""
        )[:10]
        close_value = record.get("close")
        if close_value in (None, ""):
            close_value = record.get("unit_nav")
        if not value_date or close_value in (None, ""):
            continue
        date.fromisoformat(value_date)
        close = float(close_value)
        if close <= 0:
            raise ValueError(f"close must be positive on {value_date}")
        if value_date in closes:
            raise ValueError(f"duplicate trade_date: {value_date}")
        closes[value_date] = close
    return closes
