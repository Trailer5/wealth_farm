"""A-share market breadth and speculation-temperature calculations."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import fmean, pstdev

from .models import (
    A_SHARE_MARKET_CALCULATION_VERSION,
    MarketCrossSectionRow,
    MarketFactorParameters,
    MarketFactorSnapshot,
    MarketFactorValue,
)


class AShareMarketFactorCalculator:
    """Calculate market-wide values without binding them to an asset."""

    def calculate(
        self,
        rows: list[MarketCrossSectionRow]
        | tuple[MarketCrossSectionRow, ...],
        parameters: MarketFactorParameters,
    ) -> MarketFactorSnapshot:
        input_rows = tuple(rows)
        as_of_rows = tuple(
            row
            for row in input_rows
            if row.trade_date == parameters.as_of_date
        )
        dated_rows = tuple(
            row
            for row in as_of_rows
            if row.tradable
        )
        excluded_non_tradable = len(as_of_rows) - len(dated_rows)
        computed_at = datetime.now(timezone.utc).isoformat()
        values: list[MarketFactorValue] = []

        return_rows = tuple(
            row
            for row in dated_rows
            if row.current_close is not None
            and row.previous_close is not None
        )
        returns = [_return_1d(row) for row in return_rows]
        if return_rows:
            values.append(
                self._value(
                    "advance_ratio",
                    sum(value > 0 for value in returns)
                    / len(return_rows),
                    parameters,
                    sample_count=len(return_rows),
                    excluded_non_tradable=excluded_non_tradable,
                    excluded_missing=len(dated_rows)
                    - len(return_rows),
                    computed_at=computed_at,
                )
            )
            up_count = sum(
                row.limit_state == 1 for row in return_rows
            )
            down_count = sum(
                row.limit_state == -1 for row in return_rows
            )
            values.append(
                self._value(
                    "limit_up_down_spread",
                    (up_count - down_count) / len(return_rows),
                    parameters,
                    sample_count=len(return_rows),
                    excluded_non_tradable=excluded_non_tradable,
                    excluded_missing=len(dated_rows)
                    - len(return_rows),
                    computed_at=computed_at,
                )
            )
            values.append(
                self._value(
                    "equal_weight_excess",
                    fmean(returns) - parameters.benchmark_return,
                    parameters,
                    sample_count=len(return_rows),
                    excluded_non_tradable=excluded_non_tradable,
                    excluded_missing=len(dated_rows)
                    - len(return_rows),
                    computed_at=computed_at,
                )
            )

        ma20_rows = tuple(
            row
            for row in dated_rows
            if row.current_close is not None
            and len(row.history_closes) >= 20
        )
        if ma20_rows:
            above_count = sum(
                row.current_close
                > fmean(row.history_closes[-20:])
                for row in ma20_rows
            )
            values.append(
                self._value(
                    "above_ma20_ratio",
                    above_count / len(ma20_rows),
                    parameters,
                    sample_count=len(ma20_rows),
                    excluded_non_tradable=excluded_non_tradable,
                    excluded_missing=len(dated_rows)
                    - len(ma20_rows),
                    computed_at=computed_at,
                    extra_parameters={
                        "ma_window": 20,
                        "excluded_insufficient_history": sum(
                            row.trade_date == parameters.as_of_date
                            and row.current_close is not None
                            and len(row.history_closes) < 20
                            for row in dated_rows
                        ),
                    },
                )
            )

        turnover_rows = tuple(
            row for row in dated_rows if row.amount is not None
        )
        historical_turnover = parameters.historical_turnover_amounts
        if turnover_rows and historical_turnover:
            turnover_std = pstdev(historical_turnover)
            if turnover_std > 0:
                current_turnover = sum(
                    row.amount for row in turnover_rows
                    if row.amount is not None
                )
                values.append(
                    self._value(
                        "market_turnover_amount_zscore",
                        (
                            current_turnover
                            - fmean(historical_turnover)
                        )
                        / turnover_std,
                        parameters,
                        sample_count=len(turnover_rows),
                        excluded_non_tradable=(
                            excluded_non_tradable
                        ),
                        excluded_missing=len(dated_rows)
                        - len(turnover_rows),
                        computed_at=computed_at,
                        extra_parameters={
                            "variance_method": "population",
                            "historical_sample_count": len(
                                historical_turnover
                            ),
                        },
                    )
                )

        sized_rows = tuple(
            row
            for row in return_rows
            if row.size_bucket in {"small", "large"}
        )
        small_returns = [
            _return_1d(row)
            for row in sized_rows
            if row.size_bucket == "small"
        ]
        large_returns = [
            _return_1d(row)
            for row in sized_rows
            if row.size_bucket == "large"
        ]
        if small_returns and large_returns:
            values.append(
                self._value(
                    "small_cap_relative_strength",
                    fmean(small_returns) - fmean(large_returns),
                    parameters,
                    sample_count=len(sized_rows),
                    excluded_non_tradable=excluded_non_tradable,
                    excluded_missing=len(dated_rows)
                    - len(sized_rows),
                    computed_at=computed_at,
                    extra_parameters={
                        "small_sample_count": len(small_returns),
                        "large_sample_count": len(large_returns),
                    },
                )
            )

        values.sort(key=lambda item: item.factor_name)
        return MarketFactorSnapshot(
            parameters=parameters,
            values=tuple(values),
        )

    @staticmethod
    def _value(
        factor_name: str,
        value: float,
        parameters: MarketFactorParameters,
        *,
        sample_count: int,
        excluded_non_tradable: int,
        excluded_missing: int,
        computed_at: str,
        extra_parameters: dict[str, object] | None = None,
    ) -> MarketFactorValue:
        stored_parameters: dict[str, object] = {
            "sample_count": sample_count,
            "excluded_non_tradable": excluded_non_tradable,
            "excluded_missing": excluded_missing,
            "scope_id": parameters.scope_id,
            "universe_id": parameters.universe_id,
            "universe_version": parameters.universe_version,
            "universe_effective_date": (
                parameters.universe_effective_date
            ),
            "benchmark_return": parameters.benchmark_return,
            "historical_turnover_amounts": list(
                parameters.historical_turnover_amounts
            ),
        }
        if extra_parameters:
            stored_parameters.update(extra_parameters)
        if not math.isfinite(value):
            raise ValueError(f"{factor_name} produced a non-finite value")
        return MarketFactorValue(
            scope_id=parameters.scope_id,
            universe_id=parameters.universe_id,
            universe_version=parameters.universe_version,
            factor_name=factor_name,
            as_of_date=parameters.as_of_date,
            value=value,
            calculation_version=A_SHARE_MARKET_CALCULATION_VERSION,
            parameters=stored_parameters,
            computed_at=computed_at,
        )


def _return_1d(row: MarketCrossSectionRow) -> float:
    if row.current_close is None or row.previous_close is None:
        raise ValueError("return row requires current and previous close")
    return row.current_close / row.previous_close - 1.0
