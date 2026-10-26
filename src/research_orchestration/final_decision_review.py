"""Programmatic review gates for final decision research reports."""

from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath

from src.research_contracts import (
    DecisionIntent,
    DecisionLane,
    ResearchDecisionBrief,
)

from .final_decision import final_report_gap_message
from .gap_messages import AGENT_ONLY_GAP_CODES
from .entry_models import EntryPlan
from .exploration_models import (
    ExplorationStatus,
    ResearchExplorationSession,
)
from .final_decision_models import (
    FinalDecisionOutcome,
    FinalDecisionResearchReport,
    FinalEvidenceKind,
    FinalStatementKind,
    MonitoringPlanStatus,
)
from .final_decision_renderer import (
    DECISION_HEADINGS,
    HOLDING_DECISION_HEADINGS,
    FinalDecisionReportRenderer,
)
from .position_models import PositionPlan, PositionPlanStatus
from .review import ReviewFinding, ReviewResult
from .suitability_models import SuitabilityAction, SuitabilityAssessment
from .workflow import BaselineResearchHandoff


_REQUIRED_HEADINGS = (
    "## 决策摘要",
    "## 用户目标与适用边界",
    "## 标的与核心论文",
    "## 三情景",
    "## 最强反方",
    "## 失效、退出与复查",
    "## 关键证据与未知",
)


class FinalDecisionReportReviewer:
    """Review identity, traceability, decisions, and Markdown safety."""

    def review(
        self,
        report: FinalDecisionResearchReport,
        *,
        handoff: BaselineResearchHandoff,
        session: ResearchExplorationSession,
        decision_brief: ResearchDecisionBrief,
        suitability: SuitabilityAssessment,
        entry_plan: EntryPlan,
        position_plan: PositionPlan,
    ) -> ReviewResult:
        findings: list[ReviewFinding] = []
        if session.status is not ExplorationStatus.READY_FOR_REPORT:
            findings.append(
                ReviewFinding(
                    "exploration_not_ready",
                    "Exploration is not ready for a final report.",
                )
            )
        expected_lineage = {
            "workflow_run_id": handoff.workflow_run_id,
            "session_id": session.session_id,
            "decision_brief_id": decision_brief.brief_id,
            "handoff_id": handoff.handoff_id,
            "suitability_assessment_id": (
                suitability.assessment_id
            ),
            "entry_plan_id": entry_plan.plan_id,
            "position_plan_id": position_plan.plan_id,
        }
        if any(
            getattr(report, field) != expected
            for field, expected in expected_lineage.items()
        ):
            findings.append(
                ReviewFinding(
                    "final_report_lineage_mismatch",
                    "Final report lineage does not match its inputs.",
                )
            )
        if (
            report.asset_id != session.task.asset_id
            or report.profile_id != session.task.profile_id
            or report.as_of_date != session.task.as_of_date
            or report.horizon is not decision_brief.horizon
        ):
            findings.append(
                ReviewFinding(
                    "final_report_identity_mismatch",
                    "Final report identity or cutoff is inconsistent.",
                )
            )
        if (
            session.user_id is None
            or report.user_id != session.user_id
            or report.user_id != decision_brief.user_id
        ):
            findings.append(
                ReviewFinding(
                    "final_report_user_mismatch",
                    "Final report ownership does not match the session.",
                )
            )
        if (
            handoff.report_path is None
            or PurePosixPath(
                handoff.report_path.replace("\\", "/")
            ).name
            != report.baseline_report_path
            or handoff.blocking_gaps
            or handoff.run_status is not report.baseline_status
            or handoff.report_digest
            != report.baseline_report_digest
        ):
            findings.append(
                ReviewFinding(
                    "baseline_package_invalid",
                    "Final report requires an aligned publishable baseline.",
                )
            )
        expected_lanes = {
            item.lane: item for item in report.decision_sections
        }
        if set(expected_lanes) != set(DecisionLane):
            findings.append(
                ReviewFinding(
                    "decision_lane_missing",
                    "All three decision questions require an answer.",
                )
            )
        expected_outcomes = {
            DecisionLane.SUITABILITY: (
                _suitability_outcome(suitability.action)
            ),
            DecisionLane.TIMING: _timing_outcome(entry_plan.status.value),
            DecisionLane.POSITION_SIZING: (
                _position_outcome(position_plan.status)
            ),
        }
        if any(
            expected_lanes.get(lane) is None
            or expected_lanes[lane].outcome is not outcome
            for lane, outcome in expected_outcomes.items()
        ):
            findings.append(
                ReviewFinding(
                    "decision_outcome_mismatch",
                    "Rendered decisions do not match validated lane outputs.",
                )
            )
        if (
            suitability.action is not SuitabilityAction.INSUFFICIENT
            and report.strongest_counterargument.kind
            is FinalStatementKind.UNKNOWN
        ):
            findings.append(
                ReviewFinding(
                    "counterargument_missing",
                    "A decisive suitability result needs a counterargument.",
                )
            )
        if not report.monitoring_plan.conditions:
            findings.append(
                ReviewFinding(
                    "invalidation_condition_missing",
                    "Final report requires falsification conditions.",
                )
            )
        if (
            report.monitoring_plan.alert_rule_refs
            or (
                report.monitoring_plan.conditions
                and report.monitoring_plan.status
                is not MonitoringPlanStatus.USER_CONFIRMATION_REQUIRED
            )
        ):
            findings.append(
                ReviewFinding(
                    "monitoring_boundary_violation",
                    "Monitoring must remain unarmed pending confirmation.",
                )
            )
        if (
            report.monitoring_plan.review_date is not None
            and report.monitoring_plan.review_date < report.as_of_date
        ):
            findings.append(
                ReviewFinding(
                    "review_date_before_cutoff",
                    "Review date cannot precede the report cutoff.",
                )
            )
        if (
            position_plan.status is PositionPlanStatus.AVAILABLE
            and (
                position_plan.calculation_inputs is None
                or not position_plan.evidence_refs
                or not position_plan.constraint_blocks
                or not position_plan.binding_constraints
            )
        ):
            findings.append(
                ReviewFinding(
                    "position_inputs_incomplete",
                    "Available position result lacks validated inputs.",
                )
            )
        citation_ids = {
            item.reference_id for item in report.evidence_citations
        }
        if citation_ids != set(report.evidence_refs):
            findings.append(
                ReviewFinding(
                    "evidence_traceability_incomplete",
                    "Evidence metadata does not cover report conclusions.",
                )
            )
        cutoff = date.fromisoformat(report.as_of_date)
        for citation in report.evidence_citations:
            if (
                citation.published_at is not None
                and date.fromisoformat(citation.published_at) > cutoff
            ):
                findings.append(
                    ReviewFinding(
                        "evidence_after_cutoff",
                        "Evidence publication date exceeds cutoff.",
                    )
                )
            if (
                citation.kind is FinalEvidenceKind.OFFICIAL
                and (
                    citation.url is None
                    or citation.published_at is None
                )
            ):
                findings.append(
                    ReviewFinding(
                        "official_evidence_metadata_missing",
                        "Official evidence needs date and source link.",
                    )
                )
            if (
                citation.kind is FinalEvidenceKind.DERIVED
                and (
                    citation.methodology is None
                    or not citation.limitations
                )
            ):
                findings.append(
                    ReviewFinding(
                        "derived_evidence_method_missing",
                        "Derived evidence needs method and limitations.",
                    )
                )
        gap_messages = {
            final_report_gap_message(item.code)
            for item in (
                *handoff.partial_gaps,
                *handoff.capability_gaps,
            )
            if item.code not in AGENT_ONLY_GAP_CODES
        }
        if not gap_messages.issubset(set(report.key_unknowns)):
            findings.append(
                ReviewFinding(
                    "baseline_gap_hidden",
                    "Baseline gaps must remain visible as unknowns.",
                )
            )
        return ReviewResult(tuple(dict.fromkeys(findings)))

    def review_markdown(
        self,
        report: FinalDecisionResearchReport,
        markdown: str,
        *,
        prior: ReviewResult | None = None,
    ) -> ReviewResult:
        findings = list(prior.findings if prior else ())
        holding = (
            report.user_boundary.decision_intent
            is DecisionIntent.REVIEW_HOLDING
        )
        headings = (
            *_REQUIRED_HEADINGS,
            *(HOLDING_DECISION_HEADINGS if holding else DECISION_HEADINGS),
        )
        if any(heading not in markdown for heading in headings):
            findings.append(
                ReviewFinding(
                    "final_report_section_missing",
                    "Final Markdown must contain all ten sections.",
                )
            )
        summary_labels = (
            ("是否继续持有", "何时调整", "持有多少")
            if holding
            else ("是否购买", "何时购买", "购买多少")
        )
        for label in summary_labels:
            if f"**{label}：" not in markdown:
                findings.append(
                    ReviewFinding(
                        "decision_summary_missing",
                        "Decision summary must answer all three questions.",
                    )
                )
                break
        if (
            f"]({report.baseline_report_path})" not in markdown
            or "基础研究包" not in markdown
        ):
            findings.append(
                ReviewFinding(
                    "baseline_reference_missing",
                    "Final Markdown must link the baseline package.",
                )
            )
        for path in report.chart_paths:
            if f"]({path})" not in markdown:
                findings.append(
                    ReviewFinding(
                        "chart_citation_missing",
                        "Final Markdown omits a required static chart.",
                    )
                )
                break
        image_paths = tuple(
            match.group(1)
            for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", markdown)
        )
        if any(not _valid_chart_path(path) for path in image_paths):
            findings.append(
                ReviewFinding(
                    "chart_citation_invalid",
                    "Charts must use relative static asset paths.",
                )
            )
        if _contains_internal_value(report, markdown):
            findings.append(
                ReviewFinding(
                    "internal_field_leak",
                    "Final Markdown exposes internal audit fields.",
                )
            )
        for citation in report.evidence_citations:
            rendered_title = FinalDecisionReportRenderer._sanitize(
                citation.title,
                report,
            )
            if rendered_title not in markdown:
                findings.append(
                    ReviewFinding(
                        "evidence_citation_missing",
                        "Final Markdown omits evidence metadata.",
                    )
                )
                break
            if (
                citation.kind is FinalEvidenceKind.OFFICIAL
                and citation.url not in markdown
            ):
                findings.append(
                    ReviewFinding(
                        "official_link_missing",
                        "Final Markdown omits an official source link.",
                    )
                )
                break
        citation_numbers = {
            item.reference_id: index
            for index, item in enumerate(
                report.evidence_citations,
                start=1,
            )
        }
        statements = (
            report.thesis,
            *(
                statement
                for section in report.decision_sections
                for statement in section.statements
            ),
            report.strongest_counterargument,
        )
        if any(
            statement.evidence_refs
            and not _line_has_evidence(
                markdown,
                FinalDecisionReportRenderer._sanitize(
                    statement.text,
                    report,
                ),
                FinalDecisionReportRenderer._evidence_suffix(
                    statement.evidence_refs,
                    citation_numbers,
                ),
            )
            for statement in statements
        ):
            findings.append(
                ReviewFinding(
                    "claim_evidence_mapping_missing",
                    "Each sourced claim must identify its evidence.",
                )
            )
        if any(
            scenario.evidence_refs
            and not _line_has_evidence(
                markdown,
                FinalDecisionReportRenderer._sanitize(
                    scenario.decision_impact,
                    report,
                ),
                FinalDecisionReportRenderer._evidence_suffix(
                    scenario.evidence_refs,
                    citation_numbers,
                ),
            )
            for scenario in report.scenarios
        ):
            findings.append(
                ReviewFinding(
                    "scenario_evidence_mapping_missing",
                    "Each sourced scenario must identify its evidence.",
                )
            )
        if any(
            condition.evidence_refs
            and not _line_has_evidence(
                markdown,
                FinalDecisionReportRenderer._sanitize(
                    condition.text,
                    report,
                ),
                FinalDecisionReportRenderer._evidence_suffix(
                    condition.evidence_refs,
                    citation_numbers,
                ),
            )
            for condition in report.monitoring_plan.conditions
        ):
            findings.append(
                ReviewFinding(
                    "monitoring_evidence_mapping_missing",
                    "Each sourced monitoring condition needs evidence.",
                )
            )
        return ReviewResult(tuple(dict.fromkeys(findings)))


def _suitability_outcome(
    action: SuitabilityAction,
) -> FinalDecisionOutcome:
    return {
        SuitabilityAction.CONSIDER: (
            FinalDecisionOutcome.SUITABILITY_CONSIDER
        ),
        SuitabilityAction.WATCH: (
            FinalDecisionOutcome.SUITABILITY_WATCH
        ),
        SuitabilityAction.AVOID: (
            FinalDecisionOutcome.SUITABILITY_AVOID
        ),
        SuitabilityAction.HOLD_REVIEW: (
            FinalDecisionOutcome.SUITABILITY_HOLD_REVIEW
        ),
        SuitabilityAction.INSUFFICIENT: (
            FinalDecisionOutcome.SUITABILITY_INSUFFICIENT
        ),
    }[action]


def _timing_outcome(status: str) -> FinalDecisionOutcome:
    return FinalDecisionOutcome(f"timing_{status}")


def _position_outcome(
    status: PositionPlanStatus,
) -> FinalDecisionOutcome:
    return FinalDecisionOutcome(f"position_{status.value}")


def _valid_chart_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return (
        not path.is_absolute()
        and ":" not in value
        and ".." not in path.parts
        and bool(path.parts)
        and path.parts[0] == "assets"
        and path.suffix.lower()
        in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    )


def _contains_internal_value(
    report: FinalDecisionResearchReport,
    markdown: str,
) -> bool:
    exact_values = {
        report.report_id,
        report.user_id,
        report.workflow_run_id,
        report.session_id,
        report.decision_brief_id,
        report.handoff_id,
        report.suitability_assessment_id,
        report.entry_plan_id,
        report.position_plan_id,
        report.asset_id,
        report.profile_id,
        report.monitoring_plan.plan_id,
        report.baseline_report_digest,
        *(
            item.condition_id
            for item in report.monitoring_plan.conditions
        ),
        *(
            item.reference_id
            for item in report.evidence_citations
            if not re.match(
                r"^https?://",
                item.reference_id,
                flags=re.IGNORECASE,
            )
        ),
    }
    if any(value in markdown for value in exact_values):
        return True
    raw_values = {
        item.outcome.value for item in report.decision_sections
    } | {
        "user_confirmation_required",
        "machine_observable",
        "manual_review",
        "position_sizing",
    }
    if any(
        re.search(
            rf"(?<![\w]){re.escape(value)}(?![\w])",
            markdown,
            flags=re.IGNORECASE,
        )
        for value in raw_values
    ):
        return True
    return bool(
        re.search(
            r"(?<![\w])src[\\/]|"
            r"\bresearch_(?:acceptance|workflow|orchestration)\b|"
            r"\b(?:structure_)?digest\b|"
            r"\breplay_verified\b|"
            r"\b(?:assessment|plan|session|workflow|schema)_id\b|"
            r"\b(?:suitability|entry|position|monitoring):"
            r"[a-f0-9-]{8,}\b",
            markdown,
            flags=re.IGNORECASE,
        )
    )


def _line_has_evidence(
    markdown: str,
    text: str,
    marker: str,
) -> bool:
    return any(
        text in line and marker in line
        for line in markdown.splitlines()
    )
