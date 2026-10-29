"""Versioned user investment preferences and their translation to mandates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from src.research_contracts import from_payload, to_payload

from .budget import InvestmentMandate

ZERO = Decimal("0")
ONE = Decimal("1")
# Money needed within this many days is kept as cash in allocation and adjustment.
LIQUIDITY_WINDOW_DAYS = 730


class RiskProfile(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    GROWTH = "growth"
    AGGRESSIVE = "aggressive"


class PreferenceHorizon(str, Enum):
    UNDER_1Y = "under_1y"
    ONE_TO_3Y = "1_3y"
    OVER_3Y = "over_3y"


class AggressivenessMeaning(str, Enum):
    HIGHER_EQUITY = "higher_equity"
    MORE_CONCENTRATED = "more_concentrated"
    HIGHER_VOLATILITY = "higher_volatility"
    LONGER_HORIZON = "longer_horizon"


RISK_PROFILE_LABELS = {
    RiskProfile.CONSERVATIVE: "稳健",
    RiskProfile.BALANCED: "平衡",
    RiskProfile.GROWTH: "成长",
    RiskProfile.AGGRESSIVE: "进取",
}
HORIZON_LABELS = {
    PreferenceHorizon.UNDER_1Y: "1 年内",
    PreferenceHorizon.ONE_TO_3Y: "1–3 年",
    PreferenceHorizon.OVER_3Y: "3 年以上",
}
MEANING_LABELS = {
    AggressivenessMeaning.HIGHER_EQUITY: "更高权益比例",
    AggressivenessMeaning.MORE_CONCENTRATED: "更集中",
    AggressivenessMeaning.HIGHER_VOLATILITY: "更高波动",
    AggressivenessMeaning.LONGER_HORIZON: "更长期限",
}
FIELD_LABELS = {
    "horizon": "期限",
    "equity_weight_low": "权益比例下限",
    "equity_weight_high": "权益比例上限",
    "target_volatility": "目标年化波动",
    "max_drawdown": "可接受最大回撤",
    "max_single_weight": "单只个股上限",
    "max_industry_weight": "单一行业上限",
    "min_cash_weight": "现金底线",
}
_HORIZON_ORDER = tuple(PreferenceHorizon)
_PROFILE_ORDER = tuple(RiskProfile)


@dataclass(frozen=True)
class PreferenceLimits:
    equity_weight_low: Decimal
    equity_weight_high: Decimal
    target_volatility: Decimal
    max_drawdown: Decimal
    max_single_weight: Decimal
    max_industry_weight: Decimal
    min_cash_weight: Decimal


def _d(value: str) -> Decimal:
    return Decimal(value)


# Volatility and drawdown scale with the equity share of an A-share portfolio
# (CSI 300: ~22% annual volatility, ~45% worst drawdown since 2010).
PROFILE_PRESETS: dict[RiskProfile, PreferenceLimits] = {
    RiskProfile.CONSERVATIVE: PreferenceLimits(
        _d("0.10"), _d("0.30"), _d("0.06"), _d("0.10"), _d("0.10"), _d("0.20"), _d("0.10")
    ),
    RiskProfile.BALANCED: PreferenceLimits(
        _d("0.30"), _d("0.60"), _d("0.10"), _d("0.20"), _d("0.15"), _d("0.25"), _d("0.05")
    ),
    RiskProfile.GROWTH: PreferenceLimits(
        _d("0.60"), _d("0.80"), _d("0.15"), _d("0.30"), _d("0.20"), _d("0.35"), _d("0.05")
    ),
    RiskProfile.AGGRESSIVE: PreferenceLimits(
        _d("0.80"), _d("0.95"), _d("0.20"), _d("0.40"), _d("0.25"), _d("0.40"), _d("0.05")
    ),
}
OVERRIDABLE_FIELDS = frozenset(FIELD_LABELS)


@dataclass(frozen=True)
class InvestmentPreference:
    """One confirmed version of a user's portfolio-level preference."""

    user_id: str
    version: int
    recorded_on: str
    risk_profile: RiskProfile
    horizon: PreferenceHorizon
    equity_weight_low: Decimal
    equity_weight_high: Decimal
    target_volatility: Decimal
    max_drawdown: Decimal
    max_single_weight: Decimal
    max_industry_weight: Decimal
    min_cash_weight: Decimal
    user_statement: str
    meanings: tuple[AggressivenessMeaning, ...] = ()
    overrides: tuple[str, ...] = ()
    derivation: tuple[str, ...] = ()
    liquidity_need_amount: Decimal | None = None
    liquidity_need_by: str | None = None
    supersedes_preference_id: str | None = None
    preference_id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id must not be blank")
        if self.version < 1:
            raise ValueError("version starts at 1")
        date.fromisoformat(self.recorded_on)
        if not self.user_statement.strip():
            raise ValueError("user_statement must keep the user's own words")
        if (self.version == 1) != (self.supersedes_preference_id is None):
            raise ValueError("only the first version has no superseded preference")
        if self.version == 1 and self.meanings:
            raise ValueError("an aggressiveness change needs a recorded preference to change")
        for name in (
            "equity_weight_low",
            "equity_weight_high",
            "max_single_weight",
            "max_industry_weight",
            "min_cash_weight",
        ):
            value = getattr(self, name)
            if not ZERO <= value <= ONE:
                raise ValueError(f"{name} must be within [0, 1]")
        for name in ("target_volatility", "max_drawdown"):
            value = getattr(self, name)
            if not ZERO < value <= ONE:
                raise ValueError(f"{name} must be within (0, 1]")
        if self.equity_weight_low > self.equity_weight_high:
            raise ValueError("equity_weight_low must not exceed equity_weight_high")
        if self.equity_weight_high + self.min_cash_weight > ONE:
            raise ValueError("equity_weight_high plus min_cash_weight must not exceed 1")
        if self.max_single_weight > self.max_industry_weight:
            raise ValueError("max_single_weight must not exceed max_industry_weight")
        unknown = set(self.overrides) - OVERRIDABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown override fields: {sorted(unknown)}")
        if self.liquidity_need_amount is not None and self.liquidity_need_amount <= ZERO:
            raise ValueError("liquidity_need_amount must be positive")
        if self.liquidity_need_by is not None:
            date.fromisoformat(self.liquidity_need_by)
        expected = preference_id_for(self)
        if self.preference_id and self.preference_id != expected:
            raise ValueError("preference_id does not match the preference content")
        object.__setattr__(self, "preference_id", expected)

    def to_dict(self) -> dict[str, Any]:
        return to_payload(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "InvestmentPreference":
        return from_payload(cls, payload)

    def describe(self) -> dict[str, Any]:
        """Readable fields for the conversation; numbers stay as stored."""

        lines = [
            f"风险档位：{RISK_PROFILE_LABELS[self.risk_profile]}",
            f"期限：{HORIZON_LABELS[self.horizon]}",
            f"权益比例：{_pct(self.equity_weight_low)}–{_pct(self.equity_weight_high)}",
            f"目标年化波动：{_pct(self.target_volatility)}",
            f"可接受最大回撤：{_pct(self.max_drawdown)}",
            f"单只个股上限：{_pct(self.max_single_weight)}",
            f"单一行业上限：{_pct(self.max_industry_weight)}",
            f"现金底线：{_pct(self.min_cash_weight)}",
        ]
        if self.liquidity_need_amount is not None:
            lines.append(
                f"近期用钱：{self.liquidity_need_amount} 元"
                + (f"，{self.liquidity_need_by} 前" if self.liquidity_need_by else "")
            )
        if self.meanings:
            lines.append("本次确认的“激进”含义：" + "、".join(MEANING_LABELS[item] for item in self.meanings))
        return {
            "preference_id": self.preference_id,
            "version": self.version,
            "recorded_on": self.recorded_on,
            "lines": lines,
        }


def preference_id_for(preference: InvestmentPreference) -> str:
    payload = to_payload(preference)
    payload.pop("preference_id")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"preference:{digest[:16]}"


def initial_preference(
    *,
    user_id: str,
    recorded_on: str,
    risk_profile: RiskProfile,
    horizon: PreferenceHorizon,
    user_statement: str,
    overrides: dict[str, Any] | None = None,
    liquidity_need_amount: Decimal | None = None,
    liquidity_need_by: str | None = None,
    previous: InvestmentPreference | None = None,
) -> InvestmentPreference:
    """A preset profile with the user's own overrides; restating replaces the previous version."""

    preset = PROFILE_PRESETS[risk_profile]
    base = InvestmentPreference(
        user_id=user_id,
        version=1 if previous is None else previous.version + 1,
        recorded_on=recorded_on,
        risk_profile=risk_profile,
        horizon=horizon,
        user_statement=user_statement,
        derivation=(f"按{RISK_PROFILE_LABELS[risk_profile]}档默认值设定",),
        liquidity_need_amount=liquidity_need_amount,
        liquidity_need_by=liquidity_need_by,
        supersedes_preference_id=None if previous is None else previous.preference_id,
        **_limits_payload(preset),
    )
    return _apply_overrides(base, overrides or {})


def derive_preference(
    previous: InvestmentPreference,
    *,
    meanings: tuple[AggressivenessMeaning, ...],
    recorded_on: str,
    user_statement: str,
    overrides: dict[str, Any] | None = None,
) -> InvestmentPreference:
    """Move only the dimensions the confirmed meanings name up one preset tier."""

    if not meanings:
        raise ValueError("derive_preference needs at least one confirmed meaning")
    meanings = tuple(item for item in AggressivenessMeaning if item in set(meanings))
    values: dict[str, Any] = {}
    notes: list[str] = []
    if AggressivenessMeaning.HIGHER_EQUITY in meanings:
        tier = next(
            (item for item in _PROFILE_ORDER if PROFILE_PRESETS[item].equity_weight_high > previous.equity_weight_high),
            None,
        )
        if tier is None:
            notes.append("权益比例已是最高档，未再提高")
        else:
            values["equity_weight_low"] = max(previous.equity_weight_low, PROFILE_PRESETS[tier].equity_weight_low)
            values["equity_weight_high"] = PROFILE_PRESETS[tier].equity_weight_high
            notes.append(f"更高权益比例：权益区间升到{RISK_PROFILE_LABELS[tier]}档")
    if AggressivenessMeaning.MORE_CONCENTRATED in meanings:
        for name in ("max_single_weight", "max_industry_weight"):
            raised = _next_tier_value(name, getattr(previous, name))
            if raised is None:
                notes.append(f"{FIELD_LABELS[name]}已是最高档，未再提高")
            else:
                values[name] = raised
        notes.append("更集中：单只与单一行业上限各升一档")
    if AggressivenessMeaning.LONGER_HORIZON in meanings:
        index = _HORIZON_ORDER.index(previous.horizon)
        if index + 1 < len(_HORIZON_ORDER):
            values["horizon"] = _HORIZON_ORDER[index + 1]
            notes.append("更长期限：期限延长一档；回撤容忍度不随期限自动放宽")
        else:
            notes.append("期限已是最长档，未再延长")
    risk_raising = {
        AggressivenessMeaning.HIGHER_EQUITY,
        AggressivenessMeaning.MORE_CONCENTRATED,
        AggressivenessMeaning.HIGHER_VOLATILITY,
    }
    raised_names = []
    if risk_raising & set(meanings):
        for name in ("target_volatility", "max_drawdown"):
            raised = _next_tier_value(name, getattr(previous, name))
            if raised is None:
                notes.append(f"{FIELD_LABELS[name]}已是最高档，未再提高")
            else:
                values[name] = raised
                raised_names.append(name)
    # Values the user stated earlier are theirs; a confirmed meaning does not move them.
    for name in previous.overrides:
        if name in values and values[name] != getattr(previous, name):
            del values[name]
            notes.append(
                f"{FIELD_LABELS[name]}保留用户此前说出的 {_format(getattr(previous, name))}，未随含义调整；"
                "用户确认要改时用 --set 重新说明"
            )
    raised_names = [name for name in raised_names if name in values]
    if raised_names:
        notes.append("风险预算升一档（" + "、".join(FIELD_LABELS[name] for name in raised_names) + "），需用户确认能承受")
    merged = {**_limits_payload(previous), "horizon": previous.horizon, **values}
    if merged["equity_weight_high"] + previous.min_cash_weight > ONE:
        merged["equity_weight_high"] = ONE - previous.min_cash_weight
    if merged["equity_weight_low"] > merged["equity_weight_high"]:
        merged["equity_weight_low"] = merged["equity_weight_high"]
    if merged["max_single_weight"] > merged["max_industry_weight"]:
        merged["max_industry_weight"] = merged["max_single_weight"]
    preference = InvestmentPreference(
        user_id=previous.user_id,
        version=previous.version + 1,
        recorded_on=recorded_on,
        risk_profile=_profile_for_volatility(merged["target_volatility"]),
        user_statement=user_statement,
        meanings=meanings,
        overrides=previous.overrides,
        derivation=tuple(notes),
        liquidity_need_amount=previous.liquidity_need_amount,
        liquidity_need_by=previous.liquidity_need_by,
        supersedes_preference_id=previous.preference_id,
        **{key: merged[key] for key in ("horizon", *_limits_payload(previous))},
    )
    return _apply_overrides(preference, overrides or {})


def effective_cash_floor(
    preference: InvestmentPreference,
    *,
    total_value: float,
    as_of_date: str,
) -> tuple[float, str | None]:
    """Cash floor as a weight: the preference floor, raised to cover money the user needs within two years."""

    floor = float(preference.min_cash_weight)
    amount = preference.liquidity_need_amount
    if amount is None or total_value <= 0:
        return floor, None
    by = preference.liquidity_need_by
    if by and (date.fromisoformat(by) - date.fromisoformat(as_of_date)).days > LIQUIDITY_WINDOW_DAYS:
        return floor, None
    need = float(amount) / total_value
    if need <= floor:
        return floor, None
    return min(need, 1.0), (
        f"近期用钱 {amount} 元" + (f"（{by} 前）" if by else "") + f"占组合 {need:.1%}，现金底线按此提高"
    )


def preference_changes(
    previous: InvestmentPreference | None,
    current: InvestmentPreference,
) -> list[dict[str, str]]:
    if previous is None:
        return []
    changes = []
    for name, label in FIELD_LABELS.items():
        before, after = getattr(previous, name), getattr(current, name)
        if before != after:
            changes.append(
                {
                    "field": name,
                    "label": label,
                    "before": _format(before),
                    "after": _format(after),
                }
            )
    return changes


def mandate_for_asset(
    preference: InvestmentPreference,
    *,
    implementation_asset_id: str,
    benchmark_asset_id: str,
    single_stock: bool,
    defensive_asset_id: str | None = None,
) -> InvestmentMandate:
    """Single-sleeve mandate: a stock is capped by the single-stock limit, a fund by the equity ceiling."""

    max_risk_weight = (
        preference.max_single_weight if single_stock else preference.equity_weight_high
    )
    digest = hashlib.sha256(
        "|".join(
            (preference.preference_id, implementation_asset_id, benchmark_asset_id, defensive_asset_id or "")
        ).encode("utf-8")
    ).hexdigest()
    return InvestmentMandate(
        mandate_id=f"mandate:{digest[:16]}",
        objective="absolute_return",
        implementation_asset_id=implementation_asset_id,
        benchmark_asset_id=benchmark_asset_id,
        defensive_asset_id=defensive_asset_id,
        target_volatility=preference.target_volatility,
        max_drawdown=preference.max_drawdown,
        max_tracking_error=None,
        max_risk_weight=max_risk_weight,
        min_cash_weight=preference.min_cash_weight,
    )


def _apply_overrides(
    preference: InvestmentPreference,
    overrides: dict[str, Any],
) -> InvestmentPreference:
    if not overrides:
        return preference
    unknown = set(overrides) - OVERRIDABLE_FIELDS
    if unknown:
        raise ValueError(f"unknown override fields: {sorted(unknown)}")
    values = {
        name: PreferenceHorizon(value) if name == "horizon" else Decimal(str(value))
        for name, value in overrides.items()
    }
    return replace(
        preference,
        **values,
        overrides=tuple(sorted(set(preference.overrides) | set(values))),
        derivation=(*preference.derivation, "用户指定：" + "、".join(FIELD_LABELS[name] for name in sorted(values))),
        preference_id="",
    )


def _limits_payload(source: Any) -> dict[str, Decimal]:
    return {
        name: getattr(source, name)
        for name in (
            "equity_weight_low",
            "equity_weight_high",
            "target_volatility",
            "max_drawdown",
            "max_single_weight",
            "max_industry_weight",
            "min_cash_weight",
        )
    }


def _next_tier_value(name: str, current: Decimal) -> Decimal | None:
    return next(
        (
            getattr(PROFILE_PRESETS[item], name)
            for item in _PROFILE_ORDER
            if getattr(PROFILE_PRESETS[item], name) > current
        ),
        None,
    )


def _profile_for_volatility(volatility: Decimal) -> RiskProfile:
    eligible = [item for item in _PROFILE_ORDER if PROFILE_PRESETS[item].target_volatility <= volatility]
    return eligible[-1] if eligible else RiskProfile.CONSERVATIVE


def _pct(value: Decimal) -> str:
    scaled = value * 100
    return f"{scaled:.0f}%" if scaled == scaled.to_integral_value() else f"{scaled:.1f}%"


def _format(value: Any) -> str:
    if isinstance(value, PreferenceHorizon):
        return HORIZON_LABELS[value]
    if isinstance(value, Decimal):
        return _pct(value)
    return str(value)
