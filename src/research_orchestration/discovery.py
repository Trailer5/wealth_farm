"""One-batch orchestration for discovery preparation and funnel execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.research_workflow import ResearchExecutionOptions
from src.security_discovery import (
    DiscoveryPreparationProgress,
    DiscoveryRun,
    DiscoverySpec,
)


@dataclass(frozen=True)
class DiscoveryRunRequest:
    job_id: str
    spec: DiscoverySpec
    profile_by_asset_id: dict[str, str]
    batch_size: int = 20
    max_attempts_per_member: int = 3
    options: ResearchExecutionOptions | None = None

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("job_id must not be blank")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.max_attempts_per_member < 1:
            raise ValueError(
                "max_attempts_per_member must be positive"
            )


@dataclass(frozen=True)
class DiscoveryOrchestrationResult:
    progress: DiscoveryPreparationProgress
    discovery_run: DiscoveryRun | None

    @property
    def preparation_complete(self) -> bool:
        return self.progress.job.status == "completed"


class PreparationJobProtocol(Protocol):
    def create_job(
        self,
        *,
        job_id: str,
        spec: DiscoverySpec,
        max_attempts_per_member: int,
    ) -> object:
        ...

    def run_batch(
        self,
        *,
        job_id: str,
        spec: DiscoverySpec,
        profile_by_asset_id: dict[str, str],
        batch_size: int,
        options: ResearchExecutionOptions | None = None,
    ) -> DiscoveryPreparationProgress:
        ...


class DiscoveryServiceProtocol(Protocol):
    def run(self, spec: DiscoverySpec) -> DiscoveryRun:
        ...


class DiscoveryOrchestrationPipeline:
    """Advance one bounded preparation batch and run discovery when ready."""

    def __init__(
        self,
        *,
        preparation_jobs: PreparationJobProtocol,
        discovery_service: DiscoveryServiceProtocol,
    ) -> None:
        self.preparation_jobs = preparation_jobs
        self.discovery_service = discovery_service

    def run(
        self,
        request: DiscoveryRunRequest,
    ) -> DiscoveryOrchestrationResult:
        self.preparation_jobs.create_job(
            job_id=request.job_id,
            spec=request.spec,
            max_attempts_per_member=request.max_attempts_per_member,
        )
        progress = self.preparation_jobs.run_batch(
            job_id=request.job_id,
            spec=request.spec,
            profile_by_asset_id=request.profile_by_asset_id,
            batch_size=request.batch_size,
            options=request.options,
        )
        discovery_run = (
            self.discovery_service.run(request.spec)
            if progress.job.status == "completed"
            else None
        )
        return DiscoveryOrchestrationResult(
            progress=progress,
            discovery_run=discovery_run,
        )
