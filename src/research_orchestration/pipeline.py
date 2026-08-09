"""One-call orchestration from research task to reviewed Markdown."""

from __future__ import annotations

import re
from typing import Protocol

from src.research_acceptance import (
    AcceptanceStatus,
    ResearchAcceptanceReport,
)
from src.research_workflow import ResearchExecutionStatus
from src.research_visualization import ReportArtifactService

from .dispatcher import ResearchExecutionDispatcher
from .models import (
    ResearchRunRequest,
    ResearchRunResult,
    ResearchRunStatus,
)
from .renderer import ResearchReportRenderer
from .review import ResearchReportReviewer, ReviewStatus


class ResearchReportAssemblyProtocol(Protocol):
    def assemble(
        self,
        execution: object,
        *,
        peer_comparison: object | None = None,
    ) -> ResearchAcceptanceReport:
        ...


class ResearchOrchestrationPipeline:
    def __init__(
        self,
        *,
        dispatcher: ResearchExecutionDispatcher,
        report_assembly: ResearchReportAssemblyProtocol,
        reviewer: ResearchReportReviewer | None = None,
        renderer: ResearchReportRenderer | None = None,
        publisher: ReportArtifactService | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.report_assembly = report_assembly
        self.reviewer = reviewer or ResearchReportReviewer()
        self.renderer = renderer or ResearchReportRenderer()
        self.publisher = publisher

    def run(self, request: ResearchRunRequest) -> ResearchRunResult:
        execution = self.dispatcher.execute(
            request.task,
            request.execution_options,
        )
        if execution.status is ResearchExecutionStatus.BLOCKED:
            review = self.reviewer.review_blocked_execution(execution)
            return ResearchRunResult(
                task=request.task,
                execution=execution,
                report=None,
                review=review,
                user_markdown=self.renderer.render_blocked(execution),
                status=ResearchRunStatus.BLOCKED,
            )

        report = self.report_assembly.assemble(
            execution,
            peer_comparison=request.peer_comparison,
        )
        review = self.reviewer.review(
            report,
            expected_task=request.task,
        )
        if (
            review.status is ReviewStatus.BLOCKED
            or report.overall_status is AcceptanceStatus.BLOCKED
        ):
            return ResearchRunResult(
                task=request.task,
                execution=execution,
                report=report,
                review=review,
                user_markdown=self.renderer.render_review_blocked(
                    review,
                    report,
                ),
                status=ResearchRunStatus.BLOCKED,
            )

        markdown = self.renderer.render(report)
        review = self.reviewer.review_markdown(
            report,
            markdown,
            prior=review,
        )
        if review.status is ReviewStatus.BLOCKED:
            return ResearchRunResult(
                task=request.task,
                execution=execution,
                report=report,
                review=review,
                user_markdown=self.renderer.render_review_blocked(
                    review,
                    report,
                ),
                status=ResearchRunStatus.BLOCKED,
            )

        report_path = None
        if self.publisher is not None:
            filename = request.report_filename or _report_filename(
                report.asset_name,
                request.task.as_of_date,
            )
            report_path = self.publisher.publish_markdown(
                filename=filename,
                content=markdown,
            ).report_path
        return ResearchRunResult(
            task=request.task,
            execution=execution,
            report=report,
            review=review,
            user_markdown=markdown,
            status=_run_status(execution.status, report.overall_status),
            report_path=report_path,
        )


def _run_status(
    execution_status: ResearchExecutionStatus,
    report_status: AcceptanceStatus,
) -> ResearchRunStatus:
    if (
        execution_status is ResearchExecutionStatus.PARTIAL
        or report_status is AcceptanceStatus.PARTIAL
    ):
        return ResearchRunStatus.PARTIAL
    return ResearchRunStatus.COMPLETE


def _report_filename(asset_name: str, as_of_date: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*]+', "-", asset_name).strip(" .-")
    if not safe_name:
        safe_name = "研究"
    return f"{safe_name}-基础研究包-{as_of_date}.md"
