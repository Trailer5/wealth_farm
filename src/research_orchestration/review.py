"""Deterministic structural review for assembled research reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from src.research_acceptance import (
    ClaimKind,
    ResearchFactKind,
    ScenarioKind,
)
from src.research_workflow import (
    ResearchExecutionResult,
    ResearchTaskSpec,
)


class ReviewStatus(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class ReviewResult:
    findings: tuple[ReviewFinding, ...] = ()

    @property
    def status(self) -> ReviewStatus:
        if any(item.blocking for item in self.findings):
            return ReviewStatus.BLOCKED
        return ReviewStatus.PASSED

    @property
    def passed(self) -> bool:
        return self.status is ReviewStatus.PASSED


class ResearchReportReviewer:
    """Check report structure, evidence, and user-output boundaries."""

    def review(
        self,
        report: object,
        *,
        expected_task: ResearchTaskSpec | None = None,
        user_markdown: str | None = None,
    ) -> ReviewResult:
        findings: list[ReviewFinding] = []
        task = getattr(report, "task", None)
        if task is None or not getattr(report, "asset_name", "").strip():
            findings.append(
                ReviewFinding(
                    "report_identity_missing",
                    "Report asset identity or task identity is missing.",
                )
            )
        elif expected_task is not None:
            identity_fields = ("task_id", "asset_id", "profile_id")
            if any(
                getattr(task, field, None)
                != getattr(expected_task, field, None)
                for field in identity_fields
            ):
                findings.append(
                    ReviewFinding(
                        "report_identity_mismatch",
                        "Report identity does not match the requested task.",
                    )
                )
            if (
                getattr(task, "as_of_date", None)
                != expected_task.as_of_date
            ):
                findings.append(
                    ReviewFinding(
                        "report_cutoff_mismatch",
                        "Report cutoff does not match the requested cutoff.",
                    )
                )

        scenarios = tuple(getattr(report, "scenarios", ()))
        scenario_kinds = {
            getattr(item, "kind", None) for item in scenarios
        }
        if scenario_kinds != set(ScenarioKind) or len(scenarios) != 3:
            findings.append(
                ReviewFinding(
                    "three_scenarios_missing",
                    "Report must contain optimistic, base, and "
                    "pessimistic scenarios exactly once.",
                )
            )

        invalidations = tuple(
            getattr(report, "invalidation_conditions", ())
        )
        if not invalidations or any(
            not getattr(item, "condition", "").strip()
            or not getattr(item, "evidence_required", "").strip()
            for item in invalidations
        ):
            findings.append(
                ReviewFinding(
                    "invalidation_condition_missing",
                    "Report requires observable invalidation conditions.",
                )
            )

        conclusions = tuple(getattr(report, "conclusions", ()))
        if not conclusions:
            findings.append(
                ReviewFinding(
                    "conclusion_missing",
                    "Report requires at least one conclusion.",
                )
            )

        for gap in getattr(report, "gaps", ()):
            if getattr(gap, "blocking", False):
                findings.append(
                    ReviewFinding(
                        "blocking_gap",
                        getattr(
                            gap,
                            "message",
                            "Report contains a blocking gap.",
                        ),
                    )
                )

        claims = [
            *conclusions,
            getattr(report, "strongest_counterargument", None),
            *[
                claim
                for scenario in scenarios
                for claim in getattr(scenario, "claims", ())
            ],
        ]
        for claim in claims:
            if (
                claim is not None
                and getattr(claim, "kind", None) is ClaimKind.FACT
                and not tuple(getattr(claim, "evidence_refs", ()))
            ):
                findings.append(
                    ReviewFinding(
                        "fact_evidence_missing",
                        "Every fact claim requires at least one "
                        "evidence reference.",
                    )
                )

        for section in getattr(report, "fact_sections", ()):
            for fact in getattr(section, "facts", ()):
                refs = tuple(getattr(fact, "evidence_refs", ()))
                if not refs:
                    findings.append(
                        ReviewFinding(
                            "research_fact_evidence_missing",
                            (
                                "Every structured research fact requires "
                                "an evidence reference."
                            ),
                        )
                    )
                fact_date = getattr(fact, "as_of_date", "")
                try:
                    parsed_date = date.fromisoformat(fact_date)
                except (TypeError, ValueError):
                    findings.append(
                        ReviewFinding(
                            "research_fact_date_invalid",
                            "Research fact date is invalid.",
                        )
                    )
                else:
                    cutoff = getattr(task, "as_of_date", None)
                    if cutoff is not None and parsed_date > date.fromisoformat(
                        cutoff
                    ):
                        findings.append(
                            ReviewFinding(
                                "research_fact_after_cutoff",
                                (
                                    "Research fact date exceeds the "
                                    "requested cutoff."
                                ),
                            )
                        )
                fact_kind = getattr(fact, "kind", None)
                if fact_kind is ResearchFactKind.OFFICIAL and not any(
                    re.match(r"^https?://", ref, flags=re.IGNORECASE)
                    for ref in refs
                ):
                    findings.append(
                        ReviewFinding(
                            "official_fact_source_invalid",
                            (
                                "Official facts require an external "
                                "document reference."
                            ),
                        )
                    )
                if (
                    fact_kind is ResearchFactKind.DERIVED
                    and not tuple(getattr(fact, "limitations", ()))
                ):
                    findings.append(
                        ReviewFinding(
                            "derived_fact_methodology_missing",
                            (
                                "Derived facts require a visible "
                                "methodology or scope limitation."
                            ),
                        )
                    )

        result = ReviewResult(_deduplicate(findings))
        if user_markdown is not None:
            return self.review_markdown(
                report,
                user_markdown,
                prior=result,
            )
        return result

    def review_markdown(
        self,
        report: object,
        user_markdown: str,
        *,
        prior: ReviewResult | None = None,
        charts: tuple[object, ...] = (),
    ) -> ReviewResult:
        findings = list(prior.findings if prior else ())
        if _contains_internal_value(report, user_markdown):
            findings.append(
                ReviewFinding(
                    "internal_field_leak",
                    "User Markdown contains an internal field or raw "
                    "contract value.",
                )
            )
        expected_charts = tuple(
            artifact
            for step in getattr(report, "steps", ())
            if getattr(step, "step_id", None) == "charts"
            for artifact in getattr(step, "artifacts", ())
        )
        image_lines = tuple(
            line.strip()
            for line in user_markdown.splitlines()
            if line.strip().startswith("![")
        )
        if expected_charts and len(image_lines) < len(expected_charts):
            findings.append(
                ReviewFinding(
                    "chart_citation_missing",
                    (
                        "Every generated chart artifact requires a "
                        "Markdown image citation."
                    ),
                )
            )
        missing_paths = tuple(
            str(path)
            for chart in charts
            if (
                path := getattr(chart, "markdown_path", "")
            )
            and f"]({path})" not in user_markdown
        )
        if missing_paths:
            findings.append(
                ReviewFinding(
                    "chart_citation_mismatch",
                    (
                        "Markdown does not cite every prepared chart "
                        "artifact by its published relative path."
                    ),
                )
            )
        if any(
            "](assets/" not in line
            or not re.search(r"\.(?:png|jpg|jpeg|webp|svg)\)$", line)
            for line in image_lines
        ):
            findings.append(
                ReviewFinding(
                    "chart_citation_invalid",
                    (
                        "Chart citations must use report-relative static "
                        "asset paths."
                    ),
                )
            )
        return ReviewResult(_deduplicate(findings))

    def review_blocked_execution(
        self,
        execution: ResearchExecutionResult,
    ) -> ReviewResult:
        findings = [
            ReviewFinding(
                "execution_blocked",
                "Execution is blocked; no report conclusion may be "
                "published.",
            )
        ]
        findings.extend(
            ReviewFinding("blocking_gap", gap.message)
            for gap in execution.gaps
            if gap.blocking
        )
        return ReviewResult(_deduplicate(findings))


def _contains_internal_value(
    report: object,
    markdown: str,
) -> bool:
    task = getattr(report, "task", None)
    exact_values = [
        getattr(report, "acceptance_version", None),
        getattr(report, "report_id", None),
        getattr(report, "structure_digest", None),
        *(
            [
                getattr(task, "task_id", None),
                getattr(task, "asset_id", None),
                getattr(task, "profile_id", None),
                getattr(task, "benchmark_asset_id", None),
                *getattr(task, "peer_asset_ids", ()),
            ]
            if task is not None
            else []
        ),
    ]
    if any(
        isinstance(value, str) and value and value in markdown
        for value in exact_values
    ):
        return True

    scenarios = tuple(getattr(report, "scenarios", ()))
    claims = [
        *getattr(report, "conclusions", ()),
        getattr(report, "strongest_counterargument", None),
        *[
            claim
            for scenario in scenarios
            for claim in getattr(scenario, "claims", ())
        ],
    ]
    raw_enum_values = {
        _enum_value(getattr(report, "overall_status", None)),
        *(
            {
                _enum_value(getattr(task, "question_type", None)),
                _enum_value(getattr(task, "horizon", None)),
                _enum_value(getattr(task, "output_depth", None)),
            }
            if task is not None
            else set()
        ),
        *{
            _enum_value(getattr(step, "status", None))
            for step in getattr(report, "steps", ())
        },
        *{
            _enum_value(getattr(scenario, "kind", None))
            for scenario in scenarios
        },
        *{
            _enum_value(getattr(claim, "kind", None))
            for claim in claims
            if claim is not None
        },
    }
    for value in raw_enum_values - {None, ""}:
        if re.search(
            rf"(?<![\w]){re.escape(value)}(?![\w])",
            markdown,
            flags=re.IGNORECASE,
        ):
            return True
    return bool(
        re.search(
            r"(?<![\w])src[\\/]|"
            r"\bresearch_(?:acceptance|workflow|orchestration)\b|"
            r"\b(?:structure_)?digest\b|"
            r"\breplay_verified\b",
            markdown,
            flags=re.IGNORECASE,
        )
    )


def _enum_value(value: object) -> str | None:
    raw = getattr(value, "value", None)
    return raw if isinstance(raw, str) else None


def _deduplicate(
    findings: list[ReviewFinding],
) -> tuple[ReviewFinding, ...]:
    return tuple(dict.fromkeys(findings))
