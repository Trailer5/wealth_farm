"""SQLite persistence for portfolio valuation snapshots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    PortfolioPositionMetric,
    PortfolioSnapshot,
    PortfolioValuation,
)


class SQLitePortfolioRepository:
    """Persist one current-portfolio valuation per user and date."""

    SCHEMA_VERSION = 11
    SCHEMA_DESCRIPTION = "portfolio valuation and position metrics"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        """Create portfolio snapshot tables."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    total_market_value REAL NOT NULL,
                    total_cost_value REAL,
                    unrealized_pnl REAL,
                    max_weight REAL NOT NULL,
                    concentration_hhi REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    UNIQUE (user_id, as_of_date)
                );

                CREATE TABLE IF NOT EXISTS portfolio_position_metrics (
                    snapshot_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    market_price REAL NOT NULL,
                    market_value REAL NOT NULL,
                    cost_value REAL,
                    unrealized_pnl REAL,
                    weight REAL NOT NULL,
                    PRIMARY KEY (snapshot_id, asset_id),
                    FOREIGN KEY (snapshot_id)
                        REFERENCES portfolio_snapshots (snapshot_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_date
                ON portfolio_snapshots (user_id, as_of_date);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save(self, valuation: PortfolioValuation) -> None:
        """Upsert one snapshot and replace its position metrics."""

        snapshot = valuation.snapshot
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portfolio_snapshots (
                    snapshot_id,
                    user_id,
                    as_of_date,
                    total_market_value,
                    total_cost_value,
                    unrealized_pnl,
                    max_weight,
                    concentration_hhi,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_id) DO UPDATE SET
                    total_market_value = excluded.total_market_value,
                    total_cost_value = excluded.total_cost_value,
                    unrealized_pnl = excluded.unrealized_pnl,
                    max_weight = excluded.max_weight,
                    concentration_hhi = excluded.concentration_hhi,
                    created_at = excluded.created_at
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.user_id,
                    snapshot.as_of_date,
                    snapshot.total_market_value,
                    snapshot.total_cost_value,
                    snapshot.unrealized_pnl,
                    snapshot.max_weight,
                    snapshot.concentration_hhi,
                    snapshot.created_at,
                ],
            )
            conn.execute(
                """
                DELETE FROM portfolio_position_metrics
                WHERE snapshot_id = ?
                """,
                [snapshot.snapshot_id],
            )
            conn.executemany(
                """
                INSERT INTO portfolio_position_metrics (
                    snapshot_id,
                    asset_id,
                    quantity,
                    market_price,
                    market_value,
                    cost_value,
                    unrealized_pnl,
                    weight
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        position.snapshot_id,
                        position.asset_id,
                        position.quantity,
                        position.market_price,
                        position.market_value,
                        position.cost_value,
                        position.unrealized_pnl,
                        position.weight,
                    ]
                    for position in valuation.positions
                ],
            )

    def get(self, snapshot_id: str, user_id: str) -> PortfolioValuation | None:
        """Return one snapshot scoped to a user."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, user_id, as_of_date, total_market_value,
                       total_cost_value, unrealized_pnl, max_weight,
                       concentration_hhi, created_at
                FROM portfolio_snapshots
                WHERE snapshot_id = ? AND user_id = ?
                """,
                [snapshot_id, user_id],
            ).fetchone()
            if row is None:
                return None
            positions = conn.execute(
                """
                SELECT snapshot_id, asset_id, quantity, market_price,
                       market_value, cost_value, unrealized_pnl, weight
                FROM portfolio_position_metrics
                WHERE snapshot_id = ?
                ORDER BY asset_id
                """,
                [snapshot_id],
            ).fetchall()
        return PortfolioValuation(
            snapshot=_snapshot_from_row(row),
            positions=tuple(_position_from_row(item) for item in positions),
        )

    def list_snapshots(self, user_id: str) -> list[PortfolioSnapshot]:
        """Return snapshot history for one user."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT snapshot_id, user_id, as_of_date, total_market_value,
                       total_cost_value, unrealized_pnl, max_weight,
                       concentration_hhi, created_at
                FROM portfolio_snapshots
                WHERE user_id = ?
                ORDER BY as_of_date DESC
                """,
                [user_id],
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _snapshot_from_row(row: sqlite3.Row) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id=str(row["snapshot_id"]),
        user_id=str(row["user_id"]),
        as_of_date=str(row["as_of_date"]),
        total_market_value=float(row["total_market_value"]),
        total_cost_value=(
            float(row["total_cost_value"])
            if row["total_cost_value"] is not None
            else None
        ),
        unrealized_pnl=(
            float(row["unrealized_pnl"])
            if row["unrealized_pnl"] is not None
            else None
        ),
        max_weight=float(row["max_weight"]),
        concentration_hhi=float(row["concentration_hhi"]),
        created_at=str(row["created_at"]),
    )


def _position_from_row(row: sqlite3.Row) -> PortfolioPositionMetric:
    return PortfolioPositionMetric(
        snapshot_id=str(row["snapshot_id"]),
        asset_id=str(row["asset_id"]),
        quantity=float(row["quantity"]),
        market_price=float(row["market_price"]),
        market_value=float(row["market_value"]),
        cost_value=(
            float(row["cost_value"])
            if row["cost_value"] is not None
            else None
        ),
        unrealized_pnl=(
            float(row["unrealized_pnl"])
            if row["unrealized_pnl"] is not None
            else None
        ),
        weight=float(row["weight"]),
    )
