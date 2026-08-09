"""Profile-based dispatch for formal research execution services."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from src.research_workflow import (
    ResearchExecutionGap,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchTaskSpec,
    ResearchWindows,
)


class ResearchExecutionServiceProtocol(Protocol):
    def execute(
        self,
        task: ResearchTaskSpec,
        *,
        options: object | None = None,
    ) -> ResearchExecutionResult:
        ...


class MissingExecutionOptionsError(ValueError):
    """Raised when a selected execution service requires options."""


@dataclass(frozen=True)
class ResearchExecutionServiceAdapter:
    """Normalize services whose ``options`` argument is optional or required."""

    service: object

    def execute(
        self,
        task: ResearchTaskSpec,
        options: object | None = None,
    ) -> ResearchExecutionResult:
        method = getattr(self.service, "execute", None)
        if not callable(method):
            raise TypeError("Research execution service requires execute()")

        parameters = inspect.signature(method).parameters
        option_parameter = parameters.get("options")
        if option_parameter is None:
            return method(task)
        if options is not None:
            return method(task, options=options)
        if option_parameter.default is inspect.Parameter.empty:
            raise MissingExecutionOptionsError(
                "The selected research profile requires execution options"
            )
        return method(task)


class ResearchExecutionDispatcher:
    """Select the execution service solely from ``task.profile_id``."""

    def __init__(
        self,
        services: Mapping[str, object],
    ) -> None:
        self._services = {
            profile_id: (
                service
                if isinstance(service, ResearchExecutionServiceAdapter)
                else ResearchExecutionServiceAdapter(service)
            )
            for profile_id, service in services.items()
        }

    def execute(
        self,
        task: ResearchTaskSpec,
        options: object | None = None,
    ) -> ResearchExecutionResult:
        adapter = self._services.get(task.profile_id)
        if adapter is None:
            return _blocked_execution(
                task,
                code="unknown_research_profile",
                message=(
                    "No execution service is configured for this "
                    "research profile."
                ),
            )
        try:
            return adapter.execute(task, options)
        except MissingExecutionOptionsError as exc:
            return _blocked_execution(
                task,
                code="execution_options_required",
                message=str(exc),
            )


def _blocked_execution(
    task: ResearchTaskSpec,
    *,
    code: str,
    message: str,
) -> ResearchExecutionResult:
    return ResearchExecutionResult(
        task=task,
        windows=ResearchWindows(
            bar_start=task.as_of_date,
            bar_end=task.as_of_date,
            financial_start=task.as_of_date,
            financial_end=task.as_of_date,
            evidence_start=task.as_of_date,
            evidence_end=task.as_of_date,
            strategy_window_days=0,
        ),
        status=ResearchExecutionStatus.BLOCKED,
        phases=(
            ResearchPhaseResult(
                phase_id="task",
                status=ResearchPhaseStatus.FAILED,
                summary="Research execution could not be started.",
                error=message,
            ),
        ),
        gaps=(
            ResearchExecutionGap(
                module="research_orchestration",
                code=code,
                message=message,
                blocking=True,
            ),
        ),
    )
