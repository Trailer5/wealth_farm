"""Point-in-time evidence coverage for state ETF candidates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

from .catalog import StateETFCandidate
from .models import ETFDailyObservation, StateHolderEvidence
from .registry import DEFAULT_STATE_ENTITY_REGISTRY, StateEntityRegistry


CandidateCoverageStatus = Literal[
    "confirmed",
    "holder_missing",
    "weekly_observation_missing",
]


@dataclass(frozen=True)
class StateETFCandidateStatus:
    candidate: StateETFCandidate
    official_core_holder_count: int
    latest_report_period: str | None
    latest_published_at: str | None
    weekly_observation_count: int
    status: CandidateCoverageStatus

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, StateETFCandidate):
            raise TypeError("candidate must be StateETFCandidate")
        for value, field_name in (
            (self.official_core_holder_count, "official_core_holder_count"),
            (self.weekly_observation_count, "weekly_observation_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (self.latest_report_period is None) != (
            self.latest_published_at is None
        ):
            raise ValueError("latest report period and publication date must coexist")
        if self.latest_report_period is not None:
            _require_text(self.latest_report_period, "latest_report_period")
            _parse_date(self.latest_published_at, "latest_published_at")
        expected = (
            "holder_missing"
            if self.official_core_holder_count == 0
            else (
                "weekly_observation_missing"
                if self.weekly_observation_count < 2
                else "confirmed"
            )
        )
        if self.status != expected:
            raise ValueError("status is inconsistent with holder and weekly coverage")


@dataclass(frozen=True)
class StateETFCoverageReport:
    as_of_date: str
    candidate_count: int
    confirmed_count: int
    weekly_ready_count: int
    holder_coverage_ratio: float
    weekly_coverage_ratio: float
    statuses: tuple[StateETFCandidateStatus, ...]

    def __post_init__(self) -> None:
        _parse_date(self.as_of_date, "as_of_date")
        statuses = tuple(self.statuses)
        object.__setattr__(self, "statuses", statuses)
        for value, field_name in (
            (self.candidate_count, "candidate_count"),
            (self.confirmed_count, "confirmed_count"),
            (self.weekly_ready_count, "weekly_ready_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.candidate_count != len(statuses):
            raise ValueError("candidate_count must equal the number of statuses")
        asset_ids = [item.candidate.asset_id for item in statuses]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("candidate statuses must have unique asset_ids")
        expected_confirmed = sum(
            item.official_core_holder_count > 0 for item in statuses
        )
        expected_weekly = sum(
            item.official_core_holder_count > 0
            and item.weekly_observation_count >= 2
            for item in statuses
        )
        if self.confirmed_count != expected_confirmed:
            raise ValueError("confirmed_count is inconsistent with statuses")
        if self.weekly_ready_count != expected_weekly:
            raise ValueError("weekly_ready_count is inconsistent with statuses")
        denominator = self.candidate_count
        expected_holder_ratio = (
            self.confirmed_count / denominator if denominator else 0.0
        )
        expected_weekly_ratio = (
            self.weekly_ready_count / denominator if denominator else 0.0
        )
        for value, expected, field_name in (
            (
                self.holder_coverage_ratio,
                expected_holder_ratio,
                "holder_coverage_ratio",
            ),
            (
                self.weekly_coverage_ratio,
                expected_weekly_ratio,
                "weekly_coverage_ratio",
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not math.isclose(float(value), expected)
            ):
                raise ValueError(f"{field_name} is inconsistent with counts")


class StateETFCoverageService:
    def __init__(
        self,
        registry: StateEntityRegistry = DEFAULT_STATE_ENTITY_REGISTRY,
    ) -> None:
        self.registry = registry

    def evaluate(
        self,
        candidates: Sequence[StateETFCandidate],
        evidence: Sequence[StateHolderEvidence],
        observations: Sequence[ETFDailyObservation],
        as_of_date: str,
    ) -> StateETFCoverageReport:
        as_of = _parse_date(as_of_date, "as_of_date")
        candidate_items = tuple(candidates)
        asset_ids = [candidate.asset_id for candidate in candidate_items]
        symbols = [candidate.symbol for candidate in candidate_items]
        if any(not isinstance(item, StateETFCandidate) for item in candidate_items):
            raise TypeError("candidates must contain StateETFCandidate values")
        if len(asset_ids) != len(set(asset_ids)) or len(symbols) != len(set(symbols)):
            raise ValueError("candidates must have unique symbols and asset_ids")

        statuses: list[StateETFCandidateStatus] = []
        for candidate in candidate_items:
            qualifying = [
                item
                for item in evidence
                if (
                    item.instrument_asset_id == candidate.asset_id
                    and item.source_tier == "official"
                    and item.holding_shares > 0
                    and _parse_date(item.published_at, "published_at") <= as_of
                    and self.registry.is_core(item.entity_id)
                )
            ]
            core_entities = {item.entity_id for item in qualifying}
            latest = max(
                qualifying,
                key=lambda item: (
                    item.holding_date,
                    item.published_at,
                    item.report_period,
                    item.evidence_id,
                ),
                default=None,
            )
            observed_weeks = {
                _parse_date(item.trade_date, "trade_date").isocalendar()[:2]
                for item in observations
                if (
                    item.instrument_asset_id == candidate.asset_id
                    and _parse_date(item.available_at, "available_at") <= as_of
                )
            }
            holder_count = len(core_entities)
            weekly_count = len(observed_weeks)
            status: CandidateCoverageStatus
            if holder_count == 0:
                status = "holder_missing"
            elif weekly_count < 2:
                status = "weekly_observation_missing"
            else:
                status = "confirmed"
            statuses.append(
                StateETFCandidateStatus(
                    candidate=candidate,
                    official_core_holder_count=holder_count,
                    latest_report_period=(
                        None if latest is None else latest.report_period
                    ),
                    latest_published_at=(
                        None if latest is None else latest.published_at
                    ),
                    weekly_observation_count=weekly_count,
                    status=status,
                )
            )

        confirmed_count = sum(
            item.official_core_holder_count > 0 for item in statuses
        )
        weekly_ready_count = sum(item.status == "confirmed" for item in statuses)
        candidate_count = len(statuses)
        return StateETFCoverageReport(
            as_of_date=as_of.isoformat(),
            candidate_count=candidate_count,
            confirmed_count=confirmed_count,
            weekly_ready_count=weekly_ready_count,
            holder_coverage_ratio=(
                confirmed_count / candidate_count if candidate_count else 0.0
            ),
            weekly_coverage_ratio=(
                weekly_ready_count / candidate_count if candidate_count else 0.0
            ),
            statuses=tuple(statuses),
        )


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _parse_date(value: str | None, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc
