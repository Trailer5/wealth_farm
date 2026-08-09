"""SQLite persistence for complete event-name discovery runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import EventNameDiscoveryRun


class EventNameDiscoveryConflictError(ValueError):
    """Raised when one deterministic run ID has different content."""


class SQLiteEventNameDiscoveryRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS event_name_discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS
                event_name_exploration_handoffs (
                    session_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id)
                        REFERENCES event_name_discovery_runs (run_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_event_name_discovery_event_date
                ON event_name_discovery_runs (event_id, as_of_date)
                """
            )

    def save(
        self,
        run: EventNameDiscoveryRun,
    ) -> EventNameDiscoveryRun:
        self.initialize()
        with self._connect() as connection:
            self._save_run_in_connection(connection, run)
        restored = self.get(run.run_id)
        if restored is None:
            raise RuntimeError("saved discovery run could not be restored")
        return restored

    def save_bundle(
        self,
        run: EventNameDiscoveryRun,
        sessions: tuple[object, ...],
    ) -> EventNameDiscoveryRun:
        """Atomically save discovery and its Agent exploration handoff."""

        self.initialize()
        with self._connect() as connection:
            saved_run_id = self._save_run_in_connection(
                connection,
                run,
            )
            for session in sessions:
                task = session.task
                payload = {
                    "session_id": session.session_id,
                    "run_id": saved_run_id,
                    "asset_id": task.asset_id,
                    "task": task.to_dict(),
                    "baseline_path": (
                        str(session.baseline_path)
                        if session.baseline_path is not None
                        else None
                    ),
                    "hypotheses": [
                        {
                            "hypothesis_id": item.hypothesis_id,
                            "statement": item.statement,
                            "status": item.status.value,
                            "supporting_evidence_refs": list(
                                item.supporting_evidence_refs
                            ),
                            "counterevidence_refs": list(
                                item.counterevidence_refs
                            ),
                        }
                        for item in session.hypotheses
                    ],
                }
                serialized = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                existing_session = connection.execute(
                    """
                    SELECT payload_json
                    FROM event_name_exploration_handoffs
                    WHERE session_id = ?
                    """,
                    (session.session_id,),
                ).fetchone()
                if existing_session is not None:
                    if str(existing_session["payload_json"]) != serialized:
                        raise EventNameDiscoveryConflictError(
                            "exploration session ID has different content"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO event_name_exploration_handoffs (
                        session_id, run_id, asset_id, payload_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        session.session_id,
                        saved_run_id,
                        task.asset_id,
                        serialized,
                    ),
                )
        restored = self.get(run.run_id)
        if restored is None:
            raise RuntimeError("saved discovery bundle is unavailable")
        return restored

    def list_handoffs(self, run_id: str) -> tuple[dict, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM event_name_exploration_handoffs
                WHERE run_id = ?
                ORDER BY session_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            json.loads(str(row["payload_json"])) for row in rows
        )

    def _save_run_in_connection(
        self,
        connection: sqlite3.Connection,
        run: EventNameDiscoveryRun,
    ) -> str:
        payload_dict = run.to_dict()
        payload = json.dumps(
            payload_dict,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = connection.execute(
            """
            SELECT payload_json
            FROM event_name_discovery_runs
            WHERE run_id = ?
            """,
            (run.run_id,),
        ).fetchone()
        if existing is not None:
            existing_payload = json.loads(
                str(existing["payload_json"])
            )
            if _semantic_payload(existing_payload) != _semantic_payload(
                payload_dict
            ):
                raise EventNameDiscoveryConflictError(
                    "deterministic run ID already has different content"
                )
            return run.run_id
        connection.execute(
            """
            INSERT INTO event_name_discovery_runs (
                run_id, event_id, as_of_date,
                payload_json, completed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.event.event_id,
                run.spec.as_of_date,
                payload,
                run.completed_at,
            ),
        )
        return run.run_id

    def get(self, run_id: str) -> EventNameDiscoveryRun | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM event_name_discovery_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return EventNameDiscoveryRun.from_dict(
            json.loads(str(row["payload_json"]))
        )

    def list_for_event(
        self,
        event_id: str,
    ) -> tuple[EventNameDiscoveryRun, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM event_name_discovery_runs
                WHERE event_id = ?
                ORDER BY as_of_date, completed_at
                """,
                (event_id,),
            ).fetchall()
        return tuple(
            EventNameDiscoveryRun.from_dict(
                json.loads(str(row["payload_json"]))
            )
            for row in rows
        )

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


def _semantic_payload(payload: dict) -> dict:
    value = dict(payload)
    value.pop("started_at", None)
    value.pop("completed_at", None)
    return value
