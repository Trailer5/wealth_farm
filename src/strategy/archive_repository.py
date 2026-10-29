"""SQLite persistence for strategy archives and their append-only lifecycle events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .archive import LifecycleState, StrategyArchive


class SQLiteStrategyArchiveRepository:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategy_archives (
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    registered_on TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version)
                );

                CREATE TABLE IF NOT EXISTS strategy_archive_events (
                    event_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    on_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    ledger_entry_id TEXT,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_backtests (
                    attempt_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    family TEXT NOT NULL,
                    run_on TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    flags_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_forward_signals (
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    signal_date TEXT NOT NULL,
                    weights_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    entry_ids_json TEXT NOT NULL,
                    recorded_on TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version, signal_date)
                );

                CREATE TABLE IF NOT EXISTS strategy_forward_status (
                    status_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )

    def add(self, archive: StrategyArchive, *, on_date: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO strategy_archives (strategy_id, version, payload_json, state, registered_on, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    archive.strategy_id,
                    archive.version,
                    json.dumps(archive.to_dict(), ensure_ascii=False, sort_keys=True),
                    LifecycleState.RESEARCH.value,
                    on_date,
                    _now(),
                ],
            )

    def get(self, strategy_id: str, version: str | None = None) -> tuple[StrategyArchive, LifecycleState, str] | None:
        """Archive, state and registration date; without a version the latest registered one."""

        query = "SELECT payload_json, state, registered_on FROM strategy_archives WHERE strategy_id = ?"
        params: list[Any] = [strategy_id]
        if version is not None:
            query += " AND version = ?"
            params.append(version)
        query += " ORDER BY created_at DESC, version DESC LIMIT 1"
        with closing(self._connect()) as conn:
            row = conn.execute(query, params).fetchone()
        if row is None:
            return None
        return StrategyArchive.from_dict(json.loads(row[0])), LifecycleState(row[1]), row[2]

    def list_latest(self) -> list[tuple[StrategyArchive, LifecycleState, str]]:
        with closing(self._connect()) as conn:
            ids = [row[0] for row in conn.execute("SELECT DISTINCT strategy_id FROM strategy_archives ORDER BY 1")]
        return [item for item in (self.get(strategy_id) for strategy_id in ids) if item is not None]

    def versions(self, strategy_id: str) -> list[dict[str, str]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT version, state, registered_on FROM strategy_archives WHERE strategy_id = ? ORDER BY created_at",
                [strategy_id],
            ).fetchall()
        return [{"version": row[0], "state": row[1], "registered_on": row[2]} for row in rows]

    def record_event(
        self,
        archive: StrategyArchive,
        *,
        event_type: str,
        from_state: LifecycleState | None,
        to_state: LifecycleState | None,
        on_date: str,
        reason: str,
        detail: dict[str, Any],
        ledger_entry_id: str | None = None,
    ) -> str:
        """Append an event; a transition also moves the archive's state in the same transaction."""

        event_id = f"strategy-event:{uuid4()}"
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO strategy_archive_events (event_id, strategy_id, version, event_type, from_state, to_state,"
                " on_date, reason, detail_json, ledger_entry_id, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    event_id,
                    archive.strategy_id,
                    archive.version,
                    event_type,
                    from_state.value if from_state else None,
                    to_state.value if to_state else None,
                    on_date,
                    reason,
                    json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    ledger_entry_id,
                    _now(),
                ],
            )
            if event_type == "transition" and to_state is not None:
                conn.execute(
                    "UPDATE strategy_archives SET state = ? WHERE strategy_id = ? AND version = ?",
                    [to_state.value, archive.strategy_id, archive.version],
                )
        return event_id

    def events(self, strategy_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT event_id, version, event_type, from_state, to_state, on_date, reason, detail_json,"
                " ledger_entry_id FROM strategy_archive_events WHERE strategy_id = ? ORDER BY recorded_at",
                [strategy_id],
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "version": row[1],
                "event_type": row[2],
                "from_state": row[3],
                "to_state": row[4],
                "on_date": row[5],
                "reason": row[6],
                "detail": json.loads(row[7]),
                "ledger_entry_id": row[8],
            }
            for row in rows
        ]

    def record_backtest(
        self,
        archive: StrategyArchive,
        *,
        run_on: str,
        start_date: str,
        end_date: str,
        parameters: dict[str, str],
        summary: dict[str, Any],
    ) -> str:
        """Append one backtest attempt; every run through the public entry is kept (append-only)."""

        attempt_id = f"strategy-backtest:{uuid4()}"
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO strategy_backtests (attempt_id, strategy_id, version, family, run_on, start_date, end_date,"
                " parameters_json, summary_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    attempt_id,
                    archive.strategy_id,
                    archive.version,
                    archive.family,
                    run_on,
                    start_date,
                    end_date,
                    json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str),
                    _now(),
                ],
            )
        return attempt_id

    def backtests(self, family: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT attempt_id, strategy_id, version, run_on, start_date, end_date, parameters_json, summary_json"
                " FROM strategy_backtests WHERE family = ? ORDER BY recorded_at",
                [family],
            ).fetchall()
        return [
            {
                "attempt_id": row[0],
                "strategy_id": row[1],
                "version": row[2],
                "run_on": row[3],
                "start_date": row[4],
                "end_date": row[5],
                "parameters": json.loads(row[6]),
                "summary": json.loads(row[7]),
            }
            for row in rows
        ]

    def record_evaluation(
        self, archive: StrategyArchive, *, as_of_date: str, flags: dict[str, bool], payload: dict[str, Any]
    ) -> str:
        """Append one complete validation (strategy-evaluate); the latest one per version feeds the lifecycle gate."""

        evaluation_id = f"strategy-evaluation:{uuid4()}"
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO strategy_evaluations (evaluation_id, strategy_id, version, as_of_date, flags_json,"
                " payload_json, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    evaluation_id,
                    archive.strategy_id,
                    archive.version,
                    as_of_date,
                    json.dumps(flags, sort_keys=True),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    _now(),
                ],
            )
        return evaluation_id

    def latest_evaluation(self, strategy_id: str, version: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT evaluation_id, as_of_date, flags_json FROM strategy_evaluations"
                " WHERE strategy_id = ? AND version = ? ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
                [strategy_id, version],
            ).fetchone()
        if row is None:
            return None
        return {"evaluation_id": row[0], "as_of_date": row[1], "flags": json.loads(row[2])}

    def record_forward_signal(
        self,
        archive: StrategyArchive,
        *,
        signal_date: str,
        weights: dict[str, float],
        reason: str,
        entry_ids: list[str],
        recorded_on: str,
    ) -> bool:
        """Store the signal of one trading day; a second run on the same day keeps the first record."""

        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO strategy_forward_signals (strategy_id, version, signal_date, weights_json, reason,"
                " entry_ids_json, recorded_on, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    archive.strategy_id,
                    archive.version,
                    signal_date,
                    json.dumps(weights, sort_keys=True),
                    reason,
                    json.dumps(entry_ids),
                    recorded_on,
                    _now(),
                ],
            )
        return cursor.rowcount == 1

    def forward_signals(self, strategy_id: str, version: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT signal_date, weights_json, reason, entry_ids_json, recorded_on FROM strategy_forward_signals"
                " WHERE strategy_id = ? AND version = ? ORDER BY signal_date",
                [strategy_id, version],
            ).fetchall()
        return [
            {
                "signal_date": row[0],
                "weights": json.loads(row[1]),
                "reason": row[2],
                "entry_ids": json.loads(row[3]),
                "recorded_on": row[4],
            }
            for row in rows
        ]

    def record_forward_status(self, archive: StrategyArchive, *, as_of_date: str, payload: dict[str, Any]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO strategy_forward_status (status_id, strategy_id, version, as_of_date, payload_json,"
                " recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    f"strategy-forward:{uuid4()}",
                    archive.strategy_id,
                    archive.version,
                    as_of_date,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    _now(),
                ],
            )

    def latest_forward_status(self, strategy_id: str, version: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload_json FROM strategy_forward_status WHERE strategy_id = ? AND version = ?"
                " ORDER BY recorded_at DESC, rowid DESC LIMIT 1",
                [strategy_id, version],
            ).fetchone()
        return json.loads(row[0]) if row else None

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
