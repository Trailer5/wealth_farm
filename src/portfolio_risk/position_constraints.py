"""Deterministic per-asset portfolio constraint calculation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .budget import PositionBudgetResult
from .models import (
    PortfolioConstraintSnapshot,
    PortfolioValuation,
)


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class PositionConstraintCeiling:
    constraint: str
    weight: Decimal

    def __post_init__(self) -> None:
        if not self.constraint.strip():
            raise ValueError("constraint must not be blank")
        _weight(self.weight, "weight")


@dataclass(frozen=True)
class PositionConstraintResult:
    asset_id: str
    as_of_date: str
    total_portfolio_value: Decimal
    current_asset_value: Decimal
    current_weight: Decimal
    target_weight_min: Decimal
    target_weight_max: Decimal
    target_notional_min: Decimal
    target_notional_max: Decimal
    stress_loss_at_target_max: Decimal
    ceilings: tuple[PositionConstraintCeiling, ...]
    binding_constraints: tuple[str, ...]
    current_constraint_breaches: tuple[str, ...]
    feasible: bool
    conflicts: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.total_portfolio_value, "total_portfolio_value"),
            (self.current_asset_value, "current_asset_value"),
            (self.target_notional_min, "target_notional_min"),
            (self.target_notional_max, "target_notional_max"),
            (
                self.stress_loss_at_target_max,
                "stress_loss_at_target_max",
            ),
        ):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < ZERO
            ):
                raise ValueError(
                    f"{name} must be a non-negative Decimal"
                )
        if self.total_portfolio_value <= ZERO:
            raise ValueError(
                "total_portfolio_value must be positive"
            )
        for value, name in (
            (self.current_weight, "current_weight"),
            (self.target_weight_min, "target_weight_min"),
            (self.target_weight_max, "target_weight_max"),
        ):
            _weight(value, name)
        if self.feasible and (
            self.target_weight_min > self.target_weight_max
        ):
            raise ValueError(
                "feasible target range must be ordered"
            )
        if self.feasible and self.conflicts:
            raise ValueError(
                "feasible result must not contain conflicts"
            )
        if not self.feasible and not self.conflicts:
            raise ValueError(
                "infeasible result must identify conflicts"
            )


class PositionConstraintCalculator:
    """Apply formal risk, loss, concentration and execution caps."""

    def calculate(
        self,
        *,
        asset_id: str,
        valuation: PortfolioValuation,
        constraints: PortfolioConstraintSnapshot,
        budget: PositionBudgetResult,
    ) -> PositionConstraintResult:
        if constraints.subject_asset_id != asset_id:
            raise ValueError(
                "constraint snapshot subject_asset_id mismatch"
            )
        if constraints.portfolio_snapshot_id != (
            valuation.snapshot.snapshot_id
        ):
            raise ValueError(
                "constraint snapshot portfolio_snapshot_id mismatch"
            )
        if constraints.as_of_date != valuation.snapshot.as_of_date:
            raise ValueError(
                "constraint and valuation as_of_date mismatch"
            )
        if budget.as_of_date != constraints.as_of_date:
            raise ValueError(
                "budget and constraint as_of_date mismatch"
            )
        if budget.mandate_id != constraints.mandate_id:
            raise ValueError(
                "budget and constraint mandate_id mismatch"
            )

        securities_value = Decimal(
            str(valuation.snapshot.total_market_value)
        )
        total_value = securities_value + constraints.current_cash
        if total_value <= ZERO:
            raise ValueError(
                "portfolio securities plus cash must be positive"
            )
        subject_positions = [
            item
            for item in valuation.positions
            if item.asset_id == asset_id
        ]
        if len(subject_positions) > 1:
            raise ValueError(
                "portfolio contains duplicate subject positions"
            )
        current_asset_value = (
            Decimal(str(subject_positions[0].market_value))
            if subject_positions
            else ZERO
        )
        current_weight = current_asset_value / total_value

        cash_after_floor = max(
            ZERO,
            constraints.current_cash
            - constraints.min_cash_weight * total_value,
        )
        correlation_cap = (
            constraints.max_high_correlation_weight
            if constraints.observed_correlation_to_portfolio
            > constraints.max_correlation_to_portfolio
            else ONE
        )
        stress_cap = min(
            ONE,
            constraints.max_stress_loss
            / abs(constraints.stress_scenario_return),
        )
        liquidity_amount = (
            constraints.average_daily_traded_notional
            * constraints.max_liquidity_participation
            * Decimal(constraints.liquidity_horizon_days)
        )
        ceilings = (
            PositionConstraintCeiling(
                "risk_budget",
                budget.risk_weight,
            ),
            PositionConstraintCeiling(
                "single_asset_concentration",
                constraints.max_single_asset_weight,
            ),
            PositionConstraintCeiling(
                "stress_loss",
                stress_cap,
            ),
            PositionConstraintCeiling(
                "portfolio_correlation",
                correlation_cap,
            ),
            PositionConstraintCeiling(
                "liquidity",
                min(
                    ONE,
                    (
                        current_asset_value + liquidity_amount
                    )
                    / total_value,
                ),
            ),
            PositionConstraintCeiling(
                "cash_floor",
                min(
                    ONE,
                    (
                        current_asset_value + cash_after_floor
                    )
                    / total_value,
                ),
            ),
            PositionConstraintCeiling(
                "planned_deployable_cash",
                min(
                    ONE,
                    (
                        current_asset_value
                        + constraints.planned_deployable_cash
                    )
                    / total_value,
                ),
            ),
        )
        target_max = min(item.weight for item in ceilings)
        target_min = constraints.min_target_weight
        conflicts = (
            ("minimum_target_exceeds_constraint_ceiling",)
            if target_min > target_max
            else ()
        )
        binding = tuple(
            item.constraint
            for item in ceilings
            if item.weight == target_max
        )
        breaches = tuple(
            item.constraint
            for item in ceilings
            if current_weight > item.weight
        )
        return PositionConstraintResult(
            asset_id=asset_id,
            as_of_date=constraints.as_of_date,
            total_portfolio_value=total_value,
            current_asset_value=current_asset_value,
            current_weight=current_weight,
            target_weight_min=target_min,
            target_weight_max=target_max,
            target_notional_min=target_min * total_value,
            target_notional_max=target_max * total_value,
            stress_loss_at_target_max=(
                target_max
                * abs(constraints.stress_scenario_return)
                * total_value
            ),
            ceilings=ceilings,
            binding_constraints=binding,
            current_constraint_breaches=breaches,
            feasible=not conflicts,
            conflicts=conflicts,
        )


def _weight(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < ZERO
        or value > ONE
    ):
        raise ValueError(
            f"{name} must be a finite Decimal in [0, 1]"
        )
