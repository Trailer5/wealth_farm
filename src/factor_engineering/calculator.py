"""Pure-Python baseline factors for normalized daily bars."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .models import FactorValue


CALCULATION_VERSION = "market_daily_v1"


class MarketFactorCalculator:
    """Calculate a small, explainable factor set from daily closes."""

    def calculate(
        self,
        *,
        asset_id: str,
        records: list[dict[str, Any]],
        source_dataset: str,
    ) -> list[FactorValue]:
        """Return all factors supported by the available input window."""

        normalized = _normalized_closes(records)
        if not normalized:
            return []
        dates = [item[0] for item in normalized]
        closes = [item[1] for item in normalized]
        common = {
            "asset_id": asset_id,
            "as_of_date": dates[-1],
            "calculation_version": CALCULATION_VERSION,
            "input_start_date": dates[0],
            "input_end_date": dates[-1],
            "source_dataset": source_dataset,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        factors: list[FactorValue] = [
            FactorValue(
                factor_name="drawdown",
                value=closes[-1] / max(closes) - 1.0,
                parameters={"window": "input"},
                **common,
            )
        ]
        if len(closes) >= 2:
            factors.append(
                FactorValue(
                    factor_name="return_1d",
                    value=closes[-1] / closes[-2] - 1.0,
                    parameters={"window": 1},
                    **common,
                )
            )
        if len(closes) >= 5:
            factors.append(
                FactorValue(
                    factor_name="sma_5",
                    value=sum(closes[-5:]) / 5.0,
                    parameters={"window": 5},
                    **common,
                )
            )
        if len(closes) >= 6:
            factors.append(
                FactorValue(
                    factor_name="momentum_5",
                    value=closes[-1] / closes[-6] - 1.0,
                    parameters={"window": 5},
                    **common,
                )
            )
            returns = [
                closes[index] / closes[index - 1] - 1.0
                for index in range(len(closes) - 5, len(closes))
            ]
            mean_return = sum(returns) / len(returns)
            variance = sum(
                (value - mean_return) ** 2
                for value in returns
            ) / len(returns)
            factors.append(
                FactorValue(
                    factor_name="volatility_5",
                    value=math.sqrt(variance) * math.sqrt(252.0),
                    parameters={"window": 5, "annualization": 252},
                    **common,
                )
            )
        factors.sort(key=lambda factor: factor.factor_name)
        return factors


def _normalized_closes(
    records: list[dict[str, Any]],
) -> list[tuple[str, float]]:
    by_date: dict[str, float] = {}
    for record in records:
        trade_date = str(record.get("trade_date", "")).strip()
        close_value = record.get("close")
        if not trade_date or close_value in (None, ""):
            continue
        close = float(close_value)
        if close <= 0:
            raise ValueError(f"close must be positive on {trade_date}")
        if trade_date in by_date:
            raise ValueError(f"duplicate trade_date: {trade_date}")
        by_date[trade_date] = close
    return sorted(by_date.items())
