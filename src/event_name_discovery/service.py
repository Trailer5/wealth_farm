"""Execute event-name discovery with explicit evidence gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo

from src.research_contracts import (
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_workflow import ResearchTaskFactory

from .evidence import assess_evidence
from .matcher import MATCHER_VERSION, EventNameMatcher
from .models import (
    AlternativeCatalystAssessment,
    AlternativeCatalystStatus,
    AttentionAttributionAssessment,
    AttentionAttributionMode,
    AttentionAttributionStatus,
    AssetNameSnapshot,
    BusinessRelationAssessment,
    BusinessRelationStatus,
    ConsensusFormationAssessment,
    ConsensusFormationStatus,
    EventDirection,
    EventAttentionScope,
    EventNameCandidate,
    EventNameDiscoveryRun,
    EventNameDiscoveryMode,
    EventShockAssessment,
    EventShockStatus,
    EventNameDiscoverySpec,
    EventNameMemberOutcome,
    EventNameMemberStatus,
    EventTimingQuality,
    ExternalEventAnchor,
    MarketConfirmationMode,
    MarketReactionSnapshot,
)


class EventNameRepositoryProtocol(Protocol):
    def save(
        self,
        run: EventNameDiscoveryRun,
    ) -> EventNameDiscoveryRun: ...


class EventNameDiscoveryService:
    """Discover research leads; never emit an automatic trade instruction."""

    def __init__(
        self,
        *,
        task_factory: ResearchTaskFactory,
        matcher: EventNameMatcher | None = None,
        repository: EventNameRepositoryProtocol | None = None,
    ) -> None:
        self.task_factory = task_factory
        self.matcher = matcher or EventNameMatcher()
        self.repository = repository

    def run(
        self,
        *,
        event: ExternalEventAnchor,
        spec: EventNameDiscoverySpec,
        assets: tuple[AssetNameSnapshot, ...],
        relation_assessments: Mapping[
            str,
            BusinessRelationAssessment,
        ],
        catalyst_assessments: Mapping[
            str,
            AlternativeCatalystAssessment,
        ],
        consensus_assessments: Mapping[
            str,
            ConsensusFormationAssessment,
        ],
        reactions: Mapping[str, MarketReactionSnapshot],
        attribution_assessments: Mapping[
            str,
            AttentionAttributionAssessment,
        ]
        | None = None,
        event_shock_assessment: EventShockAssessment | None = None,
        persist: bool = True,
    ) -> EventNameDiscoveryRun:
        evidence = assess_evidence()
        if not evidence.implementation_ready:
            raise RuntimeError(
                "event-name strategy evidence gate is blocked: "
                f"{evidence.blocking_reasons}"
            )
        if not assets:
            raise ValueError("assets must not be empty")
        if (
            spec.mode is EventNameDiscoveryMode.PREDICTIVE
            and reactions
        ):
            raise ValueError(
                "predictive discovery cannot receive realized reactions"
            )
        attribution_by_asset = dict(
            attribution_assessments or {}
        )
        event_date = _event_local_date(event)
        if event_date > spec.as_of_date:
            raise ValueError("event is later than discovery as_of_date")
        if (
            spec.require_global_scope
            and event.attention_scope
            is EventAttentionScope.DOMESTIC_EVENT
        ):
            raise ValueError(
                "discovery spec requires global or cross-border attention"
            )
        knowledge_cutoff = datetime.fromisoformat(
            spec.knowledge_cutoff_at.replace("Z", "+00:00")
        )
        first_public = datetime.fromisoformat(
            event.first_public_at.replace("Z", "+00:00")
        )
        if first_public > knowledge_cutoff:
            raise ValueError(
                "event was not public by knowledge_cutoff_at"
            )
        if spec.require_event_shock:
            _validate_event_shock(
                event_shock_assessment,
                event,
                spec.knowledge_cutoff_at,
            )
        asset_ids = [item.asset_id for item in assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("assets must have unique asset IDs")
        matches = self.matcher.match(
            phrases=event.phrases,
            assets=assets,
            event_date=event_date,
        )
        started_at = datetime.now(timezone.utc).isoformat()
        provisional: list[EventNameMemberOutcome] = []
        eligible: list[
            tuple[
                AssetNameSnapshot,
                BusinessRelationAssessment,
                AlternativeCatalystAssessment,
                ConsensusFormationAssessment,
                AttentionAttributionAssessment | None,
                MarketReactionSnapshot,
                tuple,
                tuple[str, ...],
            ]
        ] = []
        for asset in sorted(
            assets,
            key=lambda item: item.standard_symbol,
        ):
            asset_matches = matches.get(asset.asset_id, ())
            if not asset_matches:
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.NO_LANGUAGE_MATCH,
                        "no_explainable_name_match",
                    )
                )
                continue
            best_key = (
                asset_matches[0].tier,
                asset_matches[0].phrase_priority,
                asset_matches[0].phonetic_distance,
            )
            best_directions = {
                item.direction
                for item in asset_matches
                if (
                    item.tier,
                    item.phrase_priority,
                    item.phonetic_distance,
                )
                == best_key
            }
            if len(best_directions) > 1:
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.MATCH_DIRECTION_AMBIGUOUS,
                        "equally_ranked_phrases_have_conflicting_directions",
                        matches=asset_matches,
                    )
                )
                continue
            relation = relation_assessments.get(asset.asset_id)
            if relation is None:
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .RELATION_EVIDENCE_INSUFFICIENT
                        ),
                        "business_relation_assessment_missing",
                        matches=asset_matches,
                    )
                )
                continue
            _validate_relation_point_in_time(
                relation,
                asset.asset_id,
                spec.knowledge_cutoff_at,
            )
            if relation.status is BusinessRelationStatus.RELATED:
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.RELATED_EXCLUDED,
                        "real_economic_relation_present",
                        matches=asset_matches,
                        relation=relation,
                    )
                )
                continue
            if (
                relation.status
                is BusinessRelationStatus.INSUFFICIENT_EVIDENCE
            ):
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .RELATION_EVIDENCE_INSUFFICIENT
                        ),
                        "business_unrelatedness_not_confirmed",
                        matches=asset_matches,
                        relation=relation,
                    )
                )
                continue
            consensus = consensus_assessments.get(asset.asset_id)
            if consensus is None:
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .CONSENSUS_EVIDENCE_INSUFFICIENT
                        ),
                        "consensus_assessment_missing",
                        matches=asset_matches,
                        relation=relation,
                    )
                )
                continue
            _validate_consensus_point_in_time(
                consensus,
                asset.asset_id,
                event,
                spec.knowledge_cutoff_at,
            )
            if (
                consensus.status
                is ConsensusFormationStatus.INSUFFICIENT_EVIDENCE
            ):
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .CONSENSUS_EVIDENCE_INSUFFICIENT
                        ),
                        "consensus_formation_conditions_not_met",
                        matches=asset_matches,
                        relation=relation,
                        consensus=consensus,
                    )
                )
                continue
            reaction = reactions.get(asset.asset_id)
            if reaction is not None:
                _validate_reaction_point_in_time(
                    reaction,
                    asset.asset_id,
                    event,
                    spec.as_of_date,
                )
            catalyst = catalyst_assessments.get(asset.asset_id)
            if catalyst is None:
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .CATALYST_EVIDENCE_INSUFFICIENT
                        ),
                        "alternative_catalyst_assessment_missing",
                        matches=asset_matches,
                        relation=relation,
                        consensus=consensus,
                        reaction=reaction,
                    )
                )
                continue
            _validate_catalyst_point_in_time(
                catalyst,
                asset.asset_id,
                spec.knowledge_cutoff_at,
            )
            attribution = attribution_by_asset.get(asset.asset_id)
            if attribution is not None:
                _validate_attribution_point_in_time(
                    attribution,
                    asset.asset_id,
                    event,
                    spec.knowledge_cutoff_at,
                )
            mixed_overlay_supported = (
                attribution is not None
                and attribution.mode
                is AttentionAttributionMode.MIXED_CATALYST_OVERLAY
                and attribution.status
                in {
                    AttentionAttributionStatus.CONFIRMED,
                    AttentionAttributionStatus.PLAUSIBLE,
                }
            )
            if (
                catalyst.status
                is AlternativeCatalystStatus.CONTAMINATED
                and not mixed_overlay_supported
            ):
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .ALTERNATIVE_CATALYST_PRESENT
                        ),
                        "concurrent_alternative_catalyst_present",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                        extra_reasons=(
                            (
                                "market_move_opposes_phrase_direction",
                            )
                            if reaction is not None
                            and spec.require_direction_confirmation
                            and not _direction_matches(
                                asset_matches[0].direction,
                                reaction.abnormal_return,
                            )
                            else ()
                        ),
                    )
                )
                continue
            if (
                catalyst.status
                is AlternativeCatalystStatus.INSUFFICIENT_EVIDENCE
            ):
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .CATALYST_EVIDENCE_INSUFFICIENT
                        ),
                        "alternative_catalyst_not_cleared",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                    )
                )
                continue
            if spec.mode is EventNameDiscoveryMode.PREDICTIVE:
                best_match = asset_matches[0]
                tracks = (
                    (
                        "predicted_positive_attention"
                        if best_match.direction
                        is EventDirection.POSITIVE
                        else "predicted_negative_mispricing"
                    ),
                )
                if mixed_overlay_supported:
                    tracks = (
                        *tracks,
                        "mixed_catalyst_attention_overlay",
                    )
                eligible.append(
                    (
                        asset,
                        relation,
                        catalyst,
                        consensus,
                        attribution,
                        None,
                        asset_matches,
                        tracks,
                    )
                )
                continue
            if reaction is None:
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.MARKET_REACTION_MISSING,
                        "market_reaction_missing",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                    )
                )
                continue
            if (
                reaction.timing_quality
                is EventTimingQuality.INTRADAY_AMBIGUOUS
            ):
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.TIMING_AMBIGUOUS,
                        "daily_bar_contains_pre_event_trading",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                    )
                )
                continue
            if not _market_confirmation_passes(reaction, spec):
                provisional.append(
                    _outcome(
                        asset,
                        (
                            EventNameMemberStatus
                            .MARKET_REACTION_NOT_CONFIRMED
                        ),
                        "price_or_volume_confirmation_failed",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                    )
                )
                continue
            best_match = asset_matches[0]
            if (
                spec.require_direction_confirmation
                and not _direction_matches(
                    best_match.direction,
                    reaction.abnormal_return,
                )
            ):
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.DIRECTION_MISMATCH,
                        "market_move_opposes_phrase_direction",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                    )
                )
                continue
            tracks = _tracks(best_match.direction, reaction, spec)
            if mixed_overlay_supported:
                tracks = (*tracks, "mixed_catalyst_attention_overlay")
            eligible.append(
                (
                    asset,
                    relation,
                    catalyst,
                    consensus,
                    attribution,
                    reaction,
                    asset_matches,
                    tracks,
                )
            )

        eligible.sort(
            key=lambda item: _eligible_sort_key(item, spec.mode)
        )
        rank_by_id = {
            item[0].asset_id: rank
            for rank, item in enumerate(eligible, start=1)
        }
        selected_ids = set(
            list(rank_by_id)[: spec.max_candidates]
        )
        candidates = []
        for (
            asset,
            relation,
            catalyst,
            consensus,
            attribution,
            reaction,
            asset_matches,
            tracks,
        ) in eligible:
            if asset.asset_id not in selected_ids:
                provisional.append(
                    _outcome(
                        asset,
                        EventNameMemberStatus.CANDIDATE_LIMIT,
                        "candidate_limit_reached",
                        matches=asset_matches,
                        relation=relation,
                        catalyst=catalyst,
                        consensus=consensus,
                        attribution=attribution,
                        reaction=reaction,
                        tracks=tracks,
                        selection_rank=rank_by_id[asset.asset_id],
                    )
                )
                continue
            best_match = asset_matches[0]
            primary_track = tracks[0]
            task = self.task_factory.create(
                asset_id=asset.asset_id,
                profile_id=asset.profile_id,
                question_type=(
                    ResearchQuestionType.SPECULATION
                    if best_match.direction is EventDirection.NEGATIVE
                    else ResearchQuestionType.TIMING
                ),
                horizon=(
                    ResearchHorizon.MEDIUM_SHORT
                    if best_match.direction is EventDirection.NEGATIVE
                    else ResearchHorizon.SHORT
                ),
                as_of_date=spec.as_of_date,
                requested_outputs=(
                    "data_summary",
                    "metrics",
                    "charts",
                    "comparison",
                    "research_report",
                ),
            )
            provisional.append(
                _outcome(
                    asset,
                    EventNameMemberStatus.CANDIDATE,
                    "selected_event_name_research_lead",
                    matches=asset_matches,
                    relation=relation,
                    catalyst=catalyst,
                    consensus=consensus,
                    attribution=attribution,
                    reaction=reaction,
                    tracks=tracks,
                    selection_rank=rank_by_id[asset.asset_id],
                )
            )
            candidates.append(
                EventNameCandidate(
                    asset_id=asset.asset_id,
                    standard_symbol=asset.standard_symbol,
                    stock_name=asset.name,
                    selected_track=primary_track,
                    rank=rank_by_id[asset.asset_id],
                    name_match=best_match,
                    relation=relation,
                    catalyst=catalyst,
                    consensus=consensus,
                    attribution=attribution,
                    reaction=reaction,
                    research_task_draft=replace(
                        task,
                        task_id=_task_id(
                            event.event_id,
                            asset.asset_id,
                        ),
                    ),
                )
            )
        outcomes_by_id = {
            item.asset_id: item for item in provisional
        }
        outcomes = tuple(
            outcomes_by_id[item.asset_id]
            for item in sorted(
                assets,
                key=lambda asset: asset.standard_symbol,
            )
        )
        run = EventNameDiscoveryRun(
            run_id=_run_id(
                event,
                spec,
                assets,
                relation_assessments,
                catalyst_assessments,
                consensus_assessments,
                attribution_by_asset,
                event_shock_assessment,
                reactions,
            ),
            event=event,
            spec=spec,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            outcomes=outcomes,
            candidates=tuple(candidates),
        )
        return (
            self.repository.save(run)
            if persist and self.repository
            else run
        )


def _eligible_sort_key(item, mode: EventNameDiscoveryMode):
    best_match = item[6][0]
    consensus = item[3]
    reaction = item[5]
    if mode is EventNameDiscoveryMode.PREDICTIVE:
        consensus_rank = (
            0
            if consensus.status is ConsensusFormationStatus.CONFIRMED
            else 1
        )
        return (
            best_match.tier,
            best_match.phrase_priority,
            consensus_rank,
            item[0].standard_symbol,
        )
    assert reaction is not None
    return (
        best_match.tier,
        best_match.phrase_priority,
        -abs(reaction.abnormal_return),
        -(
            reaction.volume_zscore
            if reaction.volume_zscore is not None
            else Decimal("-Infinity")
        ),
        item[0].standard_symbol,
    )


def _outcome(
    asset: AssetNameSnapshot,
    status: EventNameMemberStatus,
    reason: str,
    *,
    matches=(),
    relation=None,
    catalyst=None,
    consensus=None,
    attribution=None,
    reaction=None,
    tracks=(),
    extra_reasons=(),
    selection_rank=None,
) -> EventNameMemberOutcome:
    return EventNameMemberOutcome(
        asset_id=asset.asset_id,
        standard_symbol=asset.standard_symbol,
        stock_name=asset.name,
        status=status,
        reasons=(reason, *tuple(extra_reasons)),
        name_matches=tuple(matches),
        relation=relation,
        catalyst=catalyst,
        consensus=consensus,
        attribution=attribution,
        reaction=reaction,
        matched_tracks=tuple(tracks),
        selection_rank=selection_rank,
    )


def _direction_matches(
    direction: EventDirection,
    abnormal_return: Decimal,
) -> bool:
    return (
        abnormal_return > 0
        if direction is EventDirection.POSITIVE
        else abnormal_return < 0
    )


def _market_confirmation_passes(
    reaction: MarketReactionSnapshot,
    spec: EventNameDiscoverySpec,
) -> bool:
    price_confirmed = (
        abs(reaction.abnormal_return)
        >= spec.min_absolute_abnormal_return
    )
    volume_confirmed = (
        reaction.volume_zscore is not None
        and reaction.volume_zscore >= spec.min_volume_zscore
    )
    if (
        spec.confirmation_mode
        is MarketConfirmationMode.PRICE_AND_VOLUME
    ):
        return price_confirmed and volume_confirmed
    return price_confirmed or volume_confirmed


def _tracks(
    direction: EventDirection,
    reaction: MarketReactionSnapshot,
    spec: EventNameDiscoverySpec,
) -> tuple[str, ...]:
    tracks = [
        (
            "positive_attention_echo"
            if direction is EventDirection.POSITIVE
            else "negative_mistaken_selloff"
        )
    ]
    reversal = reaction.reversal_abnormal_return
    if reversal is not None and (
        (
            direction is EventDirection.POSITIVE
            and reversal <= -spec.min_reversal_return
        )
        or (
            direction is EventDirection.NEGATIVE
            and reversal >= spec.min_reversal_return
        )
    ):
        tracks.append("reversal_watch")
    return tuple(tracks)


def _validate_relation_point_in_time(
    relation: BusinessRelationAssessment,
    asset_id: str,
    knowledge_cutoff_at: str,
) -> None:
    if relation.asset_id != asset_id:
        raise ValueError("relation assessment asset mismatch")
    assessed = datetime.fromisoformat(
        relation.assessed_at.replace("Z", "+00:00")
    )
    cutoff = datetime.fromisoformat(
        knowledge_cutoff_at.replace("Z", "+00:00")
    )
    if assessed > cutoff:
        raise ValueError("future relation assessment rejected")
    if any(
        datetime.fromisoformat(
            item.available_at.replace("Z", "+00:00")
        )
        > cutoff
        for item in relation.evidence
    ):
        raise ValueError("future relation evidence rejected")


def _validate_event_shock(
    assessment: EventShockAssessment | None,
    event: ExternalEventAnchor,
    knowledge_cutoff_at: str,
) -> None:
    if assessment is None:
        raise ValueError("event shock assessment is required")
    if assessment.event_id != event.event_id:
        raise ValueError("event shock assessment event mismatch")
    cutoff = datetime.fromisoformat(
        knowledge_cutoff_at.replace("Z", "+00:00")
    )
    assessed = datetime.fromisoformat(
        assessment.assessed_at.replace("Z", "+00:00")
    )
    if assessed > cutoff or any(
        datetime.fromisoformat(
            item.available_at.replace("Z", "+00:00")
        )
        > cutoff
        for item in assessment.evidence
    ):
        raise ValueError("future event shock evidence rejected")
    if assessment.status is not EventShockStatus.QUALIFIED:
        raise ValueError(
            "event does not qualify as a discrete attention shock"
        )


def _validate_catalyst_point_in_time(
    catalyst: AlternativeCatalystAssessment,
    asset_id: str,
    knowledge_cutoff_at: str,
) -> None:
    if catalyst.asset_id != asset_id:
        raise ValueError("catalyst assessment asset mismatch")
    assessed = datetime.fromisoformat(
        catalyst.assessed_at.replace("Z", "+00:00")
    )
    cutoff = datetime.fromisoformat(
        knowledge_cutoff_at.replace("Z", "+00:00")
    )
    if assessed > cutoff:
        raise ValueError("future catalyst assessment rejected")
    if any(
        datetime.fromisoformat(
            item.available_at.replace("Z", "+00:00")
        )
        > cutoff
        for item in catalyst.evidence
    ):
        raise ValueError("future catalyst evidence rejected")
    if datetime.fromisoformat(
        catalyst.window_end_at.replace("Z", "+00:00")
    ) > cutoff:
        raise ValueError("catalyst search window exceeds cutoff")


def _validate_consensus_point_in_time(
    consensus: ConsensusFormationAssessment,
    asset_id: str,
    event: ExternalEventAnchor,
    knowledge_cutoff_at: str,
) -> None:
    if consensus.asset_id != asset_id:
        raise ValueError("consensus assessment asset mismatch")
    if consensus.event_id != event.event_id:
        raise ValueError("consensus assessment event mismatch")
    cutoff = datetime.fromisoformat(
        knowledge_cutoff_at.replace("Z", "+00:00")
    )
    assessed = datetime.fromisoformat(
        consensus.assessed_at.replace("Z", "+00:00")
    )
    if assessed > cutoff:
        raise ValueError("future consensus assessment rejected")
    if any(
        datetime.fromisoformat(
            item.available_at.replace("Z", "+00:00")
        )
        > cutoff
        for item in consensus.evidence
    ):
        raise ValueError("future consensus evidence rejected")


def _validate_attribution_point_in_time(
    attribution: AttentionAttributionAssessment,
    asset_id: str,
    event: ExternalEventAnchor,
    knowledge_cutoff_at: str,
) -> None:
    if attribution.asset_id != asset_id:
        raise ValueError("attribution assessment asset mismatch")
    if attribution.event_id != event.event_id:
        raise ValueError("attribution assessment event mismatch")
    available = datetime.fromisoformat(
        attribution.available_at.replace("Z", "+00:00")
    )
    cutoff = datetime.fromisoformat(
        knowledge_cutoff_at.replace("Z", "+00:00")
    )
    if available > cutoff:
        raise ValueError("future attribution evidence rejected")


def _validate_reaction_point_in_time(
    reaction: MarketReactionSnapshot,
    asset_id: str,
    event: ExternalEventAnchor,
    as_of_date: str,
) -> None:
    if reaction.asset_id != asset_id:
        raise ValueError("market reaction asset mismatch")
    if reaction.observed_through > as_of_date:
        raise ValueError("future market reaction rejected")
    if reaction.event_id != event.event_id:
        raise ValueError("market reaction event mismatch")
    if reaction.event_first_public_at != event.first_public_at:
        raise ValueError(
            "market reaction public timestamp mismatch"
        )


def _event_local_date(event: ExternalEventAnchor) -> str:
    published = datetime.fromisoformat(
        event.first_public_at.replace("Z", "+00:00")
    )
    return published.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _run_id(
    event: ExternalEventAnchor,
    spec: EventNameDiscoverySpec,
    assets: tuple[AssetNameSnapshot, ...],
    relations: Mapping[str, BusinessRelationAssessment],
    catalysts: Mapping[str, AlternativeCatalystAssessment],
    consensus: Mapping[str, ConsensusFormationAssessment],
    attributions: Mapping[str, AttentionAttributionAssessment],
    event_shock: EventShockAssessment | None,
    reactions: Mapping[str, MarketReactionSnapshot],
) -> str:
    payload = {
        "matcher_version": MATCHER_VERSION,
        "event": event.to_dict(),
        "spec": spec.to_dict(),
        "assets": [
            {
                "asset_id": item.asset_id,
                "symbol": item.standard_symbol,
                "name": item.name,
                "effective_from": item.effective_from,
                "effective_to": item.effective_to,
                "aliases": item.aliases,
                "profile_id": item.profile_id,
                "source": item.source,
            }
            for item in sorted(assets, key=lambda value: value.asset_id)
        ],
        "relations": {
            asset_id: value.to_dict()
            for asset_id, value in sorted(relations.items())
        },
        "catalysts": {
            asset_id: value.to_dict()
            for asset_id, value in sorted(catalysts.items())
        },
        "consensus": {
            asset_id: value.to_dict()
            for asset_id, value in sorted(consensus.items())
        },
        "attributions": {
            asset_id: value.to_dict()
            for asset_id, value in sorted(attributions.items())
        },
        "event_shock": (
            event_shock.to_dict()
            if event_shock is not None
            else None
        ),
        "reactions": {
            asset_id: value.to_dict()
            for asset_id, value in sorted(reactions.items())
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"event-name-discovery:{digest[:24]}"


def _task_id(event_id: str, asset_id: str) -> str:
    digest = hashlib.sha256(
        f"{event_id}:{asset_id}".encode("utf-8")
    ).hexdigest()
    return f"research:event-name:{digest[:20]}"
