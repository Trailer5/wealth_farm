"""Lightweight task and data quality models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_FAILED = "failed"

QUALITY_PASS = "pass"
QUALITY_WARN = "warn"
QUALITY_FAIL = "fail"


@dataclass(frozen=True)
class TaskRun:
    """One local, Agent, or automated data task."""

    run_id: str
    task_name: str
    status: str
    dataset: str | None = None
    source: str | None = None
    asset_id: str | None = None
    started_at: str = ""
    finished_at: str | None = None
    record_count: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class QualityCheckResult:
    """One pass, warning, or failure produced for a task."""

    run_id: str
    check_name: str
    level: str
    message: str
    metrics: dict[str, Any] = field(default_factory=dict)
    check_id: int | None = None
    created_at: str = ""


@dataclass(frozen=True)
class QualitySummary:
    """Small quality summary used as a publication gate."""

    run: TaskRun
    checks: tuple[QualityCheckResult, ...]
    pass_count: int
    warn_count: int
    fail_count: int
    can_publish: bool
