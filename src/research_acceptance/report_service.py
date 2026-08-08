"""Map formal execution outputs into structured research reports."""

from __future__ import annotations

from src.master_data import MasterDataService
from src.peer_analysis import (
    PeerComparisonBundle,
    PeerComparisonStatus,
)
from src.research_workflow.execution_models import (
    ResearchExecutionResult,
    ResearchPhaseResult,
    ResearchPhaseStatus,
)

from .models import (
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    InvalidationCondition,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchGap,
    ResearchScenario,
    ScenarioKind,
)
from .service import RepresentativeResearchAssembler


class ResearchReportAssemblyService:
    VERSION = "research_report_v1"

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        assembler: RepresentativeResearchAssembler | None = None,
    ) -> None:
        self.master_data = master_data
        self.assembler = (
            assembler or RepresentativeResearchAssembler()
        )

    def assemble(
        self,
        execution: ResearchExecutionResult,
        *,
        peer_comparison: PeerComparisonBundle | None = None,
    ) -> ResearchAcceptanceReport:
        asset = self.master_data.get_asset(
            execution.task.asset_id
        )
        if asset is None:
            raise KeyError(
                f"Unknown asset_id: {execution.task.asset_id}"
            )
        strategy = execution.strategy_result
        gaps = [
            ResearchGap(
                module=item.module,
                code=item.code,
                message=item.message,
                blocking=item.blocking,
            )
            for item in execution.gaps
        ]
        comparison_step = _comparison_step(
            _phase(execution, "benchmark"),
            peer_comparison,
            gaps,
        )
        steps = (
            _step("task", _phase(execution, "task")),
            _combined_step(
                "data_quality",
                (
                    _phase(execution, "bars"),
                    _phase(execution, "quality"),
                ),
            ),
            _combined_step(
                "metrics",
                (
                    _phase(execution, "factors"),
                    _phase(execution, "fundamental"),
                ),
                ignore_not_applicable=True,
            ),
            _step("charts", _phase(execution, "charts")),
            comparison_step,
            _step("strategy", _phase(execution, "strategy")),
            _step("evidence", _phase(execution, "evidence")),
        )
        if strategy is None:
            gaps.append(
                ResearchGap(
                    module="strategy",
                    code="strategy_result_missing",
                    message=(
                        "Execution produced no strategy result"
                    ),
                    blocking=True,
                )
            )
        scenarios = _scenarios(strategy)
        counterargument = _counterargument(strategy)
        return self.assembler.assemble(
            acceptance_version=self.VERSION,
            task=execution.task,
            asset_name=(
                asset.display_name or asset.standard_symbol
            ),
            asset_type=asset.asset_type,
            steps=steps,
            scenarios=scenarios,
            strongest_counterargument=counterargument,
            invalidation_conditions=(
                InvalidationCondition(
                    condition=(
                        "A hard filter or veto changes on refreshed "
                        "inputs"
                    ),
                    evidence_required=(
                        f"Rerun strategy "
                        f"{strategy.strategy_id if strategy else 'unknown'}"
                    ),
                    severity="thesis_break",
                ),
            ),
            conclusions=(
                ResearchClaim(
                    kind=ClaimKind.FACT,
                    text=(
                        "Formal research execution emitted all "
                        "required phase records."
                    ),
                    evidence_refs=(
                        f"task:{execution.task.task_id}",
                        "execution:phases",
                    ),
                ),
                ResearchClaim(
                    kind=ClaimKind.INFERENCE,
                    text=(
                        "The strategy result is a research rule "
                        "outcome, not an investment instruction."
                    ),
                    evidence_refs=(
                        (
                            f"strategy:{strategy.strategy_id}"
                            if strategy
                            else "strategy:missing"
                        ),
                    ),
                ),
            ),
            gaps=tuple(gaps),
        )


def _phase(
    execution: ResearchExecutionResult,
    phase_id: str,
) -> ResearchPhaseResult:
    return next(
        item
        for item in execution.phases
        if item.phase_id == phase_id
    )


def _status(
    phase: ResearchPhaseResult,
) -> AcceptanceStepStatus:
    mapping = {
        ResearchPhaseStatus.SUCCESS: AcceptanceStepStatus.PASS,
        ResearchPhaseStatus.GAP: AcceptanceStepStatus.GAP,
        ResearchPhaseStatus.FAILED: AcceptanceStepStatus.FAIL,
        ResearchPhaseStatus.SKIPPED: AcceptanceStepStatus.GAP,
        ResearchPhaseStatus.NOT_APPLICABLE: (
            AcceptanceStepStatus.NOT_APPLICABLE
        ),
    }
    return mapping[phase.status]


def _step(
    step_id: str,
    phase: ResearchPhaseResult,
) -> AcceptanceStepResult:
    status = _status(phase)
    sources = phase.sources
    if status is AcceptanceStepStatus.PASS and not sources:
        sources = (f"execution:{phase.phase_id}",)
    return AcceptanceStepResult(
        step_id=step_id,
        status=status,
        summary=phase.summary,
        sources=sources,
        metric_versions=phase.metric_versions,
        artifacts=phase.artifacts,
        limitations=((phase.error,) if phase.error else ()),
    )


def _combined_step(
    step_id: str,
    phases: tuple[ResearchPhaseResult, ...],
    *,
    ignore_not_applicable: bool = False,
) -> AcceptanceStepResult:
    relevant = tuple(
        phase
        for phase in phases
        if not (
            ignore_not_applicable
            and phase.status is ResearchPhaseStatus.NOT_APPLICABLE
        )
    )
    statuses = {_status(phase) for phase in relevant}
    if AcceptanceStepStatus.FAIL in statuses:
        status = AcceptanceStepStatus.FAIL
    elif AcceptanceStepStatus.GAP in statuses:
        status = AcceptanceStepStatus.GAP
    elif statuses == {AcceptanceStepStatus.NOT_APPLICABLE}:
        status = AcceptanceStepStatus.NOT_APPLICABLE
    else:
        status = AcceptanceStepStatus.PASS
    sources = tuple(
        sorted(
            {
                source
                for phase in relevant
                for source in phase.sources
            }
        )
    )
    if status is AcceptanceStepStatus.PASS and not sources:
        sources = (f"execution:{step_id}",)
    return AcceptanceStepResult(
        step_id=step_id,
        status=status,
        summary="; ".join(phase.summary for phase in relevant),
        sources=sources,
        metric_versions=tuple(
            sorted(
                {
                    version
                    for phase in relevant
                    for version in phase.metric_versions
                }
            )
        ),
        artifacts=tuple(
            artifact
            for phase in relevant
            for artifact in phase.artifacts
        ),
        limitations=tuple(
            phase.error
            for phase in relevant
            if phase.error
        ),
    )


def _comparison_step(phase, peer, gaps):
    base = _step("comparison", phase)
    if peer is None:
        return base
    unavailable = [
        item.metric_name
        for item in peer.comparisons
        if item.status is not PeerComparisonStatus.AVAILABLE
    ]
    if unavailable:
        gaps.append(
            ResearchGap(
                module="peer_analysis",
                code="peer_comparison_incomplete",
                message=f"Unavailable peer metrics: {unavailable}",
                blocking=False,
            )
        )
        status = AcceptanceStepStatus.GAP
    else:
        status = base.status
    return AcceptanceStepResult(
        step_id="comparison",
        status=status,
        summary=(
            f"{base.summary}; "
            f"{len(peer.comparisons)} peer comparisons"
        ),
        sources=tuple(
            sorted({*base.sources, "peer_analysis"})
        ),
        metric_versions=base.metric_versions,
        artifacts=base.artifacts,
        limitations=base.limitations,
    )


def _scenarios(strategy):
    if strategy is None:
        claim = ResearchClaim(
            kind=ClaimKind.UNKNOWN,
            text="Scenario unavailable without strategy result.",
        )
        return tuple(
            ResearchScenario(kind=kind, claims=(claim,))
            for kind in ScenarioKind
        )
    reference = (
        f"strategy:{strategy.strategy_id}:{strategy.status.value}"
    )
    return (
        ResearchScenario(
            ScenarioKind.OPTIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    "Matched strategy conditions persist.",
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.BASE,
            (
                ResearchClaim(
                    ClaimKind.FACT,
                    f"Current strategy status is "
                    f"{strategy.status.value}.",
                    (reference,),
                ),
            ),
        ),
        ResearchScenario(
            ScenarioKind.PESSIMISTIC,
            (
                ResearchClaim(
                    ClaimKind.ASSUMPTION,
                    "Failed conditions, vetoes, or documented "
                    "limitations become material.",
                    (reference,),
                ),
            ),
        ),
    )


def _counterargument(strategy):
    if strategy is None:
        return ResearchClaim(
            ClaimKind.UNKNOWN,
            "Strongest counterargument is unknown without strategy.",
        )
    evidence = (
        *strategy.counterevidence,
        *strategy.failed_conditions,
        *strategy.vetoed_conditions,
    )
    if evidence:
        text = f"Strategy counterevidence: {list(evidence)}"
    elif strategy.limitations:
        text = f"Key strategy limitation: {strategy.limitations[0]}"
    else:
        text = (
            "No machine counterevidence was observed; "
            "the result remains a research rule."
        )
    return ResearchClaim(
        ClaimKind.INFERENCE,
        text,
        (f"strategy:{strategy.strategy_id}",),
    )
