"""Structured contracts for portfolio-aware position sizing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.portfolio_risk import PositionBudgetInput
from src.research_contracts import PositionState, ResearchHorizon


ZERO = Decimal("0")
ONE = Decimal("1")


class PositionPlanStatus(str, Enum):
    AVAILABLE = "available"
    USER_CONTEXT_REQUIRED = "user_context_required"
    UNSUPPORTED = "unsupported"


class PositionAction(str, Enum):
    NEW_ALLOCATION = "new_allocation"
    INCREASE = "increase"
    REDUCE = "reduce"
    MAINTAIN = "maintain"
    WAIT = "wait"
    NO_ACTION = "no_action"
    UNAVAILABLE = "unavailable"


class PositionAdjustmentDirection(str, Enum):
    INCREASE = "increase"
    REDUCE = "reduce"


@dataclass(frozen=True)
class PositionCalculationInput:
    asset_id: str
    as_of_date: str
    subject_price: Decimal
    realized_volatility: Decimal
    current_drawdown: Decimal
    timing_score_percentile: Decimal | None
    benchmark_volatility: Decimal | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.asset_id, "asset_id")
        date.fromisoformat(self.as_of_date)
        _positive_decimal(self.subject_price, "subject_price")
        _unit_decimal(
            self.realized_volatility,
            "realized_volatility",
            positive=True,
        )
        _signed_unit_decimal(
            self.current_drawdown,
            "current_drawdown",
        )
        if self.current_drawdown > ZERO:
            raise ValueError(
                "current_drawdown must not be positive"
            )
        if self.timing_score_percentile is not None:
            _unit_decimal(
                self.timing_score_percentile,
                "timing_score_percentile",
            )
        if self.benchmark_volatility is not None:
            _unit_decimal(
                self.benchmark_volatility,
                "benchmark_volatility",
                positive=True,
            )
        _texts(self.evidence_refs, "evidence_refs", required=True)
        _texts(self.limitations, "limitations", required=True)

    def to_budget_input(self) -> PositionBudgetInput:
        return PositionBudgetInput(
            as_of_date=self.as_of_date,
            realized_volatility=self.realized_volatility,
            current_drawdown=self.current_drawdown,
            timing_score_percentile=self.timing_score_percentile,
            benchmark_volatility=self.benchmark_volatility,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "as_of_date": self.as_of_date,
            "subject_price": str(self.subject_price),
            "realized_volatility": str(
                self.realized_volatility
            ),
            "current_drawdown": str(self.current_drawdown),
            "timing_score_percentile": _decimal_text(
                self.timing_score_percentile
            ),
            "benchmark_volatility": _decimal_text(
                self.benchmark_volatility
            ),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "PositionCalculationInput":
        _reject_unknown(
            payload,
            {
                "asset_id",
                "as_of_date",
                "subject_price",
                "realized_volatility",
                "current_drawdown",
                "timing_score_percentile",
                "benchmark_volatility",
                "evidence_refs",
                "limitations",
            },
            "PositionCalculationInput",
        )
        _payload_list(payload, "evidence_refs")
        _payload_list(payload, "limitations")
        return cls(
            asset_id=_payload_string(payload, "asset_id"),
            as_of_date=_payload_string(payload, "as_of_date"),
            subject_price=Decimal(
                str(payload["subject_price"])
            ),
            realized_volatility=Decimal(
                str(payload["realized_volatility"])
            ),
            current_drawdown=Decimal(
                str(payload["current_drawdown"])
            ),
            timing_score_percentile=_optional_decimal(
                payload.get("timing_score_percentile")
            ),
            benchmark_volatility=_optional_decimal(
                payload.get("benchmark_volatility")
            ),
            evidence_refs=tuple(
                str(item)
                for item in payload.get("evidence_refs", ())
            ),
            limitations=tuple(
                str(item)
                for item in payload.get("limitations", ())
            ),
        )


@dataclass(frozen=True)
class PositionConstraintBlock:
    constraint: str
    ceiling_weight: Decimal
    binding: bool
    current_breach: bool

    def __post_init__(self) -> None:
        _text(self.constraint, "constraint")
        _unit_decimal(self.ceiling_weight, "ceiling_weight")
        if not isinstance(self.binding, bool):
            raise TypeError("binding must be bool")
        if not isinstance(self.current_breach, bool):
            raise TypeError("current_breach must be bool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint,
            "ceiling_weight": str(self.ceiling_weight),
            "binding": self.binding,
            "current_breach": self.current_breach,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "PositionConstraintBlock":
        _reject_unknown(
            payload,
            {
                "constraint",
                "ceiling_weight",
                "binding",
                "current_breach",
            },
            "PositionConstraintBlock",
        )
        return cls(
            constraint=_payload_string(payload, "constraint"),
            ceiling_weight=Decimal(
                str(payload["ceiling_weight"])
            ),
            binding=payload["binding"],
            current_breach=payload["current_breach"],
        )


@dataclass(frozen=True)
class PositionTrancheAdjustment:
    tranche_id: str
    source_condition_id: str | None
    direction: PositionAdjustmentDirection
    target_fraction: Decimal
    notional_min: Decimal
    notional_max: Decimal
    quantity_min: Decimal
    quantity_max: Decimal

    def __post_init__(self) -> None:
        _text(self.tranche_id, "tranche_id")
        if self.source_condition_id is not None:
            _text(
                self.source_condition_id,
                "source_condition_id",
            )
        _unit_decimal(
            self.target_fraction,
            "target_fraction",
            positive=True,
        )
        for value, name in (
            (self.notional_min, "notional_min"),
            (self.notional_max, "notional_max"),
            (self.quantity_min, "quantity_min"),
            (self.quantity_max, "quantity_max"),
        ):
            _non_negative_decimal(value, name)
        if (
            self.notional_min > self.notional_max
            or self.quantity_min > self.quantity_max
        ):
            raise ValueError(
                "tranche adjustment ranges must be ordered"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tranche_id": self.tranche_id,
            "source_condition_id": self.source_condition_id,
            "direction": self.direction.value,
            "target_fraction": str(self.target_fraction),
            "notional_min": str(self.notional_min),
            "notional_max": str(self.notional_max),
            "quantity_min": str(self.quantity_min),
            "quantity_max": str(self.quantity_max),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "PositionTrancheAdjustment":
        _reject_unknown(
            payload,
            {
                "tranche_id",
                "source_condition_id",
                "direction",
                "target_fraction",
                "notional_min",
                "notional_max",
                "quantity_min",
                "quantity_max",
            },
            "PositionTrancheAdjustment",
        )
        source_condition = payload.get("source_condition_id")
        return cls(
            tranche_id=_payload_string(payload, "tranche_id"),
            source_condition_id=(
                _optional_text_value(source_condition)
                if source_condition is not None
                else None
            ),
            direction=PositionAdjustmentDirection(
                payload["direction"]
            ),
            target_fraction=Decimal(
                str(payload["target_fraction"])
            ),
            notional_min=Decimal(
                str(payload["notional_min"])
            ),
            notional_max=Decimal(
                str(payload["notional_max"])
            ),
            quantity_min=Decimal(
                str(payload["quantity_min"])
            ),
            quantity_max=Decimal(
                str(payload["quantity_max"])
            ),
        )


@dataclass(frozen=True)
class PositionProfilePolicy:
    profile_id: str
    policy_id: str
    sizing_supported: bool
    liquidity_semantics: str

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        _text(self.policy_id, "policy_id")
        _text(self.liquidity_semantics, "liquidity_semantics")
        if not isinstance(self.sizing_supported, bool):
            raise TypeError("sizing_supported must be bool")


@dataclass(frozen=True)
class PositionPlan:
    plan_id: str
    session_id: str
    workflow_run_id: str
    suitability_assessment_id: str
    entry_plan_id: str
    decision_brief_id: str | None
    asset_id: str
    profile_id: str
    policy_id: str
    as_of_date: str
    horizon: ResearchHorizon
    status: PositionPlanStatus
    action: PositionAction
    portfolio_role: str | None
    position_state: PositionState | None
    mandate_id: str | None
    portfolio_snapshot_id: str | None
    constraint_snapshot_id: str | None
    currency: str | None
    current_quantity: Decimal | None
    current_weight: Decimal | None
    target_weight_min: Decimal | None
    target_weight_max: Decimal | None
    target_notional_min: Decimal | None
    target_notional_max: Decimal | None
    risk_budget_weight: Decimal | None
    stress_loss: Decimal | None
    binding_constraints: tuple[str, ...]
    constraint_blocks: tuple[PositionConstraintBlock, ...]
    tranche_adjustments: tuple[PositionTrancheAdjustment, ...]
    calculation_inputs: PositionCalculationInput | None
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]
    assembly_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.plan_id, "plan_id"),
            (self.session_id, "session_id"),
            (self.workflow_run_id, "workflow_run_id"),
            (
                self.suitability_assessment_id,
                "suitability_assessment_id",
            ),
            (self.entry_plan_id, "entry_plan_id"),
            (self.asset_id, "asset_id"),
            (self.profile_id, "profile_id"),
            (self.policy_id, "policy_id"),
        ):
            _text(value, name)
        date.fromisoformat(self.as_of_date)
        if not self.policy_id.startswith(f"{self.profile_id}:"):
            raise ValueError(
                "position policy_id must belong to profile_id"
            )
        if self.decision_brief_id is not None:
            _text(self.decision_brief_id, "decision_brief_id")
        if self.portfolio_role is not None:
            _text(self.portfolio_role, "portfolio_role")
        for value, name in (
            (self.mandate_id, "mandate_id"),
            (
                self.portfolio_snapshot_id,
                "portfolio_snapshot_id",
            ),
            (
                self.constraint_snapshot_id,
                "constraint_snapshot_id",
            ),
            (self.currency, "currency"),
        ):
            if value is not None:
                _text(value, name)
        for value, name in (
            (self.current_quantity, "current_quantity"),
            (self.current_weight, "current_weight"),
            (self.target_weight_min, "target_weight_min"),
            (self.target_weight_max, "target_weight_max"),
            (
                self.target_notional_min,
                "target_notional_min",
            ),
            (
                self.target_notional_max,
                "target_notional_max",
            ),
            (self.risk_budget_weight, "risk_budget_weight"),
            (self.stress_loss, "stress_loss"),
        ):
            if value is not None:
                _non_negative_decimal(value, name)
        for value, name in (
            (self.current_weight, "current_weight"),
            (self.target_weight_min, "target_weight_min"),
            (self.target_weight_max, "target_weight_max"),
            (
                self.risk_budget_weight,
                "risk_budget_weight",
            ),
        ):
            if value is not None and value > ONE:
                raise ValueError(f"{name} must not exceed 1")
        _texts(
            self.binding_constraints,
            "binding_constraints",
        )
        _texts(self.evidence_refs, "evidence_refs")
        _texts(self.limitations, "limitations", required=True)
        _texts(self.assembly_gaps, "assembly_gaps")
        _validate_available(self)
        if position_plan_id(self.to_dict()) != self.plan_id:
            raise ValueError(
                "position plan_id does not match payload"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "workflow_run_id": self.workflow_run_id,
            "suitability_assessment_id": (
                self.suitability_assessment_id
            ),
            "entry_plan_id": self.entry_plan_id,
            "decision_brief_id": self.decision_brief_id,
            "asset_id": self.asset_id,
            "profile_id": self.profile_id,
            "policy_id": self.policy_id,
            "as_of_date": self.as_of_date,
            "horizon": self.horizon.value,
            "status": self.status.value,
            "action": self.action.value,
            "portfolio_role": self.portfolio_role,
            "position_state": (
                self.position_state.value
                if self.position_state is not None
                else None
            ),
            "mandate_id": self.mandate_id,
            "portfolio_snapshot_id": self.portfolio_snapshot_id,
            "constraint_snapshot_id": (
                self.constraint_snapshot_id
            ),
            "currency": self.currency,
            "current_quantity": _decimal_text(
                self.current_quantity
            ),
            "current_weight": _decimal_text(
                self.current_weight
            ),
            "target_weight_min": _decimal_text(
                self.target_weight_min
            ),
            "target_weight_max": _decimal_text(
                self.target_weight_max
            ),
            "target_notional_min": _decimal_text(
                self.target_notional_min
            ),
            "target_notional_max": _decimal_text(
                self.target_notional_max
            ),
            "risk_budget_weight": _decimal_text(
                self.risk_budget_weight
            ),
            "stress_loss": _decimal_text(self.stress_loss),
            "binding_constraints": list(
                self.binding_constraints
            ),
            "constraint_blocks": [
                item.to_dict() for item in self.constraint_blocks
            ],
            "tranche_adjustments": [
                item.to_dict() for item in self.tranche_adjustments
            ],
            "calculation_inputs": (
                self.calculation_inputs.to_dict()
                if self.calculation_inputs is not None
                else None
            ),
            "evidence_refs": list(self.evidence_refs),
            "limitations": list(self.limitations),
            "assembly_gaps": list(self.assembly_gaps),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionPlan":
        allowed = {
            "plan_id",
            "session_id",
            "workflow_run_id",
            "suitability_assessment_id",
            "entry_plan_id",
            "decision_brief_id",
            "asset_id",
            "profile_id",
            "policy_id",
            "as_of_date",
            "horizon",
            "status",
            "action",
            "portfolio_role",
            "position_state",
            "mandate_id",
            "portfolio_snapshot_id",
            "constraint_snapshot_id",
            "currency",
            "current_quantity",
            "current_weight",
            "target_weight_min",
            "target_weight_max",
            "target_notional_min",
            "target_notional_max",
            "risk_budget_weight",
            "stress_loss",
            "binding_constraints",
            "constraint_blocks",
            "tranche_adjustments",
            "calculation_inputs",
            "evidence_refs",
            "limitations",
            "assembly_gaps",
        }
        _reject_unknown(payload, allowed, "PositionPlan")
        for key in (
            "binding_constraints",
            "constraint_blocks",
            "tranche_adjustments",
            "evidence_refs",
            "limitations",
            "assembly_gaps",
        ):
            _payload_list(payload, key)
        calculation_inputs = payload.get("calculation_inputs")
        position_state = payload.get("position_state")
        return cls(
            plan_id=_payload_string(payload, "plan_id"),
            session_id=_payload_string(payload, "session_id"),
            workflow_run_id=_payload_string(
                payload,
                "workflow_run_id",
            ),
            suitability_assessment_id=_payload_string(
                payload,
                "suitability_assessment_id",
            ),
            entry_plan_id=_payload_string(
                payload,
                "entry_plan_id",
            ),
            decision_brief_id=_optional_text_value(
                payload.get("decision_brief_id")
            ),
            asset_id=_payload_string(payload, "asset_id"),
            profile_id=_payload_string(payload, "profile_id"),
            policy_id=_payload_string(payload, "policy_id"),
            as_of_date=_payload_string(payload, "as_of_date"),
            horizon=ResearchHorizon(payload["horizon"]),
            status=PositionPlanStatus(payload["status"]),
            action=PositionAction(payload["action"]),
            portfolio_role=_optional_text_value(
                payload.get("portfolio_role")
            ),
            position_state=(
                PositionState(position_state)
                if position_state is not None
                else None
            ),
            mandate_id=_optional_text_value(
                payload.get("mandate_id")
            ),
            portfolio_snapshot_id=_optional_text_value(
                payload.get("portfolio_snapshot_id")
            ),
            constraint_snapshot_id=_optional_text_value(
                payload.get("constraint_snapshot_id")
            ),
            currency=_optional_text_value(
                payload.get("currency")
            ),
            current_quantity=_optional_decimal(
                payload.get("current_quantity")
            ),
            current_weight=_optional_decimal(
                payload.get("current_weight")
            ),
            target_weight_min=_optional_decimal(
                payload.get("target_weight_min")
            ),
            target_weight_max=_optional_decimal(
                payload.get("target_weight_max")
            ),
            target_notional_min=_optional_decimal(
                payload.get("target_notional_min")
            ),
            target_notional_max=_optional_decimal(
                payload.get("target_notional_max")
            ),
            risk_budget_weight=_optional_decimal(
                payload.get("risk_budget_weight")
            ),
            stress_loss=_optional_decimal(
                payload.get("stress_loss")
            ),
            binding_constraints=tuple(
                str(item)
                for item in payload.get(
                    "binding_constraints",
                    (),
                )
            ),
            constraint_blocks=tuple(
                PositionConstraintBlock.from_dict(item)
                for item in payload.get("constraint_blocks", ())
            ),
            tranche_adjustments=tuple(
                PositionTrancheAdjustment.from_dict(item)
                for item in payload.get(
                    "tranche_adjustments",
                    (),
                )
            ),
            calculation_inputs=(
                PositionCalculationInput.from_dict(
                    calculation_inputs
                )
                if calculation_inputs is not None
                else None
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


def position_plan_id(payload: dict[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("plan_id", None)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "position:" + hashlib.sha256(encoded).hexdigest()[:24]


def _validate_available(plan: PositionPlan) -> None:
    numeric = (
        plan.current_quantity,
        plan.current_weight,
        plan.target_weight_min,
        plan.target_weight_max,
        plan.target_notional_min,
        plan.target_notional_max,
        plan.risk_budget_weight,
        plan.stress_loss,
    )
    references = (
        plan.portfolio_role,
        plan.position_state,
        plan.mandate_id,
        plan.portfolio_snapshot_id,
        plan.constraint_snapshot_id,
        plan.currency,
        plan.calculation_inputs,
    )
    if plan.status is PositionPlanStatus.AVAILABLE:
        if any(value is None for value in (*numeric, *references)):
            raise ValueError(
                "available position plan requires complete inputs"
            )
        assert plan.target_weight_min is not None
        assert plan.target_weight_max is not None
        assert plan.target_notional_min is not None
        assert plan.target_notional_max is not None
        if (
            plan.target_weight_min > plan.target_weight_max
            or plan.target_notional_min
            > plan.target_notional_max
        ):
            raise ValueError(
                "available target ranges must be ordered"
            )
        if plan.assembly_gaps:
            raise ValueError(
                "available position plan cannot retain gaps"
            )
        if not plan.evidence_refs:
            raise ValueError(
                "available position plan requires evidence_refs"
            )
        if not plan.constraint_blocks:
            raise ValueError(
                "available position plan requires constraints"
            )
        if not plan.binding_constraints:
            raise ValueError(
                "available position plan requires binding constraints"
            )
        if plan.action is PositionAction.UNAVAILABLE:
            raise ValueError(
                "available plan cannot use unavailable action"
            )
        _validate_available_semantics(plan)
    else:
        if not plan.assembly_gaps:
            raise ValueError(
                "unavailable position plan requires gaps"
            )
        if plan.action is not PositionAction.UNAVAILABLE:
            raise ValueError(
                "unavailable status requires unavailable action"
            )
        if any(value is not None for value in numeric):
            raise ValueError(
                "unavailable plan cannot expose calculated values"
            )
        if plan.constraint_blocks or plan.tranche_adjustments:
            raise ValueError(
                "unavailable plan cannot expose adjustments"
            )


def _validate_available_semantics(plan: PositionPlan) -> None:
    constraint_names = tuple(
        item.constraint for item in plan.constraint_blocks
    )
    if len(constraint_names) != len(set(constraint_names)):
        raise ValueError("position constraints must be unique")
    expected_binding = tuple(
        item.constraint
        for item in plan.constraint_blocks
        if item.binding
    )
    if set(expected_binding) != set(plan.binding_constraints):
        raise ValueError(
            "binding_constraints must match constraint blocks"
        )
    assert plan.position_state is not None
    assert plan.current_quantity is not None
    assert plan.calculation_inputs is not None
    if plan.position_state is PositionState.UNKNOWN:
        raise ValueError(
            "available plan requires known position state"
        )
    if (
        plan.calculation_inputs.asset_id != plan.asset_id
        or plan.calculation_inputs.as_of_date != plan.as_of_date
    ):
        raise ValueError(
            "calculation input identity must match position plan"
        )
    if (
        plan.position_state is PositionState.NOT_HELD
        and plan.current_quantity != ZERO
    ) or (
        plan.position_state is PositionState.HELD
        and plan.current_quantity == ZERO
    ):
        raise ValueError(
            "position state conflicts with current quantity"
        )
    if (
        plan.action is PositionAction.NEW_ALLOCATION
        and plan.position_state is not PositionState.NOT_HELD
    ):
        raise ValueError(
            "new allocation requires a not-held position"
        )
    if plan.action in (
        PositionAction.INCREASE,
        PositionAction.REDUCE,
        PositionAction.MAINTAIN,
    ) and plan.position_state is not PositionState.HELD:
        raise ValueError(
            "held-position action requires a held position"
        )
    tranche_ids = tuple(
        item.tranche_id for item in plan.tranche_adjustments
    )
    if len(tranche_ids) != len(set(tranche_ids)):
        raise ValueError("tranche ids must be unique")
    source_ids = tuple(
        item.source_condition_id
        for item in plan.tranche_adjustments
        if item.source_condition_id is not None
    )
    if len(source_ids) != len(set(source_ids)):
        raise ValueError(
            "tranche source condition ids must be unique"
        )
    if (
        plan.action
        in (
            PositionAction.NEW_ALLOCATION,
            PositionAction.INCREASE,
            PositionAction.REDUCE,
        )
        and not plan.tranche_adjustments
    ):
        raise ValueError(
            "adjustment action requires tranche amounts"
        )
    if not plan.tranche_adjustments:
        return
    if plan.action not in (
        PositionAction.NEW_ALLOCATION,
        PositionAction.INCREASE,
        PositionAction.REDUCE,
    ):
        raise ValueError(
            "non-adjustment action cannot contain tranches"
        )
    if sum(
        (
            item.target_fraction
            for item in plan.tranche_adjustments
        ),
        ZERO,
    ) != ONE:
        raise ValueError("tranche fractions must sum to 1")
    expected_direction = (
        PositionAdjustmentDirection.REDUCE
        if plan.action is PositionAction.REDUCE
        else PositionAdjustmentDirection.INCREASE
    )
    if any(
        item.direction is not expected_direction
        for item in plan.tranche_adjustments
    ):
        raise ValueError(
            "tranche direction conflicts with position action"
        )
    assert plan.target_notional_min is not None
    assert plan.target_notional_max is not None
    current_notional = (
        plan.current_quantity
        * plan.calculation_inputs.subject_price
    )
    if plan.action is PositionAction.REDUCE:
        expected_min = max(
            ZERO,
            current_notional - plan.target_notional_max,
        )
        expected_max = max(
            ZERO,
            current_notional - plan.target_notional_min,
        )
    else:
        expected_min = max(
            ZERO,
            plan.target_notional_min - current_notional,
        )
        expected_max = max(
            ZERO,
            plan.target_notional_max - current_notional,
        )
    if sum(
        (item.notional_min for item in plan.tranche_adjustments),
        ZERO,
    ) != expected_min or sum(
        (item.notional_max for item in plan.tranche_adjustments),
        ZERO,
    ) != expected_max:
        raise ValueError(
            "tranche notionals do not match target adjustment"
        )
    price = plan.calculation_inputs.subject_price
    if any(
        item.notional_min
        != expected_min * item.target_fraction
        or item.notional_max
        != expected_max * item.target_fraction
        or item.quantity_min != item.notional_min / price
        or item.quantity_max != item.notional_max / price
        for item in plan.tranche_adjustments
    ):
        raise ValueError(
            "each tranche must match its fraction and price"
        )


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _optional_decimal(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _optional_text_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional text value must be a string")
    return value


def _payload_string(
    payload: dict[str, Any],
    key: str,
) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _payload_list(
    payload: dict[str, Any],
    key: str,
) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return value


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    name: str,
) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unknown {name} fields: {sorted(unknown)}")


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _texts(
    values: tuple[str, ...],
    name: str,
    *,
    required: bool = False,
) -> None:
    if required and not values:
        raise ValueError(f"{name} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _non_negative_decimal(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < ZERO
    ):
        raise ValueError(
            f"{name} must be a finite non-negative Decimal"
        )


def _positive_decimal(value: Decimal, name: str) -> None:
    _non_negative_decimal(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be positive")


def _unit_decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool = False,
) -> None:
    _non_negative_decimal(value, name)
    if value > ONE or (positive and value <= ZERO):
        bracket = "(0, 1]" if positive else "[0, 1]"
        raise ValueError(f"{name} must be in {bracket}")


def _signed_unit_decimal(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < -ONE
        or value > ONE
    ):
        raise ValueError(f"{name} must be in [-1, 1]")
