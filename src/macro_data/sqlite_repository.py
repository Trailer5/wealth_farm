"""SQLite storage for macro observations and fetch history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data_src.models import MacroObservation


class SQLiteMacroDataRepository:
    SCHEMA_VERSION = 37
    SCHEMA_DESCRIPTION = "macro and rate series observations"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)

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

                CREATE TABLE IF NOT EXISTS macro_observations (
                    series_id TEXT NOT NULL,
                    period TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL,
                    published_at TEXT,
                    acquired_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (series_id, period, source)
                );

                CREATE TABLE IF NOT EXISTS macro_fetch_log (
                    series_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_macro_fetch_log_lookup
                ON macro_fetch_log (series_id, fetched_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, description) "
                "VALUES (?, ?)",
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def upsert(self, observations: list[MacroObservation], acquired_at: str) -> int:
        if not observations:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO macro_observations (
                    series_id, period, period_end, value, unit, source,
                    published_at, acquired_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (series_id, period, source) DO UPDATE SET
                    period_end = excluded.period_end,
                    value = excluded.value,
                    unit = excluded.unit,
                    published_at = excluded.published_at,
                    acquired_at = excluded.acquired_at,
                    raw_json = excluded.raw_json
                """,
                [
                    [
                        item.series_id,
                        item.period,
                        item.period_end,
                        item.value,
                        item.unit,
                        item.source,
                        item.published_at,
                        acquired_at,
                        json.dumps(item.raw, ensure_ascii=False, default=str),
                    ]
                    for item in observations
                ],
            )
        return len(observations)

    def list_observations(
        self,
        series_id: str,
        start_date: str,
        end_date: str,
    ) -> list[MacroObservation]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM macro_observations
                WHERE series_id = ? AND period_end >= ? AND period_end <= ?
                ORDER BY period_end, source
                """,
                [series_id, start_date, end_date],
            ).fetchall()
        return [
            MacroObservation(
                series_id=str(row["series_id"]),
                period=str(row["period"]),
                period_end=str(row["period_end"]),
                value=float(row["value"]),
                unit=str(row["unit"]),
                source=str(row["source"]),
                published_at=row["published_at"],
                raw=json.loads(row["raw_json"]),
            )
            for row in rows
        ]

    def log_fetch(
        self,
        *,
        series_id: str,
        source: str,
        start_date: str,
        end_date: str,
        fetched_at: str,
        record_count: int,
        error: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO macro_fetch_log (
                    series_id, source, start_date, end_date,
                    fetched_at, record_count, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    series_id,
                    source,
                    start_date,
                    end_date,
                    fetched_at,
                    record_count,
                    error,
                ],
            )

    def last_successful_fetch(
        self,
        series_id: str,
        start_date: str,
        end_date: str,
        *,
        max_age_seconds: int | None,
    ) -> str | None:
        """Latest fetch with records whose range contains the request."""

        conditions = [
            "series_id = ?",
            "error IS NULL",
            "record_count > 0",
            "start_date <= ?",
            "end_date >= ?",
        ]
        parameters: list[object] = [series_id, start_date, end_date]
        if max_age_seconds is not None:
            threshold = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
            conditions.append("fetched_at >= ?")
            parameters.append(threshold.isoformat())
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT MAX(fetched_at) AS fetched_at FROM macro_fetch_log
                WHERE {" AND ".join(conditions)}
                """,
                parameters,
            ).fetchone()
        return row["fetched_at"] if row and row["fetched_at"] else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()
