"""Institutional risk-budget allocation for one tradable risk sleeve."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal


ZERO = Decimal("0")
ONE = Decimal("1")
Objective = Literal["absolute_return", "relative_return"]


@dataclass(frozen=True)
class InvestmentMandate:
    mandate_id: str
    objective: Objective
    implementation_asset_id: str
    benchmark_asset_id: str
    defensive_asset_id: str | None
    target_volatility: Decimal
    max_drawdown: Decimal
    max_tracking_error: Decimal | None
    max_risk_weight: Decimal
    min_cash_weight: Decimal
    drawdown_governor_enabled: bool = False
    timing_overlay_enabled: bool = False
    volatility_floor: Decimal = Decimal("0.08")
    min_timing_multiplier: Decimal = Decimal("0.20")
    max_timing_multiplier: Decimal = Decimal("1.00")
    neutral_timing_multiplier: Decimal = Decimal("0.60")

    def __post_init__(self) -> None:
        for value, name in (
            (self.mandate_id, "mandate_id"),
            (self.implementation_asset_id, "implementation_asset_id"),
            (self.benchmark_asset_id, "benchmark_asset_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.objective not in ("absolute_return", "relative_return"):
            raise ValueError(
                "objective must be absolute_return or relative_return"
            )
        if self.defensive_asset_id is not None and (
            not isinstance(self.defensive_asset_id, str)
            or not self.defensive_asset_id.strip()
        ):
            raise ValueError(
                "defensive_asset_id must be None or non-blank"
            )
        if not isinstance(self.drawdown_governor_enabled, bool):
            raise TypeError("drawdown_governor_enabled must be bool")
        if not isinstance(self.timing_overlay_enabled, bool):
            raise TypeError("timing_overlay_enabled must be bool")
        for value, name in (
            (self.target_volatility, "target_volatility"),
            (self.max_drawdown, "max_drawdown"),
            (self.volatility_floor, "volatility_floor"),
        ):
            _decimal_between(value, name, lower_open=True)
        for value, name in (
            (self.max_risk_weight, "max_risk_weight"),
            (self.min_cash_weight, "min_cash_weight"),
            (self.min_timing_multiplier, "min_timing_multiplier"),
            (self.max_timing_multiplier, "max_timing_multiplier"),
            (
                self.neutral_timing_multiplier,
                "neutral_timing_multiplier",
            ),
        ):
            _decimal_between(value, name)
        if self.max_risk_weight + self.min_cash_weight > ONE:
            raise ValueError(
                "max_risk_weight plus min_cash_weight must not exceed 1"
            )
        if not (
            self.min_timing_multiplier
            <= self.neutral_timing_multiplier
            <= self.max_timing_multiplier
        ):
            raise ValueError(
                "neutral timing multiplier must be within timing bounds"
            )
        if self.objective == "relative_return":
            if self.max_tracking_error is None:
                raise ValueError(
                    "relative_return mandate requires max_tracking_error"
                )
            _decimal_between(
                self.max_tracking_error,
                "max_tracking_error",
                lower_open=True,
            )
        elif self.max_tracking_error is not None:
            _decimal_between(
                self.max_tracking_error,
                "max_tracking_error",
                lower_open=True,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mandate_id": self.mandate_id,
            "objective": self.objective,
            "implementation_asset_id": (
                self.implementation_asset_id
            ),
            "benchmark_asset_id": self.benchmark_asset_id,
            "defensive_asset_id": self.defensive_asset_id,
            "target_volatility": str(self.target_volatility),
            "max_drawdown": str(self.max_drawdown),
            "max_tracking_error": (
                str(self.max_tracking_error)
                if self.max_tracking_error is not None
                else None
            ),
            "max_risk_weight": str(self.max_risk_weight),
            "min_cash_weight": str(self.min_cash_weight),
            "drawdown_governor_enabled": (
                self.drawdown_governor_enabled
            ),
            "timing_overlay_enabled": (
                self.timing_overlay_enabled
            ),
            "volatility_floor": str(self.volatility_floor),
            "min_timing_multiplier": str(
                self.min_timing_multiplier
            ),
            "max_timing_multiplier": str(
                self.max_timing_multiplier
            ),
            "neutral_timing_multiplier": str(
                self.neutral_timing_multiplier
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "InvestmentMandate":
        allowed = {
            "mandate_id",
            "objective",
            "implementation_asset_id",
            "benchmark_asset_id",
            "defensive_asset_id",
            "target_volatility",
            "max_drawdown",
            "max_tracking_error",
            "max_risk_weight",
            "min_cash_weight",
            "drawdown_governor_enabled",
            "timing_overlay_enabled",
            "volatility_floor",
            "min_timing_multiplier",
            "max_timing_multiplier",
            "neutral_timing_multiplier",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Unknown InvestmentMandate fields: "
                f"{sorted(unknown)}"
            )
        tracking_error = payload.get("max_tracking_error")
        defensive_asset = payload.get("defensive_asset_id")
        if defensive_asset is not None and not isinstance(
            defensive_asset,
            str,
        ):
            raise TypeError(
                "defensive_asset_id must be a string or None"
            )
        return cls(
            mandate_id=_payload_string(payload, "mandate_id"),
            objective=payload["objective"],
            implementation_asset_id=_payload_string(
                payload,
                "implementation_asset_id",
            ),
            benchmark_asset_id=_payload_string(
                payload,
                "benchmark_asset_id",
            ),
            defensive_asset_id=(
                defensive_asset
                if defensive_asset is not None
                else None
            ),
            target_volatility=Decimal(
                str(payload["target_volatility"])
            ),
            max_drawdown=Decimal(
                str(payload["max_drawdown"])
            ),
            max_tracking_error=(
                Decimal(str(tracking_error))
                if tracking_error is not None
                else None
            ),
            max_risk_weight=Decimal(
                str(payload["max_risk_weight"])
            ),
            min_cash_weight=Decimal(
                str(payload["min_cash_weight"])
            ),
            drawdown_governor_enabled=payload.get(
                "drawdown_governor_enabled",
                False,
            ),
            timing_overlay_enabled=payload.get(
                "timing_overlay_enabled",
                False,
            ),
            volatility_floor=Decimal(
                str(payload.get("volatility_floor", "0.08"))
            ),
            min_timing_multiplier=Decimal(
                str(
                    payload.get(
                        "min_timing_multiplier",
                        "0.20",
                    )
                )
            ),
            max_timing_multiplier=Decimal(
                str(
                    payload.get(
                        "max_timing_multiplier",
                        "1.00",
                    )
                )
            ),
            neutral_timing_multiplier=Decimal(
                str(
                    payload.get(
                        "neutral_timing_multiplier",
                        "0.60",
                    )
                )
            ),
        )


@dataclass(frozen=True)
class PositionBudgetInput:
    as_of_date: str
    realized_volatility: Decimal
    current_drawdown: Decimal
    timing_score_percentile: Decimal | None
    benchmark_volatility: Decimal | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        _decimal_between(
            self.realized_volatility,
            "realized_volatility",
            lower_open=True,
        )
        if (
            not isinstance(self.current_drawdown, Decimal)
            or not self.current_drawdown.is_finite()
            or not -ONE <= self.current_drawdown <= ZERO
        ):
            raise ValueError("current_drawdown must be in [-1, 0]")
        if self.timing_score_percentile is not None:
            _decimal_between(
                self.timing_score_percentile,
                "timing_score_percentile",
            )
        if self.benchmark_volatility is not None:
            _decimal_between(
                self.benchmark_volatility,
                "benchmark_volatility",
                lower_open=True,
            )


@dataclass(frozen=True)
class PositionBudgetResult:
    as_of_date: str
    mandate_id: str
    implementation_asset_id: str
    risk_weight: Decimal
    defensive_weight: Decimal
    cash_weight: Decimal
    estimated_portfolio_volatility: Decimal
    estimated_tracking_error: Decimal | None
    binding_constraints: tuple[str, ...]
    warnings: tuple[str, ...]
    intermediate: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        for value, name in (
            (self.risk_weight, "risk_weight"),
            (self.defensive_weight, "defensive_weight"),
            (self.cash_weight, "cash_weight"),
        ):
            _decimal_between(value, name)
        if (
            self.risk_weight
            + self.defensive_weight
            + self.cash_weight
            != ONE
        ):
            raise ValueError("allocation weights must sum to 1")


class PositionBudgetCalculator:
    """Apply volatility, drawdown, timing, and tracking-error budgets."""

    def calculate(
        self,
        mandate: InvestmentMandate,
        inputs: PositionBudgetInput,
    ) -> PositionBudgetResult:
        if not isinstance(mandate, InvestmentMandate):
            raise TypeError("mandate must be InvestmentMandate")
        if not isinstance(inputs, PositionBudgetInput):
            raise TypeError("inputs must be PositionBudgetInput")

        effective_volatility = max(
            inputs.realized_volatility,
            mandate.volatility_floor,
        )
        unconstrained_vol_weight = (
            mandate.target_volatility / effective_volatility
        )
        vol_weight = min(
            mandate.max_risk_weight,
            unconstrained_vol_weight,
        )
        constraints = ["volatility_target"]
        if vol_weight == mandate.max_risk_weight:
            constraints.append("max_risk_weight")

        drawdown_multiplier = ONE
        if mandate.drawdown_governor_enabled:
            drawdown_ratio = (
                abs(inputs.current_drawdown) / mandate.max_drawdown
            )
            drawdown_multiplier = max(ZERO, ONE - drawdown_ratio)
        if mandate.drawdown_governor_enabled and drawdown_multiplier < ONE:
            constraints.append("drawdown_governor")

        timing_multiplier = ONE
        if mandate.timing_overlay_enabled:
            timing_multiplier = (
                mandate.neutral_timing_multiplier
                if inputs.timing_score_percentile is None
                else mandate.min_timing_multiplier
                + (
                    mandate.max_timing_multiplier
                    - mandate.min_timing_multiplier
                )
                * inputs.timing_score_percentile
            )
        if mandate.timing_overlay_enabled and timing_multiplier < ONE:
            constraints.append("timing_overlay")

        risk_weight = (
            vol_weight
            * drawdown_multiplier
            * timing_multiplier
        )
        risk_weight = min(
            risk_weight,
            mandate.max_risk_weight,
            ONE - mandate.min_cash_weight,
        )

        estimated_tracking_error = None
        warnings: list[str] = []
        if mandate.objective == "relative_return":
            if inputs.benchmark_volatility is None:
                raise ValueError(
                    "relative_return input requires benchmark_volatility"
                )
            estimated_tracking_error = (
                ONE - risk_weight
            ) * inputs.benchmark_volatility
            assert mandate.max_tracking_error is not None
            if estimated_tracking_error > mandate.max_tracking_error:
                warnings.append("tracking_error_budget_exceeded")

        if mandate.defensive_asset_id is None:
            defensive_weight = ZERO
            cash_weight = ONE - risk_weight
        else:
            cash_weight = mandate.min_cash_weight
            defensive_weight = ONE - risk_weight - cash_weight

        return PositionBudgetResult(
            as_of_date=inputs.as_of_date,
            mandate_id=mandate.mandate_id,
            implementation_asset_id=(
                mandate.implementation_asset_id
            ),
            risk_weight=risk_weight,
            defensive_weight=defensive_weight,
            cash_weight=cash_weight,
            estimated_portfolio_volatility=(
                risk_weight * inputs.realized_volatility
            ),
            estimated_tracking_error=estimated_tracking_error,
            binding_constraints=tuple(dict.fromkeys(constraints)),
            warnings=tuple(warnings),
            intermediate={
                "effective_volatility": effective_volatility,
                "unconstrained_vol_weight": (
                    unconstrained_vol_weight
                ),
                "vol_weight": vol_weight,
                "drawdown_multiplier": drawdown_multiplier,
                "timing_multiplier": timing_multiplier,
            },
        )


def _decimal_between(
    value: Decimal,
    name: str,
    *,
    lower_open: bool = False,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value > ONE
        or (value <= ZERO if lower_open else value < ZERO)
    ):
        bracket = "(0, 1]" if lower_open else "[0, 1]"
        raise ValueError(f"{name} must be a finite Decimal in {bracket}")


def _payload_string(
    payload: dict[str, Any],
    key: str,
) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value
