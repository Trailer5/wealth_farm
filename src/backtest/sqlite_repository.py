"""SQLite persistence for reproducible backtest runs."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .models import (
    BACKTEST_VERSION,
    BacktestRun,
    BacktestSample,
    BacktestSpec,
    BacktestTrade,
    EquityPoint,
    SkippedOrder,
    TradeSide,
)


class SQLiteBacktestRepository:
    SCHEMA_VERSION = 20
    SCHEMA_DESCRIPTION = "daily long-only backtest runs"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    spec_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_trades (
                    run_id TEXT NOT NULL,
                    trade_index INTEGER NOT NULL,
                    trade_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, trade_index),
                    FOREIGN KEY (run_id)
                        REFERENCES backtest_runs(run_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS backtest_equity_curve (
                    run_id TEXT NOT NULL,
                    point_index INTEGER NOT NULL,
                    point_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, point_index),
                    FOREIGN KEY (run_id)
                        REFERENCES backtest_runs(run_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS backtest_skipped_orders (
                    run_id TEXT NOT NULL,
                    order_index INTEGER NOT NULL,
                    order_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, order_index),
                    FOREIGN KEY (run_id)
                        REFERENCES backtest_runs(run_id)
                        ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (
                    version,
                    description
                ) VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save(self, run: BacktestRun) -> BacktestRun:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_runs (
                    run_id,
                    spec_json,
                    status,
                    metrics_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (run_id) DO UPDATE SET
                    spec_json = excluded.spec_json,
                    status = excluded.status,
                    metrics_json = excluded.metrics_json,
                    created_at = excluded.created_at
                """,
                [
                    run.spec.run_id,
                    _json(_spec_dict(run.spec)),
                    run.status,
                    _json(run.metrics),
                    run.created_at,
                ],
            )
            for table in (
                "backtest_trades",
                "backtest_equity_curve",
                "backtest_skipped_orders",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE run_id = ?",
                    [run.spec.run_id],
                )
            conn.executemany(
                """
                INSERT INTO backtest_trades (
                    run_id,
                    trade_index,
                    trade_json
                ) VALUES (?, ?, ?)
                """,
                [
                    [
                        run.spec.run_id,
                        index,
                        _json(_trade_dict(trade)),
                    ]
                    for index, trade in enumerate(run.trades)
                ],
            )
            conn.executemany(
                """
                INSERT INTO backtest_equity_curve (
                    run_id,
                    point_index,
                    point_json
                ) VALUES (?, ?, ?)
                """,
                [
                    [
                        run.spec.run_id,
                        index,
                        _json(_equity_dict(point)),
                    ]
                    for index, point in enumerate(
                        run.equity_curve
                    )
                ],
            )
            conn.executemany(
                """
                INSERT INTO backtest_skipped_orders (
                    run_id,
                    order_index,
                    order_json
                ) VALUES (?, ?, ?)
                """,
                [
                    [
                        run.spec.run_id,
                        index,
                        _json(_skipped_dict(order)),
                    ]
                    for index, order in enumerate(
                        run.skipped_orders
                    )
                ],
            )
        return run

    def get(self, run_id: str) -> BacktestRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM backtest_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            if row is None:
                return None
            trade_rows = conn.execute(
                """
                SELECT trade_json
                FROM backtest_trades
                WHERE run_id = ?
                ORDER BY trade_index
                """,
                [run_id],
            ).fetchall()
            point_rows = conn.execute(
                """
                SELECT point_json
                FROM backtest_equity_curve
                WHERE run_id = ?
                ORDER BY point_index
                """,
                [run_id],
            ).fetchall()
            skipped_rows = conn.execute(
                """
                SELECT order_json
                FROM backtest_skipped_orders
                WHERE run_id = ?
                ORDER BY order_index
                """,
                [run_id],
            ).fetchall()
        return BacktestRun(
            spec=_spec_from_dict(
                json.loads(str(row["spec_json"]))
            ),
            trades=tuple(
                _trade_from_dict(
                    json.loads(str(item["trade_json"]))
                )
                for item in trade_rows
            ),
            equity_curve=tuple(
                _equity_from_dict(
                    json.loads(str(item["point_json"]))
                )
                for item in point_rows
            ),
            skipped_orders=tuple(
                _skipped_from_dict(
                    json.loads(str(item["order_json"]))
                )
                for item in skipped_rows
            ),
            metrics={
                key: _metric_value(value)
                for key, value in json.loads(
                    str(row["metrics_json"])
                ).items()
            },
            status=str(row["status"]),
            created_at=str(row["created_at"]),
        )

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute(
                    "PRAGMA foreign_keys = ON"
                )
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _spec_dict(spec: BacktestSpec) -> dict[str, object]:
    return {
        "run_id": spec.run_id,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.strategy_version,
        "asset_id": spec.asset_id,
        "start_date": spec.start_date,
        "end_date": spec.end_date,
        "initial_cash": format(spec.initial_cash, "f"),
        "fee_rate": format(spec.fee_rate, "f"),
        "slippage_rate": format(spec.slippage_rate, "f"),
        "sample": spec.sample.value,
        "signal_delay_sessions": spec.signal_delay_sessions,
        "benchmark_asset_id": spec.benchmark_asset_id,
        "risk_free_rate": spec.risk_free_rate,
        "annualization_days": spec.annualization_days,
        "price_adjustment": spec.price_adjustment,
        "version": spec.version,
    }


def _spec_from_dict(value) -> BacktestSpec:
    return BacktestSpec(
        run_id=value["run_id"],
        strategy_id=value["strategy_id"],
        strategy_version=value["strategy_version"],
        asset_id=value["asset_id"],
        start_date=value["start_date"],
        end_date=value["end_date"],
        initial_cash=Decimal(value["initial_cash"]),
        fee_rate=Decimal(value["fee_rate"]),
        slippage_rate=Decimal(value["slippage_rate"]),
        sample=BacktestSample(value["sample"]),
        signal_delay_sessions=int(value["signal_delay_sessions"]),
        benchmark_asset_id=value.get("benchmark_asset_id"),
        risk_free_rate=float(value["risk_free_rate"]),
        annualization_days=int(value["annualization_days"]),
        price_adjustment=value["price_adjustment"],
        version=value.get("version", BACKTEST_VERSION),
    )


def _trade_dict(trade: BacktestTrade) -> dict[str, object]:
    return {
        "signal_date": trade.signal_date,
        "trade_date": trade.trade_date,
        "side": trade.side.value,
        "quantity": format(trade.quantity, "f"),
        "market_open": format(trade.market_open, "f"),
        "execution_price": format(
            trade.execution_price,
            "f",
        ),
        "notional": format(trade.notional, "f"),
        "fee": format(trade.fee, "f"),
        "slippage_cost": format(trade.slippage_cost, "f"),
        "reason": trade.reason,
    }


def _trade_from_dict(value) -> BacktestTrade:
    return BacktestTrade(
        signal_date=value["signal_date"],
        trade_date=value["trade_date"],
        side=TradeSide(value["side"]),
        quantity=Decimal(value["quantity"]),
        market_open=Decimal(value["market_open"]),
        execution_price=Decimal(value["execution_price"]),
        notional=Decimal(value["notional"]),
        fee=Decimal(value["fee"]),
        slippage_cost=Decimal(value["slippage_cost"]),
        reason=value["reason"],
    )


def _equity_dict(point: EquityPoint) -> dict[str, object]:
    return {
        "trade_date": point.trade_date,
        "cash": format(point.cash, "f"),
        "quantity": format(point.quantity, "f"),
        "close": format(point.close, "f"),
        "equity": format(point.equity, "f"),
        "benchmark_value": (
            format(point.benchmark_value, "f")
            if point.benchmark_value is not None
            else None
        ),
    }


def _equity_from_dict(value) -> EquityPoint:
    return EquityPoint(
        trade_date=value["trade_date"],
        cash=Decimal(value["cash"]),
        quantity=Decimal(value["quantity"]),
        close=Decimal(value["close"]),
        equity=Decimal(value["equity"]),
        benchmark_value=(
            Decimal(value["benchmark_value"])
            if value.get("benchmark_value") is not None
            else None
        ),
    )


def _skipped_dict(order: SkippedOrder) -> dict[str, object]:
    return {
        "signal_date": order.signal_date,
        "attempted_date": order.attempted_date,
        "reason": order.reason,
    }


def _skipped_from_dict(value) -> SkippedOrder:
    return SkippedOrder(
        signal_date=value["signal_date"],
        attempted_date=value.get("attempted_date"),
        reason=value["reason"],
    )


def _metric_value(value):
    if isinstance(value, (int, type(None))):
        return value
    if isinstance(value, str) and value in {
        item.value for item in BacktestSample
    }:
        return value
    return Decimal(str(value))
