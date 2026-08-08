"""Direction-only calibration for anonymous weekly state ETF proxies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Sequence

from .coverage import StateETFCoverageReport
from .models import ConfirmedHoldingChange, WeeklyStateFlowMetric
from .registry import DEFAULT_STATE_ENTITY_REGISTRY, StateEntityRegistry


CalibrationOutcome = Literal["match", "mismatch", "unknown"]


@dataclass(frozen=True)
class StateProxyCalibrationCase:
    instrument: str
    interval: tuple[str, str]
    confirmed_delta_shares: Decimal
    proxy_delta_shares: Decimal
    outcome: CalibrationOutcome
    available_at: str

    def __post_init__(self) -> None:
        _require_text(self.instrument, "instrument")
        interval = tuple(self.interval)
        object.__setattr__(self, "interval", interval)
        if len(interval) != 2:
            raise ValueError("interval must contain previous and current dates")
        previous = _parse_date(interval[0], "interval start")
        current = _parse_date(interval[1], "interval end")
        if current <= previous:
            raise ValueError("interval end must follow interval start")
        _require_decimal(self.confirmed_delta_shares, "confirmed_delta_shares")
        _require_decimal(self.proxy_delta_shares, "proxy_delta_shares")
        if self.outcome not in {"match", "mismatch", "unknown"}:
            raise ValueError("outcome must be match, mismatch, or unknown")
        _parse_date(self.available_at, "available_at")
        expected = _direction_outcome(
            self.confirmed_delta_shares,
            self.proxy_delta_shares,
            proxy_is_available=(self.outcome != "unknown"),
        )
        if self.outcome != "unknown" and self.outcome != expected:
            raise ValueError("outcome is inconsistent with delta directions")
        if self.outcome == "unknown" and (
            self.confirmed_delta_shares != 0
            and self.proxy_delta_shares != 0
        ):
            raise ValueError("unknown requires a zero delta or missing weekly data")


@dataclass(frozen=True)
class StateProxyCalibrationResult:
    as_of_date: str
    case_count: int
    match_count: int
    mismatch_count: int
    unknown_count: int
    direction_accuracy: float | None
    cases: tuple[StateProxyCalibrationCase, ...]

    def __post_init__(self) -> None:
        as_of = _parse_date(self.as_of_date, "as_of_date")
        cases = tuple(self.cases)
        object.__setattr__(self, "cases", cases)
        counts = {
            "match": sum(item.outcome == "match" for item in cases),
            "mismatch": sum(item.outcome == "mismatch" for item in cases),
            "unknown": sum(item.outcome == "unknown" for item in cases),
        }
        for value, field_name in (
            (self.case_count, "case_count"),
            (self.match_count, "match_count"),
            (self.mismatch_count, "mismatch_count"),
            (self.unknown_count, "unknown_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.case_count != len(cases):
            raise ValueError("case_count must equal the number of cases")
        if (
            self.match_count != counts["match"]
            or self.mismatch_count != counts["mismatch"]
            or self.unknown_count != counts["unknown"]
        ):
            raise ValueError("outcome counts are inconsistent with cases")
        if any(_parse_date(item.available_at, "available_at") > as_of for item in cases):
            raise ValueError("cases must be available by as_of_date")
        decidable = self.match_count + self.mismatch_count
        expected_accuracy = self.match_count / decidable if decidable else None
        if expected_accuracy is None:
            if self.direction_accuracy is not None:
                raise ValueError("direction_accuracy must be None without samples")
        elif (
            isinstance(self.direction_accuracy, bool)
            or not isinstance(self.direction_accuracy, (int, float))
            or not math.isfinite(float(self.direction_accuracy))
            or not math.isclose(float(self.direction_accuracy), expected_accuracy)
        ):
            raise ValueError("direction_accuracy is inconsistent with outcomes")


class StateProxyCalibrationService:
    def __init__(
        self,
        registry: StateEntityRegistry = DEFAULT_STATE_ENTITY_REGISTRY,
    ) -> None:
        self.registry = registry

    def calibrate(
        self,
        confirmed_changes: Sequence[ConfirmedHoldingChange],
        weekly_metrics: Sequence[WeeklyStateFlowMetric],
        as_of_date: str,
    ) -> StateProxyCalibrationResult:
        as_of = _parse_date(as_of_date, "as_of_date")
        grouped: dict[
            tuple[str, str, str],
            dict[str, ConfirmedHoldingChange],
        ] = {}
        for change in confirmed_changes:
            if (
                _parse_date(change.available_at, "available_at") > as_of
                or not self.registry.is_core(change.entity_id)
            ):
                continue
            key = (
                change.instrument_asset_id,
                change.previous_holding_date,
                change.current_holding_date,
            )
            by_entity = grouped.setdefault(key, {})
            previous = by_entity.get(change.entity_id)
            if previous is None or (
                change.available_at,
                change.current_published_at,
                change.current_report_period,
            ) > (
                previous.available_at,
                previous.current_published_at,
                previous.current_report_period,
            ):
                by_entity[change.entity_id] = change

        weekly_details = _weekly_member_deltas(weekly_metrics)
        cases: list[StateProxyCalibrationCase] = []
        for (instrument, previous_date, current_date), by_entity in sorted(
            grouped.items()
        ):
            changes = tuple(by_entity.values())
            current_published_at = max(
                _parse_date(item.current_published_at, "current_published_at")
                for item in changes
            )
            confirmed_delta = sum(
                (item.delta_shares for item in changes),
                Decimal("0"),
            )
            eligible_by_week: dict[date, list[tuple[int, date, Decimal]]] = {}
            for (
                member,
                week_end,
                metric_available_at,
            ), (priority, delta) in weekly_details.items():
                if (
                    member == instrument
                    and _parse_date(previous_date, "previous_holding_date")
                    < week_end
                    <= _parse_date(current_date, "current_holding_date")
                    and metric_available_at <= current_published_at
                ):
                    eligible_by_week.setdefault(week_end, []).append(
                        (priority, metric_available_at, delta)
                    )
            selected = [
                min(
                    candidates,
                    key=lambda item: (item[0], -item[1].toordinal()),
                )[2]
                for candidates in eligible_by_week.values()
            ]
            proxy_delta = sum(selected, Decimal("0"))
            outcome = _direction_outcome(
                confirmed_delta,
                proxy_delta,
                proxy_is_available=bool(selected),
            )
            cases.append(
                StateProxyCalibrationCase(
                    instrument=instrument,
                    interval=(previous_date, current_date),
                    confirmed_delta_shares=confirmed_delta,
                    proxy_delta_shares=proxy_delta,
                    outcome=outcome,
                    available_at=current_published_at.isoformat(),
                )
            )

        match_count = sum(item.outcome == "match" for item in cases)
        mismatch_count = sum(item.outcome == "mismatch" for item in cases)
        unknown_count = sum(item.outcome == "unknown" for item in cases)
        decidable = match_count + mismatch_count
        return StateProxyCalibrationResult(
            as_of_date=as_of.isoformat(),
            case_count=len(cases),
            match_count=match_count,
            mismatch_count=mismatch_count,
            unknown_count=unknown_count,
            direction_accuracy=match_count / decidable if decidable else None,
            cases=tuple(cases),
        )


@dataclass(frozen=True)
class StateTrackingStopAssessment:
    as_of_date: str
    ready_to_stop: bool
    failure_reasons: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _parse_date(self.as_of_date, "as_of_date")
        if type(self.ready_to_stop) is not bool:
            raise TypeError("ready_to_stop must be bool")
        reasons = tuple(self.failure_reasons)
        object.__setattr__(self, "failure_reasons", reasons)
        if any(not isinstance(reason, str) or not reason.strip() for reason in reasons):
            raise ValueError("failure_reasons must contain non-blank strings")
        if len(reasons) != len(set(reasons)):
            raise ValueError("failure_reasons must be unique")
        if self.ready_to_stop != (not reasons):
            raise ValueError("ready_to_stop must agree with failure_reasons")
        required = {
            "min_confirmed_etfs",
            "min_calibration_cases",
            "min_direction_accuracy",
            "research_assumption",
        }
        if not isinstance(self.parameters, dict) or not required.issubset(
            self.parameters
        ):
            raise ValueError("parameters are missing stop-threshold fields")
        if self.parameters["research_assumption"] is not True:
            raise ValueError("stop thresholds must be marked research_assumption")
        object.__setattr__(self, "parameters", dict(self.parameters))


def assess_tracking_stop(
    coverage: StateETFCoverageReport,
    calibration: StateProxyCalibrationResult,
    *,
    min_confirmed_etfs: int,
    min_calibration_cases: int,
    min_direction_accuracy: float,
) -> StateTrackingStopAssessment:
    if coverage.as_of_date != calibration.as_of_date:
        raise ValueError("coverage and calibration as_of_date must match")
    for value, field_name in (
        (min_confirmed_etfs, "min_confirmed_etfs"),
        (min_calibration_cases, "min_calibration_cases"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} must be a non-negative integer")
    if (
        isinstance(min_direction_accuracy, bool)
        or not isinstance(min_direction_accuracy, (int, float))
        or not math.isfinite(float(min_direction_accuracy))
        or not 0 <= min_direction_accuracy <= 1
    ):
        raise ValueError("min_direction_accuracy must be between zero and one")

    reasons: list[str] = []
    if coverage.confirmed_count < min_confirmed_etfs:
        reasons.append("insufficient_confirmed_etfs")
    if coverage.weekly_ready_count < min_confirmed_etfs:
        reasons.append("insufficient_weekly_ready_etfs")
    decidable_cases = (
        calibration.match_count + calibration.mismatch_count
    )
    if decidable_cases < min_calibration_cases:
        reasons.append("insufficient_calibration_cases")
    if calibration.direction_accuracy is None:
        reasons.append("direction_accuracy_unavailable")
    elif calibration.direction_accuracy < min_direction_accuracy:
        reasons.append("direction_accuracy_below_threshold")
    parameters = {
        "min_confirmed_etfs": min_confirmed_etfs,
        "min_calibration_cases": min_calibration_cases,
        "min_direction_accuracy": float(min_direction_accuracy),
        "decidable_calibration_cases": decidable_cases,
        "research_assumption": True,
        "not_a_probability_or_statistical_guarantee": True,
    }
    return StateTrackingStopAssessment(
        as_of_date=coverage.as_of_date,
        ready_to_stop=not reasons,
        failure_reasons=tuple(reasons),
        parameters=parameters,
    )


def _weekly_member_deltas(
    metrics: Sequence[WeeklyStateFlowMetric],
) -> dict[tuple[str, date, date], tuple[int, Decimal]]:
    selected: dict[tuple[str, date, date], tuple[int, Decimal]] = {}
    for metric in metrics:
        if metric.evidence_tier != "anonymous_proxy":
            continue
        details = metric.parameters.get("member_details")
        if not isinstance(details, dict):
            continue
        week_end = _parse_date(metric.week_end, "week_end")
        available_at = _parse_date(metric.available_at, "available_at")
        priority = (
            0
            if metric.factor_name == "state_held_etf_weekly_anonymous_flow_cny"
            else (
                1
                if metric.factor_name
                == "state_held_etf_weekly_positive_breadth"
                else 2
            )
        )
        for instrument, detail in details.items():
            if not isinstance(instrument, str) or not instrument.strip():
                raise ValueError("member_details keys must be instrument ids")
            if not isinstance(detail, dict) or "delta_total_shares" not in detail:
                continue
            delta = _decimal_from_detail(detail["delta_total_shares"])
            key = (instrument, week_end, available_at)
            previous = selected.get(key)
            candidate = (priority, delta)
            if previous is None or candidate[0] < previous[0]:
                selected[key] = candidate
            elif candidate[0] == previous[0] and candidate[1] != previous[1]:
                raise ValueError("duplicate weekly member deltas are inconsistent")
    return selected


def _decimal_from_detail(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("delta_total_shares must be numeric")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("delta_total_shares must be numeric") from exc
    if not result.is_finite():
        raise ValueError("delta_total_shares must be finite")
    return result


def _direction_outcome(
    confirmed: Decimal,
    proxy: Decimal,
    *,
    proxy_is_available: bool,
) -> CalibrationOutcome:
    if not proxy_is_available or confirmed == 0 or proxy == 0:
        return "unknown"
    return "match" if (confirmed > 0) == (proxy > 0) else "mismatch"


def _require_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc
