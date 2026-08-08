"""Exact SQLite persistence for fundamental metric results."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .models import (
    FundamentalInputEvidence,
    FundamentalMetricResult,
    MetricStatus,
)


class SQLiteFundamentalMetricRepository:
    SCHEMA_VERSION = 17
    SCHEMA_DESCRIPTION = "exact fundamental metric results"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS fundamental_metric_results (
                    asset_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    report_period TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value_text TEXT,
                    unit TEXT NOT NULL,
                    formula TEXT NOT NULL,
                    numerator_text TEXT,
                    denominator_text TEXT,
                    reason TEXT,
                    calculation_version TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (
                        asset_id,
                        metric_name,
                        as_of_date,
                        report_period,
                        calculation_version,
                        parameters_json
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_fundamental_results_lookup
                ON fundamental_metric_results (
                    asset_id,
                    as_of_date,
                    metric_name,
                    status
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (
                    version,
                    description
                ) VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def upsert_results(
        self,
        results: list[FundamentalMetricResult],
    ) -> int:
        if not results:
            return 0
        rows = [
            [
                result.asset_id,
                result.metric_name,
                result.as_of_date,
                result.report_period,
                result.status.value,
                _decimal_text(result.value),
                result.unit,
                result.formula,
                _decimal_text(result.numerator),
                _decimal_text(result.denominator),
                result.reason,
                result.calculation_version,
                _json(result.parameters),
                _json(
                    [
                        {
                            "field_name": item.field_name,
                            "value": format(item.value, "f"),
                            "report_period": item.report_period,
                            "source": item.source,
                            "unit": item.unit,
                            "currency": item.currency,
                        }
                        for item in result.inputs
                    ]
                ),
                result.computed_at,
            ]
            for result in results
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO fundamental_metric_results (
                    asset_id,
                    metric_name,
                    as_of_date,
                    report_period,
                    status,
                    value_text,
                    unit,
                    formula,
                    numerator_text,
                    denominator_text,
                    reason,
                    calculation_version,
                    parameters_json,
                    inputs_json,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    metric_name,
                    as_of_date,
                    report_period,
                    calculation_version,
                    parameters_json
                ) DO UPDATE SET
                    status = excluded.status,
                    value_text = excluded.value_text,
                    unit = excluded.unit,
                    formula = excluded.formula,
                    numerator_text = excluded.numerator_text,
                    denominator_text = excluded.denominator_text,
                    reason = excluded.reason,
                    inputs_json = excluded.inputs_json,
                    computed_at = excluded.computed_at
                """,
                rows,
            )
        return len(results)

    def list_results(
        self,
        asset_id: str,
        *,
        metric_name: str | None = None,
        as_of_date: str | None = None,
        as_of_or_before: str | None = None,
    ) -> list[FundamentalMetricResult]:
        conditions = ["asset_id = ?"]
        params: list[object] = [asset_id]
        if metric_name is not None:
            conditions.append("metric_name = ?")
            params.append(metric_name)
        if as_of_date is not None:
            conditions.append("as_of_date = ?")
            params.append(as_of_date)
        if as_of_or_before is not None:
            conditions.append("as_of_date <= ?")
            params.append(as_of_or_before)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM fundamental_metric_results
                WHERE {" AND ".join(conditions)}
                ORDER BY as_of_date DESC, metric_name, computed_at DESC
                """,
                params,
            ).fetchall()
        return [_result_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _result_from_row(row: sqlite3.Row) -> FundamentalMetricResult:
    input_rows = json.loads(str(row["inputs_json"]))
    return FundamentalMetricResult(
        asset_id=str(row["asset_id"]),
        metric_name=str(row["metric_name"]),
        as_of_date=str(row["as_of_date"]),
        report_period=str(row["report_period"]),
        status=MetricStatus(row["status"]),
        value=(
            Decimal(str(row["value_text"]))
            if row["value_text"] is not None
            else None
        ),
        unit=str(row["unit"]),
        formula=str(row["formula"]),
        numerator=(
            Decimal(str(row["numerator_text"]))
            if row["numerator_text"] is not None
            else None
        ),
        denominator=(
            Decimal(str(row["denominator_text"]))
            if row["denominator_text"] is not None
            else None
        ),
        reason=row["reason"],
        calculation_version=str(row["calculation_version"]),
        parameters=json.loads(str(row["parameters_json"])),
        inputs=tuple(
            FundamentalInputEvidence(
                field_name=str(item["field_name"]),
                value=Decimal(str(item["value"])),
                report_period=str(item["report_period"]),
                source=str(item["source"]),
                unit=str(item["unit"]),
                currency=item.get("currency"),
            )
            for item in input_rows
        ),
        computed_at=str(row["computed_at"]),
    )
