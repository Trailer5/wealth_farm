"""Daily long-only backtest contracts and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


BACKTEST_VERSION = "daily_long_only_v1"


class BacktestSample(str, Enum):
    IN_SAMPLE = "in_sample"
    OUT_OF_SAMPLE = "out_of_sample"
    FULL = "full"


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class BacktestSpec:
    run_id: str
    strategy_id: str
    strategy_version: str
    asset_id: str
    start_date: str
    end_date: str
    initial_cash: Decimal
    fee_rate: Decimal
    slippage_rate: Decimal
    sample: BacktestSample
    signal_delay_sessions: int = 1
    benchmark_asset_id: str | None = None
    risk_free_rate: float = 0.0
    annualization_days: int = 252
    price_adjustment: str = "provider_default"
    version: str = BACKTEST_VERSION

    def __post_init__(self) -> None:
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if start > end:
            raise ValueError("start_date must not exceed end_date")
        for value, name in (
            (self.run_id, "run_id"),
            (self.strategy_id, "strategy_id"),
            (self.strategy_version, "strategy_version"),
            (self.asset_id, "asset_id"),
            (self.price_adjustment, "price_adjustment"),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if not Decimal("0") <= self.fee_rate < Decimal("1"):
            raise ValueError("fee_rate must be in [0, 1)")
        if not Decimal("0") <= self.slippage_rate < Decimal("1"):
            raise ValueError(
                "slippage_rate must be in [0, 1)"
            )
        if self.signal_delay_sessions < 1:
            raise ValueError(
                "signal_delay_sessions must be at least 1"
            )
        if self.annualization_days <= 0:
            raise ValueError(
                "annualization_days must be positive"
            )


@dataclass(frozen=True)
class BacktestBar:
    trade_date: str
    open: Decimal
    close: Decimal
    tradable: bool = True
    limit_up: bool = False
    limit_down: bool = False

    def __post_init__(self) -> None:
        date.fromisoformat(self.trade_date)
        if self.open <= 0 or self.close <= 0:
            raise ValueError("Backtest prices must be positive")


@dataclass(frozen=True)
class BacktestTrade:
    signal_date: str
    trade_date: str
    side: TradeSide
    quantity: Decimal
    market_open: Decimal
    execution_price: Decimal
    notional: Decimal
    fee: Decimal
    slippage_cost: Decimal
    reason: str


@dataclass(frozen=True)
class EquityPoint:
    trade_date: str
    cash: Decimal
    quantity: Decimal
    close: Decimal
    equity: Decimal
    benchmark_value: Decimal | None = None


@dataclass(frozen=True)
class SkippedOrder:
    signal_date: str
    attempted_date: str | None
    reason: str


@dataclass(frozen=True)
class BacktestRun:
    spec: BacktestSpec
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    skipped_orders: tuple[SkippedOrder, ...]
    metrics: dict[str, Decimal | int | str | None]
    status: str = "completed"
    created_at: str = ""
