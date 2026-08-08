"""Pure domain calculations for market events and disclosed capital flow."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .models import FactorValue


MARKET_EVENT_VERSION = "market_event_v1"
CAPITAL_FLOW_VERSION = "capital_flow_v1"
NORTHBOUND_REGIME_BREAK = date(2024, 8, 19)

MARKET_ACTIVITY_TYPES = frozenset(
    {
        "lhb_listing",
        "block_trade",
        "buyback",
        "holding_change",
        "lock_up_release",
    }
)
CAPITAL_FLOW_SCOPES = frozenset(
    {"northbound", "southbound", "state_holder"}
)
CAPITAL_FLOW_REGIMES = frozenset(
    {
        "daily_net_flow",
        "daily_net_flow_pre_20240819",
        "daily_turnover_post_20240819",
        "quarterly_holding",
        "disclosed_holder",
    }
)


@dataclass(frozen=True)
class MarketActivityRecord:
    """A source-observed event; ``published_date`` is its usable date."""

    asset_id: str
    activity_type: str
    trade_date: str
    published_date: str
    source: str
    amount: float | None = None
    net_buy_amount: float | None = None
    institution_net_buy_amount: float | None = None
    broker_branch_net_buy_amount: float | None = None
    turnover_amount: float | None = None
    trade_price: float | None = None
    reference_close: float | None = None
    float_market_cap: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("asset_id", self.asset_id)
        _require_choice(
            "activity_type",
            self.activity_type,
            MARKET_ACTIVITY_TYPES,
        )
        _require_iso_date("trade_date", self.trade_date)
        _require_iso_date("published_date", self.published_date)
        _require_text("source", self.source)
        for name in (
            "amount",
            "turnover_amount",
            "trade_price",
            "reference_close",
            "float_market_cap",
        ):
            _require_number(name, getattr(self, name), non_negative=True)
        for name in (
            "net_buy_amount",
            "institution_net_buy_amount",
            "broker_branch_net_buy_amount",
        ):
            _require_number(name, getattr(self, name), non_negative=False)
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict")
        object.__setattr__(self, "metadata", dict(self.metadata))


class MarketEventFactorCalculator:
    """Aggregate visible event records over an inclusive calendar window."""

    def calculate(
        self,
        asset_id: str,
        records: Iterable[MarketActivityRecord],
        as_of_date: str,
        window_days: int,
    ) -> list[FactorValue]:
        _require_text("asset_id", asset_id)
        as_of = _require_iso_date("as_of_date", as_of_date)
        if (
            isinstance(window_days, bool)
            or not isinstance(window_days, int)
            or window_days < 1
        ):
            raise ValueError("window_days must be a positive integer")
        window_start = as_of - timedelta(days=window_days - 1)

        record_list = list(records)
        for record in record_list:
            if not isinstance(record, MarketActivityRecord):
                raise TypeError(
                    "records must contain MarketActivityRecord values"
                )
            if record.asset_id != asset_id:
                raise ValueError(
                    "record asset_id must match calculation asset_id"
                )
        visible = [
            record
            for record in record_list
            if date.fromisoformat(record.published_date) <= as_of
            and window_start
            <= date.fromisoformat(record.trade_date)
            <= as_of
        ]
        if not visible:
            return []

        computed_at = datetime.now(timezone.utc).isoformat()
        base_parameters: dict[str, Any] = {
            "window_days": window_days,
            "window_basis": "inclusive_calendar_days",
            "identity_inference": False,
        }
        factors: list[FactorValue] = []

        lhb = _of_type(visible, "lhb_listing")
        if lhb:
            factors.append(
                self._factor(
                    asset_id,
                    "lhb_listing_count",
                    float(len(lhb)),
                    as_of_date,
                    lhb,
                    base_parameters,
                    computed_at,
                )
            )
            self._append_optional_sum(
                factors,
                asset_id,
                "lhb_net_buy_amount",
                lhb,
                "net_buy_amount",
                as_of_date,
                base_parameters,
                computed_at,
            )
            self._append_ratio(
                factors,
                asset_id,
                "lhb_net_buy_ratio",
                lhb,
                "net_buy_amount",
                as_of_date,
                base_parameters,
                computed_at,
            )
            self._append_ratio(
                factors,
                asset_id,
                "institution_seat_net_buy_ratio",
                lhb,
                "institution_net_buy_amount",
                as_of_date,
                base_parameters,
                computed_at,
            )
            self._append_ratio(
                factors,
                asset_id,
                "broker_branch_net_buy_ratio",
                lhb,
                "broker_branch_net_buy_amount",
                as_of_date,
                base_parameters,
                computed_at,
            )

        block_trades = _of_type(visible, "block_trade")
        if block_trades:
            self._append_optional_sum(
                factors,
                asset_id,
                "block_trade_amount",
                block_trades,
                "amount",
                as_of_date,
                base_parameters,
                computed_at,
            )
            discount_records = [
                record
                for record in block_trades
                if record.trade_price is not None
                and record.reference_close is not None
                and record.reference_close > 0
            ]
            if discount_records:
                weights = [
                    _block_trade_weight(record)
                    for record in discount_records
                ]
                weight_total = sum(weights)
                discount = sum(
                    weight
                    * (1.0 - record.trade_price / record.reference_close)
                    for record, weight in zip(discount_records, weights)
                ) / weight_total
                parameters = {
                    **base_parameters,
                    "method": (
                        "weighted_mean_of_"
                        "1_minus_trade_price_over_reference_close"
                    ),
                    "weight": (
                        "turnover_amount_then_amount_then_equal"
                    ),
                }
                factors.append(
                    self._factor(
                        asset_id,
                        "block_trade_discount",
                        discount,
                        as_of_date,
                        discount_records,
                        parameters,
                        computed_at,
                    )
                )
            amount_records = [
                record
                for record in block_trades
                if record.amount is not None
            ]
            cap_records = [
                record
                for record in block_trades
                if record.float_market_cap is not None
                and record.float_market_cap > 0
            ]
            if amount_records and cap_records:
                cap_record = max(
                    cap_records,
                    key=lambda item: (
                        item.trade_date,
                        item.published_date,
                    ),
                )
                parameters = {
                    **base_parameters,
                    "method": (
                        "sum_amount_over_latest_available_"
                        "float_market_cap"
                    ),
                    "denominator_trade_date": cap_record.trade_date,
                }
                factors.append(
                    self._factor(
                        asset_id,
                        "block_trade_float_cap_ratio",
                        sum(
                            record.amount
                            for record in amount_records
                            if record.amount is not None
                        )
                        / cap_record.float_market_cap,
                        as_of_date,
                        list({id(record): record for record in (
                            *amount_records,
                            cap_record,
                        )}.values()),
                        parameters,
                        computed_at,
                    )
                )

        for activity_type, factor_name in (
            ("buyback", "disclosure_buyback_flag"),
            ("holding_change", "disclosure_holder_change_flag"),
            ("lock_up_release", "lock_up_release_announced_flag"),
        ):
            event_records = _of_type(visible, activity_type)
            if event_records:
                factors.append(
                    self._factor(
                        asset_id,
                        factor_name,
                        1.0,
                        as_of_date,
                        event_records,
                        base_parameters,
                        computed_at,
                    )
                )

        factors.sort(key=lambda item: item.factor_name)
        return factors

    def _append_optional_sum(
        self,
        factors: list[FactorValue],
        asset_id: str,
        factor_name: str,
        records: list[MarketActivityRecord],
        field_name: str,
        as_of_date: str,
        parameters: dict[str, Any],
        computed_at: str,
    ) -> None:
        available = [
            record
            for record in records
            if getattr(record, field_name) is not None
        ]
        if available:
            factors.append(
                self._factor(
                    asset_id,
                    factor_name,
                    sum(
                        getattr(record, field_name)
                        for record in available
                    ),
                    as_of_date,
                    available,
                    {**parameters, "aggregation": "sum"},
                    computed_at,
                )
            )

    def _append_ratio(
        self,
        factors: list[FactorValue],
        asset_id: str,
        factor_name: str,
        records: list[MarketActivityRecord],
        numerator_name: str,
        as_of_date: str,
        parameters: dict[str, Any],
        computed_at: str,
    ) -> None:
        available = [
            record
            for record in records
            if getattr(record, numerator_name) is not None
            and record.turnover_amount is not None
            and record.turnover_amount > 0
        ]
        denominator = sum(
            record.turnover_amount
            for record in available
            if record.turnover_amount is not None
        )
        if available and denominator > 0:
            factors.append(
                self._factor(
                    asset_id,
                    factor_name,
                    sum(
                        getattr(record, numerator_name)
                        for record in available
                    )
                    / denominator,
                    as_of_date,
                    available,
                    {
                        **parameters,
                        "method": (
                            f"sum_{numerator_name}_over_sum_"
                            "turnover_amount"
                        ),
                    },
                    computed_at,
                )
            )

    @staticmethod
    def _factor(
        asset_id: str,
        factor_name: str,
        value: float,
        as_of_date: str,
        records: list[MarketActivityRecord],
        parameters: dict[str, Any],
        computed_at: str,
    ) -> FactorValue:
        trade_dates = [record.trade_date for record in records]
        return FactorValue(
            asset_id=asset_id,
            factor_name=factor_name,
            as_of_date=as_of_date,
            value=float(value),
            calculation_version=MARKET_EVENT_VERSION,
            parameters=dict(parameters),
            input_start_date=min(trade_dates),
            input_end_date=max(trade_dates),
            source_dataset=",".join(
                sorted({record.source for record in records})
            ),
            computed_at=computed_at,
        )


@dataclass(frozen=True)
class CapitalFlowDisclosureRecord:
    """A disclosed scope-level observation with an explicit regime."""

    scope: str
    observation_date: str
    published_date: str
    regime: str
    net_flow: float | None = None
    turnover: float | None = None
    top10_turnover: float | None = None
    holding_ratio: float | None = None
    source: str = ""

    def __post_init__(self) -> None:
        _require_choice("scope", self.scope, CAPITAL_FLOW_SCOPES)
        observation = _require_iso_date(
            "observation_date",
            self.observation_date,
        )
        published = _require_iso_date(
            "published_date",
            self.published_date,
        )
        if published < observation:
            raise ValueError(
                "published_date must not precede observation_date"
            )
        _require_choice("regime", self.regime, CAPITAL_FLOW_REGIMES)
        allowed_regimes = {
            "northbound": {
                "daily_net_flow_pre_20240819",
                "daily_turnover_post_20240819",
                "quarterly_holding",
            },
            "southbound": {"daily_net_flow"},
            "state_holder": {"disclosed_holder"},
        }
        if self.regime not in allowed_regimes[self.scope]:
            if self.scope == "state_holder":
                raise ValueError(
                    "state_holder records require "
                    "regime=disclosed_holder"
                )
            raise ValueError(
                f"{self.scope} does not support regime {self.regime}"
            )
        _require_text("source", self.source)
        _require_number("net_flow", self.net_flow, non_negative=False)
        for name in ("turnover", "top10_turnover", "holding_ratio"):
            _require_number(name, getattr(self, name), non_negative=True)
        if self.holding_ratio is not None and self.holding_ratio > 1:
            raise ValueError("holding_ratio must not exceed 1")
        if (
            self.top10_turnover is not None
            and self.turnover is not None
            and self.top10_turnover > self.turnover
        ):
            raise ValueError(
                "top10_turnover must not exceed turnover"
            )


@dataclass(frozen=True)
class CapitalFlowFactorValue:
    """A lightweight factor value intentionally not bound to an asset."""

    scope_id: str
    factor_name: str
    as_of_date: str
    value: float
    calculation_version: str
    parameters: dict[str, Any] = field(default_factory=dict)
    observation_date: str = ""
    published_date: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        _require_text("scope_id", self.scope_id)
        _require_text("factor_name", self.factor_name)
        _require_iso_date("as_of_date", self.as_of_date)
        _require_number("value", self.value, non_negative=False)
        if self.calculation_version != CAPITAL_FLOW_VERSION:
            raise ValueError(
                f"calculation_version must be {CAPITAL_FLOW_VERSION}"
            )
        _require_iso_date(
            "observation_date",
            self.observation_date,
        )
        _require_iso_date("published_date", self.published_date)
        _require_text("source", self.source)


class CapitalFlowFactorCalculator:
    """Calculate only factors observable under each disclosure regime."""

    def calculate(
        self,
        scope_id: str,
        records: Iterable[CapitalFlowDisclosureRecord],
        as_of_date: str,
    ) -> list[CapitalFlowFactorValue]:
        _require_text("scope_id", scope_id)
        as_of = _require_iso_date("as_of_date", as_of_date)
        record_list = list(records)
        for record in record_list:
            if not isinstance(record, CapitalFlowDisclosureRecord):
                raise TypeError(
                    "records must contain CapitalFlowDisclosureRecord values"
                )
            observation = date.fromisoformat(record.observation_date)
            if (
                record.scope == "northbound"
                and observation >= NORTHBOUND_REGIME_BREAK
                and record.net_flow is not None
            ):
                raise ValueError(
                    "northbound net_flow is unavailable on or after "
                    "2024-08-19"
                )
            if (
                record.scope == "northbound"
                and record.regime == "daily_net_flow_pre_20240819"
                and observation >= NORTHBOUND_REGIME_BREAK
            ):
                raise ValueError(
                    "northbound pre-2024-08-19 regime crosses the "
                    "disclosure break"
                )
            if (
                record.scope == "northbound"
                and record.regime == "daily_turnover_post_20240819"
                and observation < NORTHBOUND_REGIME_BREAK
            ):
                raise ValueError(
                    "northbound post-2024-08-19 regime precedes the "
                    "disclosure break"
                )
            if (
                record.scope == "state_holder"
                and record.regime != "disclosed_holder"
            ):
                raise ValueError(
                    "state_holder records require regime=disclosed_holder"
                )

        visible = [
            record
            for record in record_list
            if date.fromisoformat(record.published_date) <= as_of
            and date.fromisoformat(record.observation_date) <= as_of
        ]
        factors: list[CapitalFlowFactorValue] = []

        northbound_pre = [
            record
            for record in visible
            if record.scope == "northbound"
            and record.regime == "daily_net_flow_pre_20240819"
            and record.net_flow is not None
        ]
        self._append_latest(
            factors,
            scope_id,
            "northbound_net_flow_pre_20240819",
            northbound_pre,
            "net_flow",
            as_of_date,
            {
                "regime": "daily_net_flow_pre_20240819",
                "regime_break": "2024-08-19",
            },
        )

        northbound_post = [
            record
            for record in visible
            if record.scope == "northbound"
            and record.regime == "daily_turnover_post_20240819"
        ]
        self._append_latest(
            factors,
            scope_id,
            "northbound_turnover",
            [
                record
                for record in northbound_post
                if record.turnover is not None
            ],
            "turnover",
            as_of_date,
            {
                "regime": "daily_turnover_post_20240819",
                "regime_break": "2024-08-19",
            },
        )
        concentration_records = [
            record
            for record in northbound_post
            if record.top10_turnover is not None
            and record.turnover is not None
            and record.turnover > 0
        ]
        if concentration_records:
            record = _latest(concentration_records)
            factors.append(
                self._value(
                    scope_id,
                    "northbound_top10_concentration",
                    record.top10_turnover / record.turnover,
                    as_of_date,
                    record,
                    {
                        "regime": "daily_turnover_post_20240819",
                        "method": "top10_turnover_over_turnover",
                        "regime_break": "2024-08-19",
                    },
                )
            )

        quarterly = [
            record
            for record in visible
            if record.scope == "northbound"
            and record.regime == "quarterly_holding"
            and record.holding_ratio is not None
        ]
        self._append_latest(
            factors,
            scope_id,
            "northbound_quarterly_holding_ratio",
            quarterly,
            "holding_ratio",
            as_of_date,
            {
                "regime": "quarterly_holding",
                "effective_date": "published_date",
            },
        )

        southbound = [
            record
            for record in visible
            if record.scope == "southbound"
            and record.regime == "daily_net_flow"
            and record.net_flow is not None
        ]
        self._append_latest(
            factors,
            scope_id,
            "southbound_net_flow",
            southbound,
            "net_flow",
            as_of_date,
            {
                "regime": "daily_net_flow",
                "scope_role": "hong_kong_risk_appetite_context",
                "a_share_direct_flow": False,
            },
        )

        state_holder = [
            record
            for record in visible
            if record.scope == "state_holder"
            and record.regime == "disclosed_holder"
            and record.holding_ratio is not None
        ]
        self._append_latest(
            factors,
            scope_id,
            "state_disclosed_holder_ratio",
            state_holder,
            "holding_ratio",
            as_of_date,
            {
                "regime": "disclosed_holder",
                "identity_source": "official_disclosure",
            },
        )

        factors.sort(key=lambda item: item.factor_name)
        return factors

    def _append_latest(
        self,
        factors: list[CapitalFlowFactorValue],
        scope_id: str,
        factor_name: str,
        records: list[CapitalFlowDisclosureRecord],
        field_name: str,
        as_of_date: str,
        parameters: dict[str, Any],
    ) -> None:
        if not records:
            return
        record = _latest(records)
        factors.append(
            self._value(
                scope_id,
                factor_name,
                getattr(record, field_name),
                as_of_date,
                record,
                parameters,
            )
        )

    @staticmethod
    def _value(
        scope_id: str,
        factor_name: str,
        value: float,
        as_of_date: str,
        record: CapitalFlowDisclosureRecord,
        parameters: dict[str, Any],
    ) -> CapitalFlowFactorValue:
        return CapitalFlowFactorValue(
            scope_id=scope_id,
            factor_name=factor_name,
            as_of_date=as_of_date,
            value=float(value),
            calculation_version=CAPITAL_FLOW_VERSION,
            parameters=dict(parameters),
            observation_date=record.observation_date,
            published_date=record.published_date,
            source=record.source,
        )


def _of_type(
    records: list[MarketActivityRecord],
    activity_type: str,
) -> list[MarketActivityRecord]:
    return [
        record
        for record in records
        if record.activity_type == activity_type
    ]


def _block_trade_weight(record: MarketActivityRecord) -> float:
    if record.turnover_amount is not None and record.turnover_amount > 0:
        return record.turnover_amount
    if record.amount is not None and record.amount > 0:
        return record.amount
    return 1.0


def _latest(
    records: list[CapitalFlowDisclosureRecord],
) -> CapitalFlowDisclosureRecord:
    return max(
        records,
        key=lambda item: (
            item.observation_date,
            item.published_date,
        ),
    )


def _require_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_choice(
    name: str,
    value: str,
    choices: frozenset[str],
) -> None:
    if value not in choices:
        raise ValueError(
            f"{name} must be one of {sorted(choices)}"
        )


def _require_iso_date(name: str, value: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date")
    return parsed


def _require_number(
    name: str,
    value: float | None,
    *,
    non_negative: bool,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")


__all__ = [
    "CAPITAL_FLOW_VERSION",
    "MARKET_EVENT_VERSION",
    "CapitalFlowDisclosureRecord",
    "CapitalFlowFactorCalculator",
    "CapitalFlowFactorValue",
    "MarketActivityRecord",
    "MarketEventFactorCalculator",
]
