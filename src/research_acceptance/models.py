"""Serializable contracts for representative research acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.research_workflow import ResearchTaskSpec


class AcceptanceStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class AcceptanceStepStatus(str, Enum):
    PASS = "pass"
    GAP = "gap"
    NOT_APPLICABLE = "not_applicable"
    FAIL = "fail"


class ClaimKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ScenarioKind(str, Enum):
    OPTIMISTIC = "optimistic"
    BASE = "base"
    PESSIMISTIC = "pessimistic"


@dataclass(frozen=True)
class AcceptanceStepResult:
    step_id: str
    status: AcceptanceStepStatus
    summary: str
    sources: tuple[str, ...] = ()
    metric_versions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.step_id, "step_id")
        _text(self.summary, "summary")
        if (
            self.status is AcceptanceStepStatus.PASS
            and not self.sources
        ):
            raise ValueError("Passing step requires a source")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "summary": self.summary,
            "sources": list(self.sources),
            "metric_versions": list(self.metric_versions),
            "artifacts": list(self.artifacts),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "AcceptanceStepResult":
        return cls(
            step_id=str(payload["step_id"]),
            status=AcceptanceStepStatus(payload["status"]),
            summary=str(payload["summary"]),
            sources=tuple(payload.get("sources", ())),
            metric_versions=tuple(
                payload.get("metric_versions", ())
            ),
            artifacts=tuple(payload.get("artifacts", ())),
            limitations=tuple(payload.get("limitations", ())),
        )


@dataclass(frozen=True)
class ResearchClaim:
    kind: ClaimKind
    text: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.text, "claim text")
        if self.kind is ClaimKind.FACT and not self.evidence_refs:
            raise ValueError("Fact claim requires evidence_refs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "text": self.text,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResearchClaim":
        return cls(
            kind=ClaimKind(payload["kind"]),
            text=str(payload["text"]),
            evidence_refs=tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class ResearchScenario:
    kind: ScenarioKind
    claims: tuple[ResearchClaim, ...]

    def __post_init__(self) -> None:
        if not self.claims:
            raise ValueError("Scenario requires at least one claim")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "claims": [claim.to_dict() for claim in self.claims],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchScenario":
        return cls(
            kind=ScenarioKind(payload["kind"]),
            claims=tuple(
                ResearchClaim.from_dict(item)
                for item in payload["claims"]
            ),
        )


@dataclass(frozen=True)
class InvalidationCondition:
    condition: str
    evidence_required: str
    severity: str

    def __post_init__(self) -> None:
        _text(self.condition, "condition")
        _text(self.evidence_required, "evidence_required")
        if self.severity not in {"warning", "thesis_break"}:
            raise ValueError(
                "severity must be warning or thesis_break"
            )

    def to_dict(self) -> dict[str, str]:
        return {
            "condition": self.condition,
            "evidence_required": self.evidence_required,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class ResearchGap:
    module: str
    code: str
    message: str
    blocking: bool

    def __post_init__(self) -> None:
        _text(self.module, "module")
        _text(self.code, "code")
        _text(self.message, "message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ResearchAcceptanceReport:
    report_id: str
    acceptance_version: str
    task: ResearchTaskSpec
    asset_name: str
    asset_type: str
    steps: tuple[AcceptanceStepResult, ...]
    scenarios: tuple[ResearchScenario, ...]
    strongest_counterargument: ResearchClaim
    invalidation_conditions: tuple[InvalidationCondition, ...]
    conclusions: tuple[ResearchClaim, ...]
    gaps: tuple[ResearchGap, ...]
    overall_status: AcceptanceStatus
    structure_digest: str
    replay_verified: bool

    def __post_init__(self) -> None:
        for value, name in (
            (self.report_id, "report_id"),
            (self.acceptance_version, "acceptance_version"),
            (self.asset_name, "asset_name"),
            (self.asset_type, "asset_type"),
            (self.structure_digest, "structure_digest"),
        ):
            _text(value, name)
        step_ids = [step.step_id for step in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step_id must be unique")
        scenario_kinds = {item.kind for item in self.scenarios}
        if scenario_kinds != set(ScenarioKind):
            raise ValueError(
                "Report requires optimistic, base, and pessimistic "
                "scenarios"
            )
        if not self.invalidation_conditions:
            raise ValueError(
                "Report requires invalidation conditions"
            )
        if not self.conclusions:
            raise ValueError("Report requires conclusions")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "acceptance_version": self.acceptance_version,
            "task": self.task.to_dict(),
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "steps": [step.to_dict() for step in self.steps],
            "scenarios": [
                scenario.to_dict() for scenario in self.scenarios
            ],
            "strongest_counterargument": (
                self.strongest_counterargument.to_dict()
            ),
            "invalidation_conditions": [
                item.to_dict()
                for item in self.invalidation_conditions
            ],
            "conclusions": [
                claim.to_dict() for claim in self.conclusions
            ],
            "gaps": [gap.to_dict() for gap in self.gaps],
            "overall_status": self.overall_status.value,
            "structure_digest": self.structure_digest,
            "replay_verified": self.replay_verified,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchAcceptanceReport":
        return cls(
            report_id=str(payload["report_id"]),
            acceptance_version=str(payload["acceptance_version"]),
            task=ResearchTaskSpec.from_dict(payload["task"]),
            asset_name=str(payload["asset_name"]),
            asset_type=str(payload["asset_type"]),
            steps=tuple(
                AcceptanceStepResult.from_dict(item)
                for item in payload["steps"]
            ),
            scenarios=tuple(
                ResearchScenario.from_dict(item)
                for item in payload["scenarios"]
            ),
            strongest_counterargument=ResearchClaim.from_dict(
                payload["strongest_counterargument"]
            ),
            invalidation_conditions=tuple(
                InvalidationCondition(
                    condition=str(item["condition"]),
                    evidence_required=str(
                        item["evidence_required"]
                    ),
                    severity=str(item["severity"]),
                )
                for item in payload["invalidation_conditions"]
            ),
            conclusions=tuple(
                ResearchClaim.from_dict(item)
                for item in payload["conclusions"]
            ),
            gaps=tuple(
                ResearchGap(
                    module=str(item["module"]),
                    code=str(item["code"]),
                    message=str(item["message"]),
                    blocking=bool(item["blocking"]),
                )
                for item in payload["gaps"]
            ),
            overall_status=AcceptanceStatus(
                payload["overall_status"]
            ),
            structure_digest=str(payload["structure_digest"]),
            replay_verified=bool(payload["replay_verified"]),
        )


@dataclass(frozen=True)
class CrossAssetAcceptanceSummary:
    acceptance_version: str
    as_of_date: str
    reports: tuple[ResearchAcceptanceReport, ...]
    complete_profile_count: int
    required_complete_profiles: int
    consistency_verified: bool
    agent_guidance_admitted: bool
    admission_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.reports:
            raise ValueError("Cross-asset summary requires reports")
        if self.complete_profile_count < 0:
            raise ValueError(
                "complete_profile_count must not be negative"
            )
        if self.required_complete_profiles < 1:
            raise ValueError(
                "required_complete_profiles must be positive"
            )
        if not self.admission_reasons:
            raise ValueError("Admission reasons must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "acceptance_version": self.acceptance_version,
            "as_of_date": self.as_of_date,
            "reports": [
                report.to_dict() for report in self.reports
            ],
            "complete_profile_count": self.complete_profile_count,
            "required_complete_profiles": (
                self.required_complete_profiles
            ),
            "consistency_verified": self.consistency_verified,
            "agent_guidance_admitted": (
                self.agent_guidance_admitted
            ),
            "admission_reasons": list(self.admission_reasons),
        }


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
