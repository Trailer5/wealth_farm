"""Independent SQLite storage for state-holder evidence and weekly metrics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from .models import StateHolderEvidence, WeeklyStateFlowMetric


class SQLiteStateCapitalFlowRepository:
    """Persist Plan 42 records without an assets-table foreign key."""

    SCHEMA_VERSION = 26
    SCHEMA_DESCRIPTION = "state capital flow holder evidence and weekly metrics"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS state_holder_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    instrument_asset_id TEXT NOT NULL,
                    report_period TEXT NOT NULL,
                    holding_date TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    holding_shares TEXT NOT NULL,
                    holding_ratio TEXT,
                    source_tier TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    parameters_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_state_holder_lookup
                ON state_holder_evidence (
                    instrument_asset_id,
                    entity_id,
                    published_at
                );

                CREATE TABLE IF NOT EXISTS state_weekly_flow_metrics (
                    basket_id TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    week_end TEXT NOT NULL,
                    estimated_for_date TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    value REAL NOT NULL,
                    calculation_version TEXT NOT NULL,
                    evidence_tier TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    PRIMARY KEY (
                        basket_id,
                        factor_name,
                        week_end,
                        calculation_version
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_state_weekly_flow_lookup
                ON state_weekly_flow_metrics (
                    basket_id,
                    week_end,
                    factor_name
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def upsert_holder_evidence(
        self,
        evidence: list[StateHolderEvidence],
    ) -> int:
        if not evidence:
            return 0
        rows = [
            [
                item.evidence_id,
                item.entity_id,
                item.instrument_asset_id,
                item.report_period,
                item.holding_date,
                item.published_at,
                str(item.holding_shares),
                None if item.holding_ratio is None else str(item.holding_ratio),
                item.source_tier,
                item.source,
                item.source_url,
                _stable_json(item.parameters),
            ]
            for item in evidence
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO state_holder_evidence (
                    evidence_id,
                    entity_id,
                    instrument_asset_id,
                    report_period,
                    holding_date,
                    published_at,
                    holding_shares,
                    holding_ratio,
                    source_tier,
                    source,
                    source_url,
                    parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (evidence_id) DO UPDATE SET
                    entity_id = excluded.entity_id,
                    instrument_asset_id = excluded.instrument_asset_id,
                    report_period = excluded.report_period,
                    holding_date = excluded.holding_date,
                    published_at = excluded.published_at,
                    holding_shares = excluded.holding_shares,
                    holding_ratio = excluded.holding_ratio,
                    source_tier = excluded.source_tier,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    parameters_json = excluded.parameters_json
                """,
                rows,
            )
        return len(evidence)

    def list_holder_evidence(
        self,
        *,
        entity_id: str | None = None,
        instrument_asset_id: str | None = None,
        source_tier: str | None = None,
        published_through: str | None = None,
    ) -> list[StateHolderEvidence]:
        conditions: list[str] = []
        parameters: list[str] = []
        for column, value in (
            ("entity_id", entity_id),
            ("instrument_asset_id", instrument_asset_id),
            ("source_tier", source_tier),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        if published_through is not None:
            conditions.append("published_at <= ?")
            parameters.append(published_through)
        where_clause = (
            f"WHERE {' AND '.join(conditions)}" if conditions else ""
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT evidence_id, entity_id, instrument_asset_id,
                       report_period, holding_date, published_at,
                       holding_shares, holding_ratio, source_tier,
                       source, source_url, parameters_json
                FROM state_holder_evidence
                {where_clause}
                ORDER BY published_at, holding_date, entity_id,
                         instrument_asset_id, evidence_id
                """,
                parameters,
            ).fetchall()
        return [_holder_from_row(row) for row in rows]

    def upsert_weekly_flow_metrics(
        self,
        metrics: list[WeeklyStateFlowMetric],
    ) -> int:
        if not metrics:
            return 0
        rows = [
            [
                metric.basket_id,
                metric.factor_name,
                metric.week_start,
                metric.week_end,
                metric.estimated_for_date,
                metric.available_at,
                metric.value,
                metric.calculation_version,
                metric.evidence_tier,
                _stable_json(metric.parameters),
            ]
            for metric in metrics
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO state_weekly_flow_metrics (
                    basket_id,
                    factor_name,
                    week_start,
                    week_end,
                    estimated_for_date,
                    available_at,
                    value,
                    calculation_version,
                    evidence_tier,
                    parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    basket_id,
                    factor_name,
                    week_end,
                    calculation_version
                ) DO UPDATE SET
                    week_start = excluded.week_start,
                    estimated_for_date = excluded.estimated_for_date,
                    available_at = excluded.available_at,
                    value = excluded.value,
                    evidence_tier = excluded.evidence_tier,
                    parameters_json = excluded.parameters_json
                """,
                rows,
            )
        return len(metrics)

    def list_weekly_flow_metrics(
        self,
        *,
        basket_id: str | None = None,
        factor_name: str | None = None,
        week_end: str | None = None,
        available_through: str | None = None,
    ) -> list[WeeklyStateFlowMetric]:
        conditions: list[str] = []
        parameters: list[str] = []
        for column, value in (
            ("basket_id", basket_id),
            ("factor_name", factor_name),
            ("week_end", week_end),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        if available_through is not None:
            conditions.append("available_at <= ?")
            parameters.append(available_through)
        where_clause = (
            f"WHERE {' AND '.join(conditions)}" if conditions else ""
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT basket_id, factor_name, week_start, week_end,
                       estimated_for_date, available_at, value,
                       calculation_version, evidence_tier, parameters_json
                FROM state_weekly_flow_metrics
                {where_clause}
                ORDER BY week_end, factor_name, basket_id
                """,
                parameters,
            ).fetchall()
        return [_metric_from_row(row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            with self._memory_connection:
                yield self._memory_connection
            return
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _stable_json(parameters: dict[str, object]) -> str:
    return json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _holder_from_row(row: sqlite3.Row) -> StateHolderEvidence:
    ratio = row["holding_ratio"]
    return StateHolderEvidence(
        evidence_id=str(row["evidence_id"]),
        entity_id=str(row["entity_id"]),
        instrument_asset_id=str(row["instrument_asset_id"]),
        report_period=str(row["report_period"]),
        holding_date=str(row["holding_date"]),
        published_at=str(row["published_at"]),
        holding_shares=Decimal(str(row["holding_shares"])),
        holding_ratio=None if ratio is None else Decimal(str(ratio)),
        source_tier=str(row["source_tier"]),  # type: ignore[arg-type]
        source=str(row["source"]),
        source_url=str(row["source_url"]),
        parameters=json.loads(str(row["parameters_json"])),
    )


def _metric_from_row(row: sqlite3.Row) -> WeeklyStateFlowMetric:
    return WeeklyStateFlowMetric(
        basket_id=str(row["basket_id"]),
        factor_name=str(row["factor_name"]),
        week_start=str(row["week_start"]),
        week_end=str(row["week_end"]),
        estimated_for_date=str(row["estimated_for_date"]),
        available_at=str(row["available_at"]),
        value=float(row["value"]),
        calculation_version=str(row["calculation_version"]),
        evidence_tier=str(row["evidence_tier"]),  # type: ignore[arg-type]
        parameters=json.loads(str(row["parameters_json"])),
    )
