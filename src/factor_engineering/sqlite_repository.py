"""SQLite persistence for versioned factor values."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import FactorValue


class SQLiteFactorRepository:
    """Persist idempotent factor values and query their history."""

    SCHEMA_VERSION = 10
    SCHEMA_DESCRIPTION = "versioned factor values"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the factor schema."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS factor_values (
                    asset_id TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    value REAL NOT NULL,
                    calculation_version TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    input_start_date TEXT NOT NULL,
                    input_end_date TEXT NOT NULL,
                    source_dataset TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (
                        asset_id,
                        factor_name,
                        as_of_date,
                        calculation_version,
                        parameters_json
                    ),
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_factor_values_asset_date
                ON factor_values (asset_id, as_of_date, factor_name);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def upsert_values(self, values: list[FactorValue]) -> int:
        """Insert or update factor values."""

        if not values:
            return 0
        rows = [
            [
                value.asset_id,
                value.factor_name,
                value.as_of_date,
                value.value,
                value.calculation_version,
                _parameters_json(value.parameters),
                value.input_start_date,
                value.input_end_date,
                value.source_dataset,
                value.computed_at,
            ]
            for value in values
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO factor_values (
                    asset_id,
                    factor_name,
                    as_of_date,
                    value,
                    calculation_version,
                    parameters_json,
                    input_start_date,
                    input_end_date,
                    source_dataset,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    factor_name,
                    as_of_date,
                    calculation_version,
                    parameters_json
                ) DO UPDATE SET
                    value = excluded.value,
                    input_start_date = excluded.input_start_date,
                    input_end_date = excluded.input_end_date,
                    source_dataset = excluded.source_dataset,
                    computed_at = excluded.computed_at
                """,
                rows,
            )
        return len(values)

    def list_values(
        self,
        asset_id: str,
        *,
        factor_name: str | None = None,
        as_of_date: str | None = None,
    ) -> list[FactorValue]:
        """Return factor values for one asset."""

        conditions = ["asset_id = ?"]
        params = [asset_id]
        if factor_name is not None:
            conditions.append("factor_name = ?")
            params.append(factor_name)
        if as_of_date is not None:
            conditions.append("as_of_date = ?")
            params.append(as_of_date)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT asset_id, factor_name, as_of_date, value,
                       calculation_version, parameters_json,
                       input_start_date, input_end_date, source_dataset,
                       computed_at
                FROM factor_values
                WHERE {" AND ".join(conditions)}
                ORDER BY as_of_date DESC, factor_name
                """,
                params,
            ).fetchall()
        return [_factor_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute("PRAGMA foreign_keys = ON")
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _parameters_json(parameters: dict[str, object]) -> str:
    return json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _factor_from_row(row: sqlite3.Row) -> FactorValue:
    return FactorValue(
        asset_id=str(row["asset_id"]),
        factor_name=str(row["factor_name"]),
        as_of_date=str(row["as_of_date"]),
        value=float(row["value"]),
        calculation_version=str(row["calculation_version"]),
        parameters=json.loads(row["parameters_json"]),
        input_start_date=str(row["input_start_date"]),
        input_end_date=str(row["input_end_date"]),
        source_dataset=str(row["source_dataset"]),
        computed_at=str(row["computed_at"]),
    )
