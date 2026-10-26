"""SQLite-backed records and coverage metadata for unified data access."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from src.data_store import SQLiteDataStore
from src.master_data import Asset

from .models import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_INDEX_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    TOTAL_RETURN_BAR_DATASETS,
    DataCoverage,
)


class SQLiteDataAccessRepository:
    """Read normalized records and track successful query coverage."""

    SCHEMA_VERSION = 5
    SCHEMA_DESCRIPTION = "unified data access coverage and trading calendar cache"

    def __init__(self, store: SQLiteDataStore) -> None:
        self.store = store

    def initialize(self) -> None:
        """Initialize shared storage and coverage metadata."""

        self.store.initialize()
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_access_coverage (
                    coverage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    range_start TEXT NOT NULL,
                    range_end TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        asset_id,
                        dataset,
                        backend,
                        range_start,
                        range_end,
                        source
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_data_access_coverage_lookup
                ON data_access_coverage (
                    asset_id,
                    dataset,
                    backend,
                    range_start,
                    range_end,
                    checked_at
                );

                CREATE TABLE IF NOT EXISTS trading_calendar (
                    market TEXT NOT NULL,
                    calendar_date TEXT NOT NULL,
                    is_trading_day INTEGER NOT NULL CHECK (is_trading_day IN (0, 1)),
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (market, calendar_date, source)
                );

                CREATE TABLE IF NOT EXISTS trading_calendar_coverage (
                    market TEXT NOT NULL,
                    range_start TEXT NOT NULL,
                    range_end TEXT NOT NULL,
                    source TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY (market, range_start, range_end, source)
                );

                CREATE INDEX IF NOT EXISTS idx_trading_calendar_coverage_lookup
                ON trading_calendar_coverage (market, range_start, range_end, source);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def get_stock_daily_bars(
        self,
        asset: Asset,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Read local A-share daily bars by canonical symbol."""

        return self.store.get_stock_daily_bars(
            asset.standard_symbol,
            start_date=start_date,
            end_date=end_date,
        )

    def has_trading_calendar_coverage(
        self,
        *,
        market: str,
        start_date: str,
        end_date: str,
        source: str,
    ) -> bool:
        """Return whether a cached calendar range contains the request."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM trading_calendar_coverage
                WHERE market = ?
                  AND source = ?
                  AND range_start <= ?
                  AND range_end >= ?
                LIMIT 1
                """,
                [market, source, start_date, end_date],
            ).fetchone()
        return row is not None

    def save_trading_calendar(
        self,
        *,
        market: str,
        start_date: str,
        end_date: str,
        source: str,
        rows: list[dict[str, object]],
    ) -> None:
        """Persist calendar rows and their checked range."""

        checked_at = datetime.now(timezone.utc).isoformat()
        values = [
            [
                market,
                str(row.get("calendar_date", "")),
                1 if str(row.get("is_trading_day", "0")) == "1" else 0,
                source,
            ]
            for row in rows
            if row.get("calendar_date")
        ]
        if not values:
            raise ValueError("Trading calendar rows must not be empty")
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO trading_calendar (
                    market,
                    calendar_date,
                    is_trading_day,
                    source
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (market, calendar_date, source) DO UPDATE SET
                    is_trading_day = excluded.is_trading_day,
                    updated_at = CURRENT_TIMESTAMP
                """,
                values,
            )
            conn.execute(
                """
                INSERT INTO trading_calendar_coverage (
                    market,
                    range_start,
                    range_end,
                    source,
                    checked_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (market, range_start, range_end, source) DO UPDATE SET
                    checked_at = excluded.checked_at
                """,
                [market, start_date, end_date, source, checked_at],
            )

    def get_trading_dates(
        self,
        *,
        market: str,
        start_date: str,
        end_date: str,
        source: str,
    ) -> list[str]:
        """Return cached trading dates in ascending order."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT calendar_date
                FROM trading_calendar
                WHERE market = ?
                  AND source = ?
                  AND calendar_date >= ?
                  AND calendar_date <= ?
                  AND is_trading_day = 1
                ORDER BY calendar_date
                """,
                [market, source, start_date, end_date],
            ).fetchall()
        return [str(row["calendar_date"]) for row in rows]

    def find_fresh_coverage(
        self,
        *,
        asset_id: str,
        dataset: str,
        start_date: str,
        end_date: str,
        max_age_seconds: int | None,
        backend: str = "sqlite",
        now: datetime | None = None,
    ) -> DataCoverage | None:
        """Return one fresh coverage record containing the requested range."""

        conditions = [
            "asset_id = ?",
            "dataset = ?",
            "backend = ?",
            "range_start <= ?",
            "range_end >= ?",
        ]
        params: list[Any] = [asset_id, dataset, backend, start_date, end_date]
        if max_age_seconds is not None:
            reference_time = now or datetime.now(timezone.utc)
            threshold = reference_time - timedelta(seconds=max_age_seconds)
            conditions.append("checked_at >= ?")
            params.append(threshold.isoformat())
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT asset_id, dataset, backend, range_start, range_end, source, checked_at
                FROM data_access_coverage
                WHERE {" AND ".join(conditions)}
                ORDER BY checked_at DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return _coverage_from_row(row) if row is not None else None

    def mark_coverage(
        self,
        *,
        asset_id: str,
        dataset: str,
        start_date: str,
        end_date: str,
        source: str,
        backend: str = "sqlite",
        checked_at: datetime | None = None,
    ) -> DataCoverage:
        """Record one successfully checked or fetched range."""

        checked_at_value = (checked_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO data_access_coverage (
                    asset_id,
                    dataset,
                    backend,
                    range_start,
                    range_end,
                    source,
                    checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    dataset,
                    backend,
                    range_start,
                    range_end,
                    source
                ) DO UPDATE SET
                    checked_at = excluded.checked_at,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    asset_id,
                    dataset,
                    backend,
                    start_date,
                    end_date,
                    source,
                    checked_at_value,
                ],
            )
        return DataCoverage(
            asset_id=asset_id,
            dataset=dataset,
            backend=backend,
            range_start=start_date,
            range_end=end_date,
            source=source,
            checked_at=checked_at_value,
        )

    def get_records(
        self,
        asset: Asset,
        dataset: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Read one supported dataset from SQLite."""

        if dataset == DATASET_STOCK_DAILY_BARS:
            return self.get_stock_daily_bars(asset, start_date, end_date)
        if dataset == DATASET_EXCHANGE_FUND_DAILY_BARS:
            return self.store.get_fund_exchange_daily_bars(
                asset.standard_symbol,
                start_date=start_date,
                end_date=end_date,
            )
        if dataset == DATASET_INDEX_DAILY_BARS:
            return self.store.get_index_daily_bars(
                asset.standard_symbol,
                start_date=start_date,
                end_date=end_date,
            )
        if dataset in TOTAL_RETURN_BAR_DATASETS:
            return self.store.get_total_return_bars(
                dataset,
                asset.standard_symbol,
                start_date=start_date,
                end_date=end_date,
            )
        raise ValueError(f"Unsupported SQLite dataset: {dataset}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.store.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _coverage_from_row(row: sqlite3.Row) -> DataCoverage:
    return DataCoverage(
        asset_id=str(row["asset_id"]),
        dataset=str(row["dataset"]),
        backend=str(row["backend"]),
        range_start=str(row["range_start"]),
        range_end=str(row["range_end"]),
        source=str(row["source"]),
        checked_at=str(row["checked_at"]),
    )
