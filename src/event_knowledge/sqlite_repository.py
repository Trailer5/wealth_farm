"""SQLite persistence for source-backed research events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ResearchEvent


class SQLiteEventRepository:
    """Persist idempotent events and query them by asset."""

    SCHEMA_VERSION = 9
    SCHEMA_DESCRIPTION = "source-backed research events"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the event schema."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS research_events (
                    event_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_record_id TEXT NOT NULL,
                    source_url TEXT,
                    confidence REAL NOT NULL
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
                    extraction_method TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE,
                    UNIQUE (source, source_record_id, event_type)
                );

                CREATE INDEX IF NOT EXISTS idx_research_events_asset_date
                ON research_events (asset_id, event_date, event_type);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def upsert_events(self, events: list[ResearchEvent]) -> int:
        """Insert or update idempotent source events."""

        if not events:
            return 0
        rows = [
            [
                event.event_id,
                event.asset_id,
                event.event_type,
                event.event_date,
                event.title,
                event.summary,
                event.source,
                event.source_record_id,
                event.source_url,
                event.confidence,
                event.extraction_method,
                event.rule_version,
                json.dumps(event.raw, ensure_ascii=False, sort_keys=True, default=str),
            ]
            for event in events
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO research_events (
                    event_id,
                    asset_id,
                    event_type,
                    event_date,
                    title,
                    summary,
                    source,
                    source_record_id,
                    source_url,
                    confidence,
                    extraction_method,
                    rule_version,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source, source_record_id, event_type) DO UPDATE SET
                    asset_id = excluded.asset_id,
                    event_date = excluded.event_date,
                    title = excluded.title,
                    summary = excluded.summary,
                    source_url = excluded.source_url,
                    confidence = excluded.confidence,
                    extraction_method = excluded.extraction_method,
                    rule_version = excluded.rule_version,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(events)

    def get_event(self, event_id: str) -> ResearchEvent | None:
        """Return one event."""

        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                FROM research_events
                WHERE event_id = ?
                """,
                [event_id],
            ).fetchone()
        return _event_from_row(row) if row is not None else None

    def list_events(
        self,
        asset_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        event_type: str | None = None,
    ) -> list[ResearchEvent]:
        """Return events for one canonical asset."""

        conditions = ["asset_id = ?"]
        params = [asset_id]
        if start_date is not None:
            conditions.append("event_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("event_date <= ?")
            params.append(end_date)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_EVENT_COLUMNS}
                FROM research_events
                WHERE {" AND ".join(conditions)}
                ORDER BY event_date DESC, event_id
                """,
                params,
            ).fetchall()
        return [_event_from_row(row) for row in rows]

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


_EVENT_COLUMNS = """
    event_id,
    asset_id,
    event_type,
    event_date,
    title,
    summary,
    source,
    source_record_id,
    source_url,
    confidence,
    extraction_method,
    rule_version,
    raw_json,
    created_at
"""


def _event_from_row(row: sqlite3.Row) -> ResearchEvent:
    return ResearchEvent(
        event_id=str(row["event_id"]),
        asset_id=str(row["asset_id"]),
        event_type=str(row["event_type"]),
        event_date=str(row["event_date"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        source=str(row["source"]),
        source_record_id=str(row["source_record_id"]),
        source_url=row["source_url"],
        confidence=float(row["confidence"]),
        extraction_method=str(row["extraction_method"]),
        rule_version=str(row["rule_version"]),
        raw=json.loads(row["raw_json"]),
        created_at=str(row["created_at"]),
    )
