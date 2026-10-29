"""Research artifacts: state assessments, views, settleable actions and plans."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from ._checks import (
    optional_text,
    require_date,
    require_decimal,
    require_instance,
    require_items,
    require_text,
    require_texts,
)
from .codec import from_payload, to_payload
from .requests import SubjectType
from .settlement import SettlementMethod, SettlementRule
from .tasks import ResearchHorizon


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ProbabilityInterval:
    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        require_decimal(self.low, "probability.low")
        require_decimal(self.high, "probability.high")
        if not Decimal(0) <= self.low < self.high <= Decimal(1):
            raise ValueError(
                "probability must satisfy 0 <= low < high <= 1"
            )


@dataclass(frozen=True)
class AssessmentDimension:
    name: str
    value: str
    evidence_refs: tuple[str, ...]
    historical_percentile: Decimal | None = None

    def __post_init__(self) -> None:
        require_text(self.name, "dimension.name")
        require_text(self.value, "dimension.value")
        require_texts(
            self.evidence_refs,
            "dimension.evidence_refs",
            nonempty=True,
        )
        if self.historical_percentile is not None:
            require_decimal(
                self.historical_percentile,
                "historical_percentile",
            )
            if not Decimal(0) <= self.historical_percentile <= Decimal(1):
                raise ValueError(
                    "historical_percentile must be within [0, 1]"
                )


@dataclass(frozen=True)
class StateAssessment:
    assessment_id: str
    subject_type: SubjectType
    subject_id: str
    as_of_date: str
    dimensions: tuple[AssessmentDimension, ...]
    label: str
    confidence: Confidence
    change_signals: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.assessment_id, "assessment_id")
        require_instance(self.subject_type, SubjectType, "subject_type")
        require_text(self.subject_id, "subject_id")
        require_date(self.as_of_date, "as_of_date")
        require_items(
            self.dimensions,
            AssessmentDimension,
            "dimensions",
            nonempty=True,
        )
        names = [item.name for item in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("dimension names must be unique")
        require_text(self.label, "label")
        require_instance(self.confidence, Confidence, "confidence")
        require_texts(
            self.change_signals,
            "change_signals",
            nonempty=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StateAssessment":
        return from_payload(cls, payload)


class ViewOrigin(str, Enum):
    SYSTEM = "system"
    USER = "user"


class ClaimComparison(str, Enum):
    OUTPERFORM = "outperform"
    UNDERPERFORM = "underperform"
    ABOVE = "above"
    BELOW = "below"


_RELATIVE_COMPARISONS = frozenset(
    {ClaimComparison.OUTPERFORM, ClaimComparison.UNDERPERFORM}
)


@dataclass(frozen=True)
class ViewClaim:
    """Measurable judgement; relative claims use the settlement benchmark."""

    metric_key: str
    comparison: ClaimComparison
    threshold: Decimal | None = None

    def __post_init__(self) -> None:
        require_text(self.metric_key, "claim.metric_key")
        require_instance(
            self.comparison,
            ClaimComparison,
            "claim.comparison",
        )
        if self.threshold is not None:
            require_decimal(self.threshold, "claim.threshold")
        elif self.comparison not in _RELATIVE_COMPARISONS:
            raise ValueError(
                f"{self.comparison.value} claims require a threshold"
            )


@dataclass(frozen=True)
class View:
    view_id: str
    subject_type: SubjectType
    subject_id: str
    origin: ViewOrigin
    proposition: str
    horizon: ResearchHorizon
    as_of_date: str
    expires_on: str
    confidence: Confidence
    user_id: str | None = None
    claim: ViewClaim | None = None
    support_refs: tuple[str, ...] = ()
    counter_refs: tuple[str, ...] = ()
    base_rate_ref: str | None = None
    probability: ProbabilityInterval | None = None
    falsifiers: tuple[str, ...] = ()
    implications: tuple[str, ...] = ()
    settlement_rule: SettlementRule | None = None

    def __post_init__(self) -> None:
        require_text(self.view_id, "view_id")
        require_instance(self.subject_type, SubjectType, "subject_type")
        require_text(self.subject_id, "subject_id")
        require_instance(self.origin, ViewOrigin, "origin")
        require_text(self.proposition, "proposition")
        require_instance(self.horizon, ResearchHorizon, "horizon")
        start = require_date(self.as_of_date, "as_of_date")
        if require_date(self.expires_on, "expires_on") <= start:
            raise ValueError("expires_on must be after as_of_date")
        require_instance(self.confidence, Confidence, "confidence")
        optional_text(self.user_id, "user_id")
        if self.claim is not None:
            require_instance(self.claim, ViewClaim, "claim")
        for name in (
            "support_refs",
            "counter_refs",
            "falsifiers",
            "implications",
        ):
            require_texts(getattr(self, name), name)
        optional_text(self.base_rate_ref, "base_rate_ref")
        if self.probability is not None:
            require_instance(
                self.probability,
                ProbabilityInterval,
                "probability",
            )
        if self.settlement_rule is not None:
            require_instance(
                self.settlement_rule,
                SettlementRule,
                "settlement_rule",
            )
        self._validate_origin()
        self._validate_claim()

    @property
    def directional(self) -> bool:
        return self.claim is not None

    def _validate_origin(self) -> None:
        if self.origin is ViewOrigin.USER and self.user_id is None:
            raise ValueError("user views require user_id")
        if self.origin is ViewOrigin.SYSTEM and self.user_id is not None:
            raise ValueError("system views must not carry user_id")

    def _validate_claim(self) -> None:
        rule = self.settlement_rule
        if (
            rule is not None
            and rule.method is SettlementMethod.PRICE_RELATIVE
            and self.subject_type is SubjectType.ASSET
            and rule.observed_id != self.subject_id
        ):
            raise ValueError(
                "asset views must settle on the subject asset"
            )
        if self.claim is None:
            return
        if rule is None:
            raise ValueError("directional views require settlement_rule")
        if (
            self.claim.comparison in _RELATIVE_COMPARISONS
            and rule.benchmark_id is None
        ):
            raise ValueError(
                "relative claims require a settlement benchmark"
            )
        if self.origin is ViewOrigin.SYSTEM and not self.falsifiers:
            raise ValueError(
                "system directional views require falsifiers"
            )

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "View":
        return from_payload(cls, payload)


class ActionType(str, Enum):
    BUY = "buy"
    ADD = "add"
    REDUCE = "reduce"
    EXIT = "exit"
    HOLD = "hold"


class TriggerKind(str, Enum):
    SCHEDULED = "scheduled"
    CONDITIONAL = "conditional"


@dataclass(frozen=True)
class ActionTrigger:
    kind: TriggerKind
    valid_from: str
    valid_until: str
    scheduled_on: str | None = None
    observability_key: str | None = None
    condition: str | None = None

    def __post_init__(self) -> None:
        require_instance(self.kind, TriggerKind, "trigger.kind")
        start = require_date(self.valid_from, "valid_from")
        end = require_date(self.valid_until, "valid_until")
        if end < start:
            raise ValueError("valid_until must not precede valid_from")
        optional_text(self.observability_key, "observability_key")
        optional_text(self.condition, "condition")
        if self.kind is TriggerKind.SCHEDULED:
            if self.scheduled_on is None:
                raise ValueError("scheduled triggers need scheduled_on")
            if not start <= require_date(
                self.scheduled_on,
                "scheduled_on",
            ) <= end:
                raise ValueError(
                    "scheduled_on must fall within the valid window"
                )
            if self.observability_key or self.condition:
                raise ValueError(
                    "scheduled triggers must not carry conditions"
                )
        else:
            if self.observability_key is None or self.condition is None:
                raise ValueError(
                    "conditional triggers need observability_key "
                    "and condition"
                )
            if self.scheduled_on is not None:
                raise ValueError(
                    "conditional triggers must not set scheduled_on"
                )


class QuantityRuleKind(str, Enum):
    POSITION_PLAN = "position_plan"
    USER_DEFINED = "user_defined"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class QuantityRule:
    kind: QuantityRuleKind
    reference: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        require_instance(self.kind, QuantityRuleKind, "quantity.kind")
        optional_text(self.reference, "quantity.reference")
        optional_text(self.description, "quantity.description")
        if self.kind is QuantityRuleKind.POSITION_PLAN:
            if self.reference is None:
                raise ValueError("position_plan quantity needs reference")
        elif self.reference is not None:
            raise ValueError(
                "only position_plan quantity carries reference"
            )
        elif self.description is None:
            raise ValueError(
                f"{self.kind.value} quantity needs description"
            )


@dataclass(frozen=True)
class SettleableAction:
    action_id: str
    asset_id: str
    action_type: ActionType
    horizon: ResearchHorizon
    trigger: ActionTrigger
    quantity_rule: QuantityRule
    settlement_rule: SettlementRule
    invalidation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.action_id, "action_id")
        require_text(self.asset_id, "asset_id")
        require_instance(self.action_type, ActionType, "action_type")
        require_instance(self.horizon, ResearchHorizon, "horizon")
        require_instance(self.trigger, ActionTrigger, "trigger")
        require_instance(
            self.quantity_rule,
            QuantityRule,
            "quantity_rule",
        )
        require_instance(
            self.settlement_rule,
            SettlementRule,
            "settlement_rule",
        )
        require_texts(self.invalidation_refs, "invalidation_refs")
        if (
            self.settlement_rule.method
            is not SettlementMethod.PRICE_RELATIVE
            or self.settlement_rule.observed_id != self.asset_id
        ):
            raise ValueError(
                "actions settle price_relative on their own asset"
            )

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SettleableAction":
        return from_payload(cls, payload)


@dataclass(frozen=True)
class TargetRange:
    """Target as a value range within a date window, never a point."""

    subject_id: str
    metric_key: str
    low: Decimal
    high: Decimal
    window_start: str
    window_end: str
    basis: str
    probability: ProbabilityInterval | None = None

    def __post_init__(self) -> None:
        require_text(self.subject_id, "target.subject_id")
        require_text(self.metric_key, "target.metric_key")
        require_decimal(self.low, "target.low")
        require_decimal(self.high, "target.high")
        if self.low >= self.high:
            raise ValueError("targets must be ranges with low < high")
        start = require_date(self.window_start, "window_start")
        if require_date(self.window_end, "window_end") <= start:
            raise ValueError(
                "target windows must span more than one date"
            )
        require_text(self.basis, "target.basis")
        if self.probability is not None:
            require_instance(
                self.probability,
                ProbabilityInterval,
                "target.probability",
            )


@dataclass(frozen=True)
class ReviewNode:
    review_on: str
    focus: str

    def __post_init__(self) -> None:
        require_date(self.review_on, "review_on")
        require_text(self.focus, "review.focus")


@dataclass(frozen=True)
class Plan:
    plan_id: str
    user_id: str
    as_of_date: str
    version: int
    actions: tuple[SettleableAction, ...]
    review_nodes: tuple[ReviewNode, ...]
    targets: tuple[TargetRange, ...] = ()
    # How portfolio-level conflicts were resolved, and actions left out with the reason.
    conflicts: tuple[str, ...] = ()
    excluded_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.plan_id, "plan_id")
        require_text(self.user_id, "user_id")
        require_date(self.as_of_date, "as_of_date")
        require_texts(self.conflicts, "conflicts")
        require_texts(self.excluded_actions, "excluded_actions")
        if (
            not isinstance(self.version, int)
            or isinstance(self.version, bool)
            or self.version < 1
        ):
            raise ValueError("version must be a positive integer")
        require_items(
            self.actions,
            SettleableAction,
            "actions",
            nonempty=True,
        )
        action_ids = [item.action_id for item in self.actions]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("action_id must be unique within a plan")
        require_items(
            self.review_nodes,
            ReviewNode,
            "review_nodes",
            nonempty=True,
        )
        require_items(self.targets, TargetRange, "targets")

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Plan":
        return from_payload(cls, payload)
