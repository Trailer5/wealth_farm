"""SQLite persistence for market-wide factor values."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import MarketFactorValue


class SQLiteMarketFactorRepository:
    """Persist market factors without an assets-table relationship."""

    SCHEMA_VERSION = 25
    SCHEMA_DESCRIPTION = "A-share market factor values"

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

                CREATE TABLE IF NOT EXISTS market_factor_values (
                    scope_id TEXT NOT NULL,
                    universe_id TEXT NOT NULL,
                    universe_version TEXT NOT NULL,
                    factor_name TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    value REAL NOT NULL,
                    calculation_version TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (
                        scope_id,
                        universe_id,
                        universe_version,
                        factor_name,
                        as_of_date,
                        calculation_version
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_market_factor_lookup
                ON market_factor_values (
                    scope_id,
                    as_of_date,
                    factor_name
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

    def upsert_values(self, values: list[MarketFactorValue]) -> int:
        if not values:
            return 0
        rows = [
            [
                value.scope_id,
                value.universe_id,
                value.universe_version,
                value.factor_name,
                value.as_of_date,
                value.value,
                value.calculation_version,
                _parameters_json(value.parameters),
                value.computed_at,
            ]
            for value in values
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO market_factor_values (
                    scope_id,
                    universe_id,
                    universe_version,
                    factor_name,
                    as_of_date,
                    value,
                    calculation_version,
                    parameters_json,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    scope_id,
                    universe_id,
                    universe_version,
                    factor_name,
                    as_of_date,
                    calculation_version
                ) DO UPDATE SET
                    value = excluded.value,
                    parameters_json = excluded.parameters_json,
                    computed_at = excluded.computed_at
                """,
                rows,
            )
        return len(values)

    def list_values(
        self,
        scope_id: str,
        *,
        factor_name: str | None = None,
        as_of_date: str | None = None,
        universe_id: str | None = None,
        universe_version: str | None = None,
    ) -> list[MarketFactorValue]:
        conditions = ["scope_id = ?"]
        query_parameters: list[str] = [scope_id]
        for column, value in (
            ("factor_name", factor_name),
            ("as_of_date", as_of_date),
            ("universe_id", universe_id),
            ("universe_version", universe_version),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                query_parameters.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT scope_id, universe_id, universe_version,
                       factor_name, as_of_date, value,
                       calculation_version, parameters_json,
                       computed_at
                FROM market_factor_values
                WHERE {" AND ".join(conditions)}
                ORDER BY as_of_date DESC, factor_name
                """,
                query_parameters,
            ).fetchall()
        return [_value_from_row(row) for row in rows]

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


def _parameters_json(parameters: dict[str, object]) -> str:
    return json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _value_from_row(row: sqlite3.Row) -> MarketFactorValue:
    return MarketFactorValue(
        scope_id=str(row["scope_id"]),
        universe_id=str(row["universe_id"]),
        universe_version=str(row["universe_version"]),
        factor_name=str(row["factor_name"]),
        as_of_date=str(row["as_of_date"]),
        value=float(row["value"]),
        calculation_version=str(row["calculation_version"]),
        parameters=json.loads(row["parameters_json"]),
        computed_at=str(row["computed_at"]),
    )
