"""SQLite cache for on-demand A-share cross-sections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SQLiteCrossSectionRepository:
    SCHEMA_VERSION = 39
    SCHEMA_DESCRIPTION = "on-demand market snapshots, valuations and report-period financials"

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

                CREATE TABLE IF NOT EXISTS cs_market_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    trade_date TEXT,
                    row_count INTEGER NOT NULL,
                    errors_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cs_market_snapshot_rows (
                    snapshot_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, symbol)
                );

                CREATE TABLE IF NOT EXISTS cs_stock_valuations (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (symbol, trade_date)
                );

                CREATE TABLE IF NOT EXISTS cs_report_rows (
                    report_period TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    announced_on TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (report_period, symbol)
                );

                CREATE TABLE IF NOT EXISTS cs_reference_rows (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (kind, key, symbol)
                );

                CREATE TABLE IF NOT EXISTS cs_fetch_log (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_cs_fetch_log_lookup
                ON cs_fetch_log (kind, key, fetched_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, description) VALUES (?, ?)",
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save_snapshot(
        self,
        snapshot_id: str,
        *,
        fetched_at: str,
        trade_date: str | None,
        rows: list[dict[str, Any]],
        errors: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cs_market_snapshots VALUES (?, ?, ?, ?, ?)",
                [snapshot_id, fetched_at, trade_date, len(rows), json.dumps(errors, ensure_ascii=False)],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO cs_market_snapshot_rows VALUES (?, ?, ?)",
                [[snapshot_id, row["symbol"], _dump(row)] for row in rows],
            )

    def latest_snapshot(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cs_market_snapshots ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _snapshot_header(row)

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            header = conn.execute(
                "SELECT * FROM cs_market_snapshots WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
            if header is None:
                return None
            rows = conn.execute(
                "SELECT payload_json FROM cs_market_snapshot_rows WHERE snapshot_id = ? ORDER BY symbol",
                [snapshot_id],
            ).fetchall()
        return {**_snapshot_header(header), "rows": [json.loads(r["payload_json"]) for r in rows]}

    def upsert_valuations(self, symbol: str, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO cs_stock_valuations VALUES (?, ?, ?)",
                [[symbol, row["trade_date"], _dump(row)] for row in rows if row.get("trade_date")],
            )

    def list_valuations(self, symbol: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM cs_stock_valuations
                WHERE symbol = ? AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
                """,
                [symbol, start_date, end_date],
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_report_rows(self, report_period: str, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cs_report_rows WHERE report_period = ?", [report_period])
            conn.executemany(
                "INSERT OR REPLACE INTO cs_report_rows VALUES (?, ?, ?, ?)",
                [
                    [report_period, row["symbol"], row.get("announced_on"), _dump(row)]
                    for row in rows
                ],
            )

    def list_report_rows(self, report_period: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM cs_report_rows WHERE report_period = ? ORDER BY symbol",
                [report_period],
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_reference(self, kind: str, key: str, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cs_reference_rows WHERE kind = ? AND key = ?", [kind, key])
            conn.executemany(
                "INSERT OR REPLACE INTO cs_reference_rows VALUES (?, ?, ?, ?)",
                [[kind, key, row["symbol"], _dump(row)] for row in rows],
            )

    def list_reference(self, kind: str, key: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM cs_reference_rows WHERE kind = ? AND key = ? ORDER BY symbol",
                [kind, key],
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def log_fetch(self, kind: str, key: str, *, fetched_at: str, record_count: int, error: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cs_fetch_log VALUES (?, ?, ?, ?, ?)",
                [kind, key, fetched_at, record_count, error],
            )

    def last_success(self, kind: str, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(fetched_at) AS fetched_at FROM cs_fetch_log
                WHERE kind = ? AND key = ? AND error IS NULL AND record_count > 0
                """,
                [kind, key],
            ).fetchone()
        return None if row is None else row["fetched_at"]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def _dump(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, default=str)


def _snapshot_header(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "fetched_at": row["fetched_at"],
        "trade_date": row["trade_date"],
        "row_count": row["row_count"],
        "errors": json.loads(row["errors_json"]),
    }
