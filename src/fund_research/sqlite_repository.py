"""SQLite persistence for fund manager and underlying relations."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import FundAssetRelation, FundRelationType


class SQLiteFundResearchRepository:
    SCHEMA_VERSION = 16
    SCHEMA_DESCRIPTION = "fund research relationships"

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

                CREATE TABLE IF NOT EXISTS fund_asset_relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fund_asset_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    related_asset_id TEXT NOT NULL DEFAULT '',
                    related_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        fund_asset_id,
                        relation_type,
                        related_asset_id,
                        related_name,
                        source,
                        effective_date
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_fund_relations_lookup
                ON fund_asset_relations (
                    fund_asset_id,
                    relation_type,
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
        relation: FundAssetRelation,
    ) -> FundAssetRelation:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fund_asset_relations (
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date,
                    confidence,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date
                ) DO UPDATE SET
                    confidence = excluded.confidence,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    relation.fund_asset_id,
                    relation.relation_type.value,
                    relation.related_asset_id or "",
                    relation.related_name,
                    relation.source,
                    relation.effective_date,
                    relation.confidence,
                    json.dumps(
                        relation.raw,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ],
            )
        return relation

    def list_relations(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
        relation_type: FundRelationType | None = None,
    ) -> list[FundAssetRelation]:
        conditions = [
            "fund_asset_id = ?",
            "effective_date <= ?",
        ]
        params: list[object] = [fund_asset_id, as_of_date]
        if relation_type is not None:
            conditions.append("relation_type = ?")
            params.append(relation_type.value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date,
                    confidence,
                    raw_json
                FROM fund_asset_relations
                WHERE {" AND ".join(conditions)}
                ORDER BY relation_type, effective_date DESC, related_name
                """,
                params,
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


def _relation_from_row(row: sqlite3.Row) -> FundAssetRelation:
    return FundAssetRelation(
        fund_asset_id=str(row["fund_asset_id"]),
        relation_type=FundRelationType(row["relation_type"]),
        related_asset_id=(
            str(row["related_asset_id"])
            if row["related_asset_id"]
            else None
        ),
        related_name=str(row["related_name"]),
        source=str(row["source"]),
        effective_date=str(row["effective_date"]),
        confidence=float(row["confidence"]),
        raw=json.loads(str(row["raw_json"])),
    )
