"""Lightweight task tracking, quality checks, and publication decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.data_access import DataAccessResponse

from .checks import check_daily_bars
from .models import (
    QUALITY_FAIL,
    QUALITY_PASS,
    QUALITY_WARN,
    TASK_FAILED,
    TASK_RUNNING,
    TASK_SUCCESS,
    QualitySummary,
    TaskRun,
)
from .sqlite_repository import SQLiteDataQualityRepository


class DataQualityService:
    """Record one task and calculate a small publication gate."""

    def __init__(self, repository: SQLiteDataQualityRepository | None = None) -> None:
        self.repository = repository or SQLiteDataQualityRepository()
        self.repository.initialize()

    def start_task(
        self,
        task_name: str,
        *,
        dataset: str | None = None,
        source: str | None = None,
        asset_id: str | None = None,
    ) -> TaskRun:
        """Start one task run."""

        run = TaskRun(
            run_id=f"run:{uuid4()}",
            task_name=task_name,
            status=TASK_RUNNING,
            dataset=dataset,
            source=source,
            asset_id=asset_id,
            started_at=_now(),
        )
        self.repository.create_task(run)
        return run

    def complete_task(self, run_id: str, record_count: int) -> TaskRun:
        """Mark one task as successfully executed."""

        return self.repository.finish_task(
            run_id,
            status=TASK_SUCCESS,
            finished_at=_now(),
            record_count=record_count,
            error=None,
        )

    def fail_task(self, run_id: str, error: str) -> TaskRun:
        """Mark one task as failed without storing a full traceback."""

        return self.repository.finish_task(
            run_id,
            status=TASK_FAILED,
            finished_at=_now(),
            record_count=None,
            error=error,
        )

    def evaluate_daily_bars(
        self,
        *,
        task_name: str,
        records: list[dict[str, Any]],
        expected_start: str,
        expected_end: str,
        coverage_start: str | None,
        coverage_end: str | None,
        asset_id: str | None,
        source: str | None = None,
        asset_needs_review: bool = False,
    ) -> QualitySummary:
        """Run and persist the first-stage checks for daily bars."""

        run = self.start_task(
            task_name,
            dataset="daily_bars",
            source=source,
            asset_id=asset_id,
        )
        try:
            checks = check_daily_bars(
                run_id=run.run_id,
                records=records,
                expected_start=expected_start,
                expected_end=expected_end,
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                asset_id=asset_id,
                asset_needs_review=asset_needs_review,
            )
            self.repository.save_checks(checks)
            self.complete_task(run.run_id, len(records))
            return self.summary(run.run_id)
        except Exception as exc:
            self.fail_task(run.run_id, f"{type(exc).__name__}: {exc}")
            raise

    def evaluate_data_access_response(
        self,
        *,
        task_name: str,
        response: DataAccessResponse,
        expected_start: str,
        expected_end: str,
    ) -> QualitySummary:
        """Evaluate daily records returned by the unified access layer."""

        return self.evaluate_daily_bars(
            task_name=task_name,
            records=[dict(record) for record in response.records],
            expected_start=expected_start,
            expected_end=expected_end,
            coverage_start=response.coverage_start,
            coverage_end=response.coverage_end,
            asset_id=response.asset.asset_id,
            source=",".join(response.sources) or None,
            asset_needs_review=False,
        )

    def summary(self, run_id: str) -> QualitySummary:
        """Return counts and the publication decision for one task."""

        run = self.repository.get_task(run_id)
        if run is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        checks = self.repository.list_checks(run_id)
        pass_count = sum(check.level == QUALITY_PASS for check in checks)
        warn_count = sum(check.level == QUALITY_WARN for check in checks)
        fail_count = sum(check.level == QUALITY_FAIL for check in checks)
        can_publish = (
            run.status == TASK_SUCCESS
            and bool(checks)
            and fail_count == 0
        )
        return QualitySummary(
            run=run,
            checks=tuple(checks),
            pass_count=pass_count,
            warn_count=warn_count,
            fail_count=fail_count,
            can_publish=can_publish,
        )

    def list_tasks(self, limit: int = 50) -> list[TaskRun]:
        """Return recent task runs for monitoring views."""

        return self.repository.list_tasks(limit=limit)

    def can_publish(self, run_id: str) -> bool:
        """Return the lightweight publication gate."""

        return self.summary(run_id).can_publish


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
