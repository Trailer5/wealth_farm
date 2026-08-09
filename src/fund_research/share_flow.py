"""ETF share-change and estimated subscription calculations."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.factor_engineering import FactorValue, SQLiteFactorRepository


EXCHANGE_FUND_FLOW_VERSION = "exchange_fund_flow_v1"
_SUPPORTED_FACTORS = frozenset(
    {"etf_share_change", "estimated_net_subscription"}
)


@dataclass(frozen=True)
class ExchangeFundShareRecord:
    asset_id: str
    stat_date: str
    shares: Decimal
    source: str
    split_adjusted: bool = False

    def __post_init__(self) -> None:
        _validate_identity(self.asset_id, self.source)
        _validate_iso_date(self.stat_date, "stat_date")
        _validate_positive_decimal(self.shares, "shares")
        if not isinstance(self.split_adjusted, bool):
            raise TypeError("split_adjusted must be a bool")


@dataclass(frozen=True)
class ExchangeFundNavRecord:
    asset_id: str
    nav_date: str
    unit_nav: Decimal
    source: str
    estimated: bool = True

    def __post_init__(self) -> None:
        _validate_identity(self.asset_id, self.source)
        _validate_iso_date(self.nav_date, "nav_date")
        _validate_positive_decimal(self.unit_nav, "unit_nav")
        if not isinstance(self.estimated, bool):
            raise TypeError("estimated must be a bool")


@dataclass(frozen=True)
class BroadETFFlowValue:
    basket_id: str
    factor: str
    as_of: str
    value: float
    version: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.basket_id.strip():
            raise ValueError("basket_id must not be blank")
        if self.factor not in _SUPPORTED_FACTORS:
            raise ValueError(f"Unsupported ETF flow factor: {self.factor}")
        _validate_iso_date(self.as_of, "as_of")
        if self.version != EXCHANGE_FUND_FLOW_VERSION:
            raise ValueError(
                f"version must be {EXCHANGE_FUND_FLOW_VERSION}"
            )
        if not math.isfinite(self.value):
            raise ValueError("value must be finite")


class ExchangeFundFlowService:
    """Calculate ETF flow factors without presenting estimates as official."""

    def __init__(
        self,
        factor_repository: SQLiteFactorRepository | None = None,
    ) -> None:
        self.factor_repository = factor_repository
        if self.factor_repository is not None:
            self.factor_repository.initialize()

    def calculate(
        self,
        asset_id: str,
        as_of_date: str,
        share_records: Sequence[ExchangeFundShareRecord],
        nav_records: Sequence[ExchangeFundNavRecord],
    ) -> tuple[FactorValue, ...]:
        if not asset_id.strip():
            raise ValueError("asset_id must not be blank")
        _validate_iso_date(as_of_date, "as_of_date")
        self._validate_asset_ids(
            asset_id,
            share_records,
            nav_records,
        )

        shares_by_date = _unique_by_date(
            (
                record
                for record in share_records
                if record.stat_date <= as_of_date
            ),
            date_attribute="stat_date",
            label="share",
        )
        ordered_shares = sorted(
            shares_by_date.values(),
            key=lambda record: record.stat_date,
        )
        if len(ordered_shares) < 2:
            return ()

        prior, current = ordered_shares[-2:]
        if prior.split_adjusted != current.split_adjusted:
            raise ValueError(
                "Adjacent share records have inconsistent split_adjusted flags"
            )

        nav_by_date = _unique_by_date(
            (
                record
                for record in nav_records
                if record.nav_date <= current.stat_date
            ),
            date_attribute="nav_date",
            label="NAV",
        )
        nav = (
            max(nav_by_date.values(), key=lambda record: record.nav_date)
            if nav_by_date
            else None
        )
        computed_at = datetime.now(timezone.utc).isoformat()
        share_delta = current.shares - prior.shares
        common_parameters: dict[str, Any] = {
            "estimated": True,
            "share_stat_date": current.stat_date,
            "nav_ref_date": nav.nav_date if nav is not None else None,
            "nav_ref_source": nav.source if nav is not None else None,
            "split_adjusted": current.split_adjusted,
        }
        share_sources = _source_dataset(prior.source, current.source)
        values = [
            FactorValue(
                asset_id=asset_id,
                factor_name="etf_share_change",
                as_of_date=as_of_date,
                value=float(share_delta / prior.shares),
                calculation_version=EXCHANGE_FUND_FLOW_VERSION,
                parameters={
                    **common_parameters,
                    "unit": "ratio",
                    "estimation_method": "share_change_ratio",
                },
                input_start_date=prior.stat_date,
                input_end_date=current.stat_date,
                source_dataset=share_sources,
                computed_at=computed_at,
            )
        ]
        if nav is not None:
            values.append(
                FactorValue(
                    asset_id=asset_id,
                    factor_name="estimated_net_subscription",
                    as_of_date=as_of_date,
                    value=float(share_delta * nav.unit_nav),
                    calculation_version=EXCHANGE_FUND_FLOW_VERSION,
                    parameters={
                        **common_parameters,
                        "unit": "currency_amount",
                        "estimation_method": (
                            "share_delta_times_latest_available_nav"
                        ),
                    },
                    input_start_date=prior.stat_date,
                    input_end_date=current.stat_date,
                    source_dataset=_source_dataset(
                        prior.source,
                        current.source,
                        nav.source,
                    ),
                    computed_at=computed_at,
                )
            )

        result = tuple(values)
        if self.factor_repository is not None:
            self.factor_repository.upsert_values(list(result))
        return result

    @staticmethod
    def aggregate_broad_basket(
        basket_id: str,
        as_of_date: str,
        member_values: Sequence[FactorValue],
        weights: Mapping[str, Decimal | float | int | str],
    ) -> BroadETFFlowValue:
        if not basket_id.strip():
            raise ValueError("basket_id must not be blank")
        _validate_iso_date(as_of_date, "as_of_date")
        if not member_values:
            raise ValueError("member_values must not be empty")

        by_asset = {value.asset_id: value for value in member_values}
        if len(by_asset) != len(member_values):
            raise ValueError("member_values must contain unique asset_id values")
        factors = {value.factor_name for value in member_values}
        if len(factors) != 1:
            raise ValueError("All member values must use the same factor")
        factor = next(iter(factors))
        if factor not in _SUPPORTED_FACTORS:
            raise ValueError(f"Unsupported ETF flow factor: {factor}")
        if any(
            value.calculation_version != EXCHANGE_FUND_FLOW_VERSION
            for value in member_values
        ):
            raise ValueError(
                "Member values must use exchange_fund_flow_v1"
            )
        if any(value.as_of_date > as_of_date for value in member_values):
            raise ValueError("Member value exceeds basket as_of_date")

        member_ids = set(by_asset)
        weight_ids = set(weights)
        if member_ids != weight_ids:
            missing_weights = sorted(member_ids - weight_ids)
            missing_values = sorted(weight_ids - member_ids)
            raise ValueError(
                "Weights must exactly cover all members; "
                f"missing_weights={missing_weights}, "
                f"missing_values={missing_values}"
            )

        decimal_weights = {
            asset_id: _coerce_weight(weight)
            for asset_id, weight in weights.items()
        }
        if any(weight < 0 for weight in decimal_weights.values()):
            raise ValueError("Basket weights must be non-negative")
        if sum(decimal_weights.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("Basket weights must sum to 1")

        weighted_value = sum(
            (
                Decimal(str(by_asset[asset_id].value)) * weight
                for asset_id, weight in decimal_weights.items()
            ),
            Decimal("0"),
        )
        members = sorted(member_ids)
        return BroadETFFlowValue(
            basket_id=basket_id,
            factor=factor,
            as_of=as_of_date,
            value=float(weighted_value),
            version=EXCHANGE_FUND_FLOW_VERSION,
            parameters={
                "members": members,
                "member_count": len(members),
                "coverage_rate": 1.0,
                "weights": {
                    asset_id: format(decimal_weights[asset_id], "f")
                    for asset_id in members
                },
            },
        )

    @staticmethod
    def _validate_asset_ids(
        asset_id: str,
        share_records: Sequence[ExchangeFundShareRecord],
        nav_records: Sequence[ExchangeFundNavRecord],
    ) -> None:
        if any(record.asset_id != asset_id for record in share_records):
            raise ValueError("Share records must match asset_id")
        if any(record.asset_id != asset_id for record in nav_records):
            raise ValueError("NAV records must match asset_id")


def _validate_identity(asset_id: str, source: str) -> None:
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise ValueError("asset_id must not be blank")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source must not be blank")


def _validate_iso_date(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc


def _validate_positive_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a positive finite Decimal")


def _coerce_weight(value: Decimal | float | int | str) -> Decimal:
    try:
        weight = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Basket weights must be numeric") from exc
    if not weight.is_finite():
        raise ValueError("Basket weights must be finite")
    return weight


def _source_dataset(*sources: str) -> str:
    return ",".join(dict.fromkeys(sources))


def _unique_by_date(
    records: Iterable[Any],
    *,
    date_attribute: str,
    label: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for record in records:
        value_date = getattr(record, date_attribute)
        if value_date in result:
            raise ValueError(
                f"duplicate {label} date: {value_date}"
            )
        result[value_date] = record
    return result
