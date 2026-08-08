"""SQLite persistence for industry assignments and peer sets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    IndustryAssignment,
    PeerSet,
    PeerSetMember,
)


class SQLitePeerAnalysisRepository:
    SCHEMA_VERSION = 19
    SCHEMA_DESCRIPTION = "industry assignments and peer sets"

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

                CREATE TABLE IF NOT EXISTS industry_assignments (
                    asset_id TEXT NOT NULL,
                    industry_code TEXT NOT NULL,
                    industry_name TEXT NOT NULL,
                    classification_source TEXT NOT NULL,
                    classification_version TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (
                        asset_id,
                        classification_source,
                        classification_version,
                        effective_date
                    )
                );

                CREATE TABLE IF NOT EXISTS peer_sets (
                    peer_set_id TEXT PRIMARY KEY,
                    target_asset_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    version TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS peer_set_members (
                    peer_set_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    included INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    cycle_note TEXT NOT NULL,
                    PRIMARY KEY (peer_set_id, asset_id),
                    FOREIGN KEY (peer_set_id)
                        REFERENCES peer_sets(peer_set_id)
                        ON DELETE CASCADE
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

    def save_industry(
        self,
        assignment: IndustryAssignment,
    ) -> IndustryAssignment:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO industry_assignments (
                    asset_id,
                    industry_code,
                    industry_name,
                    classification_source,
                    classification_version,
                    effective_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    asset_id,
                    classification_source,
                    classification_version,
                    effective_date
                ) DO UPDATE SET
                    industry_code = excluded.industry_code,
                    industry_name = excluded.industry_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    assignment.asset_id,
                    assignment.industry_code,
                    assignment.industry_name,
                    assignment.classification_source,
                    assignment.classification_version,
                    assignment.effective_date,
                ],
            )
        return assignment

    def get_industry(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> IndustryAssignment | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM industry_assignments
                WHERE asset_id = ?
                  AND effective_date <= ?
                ORDER BY effective_date DESC, updated_at DESC
                LIMIT 1
                """,
                [asset_id, as_of_date],
            ).fetchone()
        return _industry_from_row(row) if row else None

    def save_peer_set(self, peer_set: PeerSet) -> PeerSet:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO peer_sets (
                    peer_set_id,
                    target_asset_id,
                    name,
                    source,
                    version,
                    effective_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (peer_set_id) DO UPDATE SET
                    target_asset_id = excluded.target_asset_id,
                    name = excluded.name,
                    source = excluded.source,
                    version = excluded.version,
                    effective_date = excluded.effective_date,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    peer_set.peer_set_id,
                    peer_set.target_asset_id,
                    peer_set.name,
                    peer_set.source,
                    peer_set.version,
                    peer_set.effective_date,
                ],
            )
            conn.execute(
                "DELETE FROM peer_set_members WHERE peer_set_id = ?",
                [peer_set.peer_set_id],
            )
            conn.executemany(
                """
                INSERT INTO peer_set_members (
                    peer_set_id,
                    asset_id,
                    included,
                    reason,
                    cycle_note
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    [
                        peer_set.peer_set_id,
                        member.asset_id,
                        1 if member.included else 0,
                        member.reason,
                        member.cycle_note,
                    ]
                    for member in peer_set.members
                ],
            )
        return peer_set

    def get_peer_set(
        self,
        peer_set_id: str,
        *,
        as_of_date: str,
    ) -> PeerSet | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM peer_sets
                WHERE peer_set_id = ?
                  AND effective_date <= ?
                """,
                [peer_set_id, as_of_date],
            ).fetchone()
            if row is None:
                return None
            member_rows = conn.execute(
                """
                SELECT asset_id, included, reason, cycle_note
                FROM peer_set_members
                WHERE peer_set_id = ?
                ORDER BY asset_id
                """,
                [peer_set_id],
            ).fetchall()
        return PeerSet(
            peer_set_id=str(row["peer_set_id"]),
            target_asset_id=str(row["target_asset_id"]),
            name=str(row["name"]),
            source=str(row["source"]),
            version=str(row["version"]),
            effective_date=str(row["effective_date"]),
            members=tuple(
                PeerSetMember(
                    asset_id=str(member["asset_id"]),
                    included=bool(member["included"]),
                    reason=str(member["reason"]),
                    cycle_note=str(member["cycle_note"]),
                )
                for member in member_rows
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute(
                    "PRAGMA foreign_keys = ON"
                )
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _industry_from_row(row: sqlite3.Row) -> IndustryAssignment:
    return IndustryAssignment(
        asset_id=str(row["asset_id"]),
        industry_code=str(row["industry_code"]),
        industry_name=str(row["industry_name"]),
        classification_source=str(
            row["classification_source"]
        ),
        classification_version=str(
            row["classification_version"]
        ),
        effective_date=str(row["effective_date"]),
    )
