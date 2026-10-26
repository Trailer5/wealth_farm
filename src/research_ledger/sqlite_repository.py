"""SQLite storage for the append-only research ledger."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .models import (
    LedgerConflictError,
    LedgerEntry,
    LedgerEntryType,
    LedgerScope,
    Review,
    Settlement,
)


_APPEND_ONLY_TABLES = (
    "research_ledger_entries",
    "research_ledger_settlements",
    "research_ledger_reviews",
)


class SQLiteResearchLedgerRepository:
    SCHEMA_VERSION = 36
    SCHEMA_DESCRIPTION = "append-only research ledger"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        triggers = "\n".join(
            f"""
            CREATE TRIGGER IF NOT EXISTS prevent_{table}_update
            BEFORE UPDATE ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'research ledger is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS prevent_{table}_delete
            BEFORE DELETE ON {table}
            BEGIN
                SELECT RAISE(ABORT, 'research ledger is append-only');
            END;
            """
            for table in _APPEND_ONLY_TABLES
        )
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS research_ledger_entries (
                    entry_id TEXT PRIMARY KEY,
                    entry_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    user_id TEXT,
                    source_ref TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    effective_until TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    supersedes_entry_id TEXT
                        REFERENCES research_ledger_entries (entry_id),
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_research_ledger_owner
                ON research_ledger_entries (user_id, subject_id);

                CREATE INDEX IF NOT EXISTS idx_research_ledger_source
                ON research_ledger_entries (source_ref);

                CREATE TABLE IF NOT EXISTS research_ledger_settlements (
                    settlement_id TEXT PRIMARY KEY,
                    entry_id TEXT NOT NULL
                        REFERENCES research_ledger_entries (entry_id),
                    checkpoint_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_research_ledger_settlement_entry
                ON research_ledger_settlements (entry_id, checkpoint_date);

                CREATE TABLE IF NOT EXISTS research_ledger_reviews (
                    review_id TEXT PRIMARY KEY,
                    entry_id TEXT NOT NULL
                        REFERENCES research_ledger_entries (entry_id),
                    settlement_id TEXT NOT NULL
                        REFERENCES research_ledger_settlements (
                            settlement_id
                        ),
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
                + triggers
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

    def append_entries(
        self,
        entries: Sequence[LedgerEntry],
    ) -> tuple[LedgerEntry, ...]:
        """Insert all entries atomically; identical re-appends are no-ops."""

        stored = []
        with self._connect() as conn:
            for entry in entries:
                existing = self._entry(conn, entry.entry_id)
                if existing is not None:
                    if not existing.same_fact(entry):
                        raise LedgerConflictError(
                            f"Ledger entry {entry.entry_id} already "
                            "records a different fact"
                        )
                    stored.append(existing)
                    continue
                conn.execute(
                    """
                    INSERT INTO research_ledger_entries (
                        entry_id,
                        entry_type,
                        scope,
                        user_id,
                        source_ref,
                        artifact_id,
                        subject_id,
                        as_of_date,
                        effective_until,
                        payload_json,
                        content_digest,
                        supersedes_entry_id,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        entry.entry_id,
                        entry.entry_type.value,
                        entry.scope.value,
                        entry.user_id,
                        entry.source_ref,
                        entry.artifact_id,
                        entry.subject_id,
                        entry.as_of_date,
                        entry.effective_until,
                        _json(entry.payload),
                        entry.content_digest,
                        entry.supersedes_entry_id,
                        entry.recorded_at,
                    ],
                )
                stored.append(entry)
        return tuple(stored)

    def get_entry(self, entry_id: str) -> LedgerEntry | None:
        with self._connect() as conn:
            return self._entry(conn, entry_id)

    def list_entries(
        self,
        *,
        user_id: str | None,
        entry_type: LedgerEntryType | None = None,
        subject_id: str | None = None,
        source_ref: str | None = None,
    ) -> tuple[LedgerEntry, ...]:
        """Entries visible to user_id: objective ones plus the user's own."""

        clauses = ["(scope = ? OR (scope = ? AND user_id = ?))"]
        params: list[object] = [
            LedgerScope.OBJECTIVE.value,
            LedgerScope.PERSONAL.value,
            user_id,
        ]
        for column, value in (
            ("entry_type", entry_type.value if entry_type else None),
            ("subject_id", subject_id),
            ("source_ref", source_ref),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM research_ledger_entries
                WHERE {" AND ".join(clauses)}
                ORDER BY as_of_date, entry_id
                """,
                params,
            ).fetchall()
        return tuple(_entry_from_row(row) for row in rows)

    def all_entries(self) -> tuple[LedgerEntry, ...]:
        """Every entry regardless of owner, for system settlement runs."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM research_ledger_entries
                ORDER BY as_of_date, entry_id
                """
            ).fetchall()
        return tuple(_entry_from_row(row) for row in rows)

    def superseded_entry_ids(self) -> frozenset[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT supersedes_entry_id
                FROM research_ledger_entries
                WHERE supersedes_entry_id IS NOT NULL
                """
            ).fetchall()
        return frozenset(row["supersedes_entry_id"] for row in rows)

    def has_source(self, source_ref: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM research_ledger_entries
                WHERE source_ref = ?
                LIMIT 1
                """,
                [source_ref],
            ).fetchone()
        return row is not None

    def append_settlement(
        self,
        settlement: Settlement,
    ) -> tuple[Settlement, bool]:
        """Return the stored settlement and whether it was newly inserted."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM research_ledger_settlements
                WHERE settlement_id = ?
                """,
                [settlement.settlement_id],
            ).fetchone()
            if row is not None:
                return (
                    Settlement.from_dict(json.loads(row["payload_json"])),
                    False,
                )
            conn.execute(
                """
                INSERT INTO research_ledger_settlements (
                    settlement_id,
                    entry_id,
                    checkpoint_date,
                    status,
                    payload_json,
                    computed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    settlement.settlement_id,
                    settlement.entry_id,
                    settlement.checkpoint_date,
                    settlement.status.value,
                    _json(settlement.to_dict()),
                    settlement.computed_at,
                ],
            )
        return settlement, True

    def list_settlements(self, entry_id: str) -> tuple[Settlement, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM research_ledger_settlements
                WHERE entry_id = ?
                ORDER BY checkpoint_date, computed_at, settlement_id
                """,
                [entry_id],
            ).fetchall()
        return tuple(
            Settlement.from_dict(json.loads(row["payload_json"]))
            for row in rows
        )

    def get_settlement(self, settlement_id: str) -> Settlement | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM research_ledger_settlements
                WHERE settlement_id = ?
                """,
                [settlement_id],
            ).fetchone()
        if row is None:
            return None
        return Settlement.from_dict(json.loads(row["payload_json"]))

    def append_review(self, review: Review) -> Review:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM research_ledger_reviews
                WHERE review_id = ?
                """,
                [review.review_id],
            ).fetchone()
            if row is not None:
                return Review.from_dict(json.loads(row["payload_json"]))
            conn.execute(
                """
                INSERT INTO research_ledger_reviews (
                    review_id,
                    entry_id,
                    settlement_id,
                    payload_json,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    review.review_id,
                    review.entry_id,
                    review.settlement_id,
                    _json(review.to_dict()),
                    review.recorded_at,
                ],
            )
        return review

    def list_reviews(self, entry_id: str) -> tuple[Review, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM research_ledger_reviews
                WHERE entry_id = ?
                ORDER BY recorded_at, review_id
                """,
                [entry_id],
            ).fetchall()
        return tuple(
            Review.from_dict(json.loads(row["payload_json"]))
            for row in rows
        )

    def _entry(
        self,
        conn: sqlite3.Connection,
        entry_id: str,
    ) -> LedgerEntry | None:
        row = conn.execute(
            "SELECT * FROM research_ledger_entries WHERE entry_id = ?",
            [entry_id],
        ).fetchone()
        return None if row is None else _entry_from_row(row)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()


def _entry_from_row(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        entry_id=row["entry_id"],
        entry_type=LedgerEntryType(row["entry_type"]),
        scope=LedgerScope(row["scope"]),
        user_id=row["user_id"],
        source_ref=row["source_ref"],
        artifact_id=row["artifact_id"],
        subject_id=row["subject_id"],
        as_of_date=row["as_of_date"],
        effective_until=row["effective_until"],
        payload=json.loads(row["payload_json"]),
        content_digest=row["content_digest"],
        supersedes_entry_id=row["supersedes_entry_id"],
        recorded_at=row["recorded_at"],
    )


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
