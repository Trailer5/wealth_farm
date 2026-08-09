"""Panel event study for event-name discovery, not trading PnL."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Mapping

from src.event_name_discovery import (
    AlternativeCatalystStatus,
    AttentionAttributionMode,
    AttentionAttributionStatus,
    BusinessRelationStatus,
    ConsensusFormationStatus,
    EventDirection,
    EventNameDiscoveryRun,
    EventNameMemberStatus,
    EventTimingQuality,
    MATCHER_VERSION,
    MarketReactionSnapshot,
)


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class EventNameEventStudySpec:
    manifest_id: str
    frozen_at: str
    training_event_ids: tuple[str, ...]
    holdout_event_ids: tuple[str, ...]
    matcher_version: str
    discovery_rule_digest: str
    cluster_cooldown_days: int = 5

    def __post_init__(self) -> None:
        if not self.manifest_id.strip():
            raise ValueError("manifest_id must not be blank")
        frozen = datetime.fromisoformat(
            self.frozen_at.replace("Z", "+00:00")
        )
        if frozen.tzinfo is None or frozen.utcoffset() is None:
            raise ValueError("frozen_at must include a timezone")
        for values, name in (
            (self.training_event_ids, "training_event_ids"),
            (self.holdout_event_ids, "holdout_event_ids"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if set(self.training_event_ids) & set(
            self.holdout_event_ids
        ):
            raise ValueError("training and holdout events must be disjoint")
        if not self.matcher_version.strip():
            raise ValueError("matcher_version must not be blank")
        if not self.discovery_rule_digest.strip():
            raise ValueError(
                "discovery_rule_digest must not be blank"
            )
        if self.cluster_cooldown_days < 0:
            raise ValueError(
                "cluster_cooldown_days must be non-negative"
            )


@dataclass(frozen=True)
class EventNameStudyCase:
    run_id: str
    event_id: str
    event_kind: str
    asset_id: str
    standard_symbol: str
    event_trade_date: str
    direction: EventDirection
    match_tier: int
    abnormal_return: Decimal
    aligned_abnormal_return: Decimal
    volume_zscore: Decimal | None
    reversal_abnormal_return: Decimal | None
    aligned_reversal_return: Decimal | None
    selected_candidate: bool
    limit_state: str
    retrospective_label: bool


@dataclass(frozen=True)
class EventNameStudySummary:
    observation_count: int
    independent_event_count: int
    candidate_count: int
    mean_aligned_abnormal_return: Decimal | None
    median_aligned_abnormal_return: Decimal | None
    direction_hit_rate: Decimal | None
    mean_volume_zscore: Decimal | None
    reversal_observation_count: int
    reversal_hit_rate: Decimal | None


@dataclass(frozen=True)
class EventNameEventStudyResult:
    spec: EventNameEventStudySpec
    cases: tuple[EventNameStudyCase, ...]
    excluded_overlaps: tuple[EventNameStudyCase, ...]
    overall: EventNameStudySummary
    training: EventNameStudySummary
    holdout: EventNameStudySummary
    by_event_kind: tuple[tuple[str, EventNameStudySummary], ...]
    limitations: tuple[str, ...]


class EventNameEventStudyEngine:
    """Summarize all unrelated language matches, including non-candidates."""

    def analyze(
        self,
        runs: tuple[EventNameDiscoveryRun, ...],
        spec: EventNameEventStudySpec,
    ) -> EventNameEventStudyResult:
        config = spec
        if not runs:
            raise ValueError("event study requires discovery runs")
        run_ids = [item.run_id for item in runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("discovery run IDs must be unique")
        _validate_manifest(runs, config)
        extracted = tuple(
            case
            for run in sorted(
                runs,
                key=lambda item: (
                    item.event.first_public_at,
                    item.run_id,
                ),
            )
            for case in _cases_from_run(run)
        )
        if not extracted:
            raise ValueError(
                "no confirmed-unrelated matched reactions are available"
            )
        return _build_result(extracted, config)

    def analyze_predictions(
        self,
        prediction_runs: tuple[EventNameDiscoveryRun, ...],
        realized_reactions: Mapping[
            tuple[str, str],
            MarketReactionSnapshot,
        ],
        spec: EventNameEventStudySpec,
    ) -> EventNameEventStudyResult:
        if not prediction_runs:
            raise ValueError("prediction study requires runs")
        _validate_manifest(prediction_runs, spec)
        cases = []
        expected_keys = {
            (run.event.event_id, candidate.asset_id)
            for run in prediction_runs
            for candidate in run.candidates
        }
        if set(realized_reactions) != expected_keys:
            raise ValueError(
                "realized reactions must exactly match frozen predictions"
            )
        for run in prediction_runs:
            for candidate in run.candidates:
                key = (run.event.event_id, candidate.asset_id)
                reaction = realized_reactions[key]
                if reaction.event_id != run.event.event_id:
                    raise ValueError("realized reaction event mismatch")
                multiplier = (
                    ONE
                    if candidate.name_match.direction
                    is EventDirection.POSITIVE
                    else -ONE
                )
                reversal = reaction.reversal_abnormal_return
                cases.append(
                    EventNameStudyCase(
                        run_id=run.run_id,
                        event_id=run.event.event_id,
                        event_kind=run.event.event_kind,
                        asset_id=candidate.asset_id,
                        standard_symbol=candidate.standard_symbol,
                        event_trade_date=reaction.event_trade_date,
                        direction=candidate.name_match.direction,
                        match_tier=candidate.name_match.tier,
                        abnormal_return=reaction.abnormal_return,
                        aligned_abnormal_return=(
                            reaction.abnormal_return * multiplier
                        ),
                        volume_zscore=reaction.volume_zscore,
                        reversal_abnormal_return=reversal,
                        aligned_reversal_return=(
                            -reversal * multiplier
                            if reversal is not None
                            else None
                        ),
                        selected_candidate=True,
                        limit_state=reaction.limit_state,
                        retrospective_label=(
                            run.spec.retrospective_label
                        ),
                    )
                )
        return _build_result(tuple(cases), spec)


def _build_result(
    extracted: tuple[EventNameStudyCase, ...],
    config: EventNameEventStudySpec,
) -> EventNameEventStudyResult:
    cases, excluded = _cluster_cases(
        extracted,
        config.cluster_cooldown_days,
    )
    holdout_ids = set(config.holdout_event_ids)
    training = tuple(
        item for item in cases if item.event_id not in holdout_ids
    )
    holdout = tuple(
        item for item in cases if item.event_id in holdout_ids
    )
    event_kinds = sorted({item.event_kind for item in cases})
    return EventNameEventStudyResult(
        spec=config,
        cases=cases,
        excluded_overlaps=excluded,
        overall=_summary(cases),
        training=_summary(training),
        holdout=_summary(holdout),
        by_event_kind=tuple(
            (
                event_kind,
                _summary(
                    tuple(
                        item
                        for item in cases
                        if item.event_kind == event_kind
                    )
                ),
            )
            for event_kind in event_kinds
        ),
        limitations=(
            "This validates discovery behavior, not executable PnL.",
            "Positive pumps may be unbuyable at the limit-up price.",
            "A-share short-sale constraints prevent symmetric trading.",
            "P-values are withheld until independent event clusters "
            "are sufficient for a predeclared inference method.",
            "Event-family holdout must be fixed before parameter review.",
        ),
    )


def discovery_rule_digest(run_or_spec) -> str:
    spec = (
        run_or_spec.spec
        if isinstance(run_or_spec, EventNameDiscoveryRun)
        else run_or_spec
    )
    payload = spec.to_dict()
    for field_name in (
        "as_of_date",
        "knowledge_cutoff_at",
        "retrospective_label",
    ):
        payload.pop(field_name, None)
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_manifest(
    runs: tuple[EventNameDiscoveryRun, ...],
    spec: EventNameEventStudySpec,
) -> None:
    if spec.matcher_version != MATCHER_VERSION:
        raise ValueError("manifest matcher_version is not current")
    declared = set(
        (*spec.training_event_ids, *spec.holdout_event_ids)
    )
    actual = {run.event.event_id for run in runs}
    if declared != actual:
        raise ValueError(
            "manifest event IDs must exactly match discovery runs"
        )
    frozen = datetime.fromisoformat(
        spec.frozen_at.replace("Z", "+00:00")
    )
    for run in runs:
        started = datetime.fromisoformat(
            run.started_at.replace("Z", "+00:00")
        )
        if frozen > started:
            raise ValueError("manifest was frozen after discovery run")
        if discovery_rule_digest(run) != spec.discovery_rule_digest:
            raise ValueError("discovery rule digest differs from manifest")
        if (
            run.event.event_id in spec.holdout_event_ids
            and run.spec.retrospective_label
        ):
            raise ValueError(
                "retrospective labels cannot enter holdout results"
            )


def _cases_from_run(
    run: EventNameDiscoveryRun,
) -> tuple[EventNameStudyCase, ...]:
    cases = []
    for outcome in run.outcomes:
        if (
            not outcome.name_matches
            or outcome.relation is None
            or outcome.relation.status
            is not BusinessRelationStatus.CONFIRMED_UNRELATED
            or outcome.catalyst is None
            or not _attribution_is_eligible(outcome)
            or outcome.consensus is None
            or outcome.consensus.status
            is ConsensusFormationStatus.INSUFFICIENT_EVIDENCE
            or outcome.reaction is None
            or outcome.reaction.timing_quality
            is EventTimingQuality.INTRADAY_AMBIGUOUS
        ):
            continue
        match = outcome.name_matches[0]
        reaction = outcome.reaction
        multiplier = (
            ONE
            if match.direction is EventDirection.POSITIVE
            else -ONE
        )
        reversal = reaction.reversal_abnormal_return
        cases.append(
            EventNameStudyCase(
                run_id=run.run_id,
                event_id=run.event.event_id,
                event_kind=run.event.event_kind,
                asset_id=outcome.asset_id,
                standard_symbol=outcome.standard_symbol,
                event_trade_date=reaction.event_trade_date,
                direction=match.direction,
                match_tier=match.tier,
                abnormal_return=reaction.abnormal_return,
                aligned_abnormal_return=(
                    reaction.abnormal_return * multiplier
                ),
                volume_zscore=reaction.volume_zscore,
                reversal_abnormal_return=reversal,
                aligned_reversal_return=(
                    -reversal * multiplier
                    if reversal is not None
                    else None
                ),
                selected_candidate=(
                    outcome.status
                    is EventNameMemberStatus.CANDIDATE
                ),
                limit_state=reaction.limit_state,
                retrospective_label=run.spec.retrospective_label,
            )
        )
    return tuple(cases)


def _attribution_is_eligible(outcome) -> bool:
    if outcome.catalyst.status is AlternativeCatalystStatus.CLEAR:
        return True
    attribution = outcome.attribution
    return (
        attribution is not None
        and attribution.mode
        is AttentionAttributionMode.MIXED_CATALYST_OVERLAY
        and attribution.status
        in {
            AttentionAttributionStatus.CONFIRMED,
            AttentionAttributionStatus.PLAUSIBLE,
        }
    )


def _cluster_cases(
    cases: tuple[EventNameStudyCase, ...],
    cooldown_days: int,
) -> tuple[
    tuple[EventNameStudyCase, ...],
    tuple[EventNameStudyCase, ...],
]:
    by_event: dict[str, list[EventNameStudyCase]] = {}
    for item in cases:
        by_event.setdefault(item.event_id, []).append(item)
    ordered_events = sorted(
        by_event,
        key=lambda event_id: (
            min(
                item.event_trade_date
                for item in by_event[event_id]
            ),
            event_id,
        ),
    )
    kept = []
    excluded = []
    last_seen_event_date: date | None = None
    for event_id in ordered_events:
        event_cases = by_event[event_id]
        current = min(
            date.fromisoformat(item.event_trade_date)
            for item in event_cases
        )
        overlaps = (
            last_seen_event_date is not None
            and (current - last_seen_event_date).days <= cooldown_days
        )
        if overlaps:
            excluded.extend(event_cases)
        else:
            kept.extend(event_cases)
        # Update on every event so chained overlaps stay in one cluster.
        last_seen_event_date = current
    return tuple(kept), tuple(excluded)


def _summary(
    cases: tuple[EventNameStudyCase, ...],
) -> EventNameStudySummary:
    if not cases:
        return EventNameStudySummary(
            observation_count=0,
            independent_event_count=0,
            candidate_count=0,
            mean_aligned_abnormal_return=None,
            median_aligned_abnormal_return=None,
            direction_hit_rate=None,
            mean_volume_zscore=None,
            reversal_observation_count=0,
            reversal_hit_rate=None,
        )
    by_event: dict[str, list[EventNameStudyCase]] = {}
    for item in cases:
        by_event.setdefault(item.event_id, []).append(item)
    aligned = [
        _mean(
            [
                item.aligned_abnormal_return
                for item in event_cases
            ]
        )
        for event_cases in by_event.values()
    ]
    volumes = [
        _mean(event_volumes)
        for event_cases in by_event.values()
        if (
            event_volumes := [
                item.volume_zscore
                for item in event_cases
                if item.volume_zscore is not None
            ]
        )
    ]
    reversals = [
        _mean(event_reversals)
        for event_cases in by_event.values()
        if (
            event_reversals := [
                item.aligned_reversal_return
                for item in event_cases
                if item.aligned_reversal_return is not None
            ]
        )
    ]
    return EventNameStudySummary(
        observation_count=len(cases),
        independent_event_count=len(by_event),
        candidate_count=sum(
            item.selected_candidate for item in cases
        ),
        mean_aligned_abnormal_return=_mean(aligned),
        median_aligned_abnormal_return=_median(aligned),
        direction_hit_rate=(
            Decimal(sum(value > ZERO for value in aligned))
            / Decimal(len(aligned))
        ),
        mean_volume_zscore=(
            _mean(volumes) if volumes else None
        ),
        reversal_observation_count=len(reversals),
        reversal_hit_rate=(
            Decimal(sum(value > ZERO for value in reversals))
            / Decimal(len(reversals))
            if reversals
            else None
        ),
    )


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal("2")
