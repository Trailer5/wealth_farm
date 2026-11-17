"""SQLite storage for debates, positions, rebuttals and signposts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Debate, DebateRecord, Participation, Position, Rebuttal, Signpost

_TABLES = {
    "debate_positions": ("position_id", Position),
    "debate_rebuttals": ("rebuttal_id", Rebuttal),
    "debate_signposts": ("signpost_id", Signpost),
    "debate_participations": ("participation_id", Participation),
}


class SQLiteDebateRepository:
    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS debates (
                    debate_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS debate_positions (
                    position_id TEXT PRIMARY KEY,
                    debate_id TEXT NOT NULL REFERENCES debates (debate_id),
                    seq INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS debate_rebuttals (
                    rebuttal_id TEXT PRIMARY KEY,
                    debate_id TEXT NOT NULL REFERENCES debates (debate_id),
                    seq INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS debate_signposts (
                    signpost_id TEXT PRIMARY KEY,
                    debate_id TEXT NOT NULL REFERENCES debates (debate_id),
                    seq INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS debate_participations (
                    participation_id TEXT PRIMARY KEY,
                    debate_id TEXT NOT NULL REFERENCES debates (debate_id),
                    seq INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def save_debate(self, debate: Debate) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO debates (debate_id, user_id, status, as_of_date, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (debate_id) DO UPDATE SET
                    status = excluded.status, payload_json = excluded.payload_json
                """,
                (
                    debate.debate_id,
                    debate.user_id,
                    debate.status,
                    debate.as_of_date,
                    json.dumps(debate.to_dict(), ensure_ascii=False),
                ),
            )

    def get_debate(self, debate_id: str) -> Debate | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM debates WHERE debate_id = ?", (debate_id,)).fetchone()
        return Debate.from_dict(json.loads(row["payload_json"])) if row else None

    def list_debates(self, *, user_id: str | None = None, status: str | None = None) -> list[Debate]:
        clauses, params = [], []
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT payload_json FROM debates{where} ORDER BY as_of_date, debate_id", params).fetchall()
        return [Debate.from_dict(json.loads(row["payload_json"])) for row in rows]

    def next_seq(self, table: str, debate_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(f"SELECT COALESCE(MAX(seq), 0) AS seq FROM {table} WHERE debate_id = ?", (debate_id,)).fetchone()
        return int(row["seq"]) + 1

    def save(self, table: str, item: Position | Rebuttal | Signpost | Participation, seq: int | None = None) -> None:
        key, _ = _TABLES[table]
        payload = json.dumps(item.to_dict(), ensure_ascii=False)
        with self._connect() as conn:
            if seq is None:
                conn.execute(f"UPDATE {table} SET payload_json = ? WHERE {key} = ?", (payload, getattr(item, key)))
            else:
                conn.execute(
                    f"INSERT INTO {table} ({key}, debate_id, seq, payload_json) VALUES (?, ?, ?, ?)",
                    (getattr(item, key), item.debate_id, seq, payload),
                )

    def record(self, debate_id: str) -> DebateRecord | None:
        debate = self.get_debate(debate_id)
        if debate is None:
            return None
        loaded = {}
        with self._connect() as conn:
            for table, (_, model) in _TABLES.items():
                rows = conn.execute(
                    f"SELECT payload_json FROM {table} WHERE debate_id = ? ORDER BY seq", (debate_id,)
                ).fetchall()
                loaded[table] = tuple(model.from_dict(json.loads(row["payload_json"])) for row in rows)
        return DebateRecord(
            debate=debate,
            positions=loaded["debate_positions"],
            rebuttals=loaded["debate_rebuttals"],
            signposts=loaded["debate_signposts"],
            participations=loaded["debate_participations"],
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
