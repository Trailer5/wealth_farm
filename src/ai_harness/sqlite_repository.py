"""SQLite persistence for AI tasks and evidence snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import AnalysisTask, EvidenceReference, ToolExecution


class SQLiteAIHarnessRepository:
    SCHEMA_VERSION = 14
    SCHEMA_DESCRIPTION = "AI tool calls and structured analysis output"

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

                CREATE TABLE IF NOT EXISTS ai_analysis_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('context_ready', 'completed', 'failed')),
                    model_name TEXT,
                    result_json TEXT NOT NULL,
                    citations_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_evidence_refs (
                    task_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (task_id, evidence_id),
                    FOREIGN KEY (task_id)
                        REFERENCES ai_analysis_tasks (task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_tool_calls (
                    task_id TEXT NOT NULL,
                    call_index INTEGER NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
                    output_json TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, call_index),
                    FOREIGN KEY (task_id)
                        REFERENCES ai_analysis_tasks (task_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_ai_tasks_user_created
                ON ai_analysis_tasks (user_id, created_at);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def create_task(
        self,
        task: AnalysisTask,
        evidence: tuple[EvidenceReference, ...],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_analysis_tasks (
                    task_id, user_id, asset_id, task_type, question, status,
                    model_name, result_json, citations_json, error,
                    created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _task_values(task),
            )
            conn.executemany(
                """
                INSERT INTO ai_evidence_refs (
                    task_id, evidence_id, evidence_type, source_id,
                    title, content, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        task.task_id,
                        item.evidence_id,
                        item.evidence_type,
                        item.source_id,
                        item.title,
                        item.content,
                        _json(item.metadata),
                    ]
                    for item in evidence
                ],
            )

    def update_task(self, task: AnalysisTask) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE ai_analysis_tasks
                SET status = ?, model_name = ?, result_json = ?,
                    citations_json = ?, error = ?, finished_at = ?
                WHERE task_id = ?
                """,
                [
                    task.status,
                    task.model_name,
                    _json(task.result),
                    _json(list(task.citations)),
                    task.error,
                    task.finished_at,
                    task.task_id,
                ],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown task_id: {task.task_id}")

    def add_evidence(
        self,
        task_id: str,
        evidence: tuple[EvidenceReference, ...],
    ) -> None:
        if not evidence:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO ai_evidence_refs (
                    task_id, evidence_id, evidence_type, source_id,
                    title, content, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        task_id,
                        item.evidence_id,
                        item.evidence_type,
                        item.source_id,
                        item.title,
                        item.content,
                        _json(item.metadata),
                    ]
                    for item in evidence
                ],
            )

    def save_tool_execution(self, execution: ToolExecution) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ai_tool_calls (
                    task_id, call_index, tool_name, arguments_json,
                    status, output_json, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    execution.task_id,
                    execution.call_index,
                    execution.tool_name,
                    _json(execution.arguments),
                    execution.status,
                    _json(execution.output),
                    execution.error,
                    execution.created_at,
                ],
            )

    def get_task(self, task_id: str, user_id: str) -> AnalysisTask | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT {_TASK_COLUMNS}
                FROM ai_analysis_tasks
                WHERE task_id = ? AND user_id = ?
                """,
                [task_id, user_id],
            ).fetchone()
        return _task_from_row(row) if row is not None else None

    def list_tasks(self, user_id: str) -> list[AnalysisTask]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_TASK_COLUMNS}
                FROM ai_analysis_tasks
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                [user_id],
            ).fetchall()
        return [_task_from_row(row) for row in rows]

    def list_evidence(self, task_id: str) -> tuple[EvidenceReference, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT evidence_id, evidence_type, source_id, title,
                       content, metadata_json
                FROM ai_evidence_refs
                WHERE task_id = ?
                ORDER BY evidence_id
                """,
                [task_id],
            ).fetchall()
        return tuple(
            EvidenceReference(
                evidence_id=str(row["evidence_id"]),
                evidence_type=str(row["evidence_type"]),
                source_id=str(row["source_id"]),
                title=str(row["title"]),
                content=str(row["content"]),
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        )

    def list_tool_executions(
        self,
        task_id: str,
    ) -> tuple[ToolExecution, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, call_index, tool_name, arguments_json,
                       status, output_json, error, created_at
                FROM ai_tool_calls
                WHERE task_id = ?
                ORDER BY call_index
                """,
                [task_id],
            ).fetchall()
        return tuple(
            ToolExecution(
                task_id=str(row["task_id"]),
                call_index=int(row["call_index"]),
                tool_name=str(row["tool_name"]),
                arguments=json.loads(row["arguments_json"]),
                status=str(row["status"]),
                output=json.loads(row["output_json"]),
                error=row["error"],
                created_at=str(row["created_at"]),
            )
            for row in rows
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


_TASK_COLUMNS = """
    task_id, user_id, asset_id, task_type, question, status,
    model_name, result_json, citations_json, error, created_at, finished_at
"""


def _task_values(task: AnalysisTask) -> list[object]:
    return [
        task.task_id,
        task.user_id,
        task.asset_id,
        task.task_type,
        task.question,
        task.status,
        task.model_name,
        _json(task.result),
        _json(list(task.citations)),
        task.error,
        task.created_at,
        task.finished_at,
    ]


def _task_from_row(row: sqlite3.Row) -> AnalysisTask:
    return AnalysisTask(
        task_id=str(row["task_id"]),
        user_id=str(row["user_id"]),
        asset_id=str(row["asset_id"]),
        task_type=str(row["task_type"]),
        question=str(row["question"]),
        status=str(row["status"]),
        model_name=row["model_name"],
        result=json.loads(row["result_json"]),
        citations=tuple(json.loads(row["citations_json"])),
        error=row["error"],
        created_at=str(row["created_at"]),
        finished_at=row["finished_at"],
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
