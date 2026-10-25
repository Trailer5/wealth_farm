"""SQLite persistence for benchmark relationships."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .models import (
    BenchmarkComponent,
    BenchmarkComponentKind,
    BenchmarkKind,
    BenchmarkRelation,
    CompositeBenchmarkDefinition,
)


class SQLiteBenchmarkRepository:
    SCHEMA_VERSION = 29
    SCHEMA_DESCRIPTION = "official composite benchmark definitions"

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

                CREATE TABLE IF NOT EXISTS benchmark_relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    benchmark_asset_id TEXT NOT NULL,
                    benchmark_kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    is_default INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        asset_id,
                        benchmark_asset_id,
                        effective_date
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_benchmark_relation_lookup
                ON benchmark_relations (
                    asset_id,
                    is_default,
                    effective_date
                );

                CREATE TABLE IF NOT EXISTS benchmark_definitions (
                    definition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_document_id TEXT NOT NULL,
                    benchmark_text TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    resolution_errors_json TEXT NOT NULL,
                    is_default INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        asset_id,
                        effective_date,
                        source_document_id
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_benchmark_definition_lookup
                ON benchmark_definitions (
                    asset_id,
                    is_default,
                    effective_date
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

    def save_relation(
        self,
        relation: BenchmarkRelation,
    ) -> BenchmarkRelation:
        with self._connect() as conn:
            if relation.is_default:
                conn.execute(
                    """
                    UPDATE benchmark_relations
                    SET is_default = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ?
                    """,
                    [relation.asset_id],
                )
            conn.execute(
                """
                INSERT INTO benchmark_relations (
                    asset_id,
                    benchmark_asset_id,
                    benchmark_kind,
                    source,
                    effective_date,
                    reason,
                    is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    benchmark_asset_id,
                    effective_date
                ) DO UPDATE SET
                    benchmark_kind = excluded.benchmark_kind,
                    source = excluded.source,
                    reason = excluded.reason,
                    is_default = excluded.is_default,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    relation.asset_id,
                    relation.benchmark_asset_id,
                    relation.benchmark_kind.value,
                    relation.source,
                    relation.effective_date,
                    relation.reason,
                    1 if relation.is_default else 0,
                ],
            )
        return relation

    def get_default(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> BenchmarkRelation | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM benchmark_relations
                WHERE asset_id = ?
                  AND is_default = 1
                  AND effective_date <= ?
                ORDER BY effective_date DESC, relation_id DESC
                LIMIT 1
                """,
                [asset_id, as_of_date],
            ).fetchone()
        return _relation_from_row(row) if row else None

    def save_definition(
        self,
        definition: CompositeBenchmarkDefinition,
    ) -> CompositeBenchmarkDefinition:
        with self._connect() as conn:
            if definition.is_default:
                conn.execute(
                    """
                    UPDATE benchmark_definitions
                    SET is_default = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = ?
                    """,
                    [definition.asset_id],
                )
            conn.execute(
                """
                INSERT INTO benchmark_definitions (
                    asset_id,
                    effective_date,
                    source,
                    source_document_id,
                    benchmark_text,
                    components_json,
                    resolution_errors_json,
                    is_default
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    effective_date,
                    source_document_id
                ) DO UPDATE SET
                    source = excluded.source,
                    benchmark_text = excluded.benchmark_text,
                    components_json = excluded.components_json,
                    resolution_errors_json =
                        excluded.resolution_errors_json,
                    is_default = excluded.is_default,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    definition.asset_id,
                    definition.effective_date,
                    definition.source,
                    definition.source_document_id,
                    definition.benchmark_text,
                    _components_json(definition.components),
                    json.dumps(
                        definition.resolution_errors,
                        ensure_ascii=False,
                    ),
                    1 if definition.is_default else 0,
                ],
            )
        return definition

    def get_default_definition(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> CompositeBenchmarkDefinition | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM benchmark_definitions
                WHERE asset_id = ?
                  AND is_default = 1
                  AND effective_date <= ?
                ORDER BY effective_date DESC, definition_id DESC
                LIMIT 1
                """,
                [asset_id, as_of_date],
            ).fetchone()
        return _definition_from_row(row) if row else None

    def list_definitions(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> list[CompositeBenchmarkDefinition]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM benchmark_definitions
                WHERE asset_id = ?
                  AND effective_date <= ?
                ORDER BY
                    is_default DESC,
                    effective_date DESC,
                    definition_id DESC
                """,
                [asset_id, as_of_date],
            ).fetchall()
        return [_definition_from_row(row) for row in rows]

    def list_relations(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> list[BenchmarkRelation]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM benchmark_relations
                WHERE asset_id = ?
                  AND effective_date <= ?
                ORDER BY
                    is_default DESC,
                    effective_date DESC,
                    relation_id DESC
                """,
                [asset_id, as_of_date],
            ).fetchall()
        return [_relation_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _relation_from_row(row: sqlite3.Row) -> BenchmarkRelation:
    return BenchmarkRelation(
        asset_id=str(row["asset_id"]),
        benchmark_asset_id=str(row["benchmark_asset_id"]),
        benchmark_kind=BenchmarkKind(row["benchmark_kind"]),
        source=str(row["source"]),
        effective_date=str(row["effective_date"]),
        reason=str(row["reason"]),
        is_default=bool(row["is_default"]),
    )


def _components_json(
    components: tuple[BenchmarkComponent, ...],
) -> str:
    return json.dumps(
        [
            {
                "label": component.label,
                "kind": component.kind.value,
                "weight": str(component.weight),
                "source": component.source,
                "index_asset_id": component.index_asset_id,
                "annual_rate": (
                    str(component.annual_rate)
                    if component.annual_rate is not None
                    else None
                ),
                "rate_effective_date": component.rate_effective_date,
            }
            for component in components
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _definition_from_row(
    row: sqlite3.Row,
) -> CompositeBenchmarkDefinition:
    components = json.loads(str(row["components_json"]))
    return CompositeBenchmarkDefinition(
        asset_id=str(row["asset_id"]),
        effective_date=str(row["effective_date"]),
        source=str(row["source"]),
        source_document_id=str(row["source_document_id"]),
        benchmark_text=str(row["benchmark_text"]),
        components=tuple(
            BenchmarkComponent(
                label=str(item["label"]),
                kind=BenchmarkComponentKind(item["kind"]),
                weight=Decimal(str(item["weight"])),
                source=str(item["source"]),
                index_asset_id=(
                    str(item["index_asset_id"])
                    if item.get("index_asset_id")
                    else None
                ),
                annual_rate=(
                    Decimal(str(item["annual_rate"]))
                    if item.get("annual_rate") is not None
                    else None
                ),
                rate_effective_date=(
                    str(item["rate_effective_date"])
                    if item.get("rate_effective_date")
                    else None
                ),
            )
            for item in components
        ),
        resolution_errors=tuple(
            json.loads(str(row["resolution_errors_json"]))
        ),
        is_default=bool(row["is_default"]),
    )
