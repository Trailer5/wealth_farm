"""SQLite persistence for versioned research universes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    MembershipStatus,
    ResearchUniverse,
    UniverseMembership,
    UniverseQualitySummary,
    UniverseSnapshot,
)


class SQLiteResearchUniverseRepository:
    SCHEMA_VERSION = 21
    SCHEMA_DESCRIPTION = "versioned research universes"

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

                CREATE TABLE IF NOT EXISTS research_universes (
                    universe_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    name TEXT NOT NULL,
                    index_asset_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    historical_membership INTEGER NOT NULL,
                    total_source_records INTEGER NOT NULL,
                    unique_source_symbols INTEGER NOT NULL,
                    resolved_count INTEGER NOT NULL,
                    unresolved_count INTEGER NOT NULL,
                    inactive_count INTEGER NOT NULL,
                    duplicate_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (universe_id, version)
                );

                CREATE TABLE IF NOT EXISTS universe_memberships (
                    universe_id TEXT NOT NULL,
                    universe_version TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    asset_id TEXT,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (
                        universe_id,
                        universe_version,
                        source_symbol
                    ),
                    FOREIGN KEY (universe_id, universe_version)
                        REFERENCES research_universes (
                            universe_id,
                            version
                        )
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

    def save(self, snapshot: UniverseSnapshot) -> UniverseSnapshot:
        universe = snapshot.universe
        quality = snapshot.quality
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_universes (
                    universe_id,
                    version,
                    name,
                    index_asset_id,
                    source,
                    acquired_at,
                    effective_date,
                    historical_membership,
                    total_source_records,
                    unique_source_symbols,
                    resolved_count,
                    unresolved_count,
                    inactive_count,
                    duplicate_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (universe_id, version) DO UPDATE SET
                    name = excluded.name,
                    index_asset_id = excluded.index_asset_id,
                    source = excluded.source,
                    acquired_at = excluded.acquired_at,
                    effective_date = excluded.effective_date,
                    historical_membership = excluded.historical_membership,
                    total_source_records = excluded.total_source_records,
                    unique_source_symbols = excluded.unique_source_symbols,
                    resolved_count = excluded.resolved_count,
                    unresolved_count = excluded.unresolved_count,
                    inactive_count = excluded.inactive_count,
                    duplicate_count = excluded.duplicate_count
                """,
                [
                    universe.universe_id,
                    universe.version,
                    universe.name,
                    universe.index_asset_id,
                    universe.source,
                    universe.acquired_at,
                    universe.effective_date,
                    1 if universe.historical_membership else 0,
                    quality.total_source_records,
                    quality.unique_source_symbols,
                    quality.resolved_count,
                    quality.unresolved_count,
                    quality.inactive_count,
                    quality.duplicate_count,
                ],
            )
            conn.execute(
                """
                DELETE FROM universe_memberships
                WHERE universe_id = ? AND universe_version = ?
                """,
                [universe.universe_id, universe.version],
            )
            conn.executemany(
                """
                INSERT INTO universe_memberships (
                    universe_id,
                    universe_version,
                    source_symbol,
                    source_name,
                    exchange,
                    effective_date,
                    status,
                    asset_id,
                    reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        member.universe_id,
                        member.universe_version,
                        member.source_symbol,
                        member.source_name,
                        member.exchange,
                        member.effective_date,
                        member.status.value,
                        member.asset_id,
                        member.reason,
                    ]
                    for member in snapshot.memberships
                ],
            )
        return snapshot

    def get(
        self,
        universe_id: str,
        version: str,
    ) -> UniverseSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM research_universes
                WHERE universe_id = ? AND version = ?
                """,
                [universe_id, version],
            ).fetchone()
            if row is None:
                return None
            member_rows = conn.execute(
                """
                SELECT *
                FROM universe_memberships
                WHERE universe_id = ?
                  AND universe_version = ?
                ORDER BY source_symbol
                """,
                [universe_id, version],
            ).fetchall()
        return UniverseSnapshot(
            universe=ResearchUniverse(
                universe_id=str(row["universe_id"]),
                name=str(row["name"]),
                index_asset_id=str(row["index_asset_id"]),
                source=str(row["source"]),
                version=str(row["version"]),
                acquired_at=str(row["acquired_at"]),
                effective_date=str(row["effective_date"]),
                historical_membership=bool(
                    row["historical_membership"]
                ),
            ),
            memberships=tuple(
                UniverseMembership(
                    universe_id=str(member["universe_id"]),
                    universe_version=str(
                        member["universe_version"]
                    ),
                    source_symbol=str(member["source_symbol"]),
                    source_name=str(member["source_name"]),
                    exchange=str(member["exchange"]),
                    effective_date=str(member["effective_date"]),
                    status=MembershipStatus(member["status"]),
                    asset_id=member["asset_id"],
                    reason=str(member["reason"]),
                )
                for member in member_rows
            ),
            quality=UniverseQualitySummary(
                total_source_records=int(
                    row["total_source_records"]
                ),
                unique_source_symbols=int(
                    row["unique_source_symbols"]
                ),
                resolved_count=int(row["resolved_count"]),
                unresolved_count=int(row["unresolved_count"]),
                inactive_count=int(row["inactive_count"]),
                duplicate_count=int(row["duplicate_count"]),
            ),
        )

    def latest(
        self,
        universe_id: str,
    ) -> UniverseSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT version
                FROM research_universes
                WHERE universe_id = ?
                ORDER BY effective_date DESC, acquired_at DESC
                LIMIT 1
                """,
                [universe_id],
            ).fetchone()
        return (
            self.get(universe_id, str(row["version"]))
            if row
            else None
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
