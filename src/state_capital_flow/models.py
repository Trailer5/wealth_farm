"""Strict models for disclosed state holdings and weekly anonymous ETF proxies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse


STATE_CAPITAL_FLOW_CALCULATION_VERSION = "state_capital_flow_v1"

StateEntityGroup = Literal[
    "stabilization_core",
    "state_long_term",
    "other_state",
]
SourceTier = Literal["official", "aggregator"]
Direction = Literal["buy", "sell"]
WeeklyEvidenceTier = Literal[
    "anonymous_proxy",
    "confirmed_direction",
    "confirmed_lower_bound",
]


@dataclass(frozen=True)
class StateEntity:
    entity_id: str
    legal_name: str
    aliases: tuple[str, ...]
    group: StateEntityGroup
    source_url: str
    effective_date: str

    def __post_init__(self) -> None:
        _require_text(self.entity_id, "entity_id")
        _require_text(self.legal_name, "legal_name")
        aliases = tuple(self.aliases)
        if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError("aliases must contain non-blank strings")
        if len(set(aliases)) != len(aliases):
            raise ValueError("aliases must be unique")
        object.__setattr__(self, "aliases", aliases)
        if self.group not in {
            "stabilization_core",
            "state_long_term",
            "other_state",
        }:
            raise ValueError("group is not a supported state entity group")
        _require_url(self.source_url, "source_url")
        _parse_date(self.effective_date, "effective_date")


@dataclass(frozen=True)
class StateHolderEvidence:
    evidence_id: str
    entity_id: str
    instrument_asset_id: str
    report_period: str
    holding_date: str
    published_at: str
    holding_shares: Decimal
    holding_ratio: Decimal | None
    source_tier: SourceTier
    source: str
    source_url: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.entity_id, "entity_id"),
            (self.instrument_asset_id, "instrument_asset_id"),
            (self.report_period, "report_period"),
            (self.source, "source"),
        ):
            _require_text(value, name)
        holding_date = _parse_date(self.holding_date, "holding_date")
        published_at = _parse_date(self.published_at, "published_at")
        if published_at < holding_date:
            raise ValueError("published_at must not be earlier than holding_date")
        _require_decimal(self.holding_shares, "holding_shares", minimum=Decimal("0"))
        if self.holding_ratio is not None:
            _require_decimal(
                self.holding_ratio,
                "holding_ratio",
                minimum=Decimal("0"),
                maximum=Decimal("1"),
            )
        if self.source_tier not in {"official", "aggregator"}:
            raise ValueError("source_tier must be official or aggregator")
        _require_url(self.source_url, "source_url")
        if not isinstance(self.parameters, dict):
            raise TypeError("parameters must be a dict")
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True)
class StatePurchaseAnnouncement:
    announcement_id: str
    entity_id: str
    published_at: str
    event_start_date: str
    event_end_date: str
    direction: Direction
    exact_amount_cny: Decimal | None
    exact_shares: Decimal | None
    source_url: str

    def __post_init__(self) -> None:
        _require_text(self.announcement_id, "announcement_id")
        _require_text(self.entity_id, "entity_id")
        _parse_date(self.published_at, "published_at")
        event_start = _parse_date(self.event_start_date, "event_start_date")
        event_end = _parse_date(self.event_end_date, "event_end_date")
        if event_end < event_start:
            raise ValueError("event_end_date must not be earlier than event_start_date")
        if self.direction not in {"buy", "sell"}:
            raise ValueError("direction must be buy or sell")
        for value, name in (
            (self.exact_amount_cny, "exact_amount_cny"),
            (self.exact_shares, "exact_shares"),
        ):
            if value is not None:
                _require_decimal(value, name, minimum=Decimal("0"))
        _require_url(self.source_url, "source_url")


@dataclass(frozen=True)
class ETFDailyObservation:
    instrument_asset_id: str
    trade_date: str
    available_at: str
    total_shares: Decimal
    nav: Decimal | None
    source: str
    share_unit_continuity_confirmed: bool = False

    def __post_init__(self) -> None:
        _require_text(self.instrument_asset_id, "instrument_asset_id")
        trade_date = _parse_date(self.trade_date, "trade_date")
        available_at = _parse_date(self.available_at, "available_at")
        if available_at < trade_date:
            raise ValueError("available_at must not be earlier than trade_date")
        _require_decimal(self.total_shares, "total_shares", minimum=Decimal("0"))
        if self.nav is not None:
            _require_decimal(self.nav, "nav", minimum=Decimal("0"), exclusive=True)
        _require_text(self.source, "source")
        if type(self.share_unit_continuity_confirmed) is not bool:
            raise TypeError("share_unit_continuity_confirmed must be bool")


@dataclass(frozen=True)
class ConfirmedHoldingChange:
    entity_id: str
    instrument_asset_id: str
    previous_holding_shares: Decimal
    current_holding_shares: Decimal
    previous_report_period: str
    current_report_period: str
    previous_holding_date: str
    current_holding_date: str
    previous_published_at: str
    current_published_at: str
    delta_shares: Decimal
    available_at: str
    evidence_tier: Literal["confirmed_interval"] = "confirmed_interval"

    def __post_init__(self) -> None:
        for value, name in (
            (self.entity_id, "entity_id"),
            (self.instrument_asset_id, "instrument_asset_id"),
            (self.previous_report_period, "previous_report_period"),
            (self.current_report_period, "current_report_period"),
        ):
            _require_text(value, name)
        for value, name in (
            (self.previous_holding_shares, "previous_holding_shares"),
            (self.current_holding_shares, "current_holding_shares"),
        ):
            _require_decimal(value, name, minimum=Decimal("0"))
        previous_holding_date = _parse_date(
            self.previous_holding_date,
            "previous_holding_date",
        )
        current_holding_date = _parse_date(
            self.current_holding_date,
            "current_holding_date",
        )
        if current_holding_date <= previous_holding_date:
            raise ValueError("current_holding_date must follow previous_holding_date")
        previous_published = _parse_date(
            self.previous_published_at,
            "previous_published_at",
        )
        current_published = _parse_date(
            self.current_published_at,
            "current_published_at",
        )
        if current_published < previous_published:
            raise ValueError("current_published_at must not precede previous_published_at")
        _require_decimal(self.delta_shares, "delta_shares")
        if self.delta_shares != (
            self.current_holding_shares - self.previous_holding_shares
        ):
            raise ValueError("delta_shares must equal current minus previous holdings")
        if _parse_date(self.available_at, "available_at") != current_published:
            raise ValueError("available_at must equal current_published_at")
        if self.evidence_tier != "confirmed_interval":
            raise ValueError("evidence_tier must be confirmed_interval")


@dataclass(frozen=True)
class WeeklyStateFlowMetric:
    basket_id: str
    factor_name: str
    week_start: str
    week_end: str
    estimated_for_date: str
    available_at: str
    value: float
    calculation_version: str = STATE_CAPITAL_FLOW_CALCULATION_VERSION
    evidence_tier: WeeklyEvidenceTier = "anonymous_proxy"
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.basket_id, "basket_id")
        _require_text(self.factor_name, "factor_name")
        week_start = _parse_date(self.week_start, "week_start")
        week_end = _parse_date(self.week_end, "week_end")
        if week_start.weekday() != 0 or week_end.weekday() != 6:
            raise ValueError("week_start and week_end must define an ISO natural week")
        if (week_end - week_start).days != 6:
            raise ValueError("week_start and week_end must be six days apart")
        if _parse_date(self.estimated_for_date, "estimated_for_date") != week_end:
            raise ValueError("estimated_for_date must equal week_end")
        _parse_date(self.available_at, "available_at")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("value must be a number")
        if not math.isfinite(float(self.value)):
            raise ValueError("value must be finite")
        if self.calculation_version != STATE_CAPITAL_FLOW_CALCULATION_VERSION:
            raise ValueError(
                "calculation_version must be state_capital_flow_v1"
            )
        if self.evidence_tier not in {
            "anonymous_proxy",
            "confirmed_direction",
            "confirmed_lower_bound",
        }:
            raise ValueError("unsupported weekly evidence_tier")
        if not isinstance(self.parameters, dict):
            raise TypeError("parameters must be a dict")
        required = {
            "members",
            "sample_count",
            "eligible_member_count",
            "coverage_ratio",
            "identity_confirmed_by_holder_report",
            "identity_inference",
            "not_confirmed_state_flow",
        }
        if not required.issubset(self.parameters):
            raise ValueError("parameters are missing required evidence fields")
        object.__setattr__(self, "parameters", dict(self.parameters))


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _require_url(value: str, field_name: str) -> None:
    _require_text(value, field_name)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc


def _require_decimal(
    value: Decimal,
    field_name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    exclusive: bool = False,
) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if minimum is not None:
        if exclusive and value <= minimum:
            raise ValueError(f"{field_name} must be greater than {minimum}")
        if not exclusive and value < minimum:
            raise ValueError(f"{field_name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum}")
