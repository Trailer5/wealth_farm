"""Contracts for an auditable, horizon-aware discovery funnel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from src.research_contracts import (
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskSpec,
)
from src.strategy import ConditionOperator, StrategyMetricInput


class DiscoveryMemberStatus(str, Enum):
    CANDIDATE = "candidate"
    PASSED = "passed_not_selected"
    ELIMINATED = "eliminated"
    INSUFFICIENT_DATA = "insufficient_data"
    UNRESOLVED = "unresolved"
    INACTIVE = "inactive"


@dataclass(frozen=True)
class DiscoveryRule:
    rule_id: str
    metric_name: str
    operator: ConditionOperator
    threshold: Decimal
    description: str
    horizon: ResearchHorizon
    upper_threshold: Decimal | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.rule_id, "rule_id"),
            (self.metric_name, "metric_name"),
            (self.description, "description"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.operator is ConditionOperator.BETWEEN:
            if self.upper_threshold is None:
                raise ValueError(
                    "between rule requires upper_threshold"
                )
            if self.upper_threshold < self.threshold:
                raise ValueError(
                    "upper_threshold must not be below threshold"
                )
        elif self.upper_threshold is not None:
            raise ValueError(
                "upper_threshold is only valid for between"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "metric_name": self.metric_name,
            "operator": self.operator.value,
            "threshold": format(self.threshold, "f"),
            "upper_threshold": (
                format(self.upper_threshold, "f")
                if self.upper_threshold is not None
                else None
            ),
            "description": self.description,
            "horizon": self.horizon.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryRule":
        upper = payload.get("upper_threshold")
        return cls(
            rule_id=str(payload["rule_id"]),
            metric_name=str(payload["metric_name"]),
            operator=ConditionOperator(payload["operator"]),
            threshold=Decimal(str(payload["threshold"])),
            upper_threshold=(
                Decimal(str(upper)) if upper is not None else None
            ),
            description=str(payload["description"]),
            horizon=ResearchHorizon(payload["horizon"]),
        )


@dataclass(frozen=True)
class DiscoveryRankMetric:
    metric_name: str
    descending: bool

    def __post_init__(self) -> None:
        if not self.metric_name.strip():
            raise ValueError("metric_name must not be blank")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "descending": self.descending,
        }


@dataclass(frozen=True)
class DiscoveryCandidateTrack:
    track_id: str
    name: str
    rules: tuple[DiscoveryRule, ...]
    rank_by: tuple[DiscoveryRankMetric, ...]

    def __post_init__(self) -> None:
        if not self.track_id.strip() or not self.name.strip():
            raise ValueError("track_id and name must not be blank")
        if not self.rules or not self.rank_by:
            raise ValueError(
                "Candidate track requires rules and rank metrics"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "name": self.name,
            "rules": [rule.to_dict() for rule in self.rules],
            "rank_by": [
                metric.to_dict() for metric in self.rank_by
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DiscoveryCandidateTrack":
        return cls(
            track_id=str(payload["track_id"]),
            name=str(payload["name"]),
            rules=tuple(
                DiscoveryRule.from_dict(item)
                for item in payload["rules"]
            ),
            rank_by=tuple(
                DiscoveryRankMetric(
                    metric_name=str(item["metric_name"]),
                    descending=bool(item["descending"]),
                )
                for item in payload["rank_by"]
            ),
        )


@dataclass(frozen=True)
class DiscoverySpec:
    discovery_id: str
    name: str
    version: str
    universe_id: str
    as_of_date: str
    horizon: ResearchHorizon
    question_type: ResearchQuestionType
    profile_id: str
    required_metrics: tuple[str, ...]
    hard_rules: tuple[DiscoveryRule, ...]
    candidate_tracks: tuple[DiscoveryCandidateTrack, ...]
    data_window_days: int
    max_candidates: int
    max_per_track: int
    universe_version: str | None = None
    benchmark_asset_id: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        for value, name in (
            (self.discovery_id, "discovery_id"),
            (self.name, "name"),
            (self.version, "version"),
            (self.universe_id, "universe_id"),
            (self.profile_id, "profile_id"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.data_window_days < 1:
            raise ValueError("data_window_days must be positive")
        if self.max_candidates < 1 or self.max_per_track < 1:
            raise ValueError(
                "Candidate limits must be positive"
            )
        if len(set(self.required_metrics)) != len(
            self.required_metrics
        ):
            raise ValueError("required_metrics must be unique")
        if not self.hard_rules or not self.candidate_tracks:
            raise ValueError(
                "Discovery spec requires hard rules and tracks"
            )
        all_rules = self.hard_rules + tuple(
            rule
            for track in self.candidate_tracks
            for rule in track.rules
        )
        if any(rule.horizon is not self.horizon for rule in all_rules):
            raise ValueError(
                "Every discovery rule must declare the spec horizon"
            )
        used_metrics = {
            rule.metric_name for rule in all_rules
        } | {
            rank.metric_name
            for track in self.candidate_tracks
            for rank in track.rank_by
        }
        undeclared = used_metrics - set(self.required_metrics)
        if undeclared:
            raise ValueError(
                "Rules use undeclared required_metrics: "
                f"{sorted(undeclared)}"
            )
        rule_ids = [rule.rule_id for rule in all_rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("rule_id must be unique")
        track_ids = [
            track.track_id for track in self.candidate_tracks
        ]
        if len(set(track_ids)) != len(track_ids):
            raise ValueError("track_id must be unique")
        if not self.limitations:
            raise ValueError(
                "Discovery spec must state its limitations"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_id": self.discovery_id,
            "name": self.name,
            "version": self.version,
            "universe_id": self.universe_id,
            "universe_version": self.universe_version,
            "as_of_date": self.as_of_date,
            "horizon": self.horizon.value,
            "question_type": self.question_type.value,
            "profile_id": self.profile_id,
            "required_metrics": list(self.required_metrics),
            "hard_rules": [
                rule.to_dict() for rule in self.hard_rules
            ],
            "candidate_tracks": [
                track.to_dict() for track in self.candidate_tracks
            ],
            "data_window_days": self.data_window_days,
            "max_candidates": self.max_candidates,
            "max_per_track": self.max_per_track,
            "benchmark_asset_id": self.benchmark_asset_id,
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoverySpec":
        return cls(
            discovery_id=str(payload["discovery_id"]),
            name=str(payload["name"]),
            version=str(payload["version"]),
            universe_id=str(payload["universe_id"]),
            universe_version=(
                str(payload["universe_version"])
                if payload.get("universe_version") is not None
                else None
            ),
            as_of_date=str(payload["as_of_date"]),
            horizon=ResearchHorizon(payload["horizon"]),
            question_type=ResearchQuestionType(
                payload["question_type"]
            ),
            profile_id=str(payload["profile_id"]),
            required_metrics=tuple(payload["required_metrics"]),
            hard_rules=tuple(
                DiscoveryRule.from_dict(item)
                for item in payload["hard_rules"]
            ),
            candidate_tracks=tuple(
                DiscoveryCandidateTrack.from_dict(item)
                for item in payload["candidate_tracks"]
            ),
            data_window_days=int(payload["data_window_days"]),
            max_candidates=int(payload["max_candidates"]),
            max_per_track=int(payload["max_per_track"]),
            benchmark_asset_id=(
                str(payload["benchmark_asset_id"])
                if payload.get("benchmark_asset_id") is not None
                else None
            ),
            limitations=tuple(payload["limitations"]),
        )


@dataclass(frozen=True)
class DiscoveryMemberInput:
    asset_id: str
    profile_id: str
    metrics: dict[str, StrategyMetricInput]
    data_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.profile_id.strip():
            raise ValueError(
                "asset_id and profile_id must not be blank"
            )
        for name, metric in self.metrics.items():
            if name != metric.metric_name:
                raise ValueError(
                    "Metric key must equal metric_name"
                )


@dataclass(frozen=True)
class DiscoveryReason:
    code: str
    layer: int
    detail: str
    rule_id: str | None = None
    metric_name: str | None = None
    observed_value: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.detail.strip():
            raise ValueError("Reason code and detail must not be blank")
        if self.layer not in {1, 2, 3}:
            raise ValueError("Reason layer must be 1, 2, or 3")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "layer": self.layer,
            "detail": self.detail,
            "rule_id": self.rule_id,
            "metric_name": self.metric_name,
            "observed_value": (
                format(self.observed_value, "f")
                if self.observed_value is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryReason":
        observed = payload.get("observed_value")
        return cls(
            code=str(payload["code"]),
            layer=int(payload["layer"]),
            detail=str(payload["detail"]),
            rule_id=payload.get("rule_id"),
            metric_name=payload.get("metric_name"),
            observed_value=(
                Decimal(str(observed))
                if observed is not None
                else None
            ),
        )


@dataclass(frozen=True)
class DiscoveryMemberOutcome:
    source_symbol: str
    source_name: str
    asset_id: str | None
    status: DiscoveryMemberStatus
    last_layer: int
    reasons: tuple[DiscoveryReason, ...]
    missing_metrics: tuple[str, ...] = ()
    matched_rules: tuple[str, ...] = ()
    failed_rules: tuple[str, ...] = ()
    matched_tracks: tuple[str, ...] = ()
    selected_track: str | None = None

    def __post_init__(self) -> None:
        if not self.source_symbol.strip():
            raise ValueError("source_symbol must not be blank")
        if not self.reasons:
            raise ValueError("Every member outcome requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_symbol": self.source_symbol,
            "source_name": self.source_name,
            "asset_id": self.asset_id,
            "status": self.status.value,
            "last_layer": self.last_layer,
            "reasons": [
                reason.to_dict() for reason in self.reasons
            ],
            "missing_metrics": list(self.missing_metrics),
            "matched_rules": list(self.matched_rules),
            "failed_rules": list(self.failed_rules),
            "matched_tracks": list(self.matched_tracks),
            "selected_track": self.selected_track,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DiscoveryMemberOutcome":
        return cls(
            source_symbol=str(payload["source_symbol"]),
            source_name=str(payload["source_name"]),
            asset_id=payload.get("asset_id"),
            status=DiscoveryMemberStatus(payload["status"]),
            last_layer=int(payload["last_layer"]),
            reasons=tuple(
                DiscoveryReason.from_dict(item)
                for item in payload["reasons"]
            ),
            missing_metrics=tuple(payload["missing_metrics"]),
            matched_rules=tuple(payload["matched_rules"]),
            failed_rules=tuple(payload["failed_rules"]),
            matched_tracks=tuple(payload["matched_tracks"]),
            selected_track=payload.get("selected_track"),
        )


@dataclass(frozen=True)
class DiscoveryCandidate:
    asset_id: str
    source_symbol: str
    source_name: str
    selected_track: str
    matched_tracks: tuple[str, ...]
    research_task_draft: ResearchTaskSpec

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "source_symbol": self.source_symbol,
            "source_name": self.source_name,
            "selected_track": self.selected_track,
            "matched_tracks": list(self.matched_tracks),
            "research_task_draft": (
                self.research_task_draft.to_dict()
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "DiscoveryCandidate":
        return cls(
            asset_id=str(payload["asset_id"]),
            source_symbol=str(payload["source_symbol"]),
            source_name=str(payload["source_name"]),
            selected_track=str(payload["selected_track"]),
            matched_tracks=tuple(payload["matched_tracks"]),
            research_task_draft=ResearchTaskSpec.from_dict(
                payload["research_task_draft"]
            ),
        )


@dataclass(frozen=True)
class DiscoveryRun:
    run_id: str
    spec: DiscoverySpec
    universe_version: str
    benchmark_asset_id: str
    started_at: str
    completed_at: str
    outcomes: tuple[DiscoveryMemberOutcome, ...]
    candidates: tuple[DiscoveryCandidate, ...]
    is_buy_recommendation: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        datetime.fromisoformat(
            self.started_at.replace("Z", "+00:00")
        )
        datetime.fromisoformat(
            self.completed_at.replace("Z", "+00:00")
        )
        if not self.run_id.strip() or not self.outcomes:
            raise ValueError("Run ID and outcomes must not be empty")
        symbols = [item.source_symbol for item in self.outcomes]
        if len(set(symbols)) != len(symbols):
            raise ValueError(
                "Discovery run requires one outcome per member"
            )
        candidate_ids = {
            item.asset_id for item in self.candidates
        }
        outcome_ids = {
            item.asset_id
            for item in self.outcomes
            if item.status is DiscoveryMemberStatus.CANDIDATE
        }
        if candidate_ids != outcome_ids:
            raise ValueError(
                "Candidate list must match candidate outcomes"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spec": self.spec.to_dict(),
            "universe_version": self.universe_version,
            "benchmark_asset_id": self.benchmark_asset_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "outcomes": [
                outcome.to_dict() for outcome in self.outcomes
            ],
            "candidates": [
                candidate.to_dict()
                for candidate in self.candidates
            ],
            "is_buy_recommendation": False,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DiscoveryRun":
        return cls(
            run_id=str(payload["run_id"]),
            spec=DiscoverySpec.from_dict(payload["spec"]),
            universe_version=str(payload["universe_version"]),
            benchmark_asset_id=str(
                payload["benchmark_asset_id"]
            ),
            started_at=str(payload["started_at"]),
            completed_at=str(payload["completed_at"]),
            outcomes=tuple(
                DiscoveryMemberOutcome.from_dict(item)
                for item in payload["outcomes"]
            ),
            candidates=tuple(
                DiscoveryCandidate.from_dict(item)
                for item in payload["candidates"]
            ),
        )
