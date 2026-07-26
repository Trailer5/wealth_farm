"""Small, reusable quality checks for normalized daily bars."""

from __future__ import annotations

from typing import Any

from .models import (
    QUALITY_FAIL,
    QUALITY_PASS,
    QUALITY_WARN,
    QualityCheckResult,
)


REQUIRED_DAILY_BAR_FIELDS = ("symbol", "trade_date", "close", "source")


def check_daily_bars(
    *,
    run_id: str,
    records: list[dict[str, Any]],
    expected_start: str,
    expected_end: str,
    coverage_start: str | None,
    coverage_end: str | None,
    asset_id: str | None,
    asset_needs_review: bool = False,
) -> list[QualityCheckResult]:
    """Run the lightweight first-stage daily bar checks."""

    return [
        _check_non_empty(run_id, records),
        _check_required_fields(run_id, records),
        _check_duplicate_keys(run_id, records),
        _check_coverage(
            run_id,
            records,
            expected_start,
            expected_end,
            coverage_start,
            coverage_end,
        ),
        _check_ohlc(run_id, records),
        _check_asset_resolved(run_id, asset_id, asset_needs_review),
    ]


def _check_non_empty(
    run_id: str,
    records: list[dict[str, Any]],
) -> QualityCheckResult:
    if records:
        return _result(run_id, "non_empty", QUALITY_PASS, "records are present", count=len(records))
    return _result(run_id, "non_empty", QUALITY_FAIL, "result is empty", count=0)


def _check_required_fields(
    run_id: str,
    records: list[dict[str, Any]],
) -> QualityCheckResult:
    invalid_rows = 0
    for record in records:
        if any(record.get(field) in (None, "") for field in REQUIRED_DAILY_BAR_FIELDS):
            invalid_rows += 1
    if invalid_rows:
        return _result(
            run_id,
            "required_fields",
            QUALITY_FAIL,
            f"{invalid_rows} records miss required fields",
            invalid_rows=invalid_rows,
        )
    return _result(
        run_id,
        "required_fields",
        QUALITY_PASS,
        "required fields are present",
        invalid_rows=0,
    )


def _check_duplicate_keys(
    run_id: str,
    records: list[dict[str, Any]],
) -> QualityCheckResult:
    keys = [
        (
            record.get("symbol"),
            record.get("trade_date"),
            record.get("source"),
        )
        for record in records
    ]
    duplicate_count = len(keys) - len(set(keys))
    if duplicate_count:
        return _result(
            run_id,
            "duplicate_keys",
            QUALITY_FAIL,
            f"{duplicate_count} duplicate business keys",
            duplicate_count=duplicate_count,
        )
    return _result(
        run_id,
        "duplicate_keys",
        QUALITY_PASS,
        "business keys are unique",
        duplicate_count=0,
    )


def _check_coverage(
    run_id: str,
    records: list[dict[str, Any]],
    expected_start: str,
    expected_end: str,
    coverage_start: str | None,
    coverage_end: str | None,
) -> QualityCheckResult:
    dates = [
        str(record.get("trade_date"))
        for record in records
        if record.get("trade_date") not in (None, "")
    ]
    outside = [
        trade_date
        for trade_date in dates
        if trade_date < expected_start or trade_date > expected_end
    ]
    if outside:
        return _result(
            run_id,
            "coverage",
            QUALITY_FAIL,
            "records fall outside the declared range",
            outside_count=len(outside),
        )
    if (
        coverage_start is None
        or coverage_end is None
        or coverage_start > expected_start
        or coverage_end < expected_end
    ):
        return _result(
            run_id,
            "coverage",
            QUALITY_WARN,
            "declared coverage does not contain the expected range",
            expected_start=expected_start,
            expected_end=expected_end,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
    return _result(
        run_id,
        "coverage",
        QUALITY_PASS,
        "declared coverage contains the expected range",
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )


def _check_ohlc(
    run_id: str,
    records: list[dict[str, Any]],
) -> QualityCheckResult:
    invalid_rows = 0
    for record in records:
        values = {
            field: _optional_float(record.get(field))
            for field in ("open", "high", "low", "close")
        }
        if values["high"] is None or values["low"] is None:
            continue
        comparable = [
            value
            for field, value in values.items()
            if field not in {"high", "low"} and value is not None
        ]
        if (
            values["high"] < values["low"]
            or any(values["high"] < value for value in comparable)
            or any(values["low"] > value for value in comparable)
        ):
            invalid_rows += 1
    if invalid_rows:
        return _result(
            run_id,
            "ohlc",
            QUALITY_FAIL,
            f"{invalid_rows} records contain invalid OHLC relations",
            invalid_rows=invalid_rows,
        )
    return _result(
        run_id,
        "ohlc",
        QUALITY_PASS,
        "OHLC relations are valid",
        invalid_rows=0,
    )


def _check_asset_resolved(
    run_id: str,
    asset_id: str | None,
    asset_needs_review: bool,
) -> QualityCheckResult:
    if asset_id is None or asset_needs_review:
        return _result(
            run_id,
            "asset_resolved",
            QUALITY_FAIL,
            "asset identity is missing or requires review",
            asset_id=asset_id,
            needs_review=asset_needs_review,
        )
    return _result(
        run_id,
        "asset_resolved",
        QUALITY_PASS,
        "asset identity is resolved",
        asset_id=asset_id,
        needs_review=False,
    )


def _result(
    run_id: str,
    check_name: str,
    level: str,
    message: str,
    **metrics: Any,
) -> QualityCheckResult:
    return QualityCheckResult(
        run_id=run_id,
        check_name=check_name,
        level=level,
        message=message,
        metrics=metrics,
    )


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
