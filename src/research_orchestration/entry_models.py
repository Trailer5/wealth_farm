"""Structured contracts for the independent entry-timing lane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from src.research_workflow import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
)


class EntryPlanStatus(str, Enum):
    ACT = "act"
    STAGE = "stage"
    WAIT = "wait"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT = "insufficient"


class EntryConfidence(str, Enum):
    SUPPORTED = "supported"
    BOUNDED = "bounded"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT = "insufficient"


class EntryConditionKind(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"


class TimingDimension(str, Enum):
    CURRENT_REGIME = "current_regime"
    ENTRY_CONDITION = "entry_condition"
    WAIT_CONDITION = "wait_condition"
    TRANCHE_RULE = "tranche_rule"
    AVOID_CONDITION = "avoid_condition"
    CATALYST_WINDOW = "catalyst_window"
    INVALIDATION_CONDITION = "invalidation_condition"
    NEXT_TRADABLE_TIME = "next_tradable_time"
    EXECUTION_CONSTRAINT = "execution_constraint"
    HORIZON_CONFLICT = "horizon_conflict"


class TimingInputKind(str, Enum):
    VALUATION_REGIME = "valuation_regime"
    TREND_STATE = "trend_state"
    CYCLE_POSITION = "cycle_position"
    CATALYST_TIMELINE = "catalyst_timeline"
    LIQUIDITY_STATE = "liquidity_state"
    ETF_PREMIUM_DISCOUNT = "etf_premium_discount"
    MARKET_RISK_STATE = "market_risk_state"
    TRADING_CALENDAR = "trading_calendar"
    FUND_DEALING_RULE = "fund_dealing_rule"
    CROSS_HORIZON = "cross_horizon"


class TimingConditionState(str, Enum):
    SUPPORTIVE = "supportive"
    NEUTRAL = "neutral"
    ADVERSE = "adverse"
    MET = "met"
    NOT_MET = "not_met"
    WATCH = "watch"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class EntryConditionBlock:
    condition_id: str
    text: str
    kind: EntryConditionKind
    dimension: TimingDimension
    input_kind: TimingInputKind
    state: TimingConditionState
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    signal_horizon: ResearchHorizon
    as_of_date: str
    valid_from: str
    valid_until: str
    observability_key: str
    source_finding_ids: tuple[str, ...]
    window_start: str | None = None
    window_end: str | None = None
    next_tradable_at: str | None = None

    def __post_init__(self) -> None:
        for value, name in (
            (self.condition_id, "condition_id"),
            (self.text, "text"),
            (self.observability_key, "observability_key"),
        ):
            _text(value, name)
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.limitations, "limitations")
        _unique_texts(
            self.source_finding_ids,
            "source_finding_ids",
        )
        if not self.evidence_refs:
            raise ValueError(
                "entry condition requires evidence_refs"
            )
        if not self.limitations:
            raise ValueError(
                "entry condition requires limitations"
            )
        as_of = date.fromisoformat(self.as_of_date)
        valid_from = date.fromisoformat(self.valid_from)
        valid_until = date.fromisoformat(self.valid_until)
        if valid_from > valid_until:
            raise ValueError(
                "entry condition valid_from exceeds valid_until"
            )
        if as_of > valid_until:
            raise ValueError(
                "entry condition is expired at as_of_date"
            )
        validate_timing_condition_state(
            self.dimension,
            self.state,
        )
        _validate_window(self)
        _validate_next_tradable(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "text": self.text,
            "kind": self.kind.value,
            "dimension": self.dimension.value,
            "input_kind": self.input_kind.value,
            "state": self.state.value,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "signal_horizon": self.signal_horizon.value,
            "as_of_date": self.as_of_date,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "observability_key": self.observability_key,
            "source_finding_ids": list(
                self.source_finding_ids
            ),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "next_tradable_at": self.next_tradable_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EntryConditionBlock":
        _reject_unknown(
            payload,
            {
                "condition_id",
                "text",
                "kind",
                "dimension",
                "input_kind",
                "state",
                "evidence_refs",
                "limitations",
                "signal_horizon",
                "as_of_date",
                "valid_from",
                "valid_until",
                "observability_key",
                "source_finding_ids",
                "window_start",
                "window_end",
                "next_tradable_at",
            },
            "EntryConditionBlock",
        )
        return cls(
            condition_id=str(payload["condition_id"]),
            text=str(payload["text"]),
            kind=EntryConditionKind(payload["kind"]),
            dimension=TimingDimension(payload["dimension"]),
            input_kind=TimingInputKind(payload["input_kind"]),
            state=TimingConditionState(payload["state"]),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
            signal_horizon=ResearchHorizon(
                payload["signal_horizon"]
            ),
            as_of_date=str(payload["as_of_date"]),
            valid_from=str(payload["valid_from"]),
            valid_until=str(payload["valid_until"]),
            observability_key=str(
                payload["observability_key"]
            ),
            source_finding_ids=tuple(
                str(item)
                for item in payload.get(
                    "source_finding_ids",
                    (),
                )
            ),
            window_start=_optional_string(
                payload.get("window_start")
            ),
            window_end=_optional_string(
                payload.get("window_end")
            ),
            next_tradable_at=_optional_string(
                payload.get("next_tradable_at")
            ),
        )


@dataclass(frozen=True)
class EntryProfilePolicy:
    profile_id: str
    policy_id: str
    timing_supported: bool
    required_input_kinds: tuple[TimingInputKind, ...]
    execution_semantics: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.profile_id, "profile_id"),
            (self.policy_id, "policy_id"),
            (self.execution_semantics, "execution_semantics"),
        ):
            _text(value, name)
        if not isinstance(self.timing_supported, bool):
            raise TypeError("timing_supported must be bool")
        _unique(
            self.required_input_kinds,
            "required_input_kinds",
        )


@dataclass(frozen=True)
class EntryPlan:
    plan_id: str
    session_id: str
    suitability_assessment_id: str
    decision_brief_id: str | None
    asset_id: str
    profile_id: str
    policy_id: str
    as_of_date: str
    execution_horizon: ResearchHorizon
    suitability_horizon: ResearchHorizon
    status: EntryPlanStatus
    confidence: EntryConfidence
    current_regime: tuple[EntryConditionBlock, ...]
    entry_conditions: tuple[EntryConditionBlock, ...]
    wait_conditions: tuple[EntryConditionBlock, ...]
    tranche_rules: tuple[EntryConditionBlock, ...]
    avoid_conditions: tuple[EntryConditionBlock, ...]
    catalyst_windows: tuple[EntryConditionBlock, ...]
    invalidation_conditions: tuple[
        EntryConditionBlock,
        ...,
    ]
    execution_constraints: tuple[
        EntryConditionBlock,
        ...,
    ]
    horizon_conflicts: tuple[EntryConditionBlock, ...]
    next_tradable_condition: EntryConditionBlock | None
    signal_valid_from: str | None
    signal_valid_until: str | None
    next_tradable_at: str | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    assembly_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.plan_id, "plan_id"),
            (self.session_id, "session_id"),
            (
                self.suitability_assessment_id,
                "suitability_assessment_id",
            ),
            (self.asset_id, "asset_id"),
            (self.profile_id, "profile_id"),
            (self.policy_id, "policy_id"),
        ):
            _text(value, name)
        _optional_text(
            self.decision_brief_id,
            "decision_brief_id",
        )
        as_of = date.fromisoformat(self.as_of_date)
        _unique_texts(self.evidence_refs, "evidence_refs")
        _unique_texts(self.limitations, "limitations")
        _unique_texts(self.assembly_gaps, "assembly_gaps")
        if not self.limitations:
            raise ValueError("entry plan requires limitations")
        if self.signal_valid_until is not None:
            if date.fromisoformat(self.signal_valid_until) < as_of:
                raise ValueError(
                    "entry plan signal is expired"
                )
        if self.signal_valid_from is not None:
            date.fromisoformat(self.signal_valid_from)
        if (
            self.signal_valid_from is not None
            and self.signal_valid_until is not None
            and self.signal_valid_from > self.signal_valid_until
        ):
            raise ValueError(
                "entry plan signal validity is inverted"
            )
        if self.next_tradable_at is not None:
            next_tradable = datetime.fromisoformat(
                self.next_tradable_at
            )
            if next_tradable.utcoffset() is None:
                raise ValueError(
                    "next tradable timestamp requires timezone"
                )
            if next_tradable.date() < as_of:
                raise ValueError(
                    "next tradable time precedes plan as_of_date"
                )
        blocks = self.condition_blocks
        _unique(
            tuple(item.condition_id for item in blocks),
            "entry condition_id",
        )
        if any(not block.evidence_refs for block in blocks):
            raise ValueError(
                "entry plan conditions require evidence"
            )
        block_refs = {
            ref
            for block in blocks
            for ref in block.evidence_refs
        }
        if not block_refs.issubset(set(self.evidence_refs)):
            raise ValueError(
                "entry plan evidence_refs must cover conditions"
            )
        raw_valid_until = (
            min(item.valid_until for item in blocks)
            if blocks
            else None
        )
        raw_valid_from = (
            max(item.valid_from for item in blocks)
            if blocks
            else None
        )
        windows_overlap = (
            raw_valid_from is None
            or raw_valid_until is None
            or raw_valid_from <= raw_valid_until
        )
        expected_valid_until = (
            raw_valid_until if windows_overlap else None
        )
        if self.signal_valid_until != expected_valid_until:
            raise ValueError(
                "signal_valid_until does not match condition expiry"
            )
        expected_valid_from = (
            raw_valid_from if windows_overlap else None
        )
        if self.signal_valid_from != expected_valid_from:
            raise ValueError(
                "signal_valid_from does not match condition start"
            )
        if (
            self.next_tradable_condition is not None
            and self.next_tradable_at
            != self.next_tradable_condition.next_tradable_at
        ):
            raise ValueError(
                "next tradable time does not match its condition"
            )
        if self.next_tradable_condition is not None:
            if (
                self.next_tradable_condition.signal_horizon
                is not self.execution_horizon
            ):
                raise ValueError(
                    "next tradable condition horizon does not "
                    "match execution"
                )
            if (
                self.next_tradable_condition.input_kind
                not in allowed_next_tradable_input_kinds(
                    self.profile_id
                )
            ):
                raise ValueError(
                    "next tradable condition input is not "
                    "allowed for profile"
                )
        decisive = self.status not in (
            EntryPlanStatus.NOT_APPLICABLE,
            EntryPlanStatus.INSUFFICIENT,
        )
        if not self.policy_id.startswith(
            f"{self.profile_id}:"
        ):
            raise ValueError(
                "entry policy does not match profile"
            )
        if decisive and self.profile_id not in (
            PROFILE_ORDINARY_COMPANY,
            PROFILE_FINANCIAL_INSTITUTION,
            PROFILE_CYCLICAL_RESOURCE,
            PROFILE_EXCHANGE_FUND,
        ):
            raise ValueError(
                "entry profile does not support actionable plans"
            )
        if decisive and any(
            not values
            for values in (
                self.current_regime,
                self.entry_conditions,
                self.wait_conditions,
                self.tranche_rules,
                self.avoid_conditions,
                self.catalyst_windows,
                self.invalidation_conditions,
                self.execution_constraints,
            )
        ):
            raise ValueError(
                "decisive entry plan requires every timing section"
            )
        if decisive and (
            self.next_tradable_condition is None
            or self.signal_valid_from is None
            or self.signal_valid_until is None
            or self.next_tradable_at is None
        ):
            raise ValueError(
                "decisive entry plan requires validity and "
                "next tradable time"
            )
        if (
            self.next_tradable_at is not None
            and self.signal_valid_from is not None
            and datetime.fromisoformat(
                self.next_tradable_at
            ).date()
            < date.fromisoformat(self.signal_valid_from)
        ):
            raise ValueError(
                "next tradable time precedes signal validity"
            )
        if (
            self.next_tradable_at is not None
            and self.signal_valid_until is not None
            and datetime.fromisoformat(
                self.next_tradable_at
            ).date()
            > date.fromisoformat(self.signal_valid_until)
        ):
            raise ValueError(
                "next tradable time exceeds signal validity"
            )
        _validate_section_dimensions(self)
        _validate_status_semantics(self)
        expected_confidence = _expected_confidence(
            self.status,
            self.assembly_gaps,
        )
        if self.confidence is not expected_confidence:
            raise ValueError(
                "entry confidence does not match status and gaps"
            )
        if self.status in (
            EntryPlanStatus.NOT_APPLICABLE,
            EntryPlanStatus.INSUFFICIENT,
        ) and not self.assembly_gaps:
            raise ValueError(
                "non-decisive entry plan requires reasons"
            )
        expected_id = entry_plan_id(self.to_dict())
        if self.plan_id != expected_id:
            raise ValueError(
                "entry plan_id does not match content"
            )

    @property
    def condition_blocks(
        self,
    ) -> tuple[EntryConditionBlock, ...]:
        return (
            *self.current_regime,
            *self.entry_conditions,
            *self.wait_conditions,
            *self.tranche_rules,
            *self.avoid_conditions,
            *self.catalyst_windows,
            *self.invalidation_conditions,
            *self.execution_constraints,
            *self.horizon_conflicts,
            *(
                (self.next_tradable_condition,)
                if self.next_tradable_condition is not None
                else ()
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "suitability_assessment_id": (
                self.suitability_assessment_id
            ),
            "decision_brief_id": self.decision_brief_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "policy_id": self.policy_id,
            "as_of_date": self.as_of_date,
            "execution_horizon": self.execution_horizon.value,
            "suitability_horizon": (
                self.suitability_horizon.value
            ),
            "status": self.status.value,
            "confidence": self.confidence.value,
            "current_regime": _blocks_to_dict(
                self.current_regime
            ),
            "entry_conditions": _blocks_to_dict(
                self.entry_conditions
            ),
            "wait_conditions": _blocks_to_dict(
                self.wait_conditions
            ),
            "tranche_rules": _blocks_to_dict(
                self.tranche_rules
            ),
            "avoid_conditions": _blocks_to_dict(
                self.avoid_conditions
            ),
            "catalyst_windows": _blocks_to_dict(
                self.catalyst_windows
            ),
            "invalidation_conditions": _blocks_to_dict(
                self.invalidation_conditions
            ),
            "execution_constraints": _blocks_to_dict(
                self.execution_constraints
            ),
            "horizon_conflicts": _blocks_to_dict(
                self.horizon_conflicts
            ),
            "next_tradable_condition": (
                self.next_tradable_condition.to_dict()
                if self.next_tradable_condition is not None
                else None
            ),
            "signal_valid_from": self.signal_valid_from,
            "signal_valid_until": self.signal_valid_until,
            "next_tradable_at": self.next_tradable_at,
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "assembly_gaps": list(self.assembly_gaps),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "EntryPlan":
        _reject_unknown(
            payload,
            {
                "plan_id",
                "session_id",
                "suitability_assessment_id",
                "decision_brief_id",
                "asset_id",
                "profile_id",
                "policy_id",
                "as_of_date",
                "execution_horizon",
                "suitability_horizon",
                "status",
                "confidence",
                "current_regime",
                "entry_conditions",
                "wait_conditions",
                "tranche_rules",
                "avoid_conditions",
                "catalyst_windows",
                "invalidation_conditions",
                "execution_constraints",
                "horizon_conflicts",
                "next_tradable_condition",
                "signal_valid_from",
                "signal_valid_until",
                "next_tradable_at",
                "evidence_refs",
                "limitations",
                "assembly_gaps",
            },
            "EntryPlan",
        )
        next_condition = payload.get(
            "next_tradable_condition"
        )
        return cls(
            plan_id=str(payload["plan_id"]),
            session_id=str(payload["session_id"]),
            suitability_assessment_id=str(
                payload["suitability_assessment_id"]
            ),
            decision_brief_id=_optional_string(
                payload.get("decision_brief_id")
            ),
            asset_id=str(payload["asset_id"]),
            profile_id=str(payload["profile_id"]),
            policy_id=str(payload["policy_id"]),
            as_of_date=str(payload["as_of_date"]),
            execution_horizon=ResearchHorizon(
                payload["execution_horizon"]
            ),
            suitability_horizon=ResearchHorizon(
                payload["suitability_horizon"]
            ),
            status=EntryPlanStatus(payload["status"]),
            confidence=EntryConfidence(payload["confidence"]),
            current_regime=_blocks_from_dict(
                payload,
                "current_regime",
            ),
            entry_conditions=_blocks_from_dict(
                payload,
                "entry_conditions",
            ),
            wait_conditions=_blocks_from_dict(
                payload,
                "wait_conditions",
            ),
            tranche_rules=_blocks_from_dict(
                payload,
                "tranche_rules",
            ),
            avoid_conditions=_blocks_from_dict(
                payload,
                "avoid_conditions",
            ),
            catalyst_windows=_blocks_from_dict(
                payload,
                "catalyst_windows",
            ),
            invalidation_conditions=_blocks_from_dict(
                payload,
                "invalidation_conditions",
            ),
            execution_constraints=_blocks_from_dict(
                payload,
                "execution_constraints",
            ),
            horizon_conflicts=_blocks_from_dict(
                payload,
                "horizon_conflicts",
            ),
            next_tradable_condition=(
                EntryConditionBlock.from_dict(next_condition)
                if next_condition is not None
                else None
            ),
            signal_valid_from=_optional_string(
                payload.get("signal_valid_from")
            ),
            signal_valid_until=_optional_string(
                payload.get("signal_valid_until")
            ),
            next_tradable_at=_optional_string(
                payload.get("next_tradable_at")
            ),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
            assembly_gaps=tuple(
                str(item)
                for item in payload.get("assembly_gaps", ())
            ),
        )


def entry_plan_id(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("plan_id", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "entry:" + hashlib.sha256(encoded).hexdigest()[:24]


def allowed_next_tradable_input_kinds(
    profile_id: str,
) -> tuple[TimingInputKind, ...]:
    if profile_id == PROFILE_OPEN_FUND:
        return (
            TimingInputKind.FUND_DEALING_RULE,
            TimingInputKind.TRADING_CALENDAR,
        )
    return (TimingInputKind.TRADING_CALENDAR,)


def _validate_window(block: EntryConditionBlock) -> None:
    if block.dimension is TimingDimension.CATALYST_WINDOW:
        if (
            block.window_start is None
            or block.window_end is None
        ):
            raise ValueError(
                "catalyst window requires start and end"
            )
        if date.fromisoformat(
            block.window_start
        ) > date.fromisoformat(block.window_end):
            raise ValueError(
                "catalyst window start exceeds end"
            )
        if (
            date.fromisoformat(block.window_start)
            < date.fromisoformat(block.valid_from)
            or date.fromisoformat(block.window_end)
            > date.fromisoformat(block.valid_until)
        ):
            raise ValueError(
                "catalyst window exceeds condition validity"
            )
        return
    if block.window_start is not None or block.window_end is not None:
        raise ValueError(
            "only catalyst conditions may define a window"
        )


def _validate_section_dimensions(plan: EntryPlan) -> None:
    sections = (
        (plan.current_regime, TimingDimension.CURRENT_REGIME),
        (
            plan.entry_conditions,
            TimingDimension.ENTRY_CONDITION,
        ),
        (plan.wait_conditions, TimingDimension.WAIT_CONDITION),
        (plan.tranche_rules, TimingDimension.TRANCHE_RULE),
        (plan.avoid_conditions, TimingDimension.AVOID_CONDITION),
        (
            plan.catalyst_windows,
            TimingDimension.CATALYST_WINDOW,
        ),
        (
            plan.invalidation_conditions,
            TimingDimension.INVALIDATION_CONDITION,
        ),
        (
            plan.execution_constraints,
            TimingDimension.EXECUTION_CONSTRAINT,
        ),
        (
            plan.horizon_conflicts,
            TimingDimension.HORIZON_CONFLICT,
        ),
    )
    if any(
        block.dimension is not expected
        for blocks, expected in sections
        for block in blocks
    ):
        raise ValueError(
            "entry condition is stored in the wrong section"
        )
    if (
        plan.next_tradable_condition is not None
        and plan.next_tradable_condition.dimension
        is not TimingDimension.NEXT_TRADABLE_TIME
    ):
        raise ValueError(
            "next tradable condition has wrong dimension"
        )


def _validate_status_semantics(plan: EntryPlan) -> None:
    if plan.status not in (
        EntryPlanStatus.ACT,
        EntryPlanStatus.STAGE,
    ):
        return
    if plan.assembly_gaps:
        raise ValueError(
            "actionable entry plan cannot retain assembly gaps"
        )
    if any(
        item.state is TimingConditionState.UNKNOWN
        for item in plan.condition_blocks
    ):
        raise ValueError(
            "actionable entry plan has unknown condition state"
        )
    if (
        plan.next_tradable_condition is None
        or plan.next_tradable_condition.state
        is not TimingConditionState.MET
    ):
        raise ValueError(
            "actionable entry plan requires met next tradable time"
        )
    if not all(
        item.state is TimingConditionState.MET
        for item in plan.entry_conditions
    ):
        raise ValueError(
            "actionable entry plan requires met entry conditions"
        )
    blocked = (
        any(
            item.state
            in (
                TimingConditionState.MET,
                TimingConditionState.WATCH,
                TimingConditionState.ADVERSE,
            )
            for item in (
                *plan.wait_conditions,
                *plan.avoid_conditions,
                *plan.invalidation_conditions,
            )
        )
        or any(
            item.state is TimingConditionState.ADVERSE
            for item in plan.current_regime
        )
        or any(
            item.state
            in (
                TimingConditionState.NOT_MET,
                TimingConditionState.ADVERSE,
            )
            for item in plan.execution_constraints
        )
        or any(
            item.state
            in (
                TimingConditionState.NOT_MET,
                TimingConditionState.WATCH,
            )
            for item in plan.catalyst_windows
        )
        or any(
            item.state is TimingConditionState.MET
            for item in plan.horizon_conflicts
        )
    )
    if blocked:
        raise ValueError(
            "actionable entry plan has active wait or conflict"
        )
    staging_active = any(
        item.state is TimingConditionState.MET
        for item in plan.tranche_rules
    )
    if (
        plan.status is EntryPlanStatus.ACT
        and (
            staging_active
            or not all(
                item.state
                is TimingConditionState.NOT_APPLICABLE
                for item in plan.tranche_rules
            )
        )
    ):
        raise ValueError(
            "act status conflicts with tranche rules"
        )
    if (
        plan.status is EntryPlanStatus.STAGE
        and not staging_active
    ):
        raise ValueError(
            "stage status requires active tranche rule"
        )


def validate_timing_condition_state(
    dimension: TimingDimension,
    state: TimingConditionState,
) -> None:
    allowed = {
        TimingDimension.CURRENT_REGIME: {
            TimingConditionState.SUPPORTIVE,
            TimingConditionState.NEUTRAL,
            TimingConditionState.ADVERSE,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.ENTRY_CONDITION: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.WAIT_CONDITION: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.TRANCHE_RULE: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
            TimingConditionState.NOT_APPLICABLE,
        },
        TimingDimension.AVOID_CONDITION: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.CATALYST_WINDOW: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
            TimingConditionState.NOT_APPLICABLE,
        },
        TimingDimension.INVALIDATION_CONDITION: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.WATCH,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.NEXT_TRADABLE_TIME: {
            TimingConditionState.MET,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.EXECUTION_CONSTRAINT: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.ADVERSE,
            TimingConditionState.UNKNOWN,
        },
        TimingDimension.HORIZON_CONFLICT: {
            TimingConditionState.MET,
            TimingConditionState.NOT_MET,
            TimingConditionState.UNKNOWN,
        },
    }
    if state not in allowed[dimension]:
        raise ValueError(
            "timing condition state does not match dimension"
        )


def _validate_next_tradable(
    block: EntryConditionBlock,
) -> None:
    if block.dimension is TimingDimension.NEXT_TRADABLE_TIME:
        if block.next_tradable_at is None:
            raise ValueError(
                "next tradable condition requires timestamp"
            )
        next_tradable = datetime.fromisoformat(
            block.next_tradable_at
        )
        if next_tradable.utcoffset() is None:
            raise ValueError(
                "next tradable timestamp requires timezone"
            )
        if next_tradable.date() < date.fromisoformat(
            block.as_of_date
        ):
            raise ValueError(
                "next tradable timestamp precedes as_of_date"
            )
        if next_tradable.date() < date.fromisoformat(
            block.valid_from
        ):
            raise ValueError(
                "next tradable timestamp precedes validity"
            )
        if next_tradable.date() > date.fromisoformat(
            block.valid_until
        ):
            raise ValueError(
                "next tradable timestamp exceeds validity"
            )
        return
    if block.next_tradable_at is not None:
        raise ValueError(
            "only next tradable condition may define timestamp"
        )


def _expected_confidence(
    status: EntryPlanStatus,
    gaps: tuple[str, ...],
) -> EntryConfidence:
    if status is EntryPlanStatus.INSUFFICIENT:
        return EntryConfidence.INSUFFICIENT
    if status is EntryPlanStatus.NOT_APPLICABLE:
        return EntryConfidence.NOT_APPLICABLE
    if gaps or status in (
        EntryPlanStatus.STAGE,
        EntryPlanStatus.WAIT,
    ):
        return EntryConfidence.BOUNDED
    return EntryConfidence.SUPPORTED


def _blocks_to_dict(
    blocks: tuple[EntryConditionBlock, ...],
) -> list[dict[str, Any]]:
    return [item.to_dict() for item in blocks]


def _blocks_from_dict(
    payload: dict[str, Any],
    key: str,
) -> tuple[EntryConditionBlock, ...]:
    return tuple(
        EntryConditionBlock.from_dict(item)
        for item in payload.get(key, ())
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    model_name: str,
) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"Unknown {model_name} fields: {sorted(unknown)}"
        )


def _optional_text(value: str | None, name: str) -> None:
    if value is not None:
        _text(value, name)


def _unique_texts(
    values: tuple[str, ...],
    name: str,
) -> None:
    if any(
        not isinstance(item, str) or not item.strip()
        for item in values
    ):
        raise ValueError(
            f"{name} must contain non-blank strings"
        )
    _unique(values, name)


def _unique(values: tuple[object, ...], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
