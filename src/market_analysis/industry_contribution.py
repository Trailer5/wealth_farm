"""Market-level industry contribution using prior-day float-cap weights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class IndustryMarketMetric:
    industry_code: str
    industry_name: str
    trade_date: str
    return_ratio: Decimal
    float_market_cap: Decimal

    def __post_init__(self) -> None:
        if not self.industry_code.strip() or not self.industry_name.strip():
            raise ValueError(
                "industry_code and industry_name must not be blank"
            )
        date.fromisoformat(self.trade_date)
        if (
            not self.return_ratio.is_finite()
            or not self.float_market_cap.is_finite()
            or self.float_market_cap <= 0
        ):
            raise ValueError(
                "return_ratio and float_market_cap must be finite, "
                "with positive market cap"
            )


@dataclass(frozen=True)
class IndustryContribution:
    industry_code: str
    industry_name: str
    trade_date: str
    prior_weight: Decimal
    return_ratio: Decimal
    contribution: Decimal


class IndustryContributionCalculator:
    def calculate(
        self,
        prior_metrics: tuple[IndustryMarketMetric, ...],
        current_metrics: tuple[IndustryMarketMetric, ...],
    ) -> tuple[IndustryContribution, ...]:
        if not prior_metrics or not current_metrics:
            raise ValueError("prior and current metrics must not be empty")
        prior_dates = {item.trade_date for item in prior_metrics}
        current_dates = {item.trade_date for item in current_metrics}
        if len(prior_dates) != 1 or len(current_dates) != 1:
            raise ValueError("each metric set must contain one trade date")
        prior_date = next(iter(prior_dates))
        current_date = next(iter(current_dates))
        if prior_date >= current_date:
            raise ValueError("prior date must precede current date")
        prior_by_code = _unique(prior_metrics)
        current_by_code = _unique(current_metrics)
        common_codes = sorted(set(prior_by_code) & set(current_by_code))
        if not common_codes:
            raise ValueError("industry sets have no common members")
        total_cap = sum(
            (
                prior_by_code[code].float_market_cap
                for code in common_codes
            ),
            Decimal("0"),
        )
        if total_cap <= 0:
            raise ValueError("common prior market cap must be positive")
        return tuple(
            IndustryContribution(
                industry_code=code,
                industry_name=current_by_code[code].industry_name,
                trade_date=current_date,
                prior_weight=(
                    prior_by_code[code].float_market_cap / total_cap
                ),
                return_ratio=current_by_code[code].return_ratio,
                contribution=(
                    prior_by_code[code].float_market_cap
                    / total_cap
                    * current_by_code[code].return_ratio
                ),
            )
            for code in common_codes
        )


def _unique(
    values: tuple[IndustryMarketMetric, ...],
) -> dict[str, IndustryMarketMetric]:
    output = {}
    for item in values:
        if item.industry_code in output:
            raise ValueError(
                f"duplicate industry_code: {item.industry_code}"
            )
        output[item.industry_code] = item
    return output
