"""Deterministic assembly and admission checks for research acceptance."""

from __future__ import annotations

import hashlib
import json

from src.research_workflow import ResearchTaskSpec

from .models import (
    AcceptanceStatus,
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    CrossAssetAcceptanceSummary,
    InvalidationCondition,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchFactSection,
    ResearchGap,
    ResearchScenario,
)


REQUIRED_ACCEPTANCE_STEPS = (
    "task",
    "data_quality",
    "metrics",
    "charts",
    "comparison",
    "strategy",
    "evidence",
)


class RepresentativeResearchAssembler:
    """Build one report from public-module outputs, without SQL joins."""

    def assemble(
        self,
        *,
        acceptance_version: str,
        task: ResearchTaskSpec,
        asset_name: str,
        asset_type: str,
        steps: tuple[AcceptanceStepResult, ...],
        scenarios: tuple[ResearchScenario, ...],
        strongest_counterargument: ResearchClaim,
        invalidation_conditions: tuple[
            InvalidationCondition,
            ...,
        ],
        conclusions: tuple[ResearchClaim, ...],
        gaps: tuple[ResearchGap, ...],
        fact_sections: tuple[ResearchFactSection, ...] = (),
    ) -> ResearchAcceptanceReport:
        by_id = {step.step_id: step for step in steps}
        missing_steps = set(REQUIRED_ACCEPTANCE_STEPS) - set(by_id)
        extra_steps = set(by_id) - set(REQUIRED_ACCEPTANCE_STEPS)
        if missing_steps or extra_steps or len(by_id) != len(steps):
            raise ValueError(
                "Acceptance steps must match the frozen contract; "
                f"missing={sorted(missing_steps)}, "
                f"extra={sorted(extra_steps)}"
            )
        ordered_steps = tuple(
            by_id[step_id] for step_id in REQUIRED_ACCEPTANCE_STEPS
        )
        overall_status = _overall_status(
            ordered_steps,
            scenarios,
            strongest_counterargument,
            conclusions,
            gaps,
        )
        structure_payload = _structure_payload(
            acceptance_version,
            task,
            ordered_steps,
            scenarios,
            strongest_counterargument,
            invalidation_conditions,
            conclusions,
            gaps,
            fact_sections,
        )
        structure_digest = _digest(structure_payload)
        report_payload = {
            **structure_payload,
            "asset_name": asset_name,
            "asset_type": asset_type,
            "overall_status": overall_status.value,
        }
        report_id = f"acceptance:{_digest(report_payload)[:24]}"
        replay_task = ResearchTaskSpec.from_dict(task.to_dict())
        replay_digest = _digest(
            _structure_payload(
                acceptance_version,
                replay_task,
                ordered_steps,
                scenarios,
                strongest_counterargument,
                invalidation_conditions,
                conclusions,
                gaps,
                fact_sections,
            )
        )
        return ResearchAcceptanceReport(
            report_id=report_id,
            acceptance_version=acceptance_version,
            task=task,
            asset_name=asset_name,
            asset_type=asset_type,
            steps=ordered_steps,
            scenarios=scenarios,
            strongest_counterargument=strongest_counterargument,
            invalidation_conditions=invalidation_conditions,
            conclusions=conclusions,
            gaps=gaps,
            overall_status=overall_status,
            structure_digest=structure_digest,
            replay_verified=replay_digest == structure_digest,
            fact_sections=fact_sections,
        )


class CrossAssetAcceptanceService:
    def summarize(
        self,
        *,
        acceptance_version: str,
        as_of_date: str,
        reports: tuple[ResearchAcceptanceReport, ...],
        required_complete_profiles: int = 3,
    ) -> CrossAssetAcceptanceSummary:
        if not reports:
            raise ValueError("reports must not be empty")
        versions_match = all(
            report.acceptance_version == acceptance_version
            for report in reports
        )
        dates_match = all(
            report.task.as_of_date == as_of_date
            for report in reports
        )
        task_ids = [report.task.task_id for report in reports]
        consistency_verified = (
            versions_match
            and dates_match
            and len(set(task_ids)) == len(task_ids)
            and all(report.replay_verified for report in reports)
        )
        complete_profiles = {
            report.task.profile_id
            for report in reports
            if report.overall_status is AcceptanceStatus.COMPLETE
        }
        has_unexplained_failure = any(
            any(
                step.status is AcceptanceStepStatus.FAIL
                for step in report.steps
            )
            and not report.gaps
            for report in reports
        )
        admitted = (
            consistency_verified
            and len(complete_profiles)
            >= required_complete_profiles
            and not has_unexplained_failure
        )
        reasons = [
            (
                "All reports share the frozen version and task "
                "structure."
                if consistency_verified
                else (
                    "Frozen version, date, task identity, or replay "
                    "consistency failed."
                )
            ),
            (
                f"{len(complete_profiles)} distinct research profiles "
                f"completed; {required_complete_profiles} required."
            ),
        ]
        if has_unexplained_failure:
            reasons.append(
                "At least one failed step has no explicit gap record."
            )
        if admitted:
            reasons.append(
                "Representative research is admitted to Agent "
                "guidance construction."
            )
        else:
            reasons.append(
                "Agent guidance construction is not yet admitted."
            )
        return CrossAssetAcceptanceSummary(
            acceptance_version=acceptance_version,
            as_of_date=as_of_date,
            reports=reports,
            complete_profile_count=len(complete_profiles),
            required_complete_profiles=required_complete_profiles,
            consistency_verified=consistency_verified,
            agent_guidance_admitted=admitted,
            admission_reasons=tuple(reasons),
        )


def _overall_status(
    steps: tuple[AcceptanceStepResult, ...],
    scenarios: tuple[ResearchScenario, ...],
    counterargument: ResearchClaim,
    conclusions: tuple[ResearchClaim, ...],
    gaps: tuple[ResearchGap, ...],
) -> AcceptanceStatus:
    if any(gap.blocking for gap in gaps) or any(
        step.status is AcceptanceStepStatus.FAIL for step in steps
    ):
        return AcceptanceStatus.BLOCKED
    has_unknown = (
        counterargument.kind is ClaimKind.UNKNOWN
        or any(claim.kind is ClaimKind.UNKNOWN for claim in conclusions)
        or any(
            claim.kind is ClaimKind.UNKNOWN
            for scenario in scenarios
            for claim in scenario.claims
        )
    )
    if (
        gaps
        or has_unknown
        or any(
            step.status is AcceptanceStepStatus.GAP
            for step in steps
        )
    ):
        return AcceptanceStatus.PARTIAL
    return AcceptanceStatus.COMPLETE


def _structure_payload(
    acceptance_version: str,
    task: ResearchTaskSpec,
    steps: tuple[AcceptanceStepResult, ...],
    scenarios: tuple[ResearchScenario, ...],
    counterargument: ResearchClaim,
    invalidation_conditions: tuple[InvalidationCondition, ...],
    conclusions: tuple[ResearchClaim, ...],
    gaps: tuple[ResearchGap, ...],
    fact_sections: tuple[ResearchFactSection, ...],
) -> dict[str, object]:
    return {
        "acceptance_version": acceptance_version,
        "task": task.to_dict(),
        "steps": [
            {
                "step_id": step.step_id,
                "status": step.status.value,
                "source_count": len(step.sources),
                "metric_versions": list(step.metric_versions),
                "artifact_kinds": [
                    artifact.split(":", 1)[0]
                    for artifact in step.artifacts
                ],
                "limitation_count": len(step.limitations),
            }
            for step in steps
        ],
        "scenarios": [
            {
                "kind": scenario.kind.value,
                "claim_kinds": [
                    claim.kind.value for claim in scenario.claims
                ],
            }
            for scenario in scenarios
        ],
        "counterargument_kind": counterargument.kind.value,
        "invalidation_severities": [
            item.severity for item in invalidation_conditions
        ],
        "conclusion_kinds": [
            claim.kind.value for claim in conclusions
        ],
        "gap_codes": sorted(gap.code for gap in gaps),
        "fact_sections": [
            {
                "section_id": section.section_id,
                "fact_labels": [
                    fact.label for fact in section.facts
                ],
                "fact_kinds": [
                    fact.kind.value for fact in section.facts
                ],
            }
            for section in fact_sections
        ],
    }


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
