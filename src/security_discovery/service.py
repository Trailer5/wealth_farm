"""Execute the layered discovery funnel over a current universe."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from src.research_universe import (
    MembershipStatus,
    ResearchUniverseService,
)
from src.research_workflow import ResearchTaskFactory
from src.strategy import ConditionOperator

from .metrics_loader import StoredDiscoveryMetricLoader
from .models import (
    DiscoveryCandidate,
    DiscoveryMemberInput,
    DiscoveryMemberOutcome,
    DiscoveryMemberStatus,
    DiscoveryReason,
    DiscoveryRule,
    DiscoveryRun,
    DiscoverySpec,
)
from .sqlite_repository import SQLiteSecurityDiscoveryRepository


class SecurityDiscoveryService:
    def __init__(
        self,
        *,
        universe_service: ResearchUniverseService,
        task_factory: ResearchTaskFactory,
        repository: SQLiteSecurityDiscoveryRepository,
        metric_loader: StoredDiscoveryMetricLoader | None = None,
    ) -> None:
        self.universe_service = universe_service
        self.task_factory = task_factory
        self.repository = repository
        self.metric_loader = metric_loader
        self.repository.initialize()

    def run(
        self,
        spec: DiscoverySpec,
        *,
        member_inputs: Mapping[
            str,
            DiscoveryMemberInput,
        ]
        | None = None,
    ) -> DiscoveryRun:
        started_at = datetime.now(timezone.utc).isoformat()
        snapshot = self.universe_service.get(
            spec.universe_id,
            version=spec.universe_version,
        )
        if snapshot is None:
            raise KeyError(
                f"Unknown universe: {spec.universe_id}"
            )
        if snapshot.universe.historical_membership:
            raise ValueError(
                "Discovery v1 only supports a current universe snapshot"
            )
        if spec.as_of_date < snapshot.universe.effective_date:
            raise ValueError(
                "Discovery as_of_date precedes the current universe "
                "effective_date"
            )
        benchmark_asset_id = (
            spec.benchmark_asset_id
            or snapshot.universe.index_asset_id
        )

        inputs = self._load_inputs(
            spec,
            snapshot.memberships,
            member_inputs,
        )
        run_id = _run_id(
            spec,
            snapshot.universe.version,
            snapshot.memberships,
            inputs,
        )
        terminal: dict[str, DiscoveryMemberOutcome] = {}
        eligible: list[_EligibleMember] = []

        for member in snapshot.memberships:
            symbol = member.source_symbol
            if member.status is MembershipStatus.UNRESOLVED:
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.UNRESOLVED,
                    reason=DiscoveryReason(
                        code="unresolved_identity",
                        layer=1,
                        detail=member.reason,
                    ),
                )
                continue
            if member.status is MembershipStatus.INACTIVE:
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.INACTIVE,
                    reason=DiscoveryReason(
                        code="inactive_asset",
                        layer=1,
                        detail=member.reason,
                    ),
                )
                continue
            if member.asset_id is None:
                raise ValueError(
                    "Resolved universe membership has no asset_id"
                )
            discovery_input = inputs.get(member.asset_id)
            if discovery_input is None:
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.INSUFFICIENT_DATA,
                    reason=DiscoveryReason(
                        code="member_input_missing",
                        layer=1,
                        detail=(
                            "No persisted discovery metrics were "
                            "available for this member"
                        ),
                    ),
                    missing_metrics=spec.required_metrics,
                )
                continue
            if discovery_input.profile_id != spec.profile_id:
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.INSUFFICIENT_DATA,
                    reason=DiscoveryReason(
                        code="unsupported_profile",
                        layer=1,
                        detail=(
                            f"Expected profile {spec.profile_id}, got "
                            f"{discovery_input.profile_id}"
                        ),
                    ),
                )
                continue
            missing = _missing_metrics(spec, discovery_input)
            if missing or discovery_input.data_errors:
                reasons = [
                    DiscoveryReason(
                        code="metric_unavailable",
                        layer=1,
                        metric_name=name,
                        detail=_missing_detail(
                            name,
                            discovery_input,
                            spec,
                        ),
                    )
                    for name in missing
                ]
                reasons.extend(
                    DiscoveryReason(
                        code="data_error",
                        layer=1,
                        detail=error,
                    )
                    for error in discovery_input.data_errors
                )
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.INSUFFICIENT_DATA,
                    reasons=tuple(reasons),
                    missing_metrics=missing,
                )
                continue

            hard_matched, hard_failed = _evaluate_rules(
                spec.hard_rules,
                discovery_input,
            )
            if hard_failed:
                terminal[symbol] = _terminal_outcome(
                    member,
                    DiscoveryMemberStatus.ELIMINATED,
                    reasons=tuple(
                        _failed_rule_reason(
                            rule,
                            discovery_input,
                            layer=2,
                        )
                        for rule in spec.hard_rules
                        if rule.rule_id in hard_failed
                    ),
                    matched_rules=hard_matched,
                    failed_rules=hard_failed,
                    last_layer=2,
                )
                continue

            matched_tracks: list[str] = []
            track_rule_ids: list[str] = []
            for track in spec.candidate_tracks:
                matched, failed = _evaluate_rules(
                    track.rules,
                    discovery_input,
                )
                if not failed:
                    matched_tracks.append(track.track_id)
                    track_rule_ids.extend(matched)
            eligible.append(
                _EligibleMember(
                    source_symbol=member.source_symbol,
                    source_name=member.source_name,
                    asset_id=member.asset_id,
                    discovery_input=discovery_input,
                    matched_rules=tuple(
                        (*hard_matched, *track_rule_ids)
                    ),
                    matched_tracks=tuple(matched_tracks),
                )
            )

        selected = _select_candidates(spec, eligible)
        candidates: list[DiscoveryCandidate] = []
        for item in eligible:
            selected_track = selected.get(item.asset_id)
            if selected_track is None:
                code = (
                    "passed_no_candidate_track"
                    if not item.matched_tracks
                    else "passed_candidate_limit"
                )
                detail = (
                    "Passed hard filters but matched no candidate track"
                    if not item.matched_tracks
                    else (
                        "Passed and matched candidate tracks, but "
                        "the track-diversity limit was reached"
                    )
                )
                terminal[item.source_symbol] = (
                    DiscoveryMemberOutcome(
                        source_symbol=item.source_symbol,
                        source_name=item.source_name,
                        asset_id=item.asset_id,
                        status=DiscoveryMemberStatus.PASSED,
                        last_layer=3,
                        reasons=(
                            DiscoveryReason(
                                code=code,
                                layer=3,
                                detail=detail,
                            ),
                        ),
                        matched_rules=item.matched_rules,
                        matched_tracks=item.matched_tracks,
                    )
                )
                continue
            task = self.task_factory.create(
                asset_id=item.asset_id,
                profile_id=spec.profile_id,
                question_type=spec.question_type,
                horizon=spec.horizon,
                as_of_date=spec.as_of_date,
                benchmark_asset_id=benchmark_asset_id,
            )
            task = replace(
                task,
                task_id=_draft_task_id(run_id, item.asset_id),
            )
            candidates.append(
                DiscoveryCandidate(
                    asset_id=item.asset_id,
                    source_symbol=item.source_symbol,
                    source_name=item.source_name,
                    selected_track=selected_track,
                    matched_tracks=item.matched_tracks,
                    research_task_draft=task,
                )
            )
            terminal[item.source_symbol] = (
                DiscoveryMemberOutcome(
                    source_symbol=item.source_symbol,
                    source_name=item.source_name,
                    asset_id=item.asset_id,
                    status=DiscoveryMemberStatus.CANDIDATE,
                    last_layer=3,
                    reasons=(
                        DiscoveryReason(
                            code="selected_for_candidate_track",
                            layer=3,
                            detail=(
                                "Selected through the "
                                f"{selected_track} track"
                            ),
                        ),
                    ),
                    matched_rules=item.matched_rules,
                    matched_tracks=item.matched_tracks,
                    selected_track=selected_track,
                )
            )

        outcomes = tuple(
            terminal[member.source_symbol]
            for member in snapshot.memberships
        )
        run = DiscoveryRun(
            run_id=run_id,
            spec=spec,
            universe_version=snapshot.universe.version,
            benchmark_asset_id=benchmark_asset_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            outcomes=outcomes,
            candidates=tuple(candidates),
        )
        return self.repository.save(run)

    def get(self, run_id: str) -> DiscoveryRun | None:
        return self.repository.get(run_id)

    def _load_inputs(
        self,
        spec: DiscoverySpec,
        memberships,
        supplied: Mapping[str, DiscoveryMemberInput] | None,
    ) -> dict[str, DiscoveryMemberInput]:
        if supplied is not None:
            return dict(supplied)
        if self.metric_loader is None:
            return {}
        return {
            member.asset_id: self.metric_loader.load(
                asset_id=member.asset_id,
                spec=spec,
            )
            for member in memberships
            if member.status is MembershipStatus.RESOLVED
            and member.asset_id is not None
        }


class _EligibleMember:
    def __init__(
        self,
        *,
        source_symbol: str,
        source_name: str,
        asset_id: str,
        discovery_input: DiscoveryMemberInput,
        matched_rules: tuple[str, ...],
        matched_tracks: tuple[str, ...],
    ) -> None:
        self.source_symbol = source_symbol
        self.source_name = source_name
        self.asset_id = asset_id
        self.discovery_input = discovery_input
        self.matched_rules = matched_rules
        self.matched_tracks = matched_tracks


def _missing_metrics(
    spec: DiscoverySpec,
    discovery_input: DiscoveryMemberInput,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in spec.required_metrics
            if name not in discovery_input.metrics
            or not discovery_input.metrics[name].applicable
            or discovery_input.metrics[name].value is None
            or (
                discovery_input.metrics[name].as_of_date
                > spec.as_of_date
            )
        )
    )


def _missing_detail(
    name: str,
    discovery_input: DiscoveryMemberInput,
    spec: DiscoverySpec,
) -> str:
    metric = discovery_input.metrics.get(name)
    if metric is None:
        return "Required metric was not loaded"
    if metric.as_of_date > spec.as_of_date:
        return "Metric is dated after discovery as_of_date"
    return metric.reason or "Required metric is not applicable"


def _evaluate_rules(
    rules: tuple[DiscoveryRule, ...],
    discovery_input: DiscoveryMemberInput,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    matched: list[str] = []
    failed: list[str] = []
    for rule in rules:
        metric = discovery_input.metrics[rule.metric_name]
        if _compare(metric.value, rule):
            matched.append(rule.rule_id)
        else:
            failed.append(rule.rule_id)
    return tuple(matched), tuple(failed)


def _compare(value: Decimal | None, rule: DiscoveryRule) -> bool:
    if value is None:
        return False
    if rule.operator is ConditionOperator.GT:
        return value > rule.threshold
    if rule.operator is ConditionOperator.GTE:
        return value >= rule.threshold
    if rule.operator is ConditionOperator.LT:
        return value < rule.threshold
    if rule.operator is ConditionOperator.LTE:
        return value <= rule.threshold
    if rule.operator is ConditionOperator.EQ:
        return value == rule.threshold
    return (
        rule.upper_threshold is not None
        and rule.threshold <= value <= rule.upper_threshold
    )


def _failed_rule_reason(
    rule: DiscoveryRule,
    discovery_input: DiscoveryMemberInput,
    *,
    layer: int,
) -> DiscoveryReason:
    value = discovery_input.metrics[rule.metric_name].value
    return DiscoveryReason(
        code="hard_rule_failed",
        layer=layer,
        detail=rule.description,
        rule_id=rule.rule_id,
        metric_name=rule.metric_name,
        observed_value=value,
    )


def _select_candidates(
    spec: DiscoverySpec,
    eligible: list[_EligibleMember],
) -> dict[str, str]:
    pools = {
        track.track_id: _ranked_pool(track, eligible)
        for track in spec.candidate_tracks
    }
    positions = {
        track.track_id: 0 for track in spec.candidate_tracks
    }
    selected: dict[str, str] = {}
    for _ in range(spec.max_per_track):
        for track in spec.candidate_tracks:
            if len(selected) >= spec.max_candidates:
                return selected
            pool = pools[track.track_id]
            position = positions[track.track_id]
            while (
                position < len(pool)
                and pool[position].asset_id in selected
            ):
                position += 1
            positions[track.track_id] = position + 1
            if position < len(pool):
                selected[pool[position].asset_id] = track.track_id
    return selected


def _ranked_pool(
    track,
    eligible: list[_EligibleMember],
) -> list[_EligibleMember]:
    pool = [
        item
        for item in eligible
        if track.track_id in item.matched_tracks
    ]
    pool.sort(key=lambda item: item.source_symbol)
    for rank in reversed(track.rank_by):
        pool.sort(
            key=lambda item: item.discovery_input.metrics[
                rank.metric_name
            ].value,
            reverse=rank.descending,
        )
    return pool


def _terminal_outcome(
    member,
    status: DiscoveryMemberStatus,
    *,
    reason: DiscoveryReason | None = None,
    reasons: tuple[DiscoveryReason, ...] = (),
    missing_metrics: tuple[str, ...] = (),
    matched_rules: tuple[str, ...] = (),
    failed_rules: tuple[str, ...] = (),
    last_layer: int = 1,
) -> DiscoveryMemberOutcome:
    return DiscoveryMemberOutcome(
        source_symbol=member.source_symbol,
        source_name=member.source_name,
        asset_id=member.asset_id,
        status=status,
        last_layer=last_layer,
        reasons=reasons or ((reason,) if reason is not None else ()),
        missing_metrics=missing_metrics,
        matched_rules=matched_rules,
        failed_rules=failed_rules,
    )


def _run_id(
    spec: DiscoverySpec,
    universe_version: str,
    memberships,
    inputs: Mapping[str, DiscoveryMemberInput],
) -> str:
    payload = {
        "spec": spec.to_dict(),
        "universe_version": universe_version,
        "memberships": [
            {
                "source_symbol": member.source_symbol,
                "asset_id": member.asset_id,
                "status": member.status.value,
            }
            for member in memberships
        ],
        "inputs": {
            asset_id: {
                "profile_id": item.profile_id,
                "data_errors": list(item.data_errors),
                "metrics": {
                    name: {
                        "value": (
                            format(metric.value, "f")
                            if metric.value is not None
                            else None
                        ),
                        "as_of_date": metric.as_of_date,
                        "calculation_version": (
                            metric.calculation_version
                        ),
                        "applicable": metric.applicable,
                        "reason": metric.reason,
                    }
                    for name, metric in sorted(
                        item.metrics.items()
                    )
                },
            }
            for asset_id, item in sorted(inputs.items())
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
    return f"discovery:{digest[:24]}"


def _draft_task_id(run_id: str, asset_id: str) -> str:
    digest = hashlib.sha256(asset_id.encode("utf-8")).hexdigest()
    return f"research:draft:{run_id.split(':', 1)[1]}:{digest[:10]}"
