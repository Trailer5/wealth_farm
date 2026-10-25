"""Reviewed publication pipeline for final decision reports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.research_visualization import (
    PublishedReport,
    ReportArtifactService,
)
from src.research_workflow import ResearchDecisionBrief

from .entry_models import EntryPlan
from .exploration_models import ResearchExplorationSession
from .final_decision import (
    FinalDecisionReportAssemblyService,
    FinalReportNarrativeInput,
    final_decision_report_filename,
)
from .final_decision_models import FinalDecisionResearchReport
from .final_decision_renderer import FinalDecisionReportRenderer
from .final_decision_review import FinalDecisionReportReviewer
from .monitoring_models import FinalDecisionPublicationAttestation
from .position_models import PositionPlan
from .review import ReviewFinding, ReviewResult
from .suitability_models import SuitabilityAssessment
from .workflow import BaselineResearchHandoff


@dataclass(frozen=True)
class FinalDecisionPublishResult:
    report: FinalDecisionResearchReport
    review: ReviewResult
    markdown: str
    filename: str
    published: PublishedReport | None

    @property
    def report_path(self) -> Path | None:
        return (
            self.published.report_path
            if self.published is not None
            else None
        )


class FinalPublicationRecorderProtocol(Protocol):
    def record_final_publication(
        self,
        attestation: FinalDecisionPublicationAttestation,
    ) -> object: ...


class FinalDecisionReportService:
    """Assemble, review, render, re-review, then publish."""

    def __init__(
        self,
        *,
        publisher: ReportArtifactService,
        assembly: FinalDecisionReportAssemblyService | None = None,
        renderer: FinalDecisionReportRenderer | None = None,
        reviewer: FinalDecisionReportReviewer | None = None,
        publication_recorder: (
            FinalPublicationRecorderProtocol | None
        ) = None,
    ) -> None:
        self.publisher = publisher
        self.assembly = (
            assembly or FinalDecisionReportAssemblyService()
        )
        self.renderer = renderer or FinalDecisionReportRenderer()
        self.reviewer = reviewer or FinalDecisionReportReviewer()
        self.publication_recorder = publication_recorder

    def assemble_and_publish(
        self,
        *,
        handoff: BaselineResearchHandoff,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        position_plan: PositionPlan,
        narrative: FinalReportNarrativeInput,
    ) -> FinalDecisionPublishResult:
        report = self.assembly.assemble(
            handoff=handoff,
            session=session,
            decision_brief=decision_brief,
            suitability=suitability,
            entry_plan=entry_plan,
            position_plan=position_plan,
            narrative=narrative,
        )
        review = self.reviewer.review(
            report,
            handoff=handoff,
            session=session,
            decision_brief=decision_brief,
            suitability=suitability,
            entry_plan=entry_plan,
            position_plan=position_plan,
        )
        review = _artifact_review(
            review,
            report=report,
            report_root=self.publisher.report_root,
        )
        filename = final_decision_report_filename(
            report.asset_name,
            report.as_of_date,
            (
                f"{report.user_id}\0{report.workflow_run_id}"
                f"\0{report.report_id}"
            ),
        )
        if not review.passed:
            return FinalDecisionPublishResult(
                report=report,
                review=review,
                markdown=self.renderer.render_review_blocked(review),
                filename=filename,
                published=None,
            )
        markdown = self.renderer.render(report)
        review = self.reviewer.review_markdown(
            report,
            markdown,
            prior=review,
        )
        if not review.passed:
            return FinalDecisionPublishResult(
                report=report,
                review=review,
                markdown=self.renderer.render_review_blocked(review),
                filename=filename,
                published=None,
            )
        published = self.publisher._publish_reviewed_final_markdown(
            filename=filename,
            content=markdown,
        )
        if self.publication_recorder is not None:
            self.publication_recorder.record_final_publication(
                FinalDecisionPublicationAttestation(
                    report_id=report.report_id,
                    user_id=report.user_id,
                    workflow_run_id=report.workflow_run_id,
                    session_id=report.session_id,
                    decision_brief_id=report.decision_brief_id,
                    baseline_handoff_id=report.handoff_id,
                    monitoring_plan_id=(
                        report.monitoring_plan.plan_id
                    ),
                    asset_id=report.asset_id,
                    profile_id=report.profile_id,
                    as_of_date=report.as_of_date,
                    report_path=str(
                        published.report_path.resolve()
                    ),
                    markdown_digest=_file_digest(
                        published.report_path
                    ),
                    monitoring_plan_payload_json=json.dumps(
                        report.monitoring_plan.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    published_at=datetime.now(
                        timezone.utc
                    ).isoformat(),
                )
            )
        return FinalDecisionPublishResult(
            report=report,
            review=review,
            markdown=markdown,
            filename=filename,
            published=published,
        )


def _artifact_review(
    prior: ReviewResult,
    *,
    report: FinalDecisionResearchReport,
    report_root: Path,
) -> ReviewResult:
    findings = list(prior.findings)
    root = report_root.resolve()
    baseline = _contained_path(
        root,
        report.baseline_report_path,
    )
    if baseline is None or not baseline.is_file():
        findings.append(
            ReviewFinding(
                "baseline_artifact_missing",
                "Published baseline Markdown does not exist.",
            )
        )
    elif hashlib.sha256(baseline.read_bytes()).hexdigest() != (
        report.baseline_report_digest
    ):
        findings.append(
            ReviewFinding(
                "baseline_artifact_digest_mismatch",
                "Published baseline Markdown does not match handoff.",
            )
        )
    chart_manifest = {
        item.markdown_path: item
        for item in report.chart_artifacts
    }
    for path in report.chart_paths:
        chart = _contained_path(root, path)
        if chart is None or not chart.is_file():
            findings.append(
                ReviewFinding(
                    "chart_artifact_missing",
                    "A referenced baseline chart does not exist.",
                )
            )
            break
        relative_parent = Path(path).parent
        metadata_path = _contained_path(
            root,
            str(relative_parent / "metadata.json"),
        )
        data_path = _contained_path(
            root,
            str(relative_parent / "data.json"),
        )
        if (
            metadata_path is None
            or data_path is None
            or not metadata_path.is_file()
            or not data_path.is_file()
            or not _chart_metadata_matches(
                metadata_path,
                asset_id=report.asset_id,
                as_of_date=report.as_of_date,
            )
        ):
            findings.append(
                ReviewFinding(
                    "chart_artifact_provenance_mismatch",
                    "A referenced chart is not bound to this research task.",
                )
            )
            break
        reference = chart_manifest[path]
        if (
            _file_digest(chart) != reference.content_digest
            or _file_digest(metadata_path)
            != reference.metadata_digest
            or _file_digest(data_path) != reference.data_digest
        ):
            findings.append(
                ReviewFinding(
                    "chart_artifact_digest_mismatch",
                    "A referenced chart differs from the baseline handoff.",
                )
            )
            break
    return ReviewResult(tuple(dict.fromkeys(findings)))


def _contained_path(root: Path, relative: str) -> Path | None:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _chart_metadata_matches(
    metadata_path: Path,
    *,
    asset_id: str,
    as_of_date: str,
) -> bool:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("asset_id") == asset_id
        and payload.get("as_of_date") == as_of_date
        and isinstance(payload.get("input_digest"), str)
        and len(payload["input_digest"]) == 64
    )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
