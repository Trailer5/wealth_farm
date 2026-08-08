"""Pure-Python return, drawdown, and risk-adjusted metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .models import FactorValue


RISK_CALCULATION_VERSION = "risk_daily_v1"


@dataclass(frozen=True)
class RiskMetricParameters:
    window: int | None = None
    rolling_windows: tuple[int, ...] = (5, 20, 60)
    annualization_days: int = 252
    risk_free_rate: float = 0.0
    frequency: str = "daily"
    price_adjustment: str = "provider_default"

    def __post_init__(self) -> None:
        if self.window is not None and self.window < 1:
            raise ValueError("window must be positive")
        if self.annualization_days <= 0:
            raise ValueError("annualization_days must be positive")
        if (
            any(window < 1 for window in self.rolling_windows)
            or len(set(self.rolling_windows))
            != len(self.rolling_windows)
        ):
            raise ValueError(
                "rolling_windows must contain unique positive values"
            )
        if self.risk_free_rate <= -1:
            raise ValueError("risk_free_rate must be greater than -1")
        if self.frequency != "daily":
            raise ValueError("Only daily frequency is supported")
        if not self.price_adjustment.strip():
            raise ValueError("price_adjustment must not be blank")

    def to_dict(self) -> dict[str, object]:
        return {
            "window": self.window or "input",
            "annualization_days": self.annualization_days,
            "rolling_windows": list(self.rolling_windows),
            "risk_free_rate": self.risk_free_rate,
            "frequency": self.frequency,
            "price_adjustment": self.price_adjustment,
            "variance_method": "population",
        }


@dataclass(frozen=True)
class DrawdownEpisode:
    max_drawdown: float
    peak_date: str
    trough_date: str
    recovery_date: str | None
    duration_days: int
    recovery_days: int | None


@dataclass(frozen=True)
class RiskCalculationResult:
    asset_id: str
    parameters: RiskMetricParameters
    series: tuple[FactorValue, ...]
    metrics: tuple[FactorValue, ...]
    drawdown_episode: DrawdownEpisode | None

    @property
    def values(self) -> tuple[FactorValue, ...]:
        return self.series + self.metrics


class RiskMetricCalculator:
    """Calculate explicit daily risk statistics without strategy logic."""

    def calculate(
        self,
        *,
        asset_id: str,
        records: list[dict[str, Any]],
        source_dataset: str,
        parameters: RiskMetricParameters | None = None,
    ) -> RiskCalculationResult:
        config = parameters or RiskMetricParameters()
        normalized = _normalized_closes(records)
        if config.window is not None:
            normalized = normalized[-(config.window + 1) :]
        if len(normalized) < 2:
            return RiskCalculationResult(
                asset_id=asset_id,
                parameters=config,
                series=(),
                metrics=(),
                drawdown_episode=None,
            )

        dates = [item[0] for item in normalized]
        closes = [item[1] for item in normalized]
        returns = [
            closes[index] / closes[index - 1] - 1.0
            for index in range(1, len(closes))
        ]
        cumulative = [
            close / closes[0] - 1.0
            for close in closes
        ]
        drawdowns, episode = _drawdown_series(dates, closes)
        computed_at = datetime.now(timezone.utc).isoformat()
        common = {
            "asset_id": asset_id,
            "calculation_version": RISK_CALCULATION_VERSION,
            "parameters": config.to_dict(),
            "input_start_date": dates[0],
            "input_end_date": dates[-1],
            "source_dataset": source_dataset,
            "computed_at": computed_at,
        }
        daily_risk_free = (
            (1.0 + config.risk_free_rate)
            ** (1.0 / config.annualization_days)
            - 1.0
        )
        series: list[FactorValue] = []
        for index, trade_date in enumerate(dates):
            series.append(
                FactorValue(
                    factor_name="cumulative_return",
                    as_of_date=trade_date,
                    value=cumulative[index],
                    **common,
                )
            )
            series.append(
                FactorValue(
                    factor_name="drawdown_series",
                    as_of_date=trade_date,
                    value=drawdowns[index],
                    **common,
                )
            )
            if index > 0:
                series.append(
                    FactorValue(
                        factor_name="daily_return",
                        as_of_date=trade_date,
                        value=returns[index - 1],
                        **common,
                    )
                )
            for window in config.rolling_windows:
                if index >= window:
                    series.append(
                        FactorValue(
                            factor_name=(
                                f"rolling_return_{window}d"
                            ),
                            as_of_date=trade_date,
                            value=(
                                closes[index]
                                / closes[index - window]
                                - 1.0
                            ),
                            **common,
                        )
                    )
                    window_returns = returns[
                        index - window : index
                    ]
                    window_mean = (
                        sum(window_returns) / window
                    )
                    window_variance = sum(
                        (value - window_mean) ** 2
                        for value in window_returns
                    ) / window
                    window_volatility = math.sqrt(
                        window_variance
                    ) * math.sqrt(config.annualization_days)
                    series.append(
                        FactorValue(
                            factor_name=(
                                "rolling_annualized_volatility_"
                                f"{window}d"
                            ),
                            as_of_date=trade_date,
                            value=window_volatility,
                            **common,
                        )
                    )
                    if window_volatility > 0:
                        series.append(
                            FactorValue(
                                factor_name=(
                                    f"rolling_sharpe_{window}d"
                                ),
                                as_of_date=trade_date,
                                value=(
                                    (
                                        window_mean
                                        - daily_risk_free
                                    )
                                    * config.annualization_days
                                    / window_volatility
                                ),
                                **common,
                            )
                        )

        periods = len(returns)
        annualized_return = (
            closes[-1] / closes[0]
        ) ** (config.annualization_days / periods) - 1.0
        mean_return = sum(returns) / periods
        variance = sum(
            (value - mean_return) ** 2
            for value in returns
        ) / periods
        daily_volatility = math.sqrt(variance)
        annualized_volatility = daily_volatility * math.sqrt(
            config.annualization_days
        )
        downside_values = [
            min(value - daily_risk_free, 0.0)
            for value in returns
        ]
        downside_deviation = math.sqrt(
            sum(value * value for value in downside_values)
            / periods
        )
        annualized_downside = downside_deviation * math.sqrt(
            config.annualization_days
        )
        annualized_excess = (
            mean_return - daily_risk_free
        ) * config.annualization_days

        metric_parameters = dict(config.to_dict())
        metric_parameters.update(
            {
                "drawdown_peak_date": episode.peak_date,
                "drawdown_trough_date": episode.trough_date,
                "drawdown_recovery_date": episode.recovery_date,
            }
        )
        metric_common = {
            **common,
            "as_of_date": dates[-1],
        }
        metrics = [
            FactorValue(
                factor_name="annualized_return",
                value=annualized_return,
                **metric_common,
            ),
            FactorValue(
                factor_name="annualized_volatility",
                value=annualized_volatility,
                **metric_common,
            ),
            FactorValue(
                factor_name="downside_volatility",
                value=annualized_downside,
                **metric_common,
            ),
            FactorValue(
                factor_name="max_drawdown",
                value=episode.max_drawdown,
                parameters=metric_parameters,
                **{
                    key: value
                    for key, value in metric_common.items()
                    if key != "parameters"
                },
            ),
            FactorValue(
                factor_name="drawdown_duration_days",
                value=float(episode.duration_days),
                parameters=metric_parameters,
                **{
                    key: value
                    for key, value in metric_common.items()
                    if key != "parameters"
                },
            ),
        ]
        if episode.recovery_days is not None:
            metrics.append(
                FactorValue(
                    factor_name="drawdown_recovery_days",
                    value=float(episode.recovery_days),
                    parameters=metric_parameters,
                    **{
                        key: value
                        for key, value in metric_common.items()
                        if key != "parameters"
                    },
                )
            )
        if annualized_volatility > 0:
            metrics.append(
                FactorValue(
                    factor_name="sharpe_ratio",
                    value=(
                        annualized_excess
                        / annualized_volatility
                    ),
                    **metric_common,
                )
            )
        if annualized_downside > 0:
            metrics.append(
                FactorValue(
                    factor_name="sortino_ratio",
                    value=annualized_excess / annualized_downside,
                    **metric_common,
                )
            )
        if episode.max_drawdown < 0:
            metrics.append(
                FactorValue(
                    factor_name="calmar_ratio",
                    value=(
                        annualized_return
                        / abs(episode.max_drawdown)
                    ),
                    **metric_common,
                )
            )
        metrics.sort(key=lambda item: item.factor_name)
        series.sort(
            key=lambda item: (item.as_of_date, item.factor_name)
        )
        return RiskCalculationResult(
            asset_id=asset_id,
            parameters=config,
            series=tuple(series),
            metrics=tuple(metrics),
            drawdown_episode=episode,
        )


def _drawdown_series(
    dates: list[str],
    closes: list[float],
) -> tuple[list[float], DrawdownEpisode]:
    peak_value = closes[0]
    peak_index = 0
    drawdowns: list[float] = []
    max_drawdown = 0.0
    max_peak_index = 0
    trough_index = 0
    for index, close in enumerate(closes):
        if close > peak_value:
            peak_value = close
            peak_index = index
        drawdown = close / peak_value - 1.0
        drawdowns.append(drawdown)
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_peak_index = peak_index
            trough_index = index

    recovery_index = None
    if max_drawdown == 0:
        recovery_index = max_peak_index
    else:
        recovery_level = closes[max_peak_index]
        for index in range(trough_index + 1, len(closes)):
            if closes[index] >= recovery_level:
                recovery_index = index
                break
    end_index = (
        recovery_index
        if recovery_index is not None
        else len(closes) - 1
    )
    duration_days = (
        date.fromisoformat(dates[end_index])
        - date.fromisoformat(dates[max_peak_index])
    ).days
    recovery_days = (
        (
            date.fromisoformat(dates[recovery_index])
            - date.fromisoformat(dates[trough_index])
        ).days
        if recovery_index is not None
        else None
    )
    return (
        drawdowns,
        DrawdownEpisode(
            max_drawdown=max_drawdown,
            peak_date=dates[max_peak_index],
            trough_date=dates[trough_index],
            recovery_date=(
                dates[recovery_index]
                if recovery_index is not None
                else None
            ),
            duration_days=duration_days,
            recovery_days=recovery_days,
        ),
    )


def _normalized_closes(
    records: list[dict[str, Any]],
) -> list[tuple[str, float]]:
    by_date: dict[str, float] = {}
    for record in records:
        trade_date = str(record.get("trade_date", "")).strip()
        close_value = record.get("close")
        if not trade_date or close_value in (None, ""):
            continue
        date.fromisoformat(trade_date)
        close = float(close_value)
        if close <= 0:
            raise ValueError(f"close must be positive on {trade_date}")
        if trade_date in by_date:
            raise ValueError(f"duplicate trade_date: {trade_date}")
        by_date[trade_date] = close
    return sorted(by_date.items())
