"""SQLite persistence for workflow runs and baseline handoffs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .monitoring_models import (
    FinalDecisionPublicationAttestation,
    MonitoringHandoff,
)
from .workflow import BaselineResearchHandoff, ResearchWorkflowRun


class SQLiteResearchWorkflowRepository:
    SCHEMA_VERSION = 35
    SCHEMA_DESCRIPTION = (
        "research workflow, final publication, and monitoring handoffs"
    )

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
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

                CREATE TABLE IF NOT EXISTS research_workflow_runs (
                    workflow_run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    decision_brief_id TEXT,
                    baseline_status TEXT NOT NULL,
                    handoff_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS baseline_research_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_run_id)
                        REFERENCES research_workflow_runs (
                            workflow_run_id
                        ) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS research_monitoring_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL UNIQUE,
                    final_report_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_run_id)
                        REFERENCES research_workflow_runs (
                            workflow_run_id
                        ) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS final_decision_publications (
                    report_id TEXT PRIMARY KEY,
                    workflow_run_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY (workflow_run_id)
                        REFERENCES research_workflow_runs (
                            workflow_run_id
                        ) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_workflow_runs_asset
                ON research_workflow_runs (
                    asset_id,
                    updated_at
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

    def save_workflow_run(
        self,
        workflow_run: ResearchWorkflowRun,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_workflow_runs (
                    workflow_run_id,
                    task_id,
                    asset_id,
                    decision_brief_id,
                    baseline_status,
                    handoff_id,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workflow_run_id) DO UPDATE SET
                    task_id = excluded.task_id,
                    asset_id = excluded.asset_id,
                    decision_brief_id = excluded.decision_brief_id,
                    baseline_status = excluded.baseline_status,
                    handoff_id = excluded.handoff_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                [
                    workflow_run.workflow_run_id,
                    workflow_run.task.task_id,
                    workflow_run.task.asset_id,
                    workflow_run.decision_brief_id,
                    workflow_run.baseline_status.value,
                    workflow_run.handoff_id,
                    json.dumps(
                        workflow_run.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    workflow_run.created_at,
                    workflow_run.updated_at,
                ],
            )

    def save_handoff(
        self,
        handoff: BaselineResearchHandoff,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO baseline_research_handoffs (
                    handoff_id,
                    workflow_run_id,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (handoff_id) DO UPDATE SET
                    workflow_run_id = excluded.workflow_run_id,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at
                """,
                [
                    handoff.handoff_id,
                    handoff.workflow_run_id,
                    json.dumps(
                        handoff.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    handoff.created_at,
                ],
            )

    def get_workflow_run(
        self,
        workflow_run_id: str,
    ) -> ResearchWorkflowRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM research_workflow_runs
                WHERE workflow_run_id = ?
                """,
                [workflow_run_id],
            ).fetchone()
        if row is None:
            return None
        return ResearchWorkflowRun.from_dict(
            json.loads(row["payload_json"])
        )

    def get_handoff(
        self,
        handoff_id: str,
    ) -> BaselineResearchHandoff | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM baseline_research_handoffs
                WHERE handoff_id = ?
                """,
                [handoff_id],
            ).fetchone()
        if row is None:
            return None
        return BaselineResearchHandoff.from_dict(
            json.loads(row["payload_json"])
        )

    def save_monitoring_handoff(
        self,
        handoff: MonitoringHandoff,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_monitoring_handoffs (
                    handoff_id,
                    workflow_run_id,
                    final_report_id,
                    status,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (handoff_id) DO UPDATE SET
                    workflow_run_id = excluded.workflow_run_id,
                    final_report_id = excluded.final_report_id,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                [
                    handoff.handoff_id,
                    handoff.workflow_run_id,
                    handoff.final_report_id,
                    handoff.status.value,
                    json.dumps(
                        handoff.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    handoff.created_at,
                    handoff.updated_at,
                ],
            )

    def get_monitoring_handoff(
        self,
        handoff_id: str,
    ) -> MonitoringHandoff | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM research_monitoring_handoffs
                WHERE handoff_id = ?
                """,
                [handoff_id],
            ).fetchone()
        if row is None:
            return None
        return MonitoringHandoff.from_dict(
            json.loads(row["payload_json"])
        )

    def save_final_publication(
        self,
        attestation: FinalDecisionPublicationAttestation,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO final_decision_publications (
                    report_id,
                    workflow_run_id,
                    payload_json,
                    published_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (report_id) DO UPDATE SET
                    workflow_run_id = excluded.workflow_run_id,
                    payload_json = excluded.payload_json,
                    published_at = excluded.published_at
                """,
                [
                    attestation.report_id,
                    attestation.workflow_run_id,
                    json.dumps(
                        attestation.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    attestation.published_at,
                ],
            )

    def get_final_publication(
        self,
        report_id: str,
    ) -> FinalDecisionPublicationAttestation | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM final_decision_publications
                WHERE report_id = ?
                """,
                [report_id],
            ).fetchone()
        if row is None:
            return None
        return FinalDecisionPublicationAttestation.from_dict(
            json.loads(row["payload_json"])
        )

    def list_final_publications(
        self,
    ) -> tuple[FinalDecisionPublicationAttestation, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM final_decision_publications
                ORDER BY published_at, report_id
                """
            ).fetchall()
        return tuple(
            FinalDecisionPublicationAttestation.from_dict(
                json.loads(row["payload_json"])
            )
            for row in rows
        )

    def delete_handoff_for_run(
        self,
        workflow_run_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM baseline_research_handoffs
                WHERE workflow_run_id = ?
                """,
                [workflow_run_id],
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
