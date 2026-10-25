"""Current portfolio valuation and concentration models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import math
import json
from typing import Any


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_id: str
    user_id: str
    as_of_date: str
    total_market_value: float
    total_cost_value: float | None
    unrealized_pnl: float | None
    max_weight: float
    concentration_hhi: float
    base_currency: str = "CNY"
    holdings_as_of_date: str | None = None
    evidence_refs: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.snapshot_id, "snapshot_id"),
            (self.user_id, "user_id"),
            (self.base_currency, "base_currency"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        date.fromisoformat(self.as_of_date)
        if self.holdings_as_of_date is not None:
            date.fromisoformat(self.holdings_as_of_date)
        if any(
            not isinstance(ref, str) or not ref.strip()
            for ref in self.evidence_refs
        ):
            raise ValueError(
                "portfolio evidence_refs must be non-blank"
            )
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError(
                "portfolio evidence_refs must be unique"
            )
        if self.created_at:
            datetime.fromisoformat(
                self.created_at.replace("Z", "+00:00")
            )
        for value, name in (
            (self.total_market_value, "total_market_value"),
            (self.max_weight, "max_weight"),
            (self.concentration_hhi, "concentration_hhi"),
        ):
            _validate_finite_float(value, name, non_negative=True)
        for value, name in (
            (self.total_cost_value, "total_cost_value"),
            (self.unrealized_pnl, "unrealized_pnl"),
        ):
            if value is not None:
                _validate_finite_float(
                    value,
                    name,
                    non_negative=(
                        name == "total_cost_value"
                    ),
                )
        if self.max_weight > 1 or self.concentration_hhi > 1:
            raise ValueError(
                "portfolio weights and HHI must not exceed 1"
            )


@dataclass(frozen=True)
class PortfolioPositionMetric:
    snapshot_id: str
    asset_id: str
    quantity: float
    market_price: float
    market_value: float
    cost_value: float | None
    unrealized_pnl: float | None
    weight: float

    def __post_init__(self) -> None:
        for value, name in (
            (self.snapshot_id, "snapshot_id"),
            (self.asset_id, "asset_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        for value, name in (
            (self.quantity, "quantity"),
            (self.market_price, "market_price"),
            (self.market_value, "market_value"),
            (self.weight, "weight"),
        ):
            _validate_finite_float(value, name, non_negative=True)
        if self.market_price <= 0:
            raise ValueError("market_price must be positive")
        if self.weight > 1:
            raise ValueError("weight must not exceed 1")
        for value, name in (
            (self.cost_value, "cost_value"),
            (self.unrealized_pnl, "unrealized_pnl"),
        ):
            if value is not None:
                _validate_finite_float(
                    value,
                    name,
                    non_negative=(name == "cost_value"),
                )


@dataclass(frozen=True)
class PortfolioValuation:
    snapshot: PortfolioSnapshot
    positions: tuple[PortfolioPositionMetric, ...]

    def __post_init__(self) -> None:
        if any(
            item.snapshot_id != self.snapshot.snapshot_id
            for item in self.positions
        ):
            raise ValueError(
                "position snapshot_id must match portfolio snapshot"
            )
        asset_ids = tuple(item.asset_id for item in self.positions)
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError(
                "portfolio positions must have unique asset_ids"
            )
        if self.positions and not math.isclose(
            sum(item.market_value for item in self.positions),
            self.snapshot.total_market_value,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "position values must sum to portfolio total"
            )
        if self.positions and not math.isclose(
            sum(item.weight for item in self.positions),
            1.0,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("position weights must sum to 1")


@dataclass(frozen=True)
class PortfolioConstraintSnapshot:
    """User-confirmed constraints frozen for one sizing decision."""

    snapshot_id: str
    user_id: str
    mandate_id: str
    portfolio_snapshot_id: str
    portfolio_valuation_digest: str
    subject_asset_id: str
    as_of_date: str
    currency: str
    current_cash: Decimal
    planned_deployable_cash: Decimal
    min_target_weight: Decimal
    max_single_asset_weight: Decimal
    min_cash_weight: Decimal
    max_stress_loss: Decimal
    stress_scenario_return: Decimal
    max_correlation_to_portfolio: Decimal
    observed_correlation_to_portfolio: Decimal
    max_high_correlation_weight: Decimal
    average_daily_traded_notional: Decimal
    max_liquidity_participation: Decimal
    liquidity_horizon_days: int
    tranche_fractions: tuple[Decimal, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        for value, name in (
            (self.snapshot_id, "snapshot_id"),
            (self.user_id, "user_id"),
            (self.mandate_id, "mandate_id"),
            (
                self.portfolio_snapshot_id,
                "portfolio_snapshot_id",
            ),
            (
                self.portfolio_valuation_digest,
                "portfolio_valuation_digest",
            ),
            (self.subject_asset_id, "subject_asset_id"),
            (self.currency, "currency"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        date.fromisoformat(self.as_of_date)
        if not isinstance(self.created_at, str) or not self.created_at.strip():
            raise ValueError("created_at must not be blank")
        datetime.fromisoformat(
            self.created_at.replace("Z", "+00:00")
        )
        for value, name in (
            (self.current_cash, "current_cash"),
            (
                self.planned_deployable_cash,
                "planned_deployable_cash",
            ),
            (
                self.average_daily_traded_notional,
                "average_daily_traded_notional",
            ),
        ):
            _validate_non_negative_decimal(value, name)
        if self.average_daily_traded_notional <= 0:
            raise ValueError(
                "average_daily_traded_notional must be positive"
            )
        if self.planned_deployable_cash > self.current_cash:
            raise ValueError(
                "planned_deployable_cash must not exceed current_cash"
            )
        for value, name in (
            (self.min_target_weight, "min_target_weight"),
            (
                self.max_single_asset_weight,
                "max_single_asset_weight",
            ),
            (self.min_cash_weight, "min_cash_weight"),
            (self.max_stress_loss, "max_stress_loss"),
            (
                self.max_correlation_to_portfolio,
                "max_correlation_to_portfolio",
            ),
            (
                self.max_high_correlation_weight,
                "max_high_correlation_weight",
            ),
            (
                self.max_liquidity_participation,
                "max_liquidity_participation",
            ),
        ):
            _validate_weight(value, name)
        if self.max_single_asset_weight <= 0:
            raise ValueError(
                "max_single_asset_weight must be positive"
            )
        if self.max_stress_loss <= 0:
            raise ValueError("max_stress_loss must be positive")
        if self.max_liquidity_participation <= 0:
            raise ValueError(
                "max_liquidity_participation must be positive"
            )
        if self.min_target_weight > self.max_single_asset_weight:
            raise ValueError(
                "min_target_weight must not exceed "
                "max_single_asset_weight"
            )
        _validate_signed_unit_decimal(
            self.stress_scenario_return,
            "stress_scenario_return",
        )
        if self.stress_scenario_return >= 0:
            raise ValueError(
                "stress_scenario_return must be negative"
            )
        _validate_signed_unit_decimal(
            self.observed_correlation_to_portfolio,
            "observed_correlation_to_portfolio",
        )
        if (
            not isinstance(self.liquidity_horizon_days, int)
            or isinstance(self.liquidity_horizon_days, bool)
            or self.liquidity_horizon_days <= 0
        ):
            raise ValueError(
                "liquidity_horizon_days must be a positive integer"
            )
        for fraction in self.tranche_fractions:
            _validate_weight(fraction, "tranche_fraction")
            if fraction <= 0:
                raise ValueError(
                    "tranche fractions must be positive"
                )
        if self.tranche_fractions and sum(
            self.tranche_fractions,
            Decimal("0"),
        ) != Decimal("1"):
            raise ValueError("tranche fractions must sum to 1")
        if not self.evidence_refs or any(
            not isinstance(ref, str) or not ref.strip()
            for ref in self.evidence_refs
        ):
            raise ValueError(
                "evidence_refs must contain non-blank references"
            )
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "user_id": self.user_id,
            "mandate_id": self.mandate_id,
            "portfolio_snapshot_id": self.portfolio_snapshot_id,
            "portfolio_valuation_digest": (
                self.portfolio_valuation_digest
            ),
            "subject_asset_id": self.subject_asset_id,
            "as_of_date": self.as_of_date,
            "currency": self.currency,
            "current_cash": str(self.current_cash),
            "planned_deployable_cash": str(
                self.planned_deployable_cash
            ),
            "min_target_weight": str(self.min_target_weight),
            "max_single_asset_weight": str(
                self.max_single_asset_weight
            ),
            "min_cash_weight": str(self.min_cash_weight),
            "max_stress_loss": str(self.max_stress_loss),
            "stress_scenario_return": str(
                self.stress_scenario_return
            ),
            "max_correlation_to_portfolio": str(
                self.max_correlation_to_portfolio
            ),
            "observed_correlation_to_portfolio": str(
                self.observed_correlation_to_portfolio
            ),
            "max_high_correlation_weight": str(
                self.max_high_correlation_weight
            ),
            "average_daily_traded_notional": str(
                self.average_daily_traded_notional
            ),
            "max_liquidity_participation": str(
                self.max_liquidity_participation
            ),
            "liquidity_horizon_days": (
                self.liquidity_horizon_days
            ),
            "tranche_fractions": [
                str(value) for value in self.tranche_fractions
            ],
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "PortfolioConstraintSnapshot":
        allowed = {
            "snapshot_id",
            "user_id",
            "mandate_id",
            "portfolio_snapshot_id",
            "portfolio_valuation_digest",
            "subject_asset_id",
            "as_of_date",
            "currency",
            "current_cash",
            "planned_deployable_cash",
            "min_target_weight",
            "max_single_asset_weight",
            "min_cash_weight",
            "max_stress_loss",
            "stress_scenario_return",
            "max_correlation_to_portfolio",
            "observed_correlation_to_portfolio",
            "max_high_correlation_weight",
            "average_daily_traded_notional",
            "max_liquidity_participation",
            "liquidity_horizon_days",
            "tranche_fractions",
            "evidence_refs",
            "created_at",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(
                "Unknown PortfolioConstraintSnapshot fields: "
                f"{sorted(unknown)}"
            )
        for key in (
            "snapshot_id",
            "user_id",
            "mandate_id",
            "portfolio_snapshot_id",
            "portfolio_valuation_digest",
            "subject_asset_id",
            "as_of_date",
            "currency",
            "created_at",
        ):
            _require_payload_type(payload, key, str)
        for key in ("tranche_fractions", "evidence_refs"):
            value = payload.get(key, [])
            if not isinstance(value, list):
                raise TypeError(f"{key} must be a list")
        horizon = payload["liquidity_horizon_days"]
        if isinstance(horizon, bool) or not isinstance(horizon, int):
            raise TypeError(
                "liquidity_horizon_days must be an integer"
            )
        return cls(
            snapshot_id=payload["snapshot_id"],
            user_id=payload["user_id"],
            mandate_id=payload["mandate_id"],
            portfolio_snapshot_id=payload[
                "portfolio_snapshot_id"
            ],
            portfolio_valuation_digest=payload[
                "portfolio_valuation_digest"
            ],
            subject_asset_id=payload["subject_asset_id"],
            as_of_date=payload["as_of_date"],
            currency=payload["currency"],
            current_cash=Decimal(str(payload["current_cash"])),
            planned_deployable_cash=Decimal(
                str(payload["planned_deployable_cash"])
            ),
            min_target_weight=Decimal(
                str(payload["min_target_weight"])
            ),
            max_single_asset_weight=Decimal(
                str(payload["max_single_asset_weight"])
            ),
            min_cash_weight=Decimal(
                str(payload["min_cash_weight"])
            ),
            max_stress_loss=Decimal(
                str(payload["max_stress_loss"])
            ),
            stress_scenario_return=Decimal(
                str(payload["stress_scenario_return"])
            ),
            max_correlation_to_portfolio=Decimal(
                str(payload["max_correlation_to_portfolio"])
            ),
            observed_correlation_to_portfolio=Decimal(
                str(
                    payload[
                        "observed_correlation_to_portfolio"
                    ]
                )
            ),
            max_high_correlation_weight=Decimal(
                str(payload["max_high_correlation_weight"])
            ),
            average_daily_traded_notional=Decimal(
                str(payload["average_daily_traded_notional"])
            ),
            max_liquidity_participation=Decimal(
                str(payload["max_liquidity_participation"])
            ),
            liquidity_horizon_days=int(
                payload["liquidity_horizon_days"]
            ),
            tranche_fractions=tuple(
                Decimal(str(value))
                for value in payload.get(
                    "tranche_fractions",
                    [],
                )
            ),
            evidence_refs=tuple(
                value for value in payload.get("evidence_refs", [])
            ),
            created_at=payload["created_at"],
        )


def _validate_non_negative_decimal(
    value: Decimal,
    name: str,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
    ):
        raise ValueError(
            f"{name} must be a finite non-negative Decimal"
        )


def portfolio_valuation_digest(
    valuation: PortfolioValuation,
) -> str:
    snapshot = valuation.snapshot
    payload = {
        "snapshot_id": snapshot.snapshot_id,
        "user_id": snapshot.user_id,
        "as_of_date": snapshot.as_of_date,
        "total_market_value": snapshot.total_market_value,
        "total_cost_value": snapshot.total_cost_value,
        "unrealized_pnl": snapshot.unrealized_pnl,
        "max_weight": snapshot.max_weight,
        "concentration_hhi": snapshot.concentration_hhi,
        "base_currency": snapshot.base_currency,
        "holdings_as_of_date": snapshot.holdings_as_of_date,
        "evidence_refs": list(snapshot.evidence_refs),
        "positions": [
            {
                "asset_id": item.asset_id,
                "quantity": item.quantity,
                "market_price": item.market_price,
                "market_value": item.market_value,
                "cost_value": item.cost_value,
                "unrealized_pnl": item.unrealized_pnl,
                "weight": item.weight,
            }
            for item in valuation.positions
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_weight(value: Decimal, name: str) -> None:
    _validate_non_negative_decimal(value, name)
    if value > 1:
        raise ValueError(f"{name} must not exceed 1")


def _validate_signed_unit_decimal(
    value: Decimal,
    name: str,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < -1
        or value > 1
    ):
        raise ValueError(
            f"{name} must be a finite Decimal in [-1, 1]"
        )


def _validate_finite_float(
    value: float,
    name: str,
    *,
    non_negative: bool,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or (non_negative and value < 0)
    ):
        qualifier = " non-negative" if non_negative else ""
        raise ValueError(
            f"{name} must be a finite{qualifier} number"
        )


def _require_payload_type(
    payload: dict[str, Any],
    key: str,
    expected: type,
) -> None:
    if not isinstance(payload[key], expected):
        raise TypeError(
            f"{key} must be {expected.__name__}"
        )
