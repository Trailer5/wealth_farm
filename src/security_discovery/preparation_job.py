"""Persistent, manually stepped jobs for discovery metric preparation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from src.research_workflow import ResearchExecutionOptions

from .models import DiscoverySpec
from .preparation import (
    DiscoveryMetricPreparationService,
    MetricPreparationStatus,
)


@dataclass(frozen=True)
class DiscoveryPreparationJob:
    job_id: str
    universe_id: str
    universe_version: str
    as_of_date: str
    status: str
    max_attempts_per_member: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DiscoveryPreparationProgress:
    job: DiscoveryPreparationJob
    counts: dict[str, int]
    last_batch_remote_attempted: int


class SQLiteDiscoveryPreparationJobRepository:
    SCHEMA_VERSION = 24
    SCHEMA_DESCRIPTION = "resumable discovery metric jobs"

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
                CREATE TABLE IF NOT EXISTS discovery_preparation_jobs (
                    job_id TEXT PRIMARY KEY,
                    universe_id TEXT NOT NULL,
                    universe_version TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    max_attempts_per_member INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_preparation_members (
                    job_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    missing_metrics_json TEXT NOT NULL,
                    last_error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (job_id, asset_id),
                    FOREIGN KEY (job_id)
                        REFERENCES discovery_preparation_jobs (job_id)
                        ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (
                    version, description
                ) VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save_job(
        self,
        job: DiscoveryPreparationJob,
    ) -> DiscoveryPreparationJob:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_preparation_jobs (
                    job_id, universe_id, universe_version, as_of_date,
                    status, max_attempts_per_member, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                [
                    job.job_id,
                    job.universe_id,
                    job.universe_version,
                    job.as_of_date,
                    job.status,
                    job.max_attempts_per_member,
                    job.created_at,
                    job.updated_at,
                ],
            )
        return job

    def get_job(
        self,
        job_id: str,
    ) -> DiscoveryPreparationJob | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM discovery_preparation_jobs
                WHERE job_id = ?
                """,
                [job_id],
            ).fetchone()
        return _job(row) if row else None

    def upsert_member(
        self,
        *,
        job_id: str,
        asset_id: str,
        source_symbol: str,
        status: str,
        attempts: int,
        missing_metrics: tuple[str, ...],
        last_error: str | None,
        updated_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_preparation_members (
                    job_id, asset_id, source_symbol, status, attempts,
                    missing_metrics_json, last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, asset_id) DO UPDATE SET
                    source_symbol = excluded.source_symbol,
                    status = excluded.status,
                    attempts = excluded.attempts,
                    missing_metrics_json = excluded.missing_metrics_json,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                [
                    job_id,
                    asset_id,
                    source_symbol,
                    status,
                    attempts,
                    json.dumps(list(missing_metrics)),
                    last_error,
                    updated_at,
                ],
            )

    def member_states(
        self,
        job_id: str,
    ) -> dict[str, dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM discovery_preparation_members
                WHERE job_id = ?
                """,
                [job_id],
            ).fetchall()
        return {
            str(row["asset_id"]): {
                "status": str(row["status"]),
                "attempts": int(row["attempts"]),
                "source_symbol": str(row["source_symbol"]),
                "missing_metrics": tuple(
                    json.loads(row["missing_metrics_json"])
                ),
                "last_error": row["last_error"],
            }
            for row in rows
        }

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


class DiscoveryPreparationJobService:
    def __init__(
        self,
        *,
        preparation_service: DiscoveryMetricPreparationService,
        repository: SQLiteDiscoveryPreparationJobRepository,
    ) -> None:
        self.preparation_service = preparation_service
        self.repository = repository
        self.repository.initialize()

    def create_job(
        self,
        *,
        job_id: str,
        spec: DiscoverySpec,
        max_attempts_per_member: int = 3,
    ) -> DiscoveryPreparationJob:
        if max_attempts_per_member < 1:
            raise ValueError(
                "max_attempts_per_member must be positive"
            )
        if spec.universe_version is None:
            raise ValueError(
                "Preparation job requires universe_version"
            )
        existing = self.repository.get_job(job_id)
        if existing is not None:
            if (
                existing.universe_id != spec.universe_id
                or existing.universe_version
                != spec.universe_version
                or existing.as_of_date != spec.as_of_date
            ):
                raise ValueError(
                    "Existing job does not match DiscoverySpec"
                )
            return existing
        now = _now()
        return self.repository.save_job(
            DiscoveryPreparationJob(
                job_id=job_id,
                universe_id=spec.universe_id,
                universe_version=spec.universe_version,
                as_of_date=spec.as_of_date,
                status="pending",
                max_attempts_per_member=max_attempts_per_member,
                created_at=now,
                updated_at=now,
            )
        )

    def run_batch(
        self,
        *,
        job_id: str,
        spec: DiscoverySpec,
        profile_by_asset_id: dict[str, str],
        batch_size: int,
        options: ResearchExecutionOptions | None = None,
    ) -> DiscoveryPreparationProgress:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        job = self.repository.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown preparation job: {job_id}")
        if (
            job.universe_id != spec.universe_id
            or job.universe_version != spec.universe_version
            or job.as_of_date != spec.as_of_date
        ):
            raise ValueError("Job and DiscoverySpec do not match")
        previous = self.repository.member_states(job_id)
        exhausted = {
            asset_id
            for asset_id, state in previous.items()
            if int(state["attempts"])
            >= job.max_attempts_per_member
            and state["status"] not in {"ready"}
        }
        result = self.preparation_service.prepare_universe(
            spec,
            profile_by_asset_id=profile_by_asset_id,
            remote_member_limit=batch_size,
            options=options,
            skip_remote_asset_ids=exhausted,
        )
        now = _now()
        for member in result.members:
            if member.asset_id is None:
                continue
            old = previous.get(member.asset_id, {})
            attempts = int(old.get("attempts", 0))
            if member.remote_attempted:
                attempts += 1
            status = _member_status(
                member.status,
                attempts=attempts,
                max_attempts=job.max_attempts_per_member,
            )
            self.repository.upsert_member(
                job_id=job_id,
                asset_id=member.asset_id,
                source_symbol=member.source_symbol,
                status=status,
                attempts=attempts,
                missing_metrics=member.missing_metrics,
                last_error=member.error,
                updated_at=now,
            )
        states = self.repository.member_states(job_id)
        retryable = any(
            state["status"] in {"pending", "failed"}
            and int(state["attempts"])
            < job.max_attempts_per_member
            for state in states.values()
        )
        exhausted_count = sum(
            state["status"] == "exhausted"
            for state in states.values()
        )
        status = (
            "pending"
            if retryable
            else (
                "completed_with_failures"
                if exhausted_count
                else "complete"
            )
        )
        job = self.repository.save_job(
            replace(job, status=status, updated_at=now)
        )
        counts: dict[str, int] = {}
        for state in states.values():
            value = str(state["status"])
            counts[value] = counts.get(value, 0) + 1
        return DiscoveryPreparationProgress(
            job=job,
            counts=counts,
            last_batch_remote_attempted=result.remote_attempted,
        )


def _member_status(
    status: MetricPreparationStatus,
    *,
    attempts: int,
    max_attempts: int,
) -> str:
    if status in {
        MetricPreparationStatus.PREPARED,
        MetricPreparationStatus.CACHED,
    }:
        return "ready"
    if status is MetricPreparationStatus.FAILED:
        return (
            "exhausted"
            if attempts >= max_attempts
            else "failed"
        )
    if status is MetricPreparationStatus.MISSING:
        return (
            "exhausted"
            if attempts >= max_attempts
            else "pending"
        )
    return status.value


def _job(row: sqlite3.Row) -> DiscoveryPreparationJob:
    return DiscoveryPreparationJob(
        job_id=str(row["job_id"]),
        universe_id=str(row["universe_id"]),
        universe_version=str(row["universe_version"]),
        as_of_date=str(row["as_of_date"]),
        status=str(row["status"]),
        max_attempts_per_member=int(
            row["max_attempts_per_member"]
        ),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
