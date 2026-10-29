"""SQLite persistence for normalized financial research points."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from .models import (
    FinancialDataPoint,
    FinancialPeriodKind,
    FinancialValueScope,
)
from .normalizer import canonical_field_name


class SQLiteFinancialDataRepository:
    SCHEMA_VERSION = 15
    SCHEMA_DESCRIPTION = (
        "normalized versioned financial research data"
    )

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

                CREATE TABLE IF NOT EXISTS financial_data_points (
                    point_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    report_period TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    period_kind TEXT NOT NULL,
                    value_scope TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    raw_value TEXT NOT NULL,
                    currency TEXT,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        asset_id,
                        dataset,
                        report_period,
                        field_name,
                        source,
                        revision
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_financial_points_query
                ON financial_data_points (
                    asset_id,
                    dataset,
                    report_period,
                    published_at,
                    field_name
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

    def save_points(
        self,
        points: list[FinancialDataPoint],
    ) -> list[FinancialDataPoint]:
        stored: list[FinancialDataPoint] = []
        if not points:
            return stored
        with self._connect() as conn:
            for point in points:
                latest_row = conn.execute(
                    """
                    SELECT *
                    FROM financial_data_points
                    WHERE asset_id = ?
                      AND dataset = ?
                      AND report_period = ?
                      AND field_name = ?
                      AND source = ?
                    ORDER BY revision DESC
                    LIMIT 1
                    """,
                    [
                        point.asset_id,
                        point.dataset,
                        point.report_period,
                        point.field_name,
                        point.source,
                    ],
                ).fetchone()
                if (
                    latest_row is not None
                    and _same_content(latest_row, point)
                ):
                    stored.append(_point_from_row(latest_row))
                    continue
                revision = (
                    int(latest_row["revision"]) + 1
                    if latest_row is not None
                    else 1
                )
                versioned = replace(point, revision=revision)
                conn.execute(
                    """
                    INSERT INTO financial_data_points (
                        asset_id,
                        dataset,
                        report_period,
                        published_at,
                        period_kind,
                        value_scope,
                        field_name,
                        field_label,
                        normalized_value,
                        raw_value,
                        currency,
                        unit,
                        source,
                        revision,
                        fetched_at,
                        raw_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    [
                        versioned.asset_id,
                        versioned.dataset,
                        versioned.report_period,
                        versioned.published_at,
                        versioned.period_kind.value,
                        versioned.value_scope.value,
                        versioned.field_name,
                        versioned.field_label,
                        format(
                            versioned.normalized_value,
                            "f",
                        ),
                        versioned.raw_value,
                        versioned.currency,
                        versioned.unit,
                        versioned.source,
                        versioned.revision,
                        versioned.fetched_at,
                        json.dumps(
                            versioned.raw,
                            ensure_ascii=False,
                            sort_keys=True,
                            default=str,
                        ),
                    ],
                )
                stored.append(versioned)
        return stored

    def list_points(
        self,
        *,
        asset_id: str,
        dataset: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        field_name: str | None = None,
    ) -> list[FinancialDataPoint]:
        conditions = [
            "asset_id = ?",
            "dataset = ?",
            "report_period >= ?",
            "report_period <= ?",
            "published_at <= ?",
        ]
        params: list[object] = [
            asset_id,
            dataset,
            start_date,
            end_date,
            as_of_date,
        ]
        if field_name is not None:
            conditions.append("field_name = ?")
            params.append(field_name)
        where_sql = " AND ".join(conditions)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY
                                asset_id,
                                dataset,
                                report_period,
                                field_name,
                                source
                            ORDER BY revision DESC
                        ) AS row_number
                    FROM financial_data_points
                    WHERE {where_sql}
                )
                SELECT *
                FROM ranked
                WHERE row_number = 1
                ORDER BY
                    report_period,
                    field_name,
                    source
                """,
                params,
            ).fetchall()
        return [_point_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute(
                    "PRAGMA foreign_keys = ON"
                )
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _same_content(
    row: sqlite3.Row,
    point: FinancialDataPoint,
) -> bool:
    return (
        str(row["published_at"]) == point.published_at
        and str(row["period_kind"]) == point.period_kind.value
        and str(row["value_scope"]) == point.value_scope.value
        and str(row["field_label"]) == point.field_label
        and str(row["normalized_value"])
        == format(point.normalized_value, "f")
        and str(row["raw_value"]) == point.raw_value
        and row["currency"] == point.currency
        and str(row["unit"]) == point.unit
    )


def _point_from_row(row: sqlite3.Row) -> FinancialDataPoint:
    return FinancialDataPoint(
        asset_id=str(row["asset_id"]),
        dataset=str(row["dataset"]),
        report_period=str(row["report_period"]),
        published_at=str(row["published_at"]),
        period_kind=FinancialPeriodKind(row["period_kind"]),
        value_scope=FinancialValueScope(row["value_scope"]),
        field_name=canonical_field_name(str(row["field_label"])),
        field_label=str(row["field_label"]),
        normalized_value=Decimal(str(row["normalized_value"])),
        raw_value=str(row["raw_value"]),
        currency=row["currency"],
        unit=str(row["unit"]),
        source=str(row["source"]),
        revision=int(row["revision"]),
        fetched_at=str(row["fetched_at"]),
        raw=json.loads(str(row["raw_json"])),
    )
