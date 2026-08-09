"""Deterministic selection of a tradable ETF for one target index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class ETFInstrumentProfile:
    asset_id: str
    standard_symbol: str
    legal_name: str
    exchange: str
    manager: str
    underlying_index_asset_id: str
    inception_date: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.asset_id, "asset_id"),
            (self.standard_symbol, "standard_symbol"),
            (self.legal_name, "legal_name"),
            (self.exchange, "exchange"),
            (self.manager, "manager"),
            (
                self.underlying_index_asset_id,
                "underlying_index_asset_id",
            ),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be blank")
        date.fromisoformat(self.inception_date)


@dataclass(frozen=True)
class ETFSelectionObservation:
    asset_id: str
    as_of_date: str
    available_at: str
    history_days: int
    median_amount_60d: Decimal
    aum: Decimal
    annual_expense_rate: Decimal
    price_tracking_error: Decimal | None
    data_coverage: Decimal
    source_refs: tuple[str, ...]
    nav_tracking_error: Decimal | None = None

    def __post_init__(self) -> None:
        date.fromisoformat(self.as_of_date)
        date.fromisoformat(self.available_at)
        if self.available_at > self.as_of_date:
            raise ValueError("available_at must not exceed as_of_date")
        if self.history_days < 0:
            raise ValueError("history_days must be non-negative")
        for value, name, positive in (
            (self.median_amount_60d, "median_amount_60d", True),
            (self.aum, "aum", True),
            (self.annual_expense_rate, "annual_expense_rate", False),
            (self.data_coverage, "data_coverage", False),
        ):
            _decimal(value, name, positive=positive)
        if self.data_coverage > ONE:
            raise ValueError("data_coverage must not exceed 1")
        if self.price_tracking_error is not None:
            _decimal(
                self.price_tracking_error,
                "price_tracking_error",
                positive=False,
            )
        if self.nav_tracking_error is not None:
            _decimal(
                self.nav_tracking_error,
                "nav_tracking_error",
                positive=False,
            )
        if not self.source_refs:
            raise ValueError("source_refs must not be empty")


@dataclass(frozen=True)
class ETFInstrumentSelectionRequest:
    benchmark_asset_id: str
    as_of_date: str
    expected_holding_days: int
    order_notional: Decimal
    max_participation_rate: Decimal = Decimal("0.05")
    min_history_days: int = 252
    min_data_coverage: Decimal = Decimal("0.95")

    def __post_init__(self) -> None:
        if not self.benchmark_asset_id.strip():
            raise ValueError("benchmark_asset_id must not be blank")
        date.fromisoformat(self.as_of_date)
        if self.expected_holding_days < 1:
            raise ValueError(
                "expected_holding_days must be positive"
            )
        if self.min_history_days < 1:
            raise ValueError("min_history_days must be positive")
        _decimal(self.order_notional, "order_notional", positive=True)
        _decimal(
            self.max_participation_rate,
            "max_participation_rate",
            positive=True,
        )
        _decimal(
            self.min_data_coverage,
            "min_data_coverage",
            positive=True,
        )
        if (
            self.max_participation_rate > ONE
            or self.min_data_coverage > ONE
        ):
            raise ValueError("rates must not exceed 1")


@dataclass(frozen=True)
class ETFSelectionCandidate:
    profile: ETFInstrumentProfile
    observation: ETFSelectionObservation | None
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    estimated_holding_fee_rate: Decimal | None


@dataclass(frozen=True)
class ETFInstrumentSelectionDecision:
    request: ETFInstrumentSelectionRequest
    selected_asset_id: str
    selected_symbol: str
    candidates: tuple[ETFSelectionCandidate, ...]
    selection_basis: tuple[str, ...]


class ETFInstrumentSelectionService:
    """Apply quality gates, then rank without an opaque weighted score."""

    def select(
        self,
        request: ETFInstrumentSelectionRequest,
        profiles: tuple[ETFInstrumentProfile, ...],
        observations: tuple[ETFSelectionObservation, ...],
    ) -> ETFInstrumentSelectionDecision:
        if not profiles:
            raise ValueError("profiles must not be empty")
        by_asset = {}
        for observation in observations:
            if observation.asset_id in by_asset:
                raise ValueError(
                    f"duplicate observation: {observation.asset_id}"
                )
            by_asset[observation.asset_id] = observation

        candidates = []
        eligible = []
        for profile in sorted(
            profiles,
            key=lambda item: item.standard_symbol,
        ):
            observation = by_asset.get(profile.asset_id)
            reasons = _exclusion_reasons(
                request,
                profile,
                observation,
            )
            fee_rate = (
                observation.annual_expense_rate
                * Decimal(request.expected_holding_days)
                / Decimal("365")
                if observation is not None
                else None
            )
            candidate = ETFSelectionCandidate(
                profile=profile,
                observation=observation,
                eligible=not reasons,
                exclusion_reasons=reasons,
                estimated_holding_fee_rate=fee_rate,
            )
            candidates.append(candidate)
            if not reasons:
                eligible.append(candidate)
        if not eligible:
            raise ValueError("no ETF candidate passed selection gates")

        long_holding = request.expected_holding_days >= 252
        if long_holding:
            eligible.sort(
                key=lambda item: (
                    item.estimated_holding_fee_rate,
                    _tracking_error_key(item),
                    -item.observation.median_amount_60d,
                    -item.observation.aum,
                    item.profile.standard_symbol,
                )
            )
            basis = (
                "exact_index_match",
                "point_in_time_data_quality",
                "order_capacity",
                "holding_period_fee",
                "nav_tracking_error",
                "price_tracking_error_fallback",
                "liquidity",
            )
        else:
            eligible.sort(
                key=lambda item: (
                    -item.observation.median_amount_60d,
                    -item.observation.aum,
                    item.estimated_holding_fee_rate,
                    _tracking_error_key(item),
                    item.profile.standard_symbol,
                )
            )
            basis = (
                "exact_index_match",
                "point_in_time_data_quality",
                "order_capacity",
                "liquidity",
                "holding_period_fee",
                "nav_tracking_error",
                "price_tracking_error",
            )
        selected = eligible[0]
        return ETFInstrumentSelectionDecision(
            request=request,
            selected_asset_id=selected.profile.asset_id,
            selected_symbol=selected.profile.standard_symbol,
            candidates=tuple(candidates),
            selection_basis=basis,
        )


def _exclusion_reasons(
    request: ETFInstrumentSelectionRequest,
    profile: ETFInstrumentProfile,
    observation: ETFSelectionObservation | None,
) -> tuple[str, ...]:
    reasons = []
    if profile.underlying_index_asset_id != request.benchmark_asset_id:
        reasons.append("underlying_index_mismatch")
    if profile.inception_date > request.as_of_date:
        reasons.append("not_incepted_as_of_date")
    if observation is None:
        reasons.append("observation_missing")
        return tuple(reasons)
    if observation.as_of_date > request.as_of_date:
        reasons.append("future_observation")
    if observation.available_at > request.as_of_date:
        reasons.append("future_availability")
    if observation.history_days < request.min_history_days:
        reasons.append("history_insufficient")
    if observation.data_coverage < request.min_data_coverage:
        reasons.append("data_coverage_insufficient")
    capacity = (
        observation.median_amount_60d
        * request.max_participation_rate
    )
    if capacity < request.order_notional:
        reasons.append("order_capacity_insufficient")
    return tuple(reasons)


def _tracking_error_key(
    candidate: ETFSelectionCandidate,
) -> Decimal:
    assert candidate.observation is not None
    if candidate.observation.nav_tracking_error is not None:
        return candidate.observation.nav_tracking_error
    return (
        candidate.observation.price_tracking_error
        if candidate.observation.price_tracking_error is not None
        else Decimal("999")
    )


def _decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool,
) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or (value <= ZERO if positive else value < ZERO)
    ):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(
            f"{name} must be a {qualifier} finite Decimal"
        )
