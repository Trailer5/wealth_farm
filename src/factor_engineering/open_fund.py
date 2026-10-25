"""Calendar-window metrics for official open-end fund NAV records."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .models import FactorValue


OPEN_FUND_STANDARD_CALCULATION_VERSION = "open_fund_standard_v1"


@dataclass(frozen=True)
class OpenFundWindowDefinition:
    key: str
    label: str
    months: int | None = None
    year_to_date: bool = False
    since_inception: bool = False


OPEN_FUND_STANDARD_WINDOWS = (
    OpenFundWindowDefinition("1m", "近1月", months=1),
    OpenFundWindowDefinition("3m", "近3月", months=3),
    OpenFundWindowDefinition("6m", "近6月", months=6),
    OpenFundWindowDefinition("ytd", "年初以来", year_to_date=True),
    OpenFundWindowDefinition("1y", "近1年", months=12),
    OpenFundWindowDefinition("3y", "近3年", months=36),
    OpenFundWindowDefinition("5y", "近5年", months=60),
    OpenFundWindowDefinition(
        "since_inception",
        "成立以来",
        since_inception=True,
    ),
)


@dataclass(frozen=True)
class OpenFundWindowUnavailable:
    key: str
    label: str
    reason: str
    target_start_date: str | None


@dataclass(frozen=True)
class OpenFundStandardMetricResult:
    asset_id: str
    as_of_date: str
    latest_observation_date: str
    nav_basis: str
    return_semantics: str
    values: tuple[FactorValue, ...]
    unavailable_windows: tuple[OpenFundWindowUnavailable, ...]


class OpenFundStandardMetricCalculator:
    """Calculate traceable returns for standard calendar windows."""

    def __init__(self, *, start_tolerance_days: int = 10) -> None:
        if start_tolerance_days < 0:
            raise ValueError("start_tolerance_days must not be negative")
        self.start_tolerance_days = start_tolerance_days

    def calculate(
        self,
        *,
        asset_id: str,
        records: list[dict[str, Any]],
        source_dataset: str,
        as_of_date: str,
    ) -> OpenFundStandardMetricResult:
        requested_as_of = date.fromisoformat(as_of_date)
        normalized = _normalized_navs(records, requested_as_of)
        if not normalized:
            raise ValueError("No positive official NAV observations")
        nav_basis = _nav_basis(normalized)
        return_semantics = (
            "accumulated_nav_change_not_reinvested_total_return"
            if nav_basis == "accumulated_nav"
            else "unit_nav_change_excludes_cash_distributions"
        )
        observations = [
            (value_date, values[nav_basis])
            for value_date, values in normalized
        ]
        latest_date, latest_value = observations[-1]
        computed_at = datetime.now(timezone.utc).isoformat()
        values = []
        unavailable = []
        for window in OPEN_FUND_STANDARD_WINDOWS:
            target = _window_target(latest_date, window)
            start = _start_observation(
                observations,
                target=target,
                since_inception=window.since_inception,
                tolerance_days=self.start_tolerance_days,
            )
            if start is None:
                reason = (
                    "history_shorter_than_window"
                    if target is not None
                    and observations[0][0] > target
                    else "no_observation_near_window_start"
                )
                unavailable.append(
                    OpenFundWindowUnavailable(
                        key=window.key,
                        label=window.label,
                        reason=reason,
                        target_start_date=(
                            target.isoformat() if target else None
                        ),
                    )
                )
                continue
            start_date, start_value = start
            if start_date == latest_date:
                unavailable.append(
                    OpenFundWindowUnavailable(
                        key=window.key,
                        label=window.label,
                        reason="fewer_than_two_observations",
                        target_start_date=(
                            target.isoformat() if target else None
                        ),
                    )
                )
                continue
            observation_count = sum(
                start_date <= value_date <= latest_date
                for value_date, _ in observations
            )
            values.append(
                FactorValue(
                    asset_id=asset_id,
                    factor_name=(
                        f"open_fund_standard_return_{window.key}"
                    ),
                    as_of_date=latest_date.isoformat(),
                    value=latest_value / start_value - 1.0,
                    calculation_version=(
                        OPEN_FUND_STANDARD_CALCULATION_VERSION
                    ),
                    parameters={
                        "window_key": window.key,
                        "window_label": window.label,
                        "target_start_date": (
                            target.isoformat() if target else None
                        ),
                        "start_observation_rule": (
                            "earliest_observation"
                            if window.since_inception
                            else "latest_on_or_before_target"
                        ),
                        "start_tolerance_days": (
                            None
                            if window.since_inception
                            else self.start_tolerance_days
                        ),
                        "nav_basis": nav_basis,
                        "return_semantics": return_semantics,
                        "observation_count": observation_count,
                        "requested_as_of_date": as_of_date,
                    },
                    input_start_date=start_date.isoformat(),
                    input_end_date=latest_date.isoformat(),
                    source_dataset=source_dataset,
                    computed_at=computed_at,
                )
            )
        return OpenFundStandardMetricResult(
            asset_id=asset_id,
            as_of_date=as_of_date,
            latest_observation_date=latest_date.isoformat(),
            nav_basis=nav_basis,
            return_semantics=return_semantics,
            values=tuple(values),
            unavailable_windows=tuple(unavailable),
        )


def _normalized_navs(
    records: list[dict[str, Any]],
    as_of_date: date,
) -> list[tuple[date, dict[str, float | None]]]:
    by_date: dict[date, dict[str, float | None]] = {}
    for record in sorted(
        records,
        key=lambda item: (
            str(item.get("nav_date") or item.get("trade_date") or ""),
            str(item.get("source") or ""),
        ),
    ):
        raw_date = str(
            record.get("nav_date") or record.get("trade_date") or ""
        )[:10]
        if not raw_date:
            continue
        value_date = date.fromisoformat(raw_date)
        if value_date > as_of_date or value_date in by_date:
            continue
        unit_nav = _positive_float(
            record.get("unit_nav", record.get("close"))
        )
        if unit_nav is None:
            continue
        by_date[value_date] = {
            "unit_nav": unit_nav,
            "accumulated_nav": _positive_float(
                record.get("accumulated_nav")
            ),
        }
    return sorted(by_date.items())


def _nav_basis(
    observations: list[tuple[date, dict[str, float | None]]],
) -> str:
    if all(
        values["accumulated_nav"] is not None
        for _, values in observations
    ):
        return "accumulated_nav"
    return "unit_nav"


def _window_target(
    latest_date: date,
    window: OpenFundWindowDefinition,
) -> date | None:
    if window.since_inception:
        return None
    if window.year_to_date:
        return date(latest_date.year - 1, 12, 31)
    assert window.months is not None
    return _shift_months(latest_date, -window.months)


def _start_observation(
    observations: list[tuple[date, float]],
    *,
    target: date | None,
    since_inception: bool,
    tolerance_days: int,
) -> tuple[date, float] | None:
    if since_inception:
        return observations[0]
    assert target is not None
    candidates = [item for item in observations if item[0] <= target]
    if not candidates:
        return None
    selected = candidates[-1]
    if (target - selected[0]).days > tolerance_days:
        return None
    return selected


def _shift_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _positive_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    return number if number > 0 else None
