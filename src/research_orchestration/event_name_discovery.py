"""Public orchestration for event-name discovery and exploration handoff."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

from src.event_name_discovery import (
    AssetNameSnapshot,
    AssetNameSnapshotLoader,
    AlternativeCatalystAssessment,
    AttentionAttributionAssessment,
    BusinessRelationAssessment,
    ConsensusFormationAssessment,
    EventMarketReactionCalculator,
    EventNameDiscoveryRun,
    EventNameDiscoveryMode,
    EventShockAssessment,
    EventNameDiscoveryService,
    EventNameDiscoverySpec,
    ExternalEventAnchor,
    MarketReactionSnapshot,
    ReactionBar,
)

from .exploration import (
    ResearchExplorationService,
    ResearchExplorationSession,
    ResearchHypothesis,
)


@dataclass(frozen=True)
class EventNameDiscoveryRunRequest:
    event: ExternalEventAnchor
    spec: EventNameDiscoverySpec
    relation_assessments: Mapping[
        str,
        BusinessRelationAssessment,
    ]
    catalyst_assessments: Mapping[
        str,
        AlternativeCatalystAssessment,
    ]
    consensus_assessments: Mapping[
        str,
        ConsensusFormationAssessment,
    ]
    attribution_assessments: Mapping[
        str,
        AttentionAttributionAssessment,
    ] = field(default_factory=dict)
    event_shock_assessment: EventShockAssessment | None = None
    assets: tuple[AssetNameSnapshot, ...] | None = None
    precomputed_reactions: Mapping[
        str,
        MarketReactionSnapshot,
    ] = field(default_factory=dict)
    asset_bars_by_id: Mapping[
        str,
        tuple[ReactionBar, ...],
    ] = field(default_factory=dict)
    benchmark_bars: tuple[ReactionBar, ...] = ()
    trading_calendar: tuple[str, ...] = ()
    calendar_id: str = ""
    allow_current_name_fallback: bool = False

    def __post_init__(self) -> None:
        if self.assets is not None and not self.assets:
            raise ValueError("assets must not be an empty tuple")


@dataclass(frozen=True)
class EventNameDiscoveryOrchestrationResult:
    discovery_run: EventNameDiscoveryRun
    exploration_sessions: tuple[ResearchExplorationSession, ...]
    reaction_preparation_errors: tuple[tuple[str, str], ...]


class EventNameDiscoveryOrchestrationPipeline:
    """Prepare bounded reactions, run discovery, and draft exploration."""

    def __init__(
        self,
        *,
        discovery_service: EventNameDiscoveryService,
        name_loader: AssetNameSnapshotLoader | None = None,
        reaction_calculator: (
            EventMarketReactionCalculator | None
        ) = None,
        exploration_service: ResearchExplorationService | None = None,
    ) -> None:
        self.discovery_service = discovery_service
        self.name_loader = name_loader
        self.reaction_calculator = (
            reaction_calculator or EventMarketReactionCalculator()
        )
        self.exploration_service = (
            exploration_service or ResearchExplorationService()
        )

    def run(
        self,
        request: EventNameDiscoveryRunRequest,
    ) -> EventNameDiscoveryOrchestrationResult:
        event_date = _event_local_date(request.event)
        assets = request.assets
        if assets is None:
            if self.name_loader is None:
                raise ValueError(
                    "name_loader is required when assets are not supplied"
                )
            assets = self.name_loader.load(
                as_of_date=event_date,
                allow_current_name_fallback=(
                    request.allow_current_name_fallback
                ),
            )
        if not assets:
            raise ValueError(
                "no point-in-time asset names are available"
            )
        matched = self.discovery_service.matcher.match(
            phrases=request.event.phrases,
            assets=assets,
            event_date=event_date,
        )
        if (
            request.spec.mode is EventNameDiscoveryMode.PREDICTIVE
            and (
                request.precomputed_reactions
                or request.asset_bars_by_id
                or request.benchmark_bars
            )
        ):
            raise ValueError(
                "predictive orchestration cannot receive outcome data"
            )
        reactions = dict(request.precomputed_reactions)
        errors = []
        for asset_id in sorted(matched):
            if asset_id in reactions:
                continue
            bars = request.asset_bars_by_id.get(asset_id)
            if bars is None or not request.benchmark_bars:
                continue
            try:
                reactions[asset_id] = (
                    self.reaction_calculator.calculate(
                        asset_id=asset_id,
                        event=request.event,
                        asset_bars=bars,
                        benchmark_bars=request.benchmark_bars,
                        trading_calendar=request.trading_calendar,
                        calendar_id=request.calendar_id,
                        as_of_date=request.spec.as_of_date,
                    )
                )
            except ValueError as exc:
                errors.append((asset_id, str(exc)))
        run = self.discovery_service.run(
            event=request.event,
            spec=request.spec,
            assets=assets,
            relation_assessments=request.relation_assessments,
            catalyst_assessments=request.catalyst_assessments,
            consensus_assessments=request.consensus_assessments,
            reactions=reactions,
            attribution_assessments=request.attribution_assessments,
            event_shock_assessment=request.event_shock_assessment,
            persist=False,
        )
        sessions = tuple(
            self._start_exploration(run, candidate.asset_id)
            for candidate in run.candidates
        )
        repository = self.discovery_service.repository
        if repository is not None:
            save_bundle = getattr(repository, "save_bundle", None)
            if save_bundle is None:
                raise TypeError(
                    "event-name repository must support atomic handoff"
                )
            run = save_bundle(run, sessions)
        return EventNameDiscoveryOrchestrationResult(
            discovery_run=run,
            exploration_sessions=sessions,
            reaction_preparation_errors=tuple(errors),
        )

    def _start_exploration(
        self,
        run: EventNameDiscoveryRun,
        asset_id: str,
    ) -> ResearchExplorationSession:
        candidate = next(
            item
            for item in run.candidates
            if item.asset_id == asset_id
        )
        match = candidate.name_match
        relation_refs = tuple(
            item.source_ref for item in candidate.relation.evidence
        )
        catalyst_refs = tuple(
            item.source_ref for item in candidate.catalyst.evidence
        )
        consensus_refs = tuple(
            item.source_ref for item in candidate.consensus.evidence
        )
        attribution_refs = (
            candidate.attribution.comparison_refs
            if candidate.attribution is not None
            else catalyst_refs
        )
        attribution_statement = (
            "名称注意力是在直接催化基础上的增量叙事"
            if candidate.attribution is not None
            else "名称注意力是当前可识别的主要事件解释"
        )
        reaction_refs = (
            candidate.reaction.source_refs
            if candidate.reaction is not None
            else run.event.evidence_refs
        )
        pressure_statement = (
            "预测事件后将出现与短语方向一致的临时价格压力"
            if candidate.reaction is None
            else "异常价格压力可能在事件注意力下降后反转"
        )
        return self.exploration_service.start(
            session_id=(
                f"exploration:{run.run_id.split(':', 1)[1]}:"
                f"{candidate.standard_symbol}"
            ),
            task=candidate.research_task_draft,
            baseline_path=None,
            hypotheses=(
                ResearchHypothesis(
                    hypothesis_id="name_attention_spillover",
                    statement=(
                        "该标的异动由事件与证券名称的显著性联想推动"
                    ),
                    supporting_evidence_refs=(
                        match.explanation,
                        *run.event.evidence_refs,
                    ),
                ),
                ResearchHypothesis(
                    hypothesis_id="economic_unrelatedness",
                    statement=(
                        "该标的与事件不存在可验证的真实经济关系"
                    ),
                    supporting_evidence_refs=relation_refs,
                ),
                ResearchHypothesis(
                    hypothesis_id="consensus_coordination",
                    statement=(
                        "重大事件、简单叙事和市场约束形成了"
                        "无需显式串谋的共识协调点"
                    ),
                    supporting_evidence_refs=consensus_refs,
                ),
                ResearchHypothesis(
                    hypothesis_id="alternative_catalysts_cleared",
                    statement=(
                        "事件窗口内没有更直接的公司或行业催化剂"
                    ),
                    supporting_evidence_refs=catalyst_refs,
                ),
                ResearchHypothesis(
                    hypothesis_id="name_effect_attribution",
                    statement=attribution_statement,
                    supporting_evidence_refs=attribution_refs,
                ),
                ResearchHypothesis(
                    hypothesis_id="temporary_price_pressure",
                    statement=pressure_statement,
                    supporting_evidence_refs=tuple(reaction_refs),
                ),
            ),
        )


def _event_local_date(event: ExternalEventAnchor) -> str:
    value = datetime.fromisoformat(
        event.first_public_at.replace("Z", "+00:00")
    )
    return value.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
