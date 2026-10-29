"""SQLite store for discovery funnel runs (each run is a point-in-time record)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SQLiteDiscoveryFunnelRepository:
    SCHEMA_VERSION = 40
    SCHEMA_DESCRIPTION = "multi-strategy discovery funnel runs"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
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

                CREATE TABLE IF NOT EXISTS discovery_funnel_runs (
                    run_id TEXT PRIMARY KEY,
                    as_of_date TEXT NOT NULL,
                    scope_label TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, description) VALUES (?, ?)",
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save(self, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO discovery_funnel_runs VALUES (?, ?, ?, ?, ?)",
                [
                    payload["run_id"],
                    payload["as_of_date"],
                    payload["scope"]["label"],
                    payload["created_at"],
                    json.dumps(payload, ensure_ascii=False, default=str),
                ],
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM discovery_funnel_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
        return None if row is None else json.loads(row["payload_json"])

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, as_of_date, scope_label, created_at
                FROM discovery_funnel_runs ORDER BY created_at DESC LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()
