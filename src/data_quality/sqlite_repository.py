"""SQLite persistence for lightweight task and quality records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import QualityCheckResult, TaskRun


class SQLiteDataQualityRepository:
    """Persist task runs and their small set of quality checks."""

    SCHEMA_VERSION = 6
    SCHEMA_DESCRIPTION = "lightweight task runs and quality check results"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create quality tables if they do not exist."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
                    dataset TEXT,
                    source TEXT,
                    asset_id TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    record_count INTEGER,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_task_runs_status_started
                ON task_runs (status, started_at);

                CREATE TABLE IF NOT EXISTS quality_check_results (
                    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    level TEXT NOT NULL CHECK (level IN ('pass', 'warn', 'fail')),
                    message TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES task_runs (run_id) ON DELETE CASCADE,
                    UNIQUE (run_id, check_name)
                );

                CREATE INDEX IF NOT EXISTS idx_quality_checks_run_level
                ON quality_check_results (run_id, level);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def create_task(self, run: TaskRun) -> None:
        """Insert one running task."""

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_runs (
                    run_id,
                    task_name,
                    status,
                    dataset,
                    source,
                    asset_id,
                    started_at,
                    finished_at,
                    record_count,
                    error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run.run_id,
                    run.task_name,
                    run.status,
                    run.dataset,
                    run.source,
                    run.asset_id,
                    run.started_at,
                    run.finished_at,
                    run.record_count,
                    run.error,
                ],
            )

    def finish_task(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str,
        record_count: int | None,
        error: str | None,
    ) -> TaskRun:
        """Complete or fail one existing task."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_runs
                SET status = ?, finished_at = ?, record_count = ?, error = ?
                WHERE run_id = ?
                """,
                [status, finished_at, record_count, error, run_id],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown run_id: {run_id}")
        run = self.get_task(run_id)
        if run is None:
            raise RuntimeError("Task disappeared after update")
        return run

    def save_checks(self, checks: list[QualityCheckResult]) -> None:
        """Insert or replace one result per check name."""

        if not checks:
            return
        rows = [
            [
                check.run_id,
                check.check_name,
                check.level,
                check.message,
                json.dumps(check.metrics, ensure_ascii=False, sort_keys=True, default=str),
            ]
            for check in checks
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO quality_check_results (
                    run_id,
                    check_name,
                    level,
                    message,
                    metrics_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (run_id, check_name) DO UPDATE SET
                    level = excluded.level,
                    message = excluded.message,
                    metrics_json = excluded.metrics_json,
                    created_at = CURRENT_TIMESTAMP
                """,
                rows,
            )

    def get_task(self, run_id: str) -> TaskRun | None:
        """Return one task by id."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    run_id,
                    task_name,
                    status,
                    dataset,
                    source,
                    asset_id,
                    started_at,
                    finished_at,
                    record_count,
                    error
                FROM task_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        return _task_from_row(row) if row is not None else None

    def list_tasks(self, limit: int = 50) -> list[TaskRun]:
        """Return recent task runs, newest first."""

        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    run_id,
                    task_name,
                    status,
                    dataset,
                    source,
                    asset_id,
                    started_at,
                    finished_at,
                    record_count,
                    error
                FROM task_runs
                ORDER BY started_at DESC, rowid DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_checks(self, run_id: str) -> list[QualityCheckResult]:
        """Return checks for one task."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    check_id,
                    run_id,
                    check_name,
                    level,
                    message,
                    metrics_json,
                    created_at
                FROM quality_check_results
                WHERE run_id = ?
                ORDER BY check_id
                """,
                [run_id],
            ).fetchall()
        return [_check_from_row(row) for row in rows]

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


def _task_from_row(row: sqlite3.Row) -> TaskRun:
    return TaskRun(
        run_id=str(row["run_id"]),
        task_name=str(row["task_name"]),
        status=str(row["status"]),
        dataset=row["dataset"],
        source=row["source"],
        asset_id=row["asset_id"],
        started_at=str(row["started_at"]),
        finished_at=row["finished_at"],
        record_count=row["record_count"],
        error=row["error"],
    )


def _check_from_row(row: sqlite3.Row) -> QualityCheckResult:
    return QualityCheckResult(
        run_id=str(row["run_id"]),
        check_name=str(row["check_name"]),
        level=str(row["level"]),
        message=str(row["message"]),
        metrics=json.loads(row["metrics_json"]),
        check_id=int(row["check_id"]),
        created_at=str(row["created_at"]),
    )
