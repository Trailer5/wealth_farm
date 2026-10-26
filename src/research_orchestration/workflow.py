"""Baseline handoff and top-level workflow-run coordination."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import uuid4

from src.master_data import MasterDataService
from src.research_contracts import (
    DecisionLane,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from src.research_workflow import ResearchExecutionGap

from .capabilities import ResearchCapabilityRegistry
from .models import (
    ResearchRunRequest,
    ResearchRunResult,
    ResearchRunStatus,
)
from .monitoring_models import (
    FinalDecisionPublicationAttestation,
    MonitoringConditionDisposition,
    MonitoringHandoff,
    MonitoringHandoffStatus,
    MonitoringRuleType,
    monitoring_rule_matches_observability,
)


class HandoffGapSource(str, Enum):
    EXECUTION = "execution"
    ACCEPTANCE = "acceptance"
    CAPABILITY = "capability"


@dataclass(frozen=True)
class ResearchAssetIdentity:
    asset_id: str
    symbol: str
    asset_type: str
    display_name: str | None
    market: str | None
    exchange: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "display_name": self.display_name,
            "market": self.market,
            "exchange": self.exchange,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchAssetIdentity":
        return cls(
            asset_id=str(payload["asset_id"]),
            symbol=str(payload["symbol"]),
            asset_type=str(payload["asset_type"]),
            display_name=_optional_string(
                payload.get("display_name")
            ),
            market=_optional_string(payload.get("market")),
            exchange=_optional_string(payload.get("exchange")),
        )


@dataclass(frozen=True)
class ResearchHandoffGap:
    source: HandoffGapSource
    module: str
    code: str
    message: str
    blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "module": self.module,
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchHandoffGap":
        return cls(
            source=HandoffGapSource(payload["source"]),
            module=str(payload["module"]),
            code=str(payload["code"]),
            message=str(payload["message"]),
            blocking=bool(payload["blocking"]),
        )


@dataclass(frozen=True)
class ResearchHandoffPhaseStatus:
    phase_id: str
    status: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "phase_id": self.phase_id,
            "status": self.status,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchHandoffPhaseStatus":
        return cls(
            phase_id=str(payload["phase_id"]),
            status=str(payload["status"]),
            summary=str(payload["summary"]),
        )


@dataclass(frozen=True)
class BaselineChartArtifactReference:
    markdown_path: str
    content_digest: str
    metadata_digest: str
    data_digest: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.markdown_path.replace("\\", "/"))
        if (
            path.is_absolute()
            or ":" in self.markdown_path
            or ".." in path.parts
            or not path.parts
            or path.parts[0] != "assets"
            or path.suffix.lower()
            not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
        ):
            raise ValueError(
                "baseline chart path must be a relative static asset"
            )
        for value, name in (
            (self.content_digest, "content_digest"),
            (self.metadata_digest, "metadata_digest"),
            (self.data_digest, "data_digest"),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError(f"{name} must be a SHA256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "markdown_path": self.markdown_path,
            "content_digest": self.content_digest,
            "metadata_digest": self.metadata_digest,
            "data_digest": self.data_digest,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "BaselineChartArtifactReference":
        return cls(
            markdown_path=str(payload["markdown_path"]),
            content_digest=str(payload["content_digest"]),
            metadata_digest=str(payload["metadata_digest"]),
            data_digest=str(payload["data_digest"]),
        )


@dataclass(frozen=True)
class BaselineResearchHandoff:
    handoff_id: str
    workflow_run_id: str
    task: ResearchTaskSpec
    run_status: ResearchRunStatus
    report_path: str | None
    asset_identity: ResearchAssetIdentity
    decision_lanes: tuple[DecisionLane, ...]
    available_fact_sections: tuple[str, ...]
    phase_statuses: tuple[ResearchHandoffPhaseStatus, ...]
    partial_gaps: tuple[ResearchHandoffGap, ...]
    blocking_gaps: tuple[ResearchHandoffGap, ...]
    capability_gaps: tuple[ResearchHandoffGap, ...]
    candidate_questions: tuple[str, ...]
    candidate_hypotheses: tuple[str, ...]
    created_at: str
    report_digest: str | None = None
    chart_artifacts: tuple[
        BaselineChartArtifactReference,
        ...,
    ] = ()

    def __post_init__(self) -> None:
        if self.run_status is ResearchRunStatus.BLOCKED:
            raise ValueError(
                "blocked runs must not create a baseline handoff"
            )
        datetime.fromisoformat(self.created_at)
        if self.report_digest is not None and (
            len(self.report_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.report_digest
            )
        ):
            raise ValueError(
                "report_digest must be a SHA256 digest"
            )
        chart_paths = tuple(
            item.markdown_path for item in self.chart_artifacts
        )
        if len(chart_paths) != len(set(chart_paths)):
            raise ValueError(
                "baseline chart artifact paths must be unique"
            )
        if any(item.blocking for item in self.partial_gaps):
            raise ValueError("partial_gaps must be non-blocking")
        if any(
            not item.blocking for item in self.blocking_gaps
        ):
            raise ValueError("blocking_gaps must be blocking")
        if any(
            item.source is not HandoffGapSource.CAPABILITY
            for item in self.capability_gaps
        ):
            raise ValueError(
                "capability_gaps must use capability source"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "workflow_run_id": self.workflow_run_id,
            "task": self.task.to_dict(),
            "run_status": self.run_status.value,
            "report_path": self.report_path,
            "asset_identity": self.asset_identity.to_dict(),
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "available_fact_sections": list(
                self.available_fact_sections
            ),
            "phase_statuses": [
                item.to_dict() for item in self.phase_statuses
            ],
            "partial_gaps": [
                item.to_dict() for item in self.partial_gaps
            ],
            "blocking_gaps": [
                item.to_dict() for item in self.blocking_gaps
            ],
            "capability_gaps": [
                item.to_dict() for item in self.capability_gaps
            ],
            "candidate_questions": list(
                self.candidate_questions
            ),
            "candidate_hypotheses": list(
                self.candidate_hypotheses
            ),
            "created_at": self.created_at,
            "report_digest": self.report_digest,
            "chart_artifacts": [
                item.to_dict() for item in self.chart_artifacts
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "BaselineResearchHandoff":
        return cls(
            handoff_id=str(payload["handoff_id"]),
            workflow_run_id=str(payload["workflow_run_id"]),
            task=ResearchTaskSpec.from_dict(payload["task"]),
            run_status=ResearchRunStatus(payload["run_status"]),
            report_path=_optional_string(
                payload.get("report_path")
            ),
            asset_identity=ResearchAssetIdentity.from_dict(
                payload["asset_identity"]
            ),
            decision_lanes=tuple(
                DecisionLane(item)
                for item in payload.get("decision_lanes", ())
            ),
            available_fact_sections=tuple(
                str(item)
                for item in payload.get(
                    "available_fact_sections",
                    (),
                )
            ),
            phase_statuses=tuple(
                ResearchHandoffPhaseStatus.from_dict(item)
                for item in payload.get("phase_statuses", ())
            ),
            partial_gaps=tuple(
                ResearchHandoffGap.from_dict(item)
                for item in payload.get("partial_gaps", ())
            ),
            blocking_gaps=tuple(
                ResearchHandoffGap.from_dict(item)
                for item in payload.get("blocking_gaps", ())
            ),
            capability_gaps=tuple(
                ResearchHandoffGap.from_dict(item)
                for item in payload.get("capability_gaps", ())
            ),
            candidate_questions=tuple(
                str(item)
                for item in payload.get("candidate_questions", ())
            ),
            candidate_hypotheses=tuple(
                str(item)
                for item in payload.get(
                    "candidate_hypotheses",
                    (),
                )
            ),
            created_at=str(payload["created_at"]),
            report_digest=_optional_string(
                payload.get("report_digest")
            ),
            chart_artifacts=tuple(
                BaselineChartArtifactReference.from_dict(item)
                for item in payload.get("chart_artifacts", ())
            ),
        )


@dataclass(frozen=True)
class ResearchWorkflowRun:
    workflow_run_id: str
    task: ResearchTaskSpec
    decision_brief_id: str | None
    decision_lanes: tuple[DecisionLane, ...]
    baseline_status: ResearchRunStatus
    baseline_report_path: str | None
    handoff_id: str | None
    blocking_gap_codes: tuple[str, ...]
    blocking_gaps: tuple[ResearchHandoffGap, ...]
    exploration_session_id: str | None
    created_at: str
    updated_at: str
    final_report_id: str | None = None
    monitoring_handoff_id: str | None = None
    monitoring_status: MonitoringHandoffStatus | None = None
    alert_rule_refs: tuple[str, ...] = ()
    monitoring_gap_codes: tuple[str, ...] = ()
    user_id: str | None = None

    def __post_init__(self) -> None:
        datetime.fromisoformat(self.created_at)
        datetime.fromisoformat(self.updated_at)
        if any(not gap.blocking for gap in self.blocking_gaps):
            raise ValueError(
                "workflow blocking_gaps must be blocking"
            )
        if self.baseline_status is ResearchRunStatus.BLOCKED:
            if self.handoff_id is not None:
                raise ValueError(
                    "blocked workflow run must not have handoff_id"
                )
            if self.exploration_session_id is not None:
                raise ValueError(
                    "blocked workflow run must not have exploration session"
                )
            if (
                self.final_report_id is not None
                or self.monitoring_handoff_id is not None
                or self.monitoring_status is not None
                or self.alert_rule_refs
            ):
                raise ValueError(
                    "blocked workflow run cannot carry final monitoring state"
                )
        elif self.handoff_id is None:
            raise ValueError(
                "publishable baseline requires handoff_id"
            )
        if self.final_report_id is None and (
            self.monitoring_handoff_id is not None
            or self.monitoring_status is not None
            or self.alert_rule_refs
            or self.monitoring_gap_codes
        ):
            raise ValueError(
                "monitoring state requires a final report"
            )
        if self.final_report_id is not None and self.user_id is None:
            raise ValueError("final report requires workflow user ownership")
        if (
            self.monitoring_handoff_id is None
            and (
                self.monitoring_status is not None
                or self.alert_rule_refs
                or self.monitoring_gap_codes
            )
        ):
            raise ValueError(
                "monitoring status requires a monitoring handoff"
            )
        if (
            self.final_report_id is not None
            and self.exploration_session_id is None
        ):
            raise ValueError(
                "final report requires a linked exploration session"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_run_id": self.workflow_run_id,
            "task": self.task.to_dict(),
            "decision_brief_id": self.decision_brief_id,
            "decision_lanes": [
                item.value for item in self.decision_lanes
            ],
            "baseline_status": self.baseline_status.value,
            "baseline_report_path": self.baseline_report_path,
            "handoff_id": self.handoff_id,
            "blocking_gap_codes": list(
                self.blocking_gap_codes
            ),
            "blocking_gaps": [
                item.to_dict() for item in self.blocking_gaps
            ],
            "exploration_session_id": (
                self.exploration_session_id
            ),
            "final_report_id": self.final_report_id,
            "monitoring_handoff_id": self.monitoring_handoff_id,
            "monitoring_status": (
                self.monitoring_status.value
                if self.monitoring_status is not None
                else None
            ),
            "alert_rule_refs": list(self.alert_rule_refs),
            "monitoring_gap_codes": list(
                self.monitoring_gap_codes
            ),
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "ResearchWorkflowRun":
        return cls(
            workflow_run_id=str(payload["workflow_run_id"]),
            task=ResearchTaskSpec.from_dict(payload["task"]),
            decision_brief_id=_optional_string(
                payload.get("decision_brief_id")
            ),
            decision_lanes=tuple(
                DecisionLane(item)
                for item in payload.get("decision_lanes", ())
            ),
            baseline_status=ResearchRunStatus(
                payload["baseline_status"]
            ),
            baseline_report_path=_optional_string(
                payload.get("baseline_report_path")
            ),
            handoff_id=_optional_string(
                payload.get("handoff_id")
            ),
            blocking_gap_codes=tuple(
                str(item)
                for item in payload.get(
                    "blocking_gap_codes",
                    (),
                )
            ),
            blocking_gaps=tuple(
                ResearchHandoffGap.from_dict(item)
                for item in payload.get("blocking_gaps", ())
            ),
            exploration_session_id=_optional_string(
                payload.get("exploration_session_id")
            ),
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
            final_report_id=_optional_string(
                payload.get("final_report_id")
            ),
            monitoring_handoff_id=_optional_string(
                payload.get("monitoring_handoff_id")
            ),
            monitoring_status=(
                MonitoringHandoffStatus(
                    payload["monitoring_status"]
                )
                if payload.get("monitoring_status") is not None
                else None
            ),
            alert_rule_refs=tuple(
                str(item)
                for item in payload.get("alert_rule_refs", ())
            ),
            monitoring_gap_codes=tuple(
                str(item)
                for item in payload.get(
                    "monitoring_gap_codes",
                    (),
                )
            ),
            user_id=_optional_string(payload.get("user_id")),
        )


class ResearchWorkflowRepositoryProtocol(Protocol):
    def initialize(self) -> None: ...

    def save_workflow_run(
        self,
        workflow_run: ResearchWorkflowRun,
    ) -> None: ...

    def save_handoff(
        self,
        handoff: BaselineResearchHandoff,
    ) -> None: ...

    def get_workflow_run(
        self,
        workflow_run_id: str,
    ) -> ResearchWorkflowRun | None: ...

    def get_handoff(
        self,
        handoff_id: str,
    ) -> BaselineResearchHandoff | None: ...

    def delete_handoff_for_run(
        self,
        workflow_run_id: str,
    ) -> None: ...

    def save_monitoring_handoff(
        self,
        handoff: MonitoringHandoff,
    ) -> None: ...

    def get_monitoring_handoff(
        self,
        handoff_id: str,
    ) -> MonitoringHandoff | None: ...

    def save_final_publication(
        self,
        attestation: FinalDecisionPublicationAttestation,
    ) -> None: ...

    def get_final_publication(
        self,
        report_id: str,
    ) -> FinalDecisionPublicationAttestation | None: ...

    def list_final_publications(
        self,
    ) -> tuple[FinalDecisionPublicationAttestation, ...]: ...


class ExplorationOwnershipRepositoryProtocol(Protocol):
    def get_exploration_session(
        self,
        user_id: str,
        session_id: str,
    ) -> object | None: ...


class ResearchWorkflowCoordinator:
    """Persist baseline state and create exploration-safe handoffs."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        capability_registry: ResearchCapabilityRegistry,
        repository: ResearchWorkflowRepositoryProtocol,
        exploration_repository: (
            ExplorationOwnershipRepositoryProtocol | None
        ) = None,
    ) -> None:
        self.master_data = master_data
        self.capability_registry = capability_registry
        self.repository = repository
        self.exploration_repository = exploration_repository
        self.repository.initialize()

    def record_result(
        self,
        request: ResearchRunRequest,
        result: ResearchRunResult,
    ) -> tuple[
        ResearchWorkflowRun,
        BaselineResearchHandoff | None,
    ]:
        workflow_run_id = (
            request.workflow_run_id
            or f"research-workflow:{uuid4()}"
        )
        existing = self.repository.get_workflow_run(
            workflow_run_id
        )
        if (
            existing is not None
            and existing.exploration_session_id is not None
        ):
            raise ValueError(
                "workflow baseline cannot be overwritten after "
                "exploration has started"
            )
        now = _now()
        decision_lanes = (
            request.decision_lanes
            or _default_decision_lanes(result.task.question_type)
        )
        blocking_gaps = _blocking_gaps(result)
        handoff = None
        handoff_id = None
        if result.status is not ResearchRunStatus.BLOCKED:
            handoff_id = f"baseline-handoff:{workflow_run_id}"
            handoff = self._build_handoff(
                handoff_id=handoff_id,
                workflow_run_id=workflow_run_id,
                decision_lanes=decision_lanes,
                result=result,
                created_at=now,
            )
        workflow_run = ResearchWorkflowRun(
            workflow_run_id=workflow_run_id,
            task=result.task,
            decision_brief_id=request.decision_brief_id,
            decision_lanes=decision_lanes,
            baseline_status=result.status,
            baseline_report_path=(
                str(result.report_path)
                if result.report_path is not None
                else None
            ),
            handoff_id=handoff_id,
            blocking_gap_codes=tuple(
                dict.fromkeys(item.code for item in blocking_gaps)
            ),
            blocking_gaps=blocking_gaps,
            exploration_session_id=None,
            created_at=(
                existing.created_at
                if existing is not None
                else now
            ),
            updated_at=now,
        )
        self.repository.save_workflow_run(workflow_run)
        if handoff is not None:
            self.repository.save_handoff(handoff)
        else:
            self.repository.delete_handoff_for_run(
                workflow_run_id
            )
        return workflow_run, handoff

    def get_workflow_run(
        self,
        workflow_run_id: str,
    ) -> ResearchWorkflowRun | None:
        return self.repository.get_workflow_run(workflow_run_id)

    def get_handoff(
        self,
        handoff_id: str,
    ) -> BaselineResearchHandoff | None:
        return self.repository.get_handoff(handoff_id)

    def get_handoff_for_run(
        self,
        workflow_run_id: str,
    ) -> BaselineResearchHandoff | None:
        workflow_run = self.get_workflow_run(workflow_run_id)
        if workflow_run is None or workflow_run.handoff_id is None:
            return None
        return self.get_handoff(workflow_run.handoff_id)

    def get_monitoring_handoff(
        self,
        handoff_id: str,
    ) -> MonitoringHandoff | None:
        return self.repository.get_monitoring_handoff(handoff_id)

    def get_monitoring_handoff_for_run(
        self,
        workflow_run_id: str,
    ) -> MonitoringHandoff | None:
        workflow_run = self.get_workflow_run(workflow_run_id)
        if (
            workflow_run is None
            or workflow_run.monitoring_handoff_id is None
        ):
            return None
        return self.get_monitoring_handoff(
            workflow_run.monitoring_handoff_id
        )

    def get_final_publication(
        self,
        report_id: str,
    ) -> FinalDecisionPublicationAttestation | None:
        return self.repository.get_final_publication(report_id)

    def list_final_publications(
        self,
    ) -> tuple[FinalDecisionPublicationAttestation, ...]:
        return self.repository.list_final_publications()

    def record_final_publication(
        self,
        attestation: FinalDecisionPublicationAttestation,
    ) -> ResearchWorkflowRun:
        workflow_run = self.get_workflow_run(
            attestation.workflow_run_id
        )
        if workflow_run is None:
            raise KeyError(
                f"Unknown workflow_run_id: "
                f"{attestation.workflow_run_id}"
            )
        expected = (
            (
                workflow_run.decision_brief_id,
                attestation.decision_brief_id,
                "decision brief",
            ),
            (
                workflow_run.exploration_session_id,
                attestation.session_id,
                "exploration session",
            ),
            (
                workflow_run.handoff_id,
                attestation.baseline_handoff_id,
                "baseline handoff",
            ),
            (
                workflow_run.task.asset_id,
                attestation.asset_id,
                "asset",
            ),
            (
                workflow_run.task.profile_id,
                attestation.profile_id,
                "profile",
            ),
            (
                workflow_run.task.as_of_date,
                attestation.as_of_date,
                "cutoff date",
            ),
        )
        for actual, publication_value, label in expected:
            if actual != publication_value:
                raise ValueError(
                    f"final publication {label} does not match workflow"
                )
        if workflow_run.user_id is None:
            raise ValueError(
                "workflow user ownership must be set by exploration"
            )
        if workflow_run.user_id != attestation.user_id:
            raise ValueError(
                "workflow belongs to another final-report user"
            )
        if (
            workflow_run.final_report_id is not None
            and workflow_run.final_report_id
            != attestation.report_id
        ):
            raise ValueError(
                "workflow is already linked to another final report"
            )
        existing = self.get_final_publication(
            attestation.report_id
        )
        if existing is not None and existing != attestation:
            raise ValueError(
                "final publication attestation cannot be replaced"
            )
        self.repository.save_final_publication(attestation)
        updated = replace(
            workflow_run,
            final_report_id=attestation.report_id,
            updated_at=_now(),
        )
        self.repository.save_workflow_run(updated)
        return updated

    def record_monitoring_handoff(
        self,
        handoff: MonitoringHandoff,
    ) -> ResearchWorkflowRun:
        workflow_run = self.get_workflow_run(
            handoff.workflow_run_id
        )
        if workflow_run is None:
            raise KeyError(
                f"Unknown workflow_run_id: {handoff.workflow_run_id}"
            )
        if (
            workflow_run.baseline_status
            is ResearchRunStatus.BLOCKED
            or workflow_run.exploration_session_id is None
        ):
            raise ValueError(
                "monitoring requires a completed exploration workflow"
            )
        attestation = self.get_final_publication(
            handoff.final_report_id
        )
        if attestation is None:
            raise ValueError(
                "monitoring requires a persisted final publication"
            )
        if (
            attestation.user_id != handoff.user_id
            or attestation.workflow_run_id
            != handoff.workflow_run_id
            or attestation.monitoring_plan_id
            != handoff.monitoring_plan_id
            or attestation.asset_id != handoff.asset_id
        ):
            raise ValueError(
                "monitoring handoff does not match final publication"
            )
        _validate_monitoring_against_publication(
            handoff,
            attestation,
        )
        existing_handoff = self.get_monitoring_handoff(
            handoff.handoff_id
        )
        _validate_monitoring_transition(
            existing_handoff,
            handoff,
        )
        if workflow_run.user_id != handoff.user_id:
            raise ValueError(
                "monitoring handoff belongs to another workflow user"
            )
        if (
            workflow_run.final_report_id is not None
            and workflow_run.final_report_id
            != handoff.final_report_id
        ):
            raise ValueError(
                "workflow run is already linked to another final report"
            )
        if (
            workflow_run.monitoring_handoff_id is not None
            and workflow_run.monitoring_handoff_id
            != handoff.handoff_id
        ):
            raise ValueError(
                "workflow run is already linked to another monitoring handoff"
            )
        self.repository.save_monitoring_handoff(handoff)
        updated = replace(
            workflow_run,
            final_report_id=handoff.final_report_id,
            monitoring_handoff_id=handoff.handoff_id,
            monitoring_status=handoff.status,
            alert_rule_refs=handoff.alert_rule_refs,
            monitoring_gap_codes=handoff.gap_codes,
            updated_at=_now(),
        )
        self.repository.save_workflow_run(updated)
        return updated

    def is_alert_rule_authorized(
        self,
        user_id: str,
        rule: object,
    ) -> bool:
        provenance = getattr(rule, "provenance", None)
        if provenance is None:
            return True
        workflow_run = self.get_workflow_run(
            provenance.workflow_run_id
        )
        if (
            workflow_run is None
            or workflow_run.user_id != user_id
            or workflow_run.final_report_id
            != provenance.final_report_id
            or workflow_run.monitoring_handoff_id is None
            or getattr(rule, "rule_id", None)
            not in workflow_run.alert_rule_refs
        ):
            return False
        handoff = self.get_monitoring_handoff(
            workflow_run.monitoring_handoff_id
        )
        if (
            handoff is None
            or handoff.user_id != user_id
            or handoff.final_report_id
            != provenance.final_report_id
            or handoff.monitoring_plan_id
            != provenance.monitoring_plan_id
            or handoff.status
            not in {
                MonitoringHandoffStatus.ARMED,
                MonitoringHandoffStatus.PARTIALLY_ARMED,
            }
        ):
            return False
        registration = next(
            (
                item
                for item in handoff.registrations
                if item.rule_id == getattr(rule, "rule_id", None)
            ),
            None,
        )
        if registration is None:
            return False
        candidate = next(
            (
                item
                for item in handoff.rule_candidates
                if item.candidate_id
                == registration.candidate_id
            ),
            None,
        )
        return (
            candidate is not None
            and candidate.condition_id
            == provenance.monitoring_condition_id
            and candidate.condition_source
            == provenance.condition_source
            and candidate.evidence_refs
            == provenance.evidence_refs
            and candidate.candidate_id
            in handoff.confirmed_candidate_ids
        )

    def link_exploration_session(
        self,
        *,
        workflow_run_id: str,
        session_id: str,
        user_id: str,
    ) -> ResearchWorkflowRun:
        workflow_run = self.get_workflow_run(workflow_run_id)
        if workflow_run is None:
            raise KeyError(
                f"Unknown workflow_run_id: {workflow_run_id}"
            )
        if (
            workflow_run.baseline_status
            is ResearchRunStatus.BLOCKED
            or workflow_run.handoff_id is None
        ):
            raise ValueError(
                "blocked workflow run cannot start exploration"
            )
        if (
            workflow_run.exploration_session_id is not None
            and workflow_run.exploration_session_id != session_id
        ):
            raise ValueError(
                "workflow run already has an exploration session"
            )
        if (
            not isinstance(user_id, str)
            or not user_id.strip()
        ):
            raise ValueError("workflow exploration user is required")
        if self.exploration_repository is None:
            raise ValueError(
                "workflow coordinator cannot verify exploration ownership"
            )
        persisted = self.exploration_repository.get_exploration_session(
            user_id,
            session_id,
        )
        if persisted is None:
            raise ValueError(
                "exploration session ownership is not persisted"
            )
        persisted_brief_id = getattr(persisted, "decision_brief_id", None)
        # A run without a brief (run-single or an impersonal recipe) takes the
        # brief chosen when its first exploration session starts.
        binds_brief = (
            workflow_run.decision_brief_id is None
            and workflow_run.exploration_session_id is None
        )
        if (
            getattr(persisted, "user_id", None) != user_id
            or getattr(persisted, "session_id", None) != session_id
            or getattr(persisted, "workflow_run_id", None)
            != workflow_run_id
            or getattr(persisted, "handoff_id", None)
            != workflow_run.handoff_id
            or (
                persisted_brief_id != workflow_run.decision_brief_id
                and not binds_brief
            )
            or getattr(persisted, "asset_id", None)
            != workflow_run.task.asset_id
        ):
            raise ValueError(
                "persisted exploration session does not match workflow"
            )
        if (
            workflow_run.user_id is not None
            and workflow_run.user_id != user_id
        ):
            raise ValueError(
                "workflow run belongs to another exploration user"
            )
        updated = replace(
            workflow_run,
            decision_brief_id=(
                persisted_brief_id
                if binds_brief
                else workflow_run.decision_brief_id
            ),
            exploration_session_id=session_id,
            user_id=user_id,
            updated_at=_now(),
        )
        self.repository.save_workflow_run(updated)
        return updated

    def _build_handoff(
        self,
        *,
        handoff_id: str,
        workflow_run_id: str,
        decision_lanes: tuple[DecisionLane, ...],
        result: ResearchRunResult,
        created_at: str,
    ) -> BaselineResearchHandoff:
        asset = self.master_data.get_asset(result.task.asset_id)
        if asset is None:
            raise KeyError(
                f"Unknown asset_id: {result.task.asset_id}"
            )
        lane_values: tuple[DecisionLane | None, ...] = (
            decision_lanes if decision_lanes else (None,)
        )
        views = tuple(
            self.capability_registry.resolve(
                profile_id=result.task.profile_id,
                question_type=result.task.question_type,
                decision_lane=lane,
            )
            for lane in lane_values
        )
        capability_gaps = tuple(
            ResearchHandoffGap(
                source=HandoffGapSource.CAPABILITY,
                module="research_orchestration",
                code=capability,
                message=(
                    "Current workflow does not automate capability: "
                    f"{capability}"
                ),
                blocking=False,
            )
            for capability in _merge(
                *(
                    view.unsupported_capabilities
                    for view in views
                )
            )
        )
        all_gaps = _result_gaps(result)
        report = result.report
        return BaselineResearchHandoff(
            handoff_id=handoff_id,
            workflow_run_id=workflow_run_id,
            task=result.task,
            run_status=result.status,
            report_path=(
                str(result.report_path)
                if result.report_path is not None
                else None
            ),
            asset_identity=ResearchAssetIdentity(
                asset_id=asset.asset_id,
                symbol=asset.standard_symbol,
                asset_type=asset.asset_type,
                display_name=asset.display_name,
                market=asset.market,
                exchange=asset.exchange,
            ),
            decision_lanes=decision_lanes,
            available_fact_sections=tuple(
                section.section_id
                for section in (
                    report.fact_sections
                    if report is not None
                    else ()
                )
            ),
            phase_statuses=tuple(
                ResearchHandoffPhaseStatus(
                    phase_id=phase.phase_id,
                    status=phase.status.value,
                    summary=phase.summary,
                )
                for phase in getattr(
                    result.execution,
                    "phases",
                    (),
                )
            ),
            partial_gaps=tuple(
                gap for gap in all_gaps if not gap.blocking
            ),
            blocking_gaps=tuple(
                gap for gap in all_gaps if gap.blocking
            ),
            capability_gaps=capability_gaps,
            candidate_questions=_merge(
                *(view.candidate_questions for view in views)
            ),
            candidate_hypotheses=_merge(
                *(view.candidate_hypotheses for view in views)
            ),
            created_at=created_at,
            report_digest=(
                _markdown_digest(result.user_markdown)
                if isinstance(
                    getattr(result, "user_markdown", None),
                    str,
                )
                and result.user_markdown.strip()
                else None
            ),
            chart_artifacts=_baseline_chart_manifest(result),
        )


def _markdown_digest(content: str) -> str:
    normalized = content.rstrip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_monitoring_against_publication(
    handoff: MonitoringHandoff,
    attestation: FinalDecisionPublicationAttestation,
) -> None:
    plan = attestation.monitoring_plan
    canonical = {
        item.condition_id: item for item in plan.conditions
    }
    handoff_conditions = {
        item.condition_id: item for item in handoff.conditions
    }
    synthetic_ids = set(handoff_conditions).difference(canonical)
    expected_synthetic_count = 1 if plan.review_date is not None else 0
    if len(synthetic_ids) != expected_synthetic_count:
        raise ValueError(
            "monitoring handoff has an invalid review-date condition"
        )
    for condition_id, condition in handoff_conditions.items():
        original = canonical.get(condition_id)
        if original is None:
            if not (
                condition.source == "最终报告复查日期"
                and plan.review_date is not None
                and condition.review_by == plan.review_date
                and condition.disposition
                is MonitoringConditionDisposition.RULE_CANDIDATE
            ):
                raise ValueError(
                    "monitoring condition is absent from publication"
                )
            continue
        if (
            condition.text != original.text
            or condition.source != original.source
            or condition.review_by != original.review_by
            or condition.evidence_refs != original.evidence_refs
            or condition.limitations[: len(original.limitations)]
            != original.limitations
        ):
            raise ValueError(
                "monitoring condition differs from publication"
            )
        if (
            original.kind.value == "manual_review"
            and condition.disposition
            is not MonitoringConditionDisposition.MANUAL_REVIEW
        ):
            raise ValueError(
                "manual publication condition cannot become a rule"
            )
        if (
            original.kind.value == "machine_observable"
            and condition.disposition
            is MonitoringConditionDisposition.MANUAL_REVIEW
        ):
            raise ValueError(
                "machine publication condition has invalid disposition"
            )
    if not set(canonical).issubset(handoff_conditions):
        raise ValueError(
            "monitoring handoff dropped a publication condition"
        )
    for candidate in handoff.rule_candidates:
        condition = handoff_conditions[candidate.condition_id]
        if candidate.subject_asset_id != attestation.asset_id:
            raise ValueError(
                "monitoring candidate subject asset differs from report"
            )
        if (
            candidate.rule_type
            is MonitoringRuleType.MAX_WEIGHT_ABOVE
        ):
            if candidate.alert_asset_id is not None:
                raise ValueError(
                    "portfolio monitoring candidate must not scope an asset"
                )
        elif candidate.alert_asset_id != attestation.asset_id:
            raise ValueError(
                "monitoring candidate alert asset differs from report"
            )
        if (
            condition.disposition
            is not MonitoringConditionDisposition.RULE_CANDIDATE
            or candidate.condition_source != condition.source
            or candidate.evidence_refs != condition.evidence_refs
        ):
            raise ValueError(
                "monitoring candidate differs from its condition"
            )
        original = canonical.get(candidate.condition_id)
        if original is not None and not (
            original.kind.value == "machine_observable"
            and monitoring_rule_matches_observability(
                candidate.rule_type,
                original.observability_key,
            )
        ):
            raise ValueError(
                "monitoring candidate does not match publication observability"
            )
        if candidate.condition_id not in canonical and not (
            candidate.rule_type is MonitoringRuleType.REVIEW_DATE
            and candidate.review_date == plan.review_date
        ):
            raise ValueError(
                "synthetic monitoring candidate is not the review date"
            )


def _validate_monitoring_transition(
    existing: MonitoringHandoff | None,
    updated: MonitoringHandoff,
) -> None:
    if existing is None:
        if (
            updated.status
            not in {
                MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED,
                MonitoringHandoffStatus.MANUAL_REVIEW_ONLY,
                MonitoringHandoffStatus.UNSUPPORTED,
            }
            or updated.confirmed_candidate_ids
            or updated.registrations
        ):
            raise ValueError(
                "initial monitoring handoff must be an unconfirmed proposal"
            )
        return
    immutable = (
        "user_id",
        "workflow_run_id",
        "final_report_id",
        "monitoring_plan_id",
        "asset_id",
        "conditions",
        "rule_candidates",
        "created_at",
    )
    if any(
        getattr(existing, field) != getattr(updated, field)
        for field in immutable
    ):
        raise ValueError(
            "monitoring handoff immutable fields cannot change"
        )
    if not set(existing.confirmed_candidate_ids).issubset(
        updated.confirmed_candidate_ids
    ):
        raise ValueError(
            "monitoring confirmation cannot be removed"
        )
    existing_registrations = {
        item.candidate_id: item for item in existing.registrations
    }
    updated_registrations = {
        item.candidate_id: item for item in updated.registrations
    }
    if any(
        updated_registrations.get(candidate_id) != registration
        for candidate_id, registration
        in existing_registrations.items()
    ):
        raise ValueError(
            "monitoring registration cannot be removed or replaced"
        )
    allowed = {
        MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED: {
            MonitoringHandoffStatus.USER_CONFIRMATION_REQUIRED,
            MonitoringHandoffStatus.ACTIVATION_PENDING,
        },
        MonitoringHandoffStatus.ACTIVATION_PENDING: {
            MonitoringHandoffStatus.ACTIVATION_PENDING,
            MonitoringHandoffStatus.ARMED,
            MonitoringHandoffStatus.PARTIALLY_ARMED,
            MonitoringHandoffStatus.REGISTRATION_FAILED,
        },
        MonitoringHandoffStatus.PARTIALLY_ARMED: {
            MonitoringHandoffStatus.PARTIALLY_ARMED,
            MonitoringHandoffStatus.ACTIVATION_PENDING,
            MonitoringHandoffStatus.ARMED,
        },
        MonitoringHandoffStatus.REGISTRATION_FAILED: {
            MonitoringHandoffStatus.REGISTRATION_FAILED,
            MonitoringHandoffStatus.ACTIVATION_PENDING,
            MonitoringHandoffStatus.PARTIALLY_ARMED,
            MonitoringHandoffStatus.ARMED,
        },
        MonitoringHandoffStatus.ARMED: {
            MonitoringHandoffStatus.ARMED,
        },
        MonitoringHandoffStatus.MANUAL_REVIEW_ONLY: {
            MonitoringHandoffStatus.MANUAL_REVIEW_ONLY,
        },
        MonitoringHandoffStatus.UNSUPPORTED: {
            MonitoringHandoffStatus.UNSUPPORTED,
        },
    }
    if updated.status not in allowed[existing.status]:
        raise ValueError("invalid monitoring handoff status transition")


def _baseline_chart_manifest(
    result: ResearchRunResult,
) -> tuple[BaselineChartArtifactReference, ...]:
    if (
        getattr(result, "report_path", None) is None
        or not isinstance(
            getattr(result, "user_markdown", None),
            str,
        )
    ):
        return ()
    paths = tuple(
        dict.fromkeys(
            match.group(1)
            for match in re.finditer(
                r"!\[[^\]]*\]\((assets/[^\r\n]+)\)",
                result.user_markdown,
            )
        )
    )
    root = result.report_path.parent.resolve()
    manifest = []
    for raw_path in paths:
        relative = PurePosixPath(raw_path)
        chart = (root / Path(*relative.parts)).resolve()
        metadata = chart.parent / "metadata.json"
        data = chart.parent / "data.json"
        try:
            chart.relative_to(root)
            metadata.resolve().relative_to(root)
            data.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "baseline chart artifact escapes report root"
            ) from exc
        if not all(
            path.is_file() for path in (chart, metadata, data)
        ):
            raise ValueError(
                "baseline chart artifact bundle is incomplete"
            )
        manifest.append(
            BaselineChartArtifactReference(
                markdown_path=relative.as_posix(),
                content_digest=_file_digest(chart),
                metadata_digest=_file_digest(metadata),
                data_digest=_file_digest(data),
            )
        )
    return tuple(manifest)


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result_gaps(
    result: ResearchRunResult,
) -> tuple[ResearchHandoffGap, ...]:
    execution = tuple(
        _execution_gap(item) for item in result.execution.gaps
    )
    acceptance = tuple(
        ResearchHandoffGap(
            source=HandoffGapSource.ACCEPTANCE,
            module=item.module,
            code=item.code,
            message=item.message,
            blocking=item.blocking,
        )
        for item in (
            result.report.gaps
            if result.report is not None
            else ()
        )
    )
    return _dedupe_gaps((*execution, *acceptance))


def _blocking_gaps(
    result: ResearchRunResult,
) -> tuple[ResearchHandoffGap, ...]:
    gaps = [
        gap for gap in _result_gaps(result) if gap.blocking
    ]
    gaps.extend(
        ResearchHandoffGap(
            source=HandoffGapSource.ACCEPTANCE,
            module="research_orchestration",
            code=item.code,
            message=item.message,
            blocking=True,
        )
        for item in result.review.findings
        if item.blocking
    )
    return _dedupe_gaps(tuple(gaps))


def _execution_gap(
    gap: ResearchExecutionGap,
) -> ResearchHandoffGap:
    return ResearchHandoffGap(
        source=HandoffGapSource.EXECUTION,
        module=gap.module,
        code=gap.code,
        message=gap.message,
        blocking=gap.blocking,
    )


def _dedupe_gaps(
    gaps: tuple[ResearchHandoffGap, ...],
) -> tuple[ResearchHandoffGap, ...]:
    output = []
    seen = set()
    for gap in gaps:
        key = (gap.source, gap.module, gap.code)
        if key not in seen:
            seen.add(key)
            output.append(gap)
    return tuple(output)


def _default_decision_lanes(
    question_type: ResearchQuestionType,
) -> tuple[DecisionLane, ...]:
    if question_type is ResearchQuestionType.UNDERSTANDING:
        return ()
    if question_type is ResearchQuestionType.TIMING:
        return (
            DecisionLane.SUITABILITY,
            DecisionLane.TIMING,
        )
    return (DecisionLane.SUITABILITY,)


def _merge(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item
            for group in groups
            for item in group
        )
    )


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
