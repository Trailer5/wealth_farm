"""Daily A-share margin-trading factor calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
from typing import Mapping, Sequence

from .models import FactorValue


MARGIN_CALCULATION_VERSION = "margin_daily_v1"
_UNIT = "CNY"


@dataclass(frozen=True)
class MarginTradingRecord:
    """Normalized daily margin-trading data with CNY amount fields."""

    symbol: str
    trade_date: str
    margin_balance: float | None
    margin_buy_amount: float | None
    margin_repay_amount: float | None = None
    short_balance: float | None = None
    short_volume: float | None = None
    short_sell_volume: float | None = None
    short_repay_volume: float | None = None
    total_margin_balance: float | None = None
    source: str = ""
    scope: str = ""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be blank")
        if not self.source.strip():
            raise ValueError("source must not be blank")
        if not self.scope.strip():
            raise ValueError("scope must not be blank")
        try:
            parsed_date = date.fromisoformat(self.trade_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("trade_date must be an ISO date") from exc
        if parsed_date.isoformat() != self.trade_date:
            raise ValueError("trade_date must be an ISO date")

        for item in fields(self):
            if item.name in {
                "symbol",
                "trade_date",
                "source",
                "scope",
            }:
                continue
            value = getattr(self, item.name)
            if value is None:
                continue
            try:
                numeric_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{item.name} must be numeric when provided"
                ) from exc
            if not math.isfinite(numeric_value) or numeric_value < 0:
                raise ValueError(
                    f"{item.name} must be non-negative when provided"
                )


@dataclass(frozen=True)
class MarginFactorParameters:
    window: int = 5

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")

    def to_dict(self) -> dict[str, object]:
        return {"window": self.window, "unit": _UNIT}


class MarginFactorCalculator:
    """Calculate explainable daily leverage factors without imputation."""

    def calculate(
        self,
        *,
        asset_id: str,
        records: Sequence[MarginTradingRecord],
        daily_amount_by_date: Mapping[str, float | None],
        as_of_date: str,
        parameters: MarginFactorParameters | None = None,
    ) -> list[FactorValue]:
        cutoff = _iso_date(as_of_date, field_name="as_of_date")
        config = parameters or MarginFactorParameters()
        eligible = sorted(
            (
                record
                for record in records
                if record.trade_date <= cutoff
            ),
            key=lambda record: record.trade_date,
        )
        _reject_duplicate_dates(eligible)
        if not eligible:
            return []

        selected = eligible[-(config.window + 1) :]
        current = selected[-1]
        computed_at = datetime.now(timezone.utc).isoformat()
        common = {
            "asset_id": asset_id,
            "as_of_date": current.trade_date,
            "calculation_version": MARGIN_CALCULATION_VERSION,
            "parameters": config.to_dict(),
            "input_start_date": selected[0].trade_date,
            "input_end_date": current.trade_date,
            "source_dataset": ",".join(
                sorted(
                    {
                        record.source
                        for record in selected
                        if record.source
                    }
                )
            ),
            "computed_at": computed_at,
        }
        factors: list[FactorValue] = []

        if len(selected) >= config.window + 1:
            prior = selected[0]
            if (
                current.margin_balance is not None
                and prior.margin_balance is not None
                and prior.margin_balance > 0
            ):
                factors.append(
                    FactorValue(
                        factor_name="margin_balance_change",
                        value=(
                            float(current.margin_balance)
                            - float(prior.margin_balance)
                        )
                        / float(prior.margin_balance),
                        **common,
                    )
                )

        daily_amount = daily_amount_by_date.get(current.trade_date)
        if daily_amount is not None and float(daily_amount) > 0:
            denominator = float(daily_amount)
            if current.margin_buy_amount is not None:
                factors.append(
                    FactorValue(
                        factor_name="margin_buy_ratio",
                        value=float(current.margin_buy_amount)
                        / denominator,
                        **common,
                    )
                )
            if current.margin_balance is not None:
                factors.append(
                    FactorValue(
                        factor_name="margin_balance_to_amount",
                        value=float(current.margin_balance)
                        / denominator,
                        **common,
                    )
                )

        factors.sort(key=lambda factor: factor.factor_name)
        return factors


def _iso_date(value: str, *, field_name: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must be an ISO date")
    return value


def _reject_duplicate_dates(
    records: Sequence[MarginTradingRecord],
) -> None:
    previous_date: str | None = None
    for record in records:
        if record.trade_date == previous_date:
            raise ValueError(
                f"duplicate trade_date: {record.trade_date}"
            )
        previous_date = record.trade_date
