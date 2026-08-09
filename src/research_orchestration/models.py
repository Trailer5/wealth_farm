"""Public contracts for one unified research orchestration run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from src.research_acceptance import ResearchAcceptanceReport
from src.research_workflow import (
    ResearchExecutionResult,
    ResearchTaskSpec,
)

if TYPE_CHECKING:
    from .review import ReviewResult


class ResearchRunStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResearchRunRequest:
    task: ResearchTaskSpec
    execution_options: object | None = None
    peer_comparison: object | None = None
    report_filename: str | None = None


@dataclass(frozen=True)
class ResearchRunResult:
    task: ResearchTaskSpec
    execution: ResearchExecutionResult
    report: ResearchAcceptanceReport | None
    review: ReviewResult
    user_markdown: str
    status: ResearchRunStatus
    report_path: Path | None = None
