"""Public contracts for one unified research orchestration run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from src.research_acceptance import ResearchAcceptanceReport
from src.research_contracts import DecisionLane, ResearchTaskSpec
from src.research_workflow import ResearchExecutionResult

if TYPE_CHECKING:
    from .review import ReviewResult
    from .workflow import (
        BaselineResearchHandoff,
        ResearchWorkflowRun,
    )


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
    decision_brief_id: str | None = None
    decision_lanes: tuple[DecisionLane, ...] = ()
    workflow_run_id: str | None = None

    def __post_init__(self) -> None:
        if (
            self.report_filename is not None
            and "完整决策报告" in self.report_filename
        ):
            raise ValueError(
                "baseline run cannot use final decision report filename"
            )
        if any(
            not isinstance(item, DecisionLane)
            for item in self.decision_lanes
        ):
            raise TypeError(
                "decision_lanes must contain DecisionLane"
            )
        if len(self.decision_lanes) != len(
            set(self.decision_lanes)
        ):
            raise ValueError("decision_lanes must be unique")
        for value, name in (
            (self.decision_brief_id, "decision_brief_id"),
            (self.workflow_run_id, "workflow_run_id"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True)
class ResearchRunResult:
    task: ResearchTaskSpec
    execution: ResearchExecutionResult
    report: ResearchAcceptanceReport | None
    review: ReviewResult
    user_markdown: str
    status: ResearchRunStatus
    report_path: Path | None = None
    workflow_run: ResearchWorkflowRun | None = None
    handoff: BaselineResearchHandoff | None = None
