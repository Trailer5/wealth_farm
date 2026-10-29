"""SQLite persistence for portfolio valuation snapshots."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .budget import InvestmentMandate
from .preference import InvestmentPreference
from .trades import TradeRecord
from .models import (
    PortfolioConstraintSnapshot,
    PortfolioPositionMetric,
    PortfolioSnapshot,
    PortfolioValuation,
    portfolio_valuation_digest,
)


class SQLitePortfolioRepository:
    """Persist one current-portfolio valuation per user and date."""

    SCHEMA_VERSION = 33
    SCHEMA_DESCRIPTION = (
        "portfolio constraints, mandates, valuation, and metrics"
    )

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
                    base_currency TEXT NOT NULL DEFAULT 'CNY',
                    holdings_as_of_date TEXT,
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    valuation_digest TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS investment_mandates (
                    mandate_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_investment_mandates_user
                ON investment_mandates (user_id, mandate_id);

                CREATE TABLE IF NOT EXISTS investment_preferences (
                    preference_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    recorded_on TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    UNIQUE (user_id, version)
                );

                CREATE TABLE IF NOT EXISTS trade_records (
                    trade_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_trade_records_user_date
                ON trade_records (user_id, trade_date);

                CREATE TABLE IF NOT EXISTS portfolio_constraint_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    mandate_id TEXT NOT NULL,
                    portfolio_snapshot_id TEXT NOT NULL,
                    portfolio_valuation_digest TEXT NOT NULL DEFAULT '',
                    subject_asset_id TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (mandate_id)
                        REFERENCES investment_mandates (mandate_id) ON DELETE RESTRICT,
                    FOREIGN KEY (portfolio_snapshot_id)
                        REFERENCES portfolio_snapshots (snapshot_id) ON DELETE RESTRICT,
                    FOREIGN KEY (subject_asset_id)
                        REFERENCES assets (asset_id) ON DELETE RESTRICT
                );

                CREATE INDEX IF NOT EXISTS idx_constraint_snapshots_user_date
                ON portfolio_constraint_snapshots (user_id, as_of_date);

                CREATE TRIGGER IF NOT EXISTS freeze_referenced_portfolio_update
                BEFORE UPDATE ON portfolio_snapshots
                WHEN EXISTS (
                    SELECT 1
                    FROM portfolio_constraint_snapshots
                    WHERE portfolio_snapshot_id = OLD.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'referenced portfolio snapshot is immutable'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS freeze_referenced_portfolio_delete
                BEFORE DELETE ON portfolio_snapshots
                WHEN EXISTS (
                    SELECT 1
                    FROM portfolio_constraint_snapshots
                    WHERE portfolio_snapshot_id = OLD.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'referenced portfolio snapshot is immutable'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS freeze_referenced_position_insert
                BEFORE INSERT ON portfolio_position_metrics
                WHEN EXISTS (
                    SELECT 1
                    FROM portfolio_constraint_snapshots
                    WHERE portfolio_snapshot_id = NEW.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'referenced portfolio positions are immutable'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS freeze_referenced_position_update
                BEFORE UPDATE ON portfolio_position_metrics
                WHEN EXISTS (
                    SELECT 1
                    FROM portfolio_constraint_snapshots
                    WHERE portfolio_snapshot_id = OLD.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'referenced portfolio positions are immutable'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS freeze_referenced_position_delete
                BEFORE DELETE ON portfolio_position_metrics
                WHEN EXISTS (
                    SELECT 1
                    FROM portfolio_constraint_snapshots
                    WHERE portfolio_snapshot_id = OLD.snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'referenced portfolio positions are immutable'
                    );
                END;
                """
            )
            _ensure_column(
                conn,
                table="portfolio_snapshots",
                column="base_currency",
                definition="TEXT NOT NULL DEFAULT 'CNY'",
            )
            _ensure_column(
                conn,
                table="portfolio_snapshots",
                column="holdings_as_of_date",
                definition="TEXT",
            )
            _ensure_column(
                conn,
                table="portfolio_snapshots",
                column="evidence_refs_json",
                definition="TEXT NOT NULL DEFAULT '[]'",
            )
            _ensure_column(
                conn,
                table="portfolio_snapshots",
                column="valuation_digest",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                conn,
                table="portfolio_constraint_snapshots",
                column="portfolio_valuation_digest",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS validate_constraint_valuation_digest
                BEFORE INSERT ON portfolio_constraint_snapshots
                WHEN NEW.portfolio_valuation_digest != (
                    SELECT valuation_digest
                    FROM portfolio_snapshots
                    WHERE snapshot_id = NEW.portfolio_snapshot_id
                )
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'constraint portfolio valuation digest mismatch'
                    );
                END;
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
            referenced = conn.execute(
                """
                SELECT 1
                FROM portfolio_constraint_snapshots
                WHERE portfolio_snapshot_id = ?
                LIMIT 1
                """,
                [snapshot.snapshot_id],
            ).fetchone()
            if referenced is not None:
                existing = self.get(
                    snapshot.snapshot_id,
                    snapshot.user_id,
                )
                if (
                    existing is None
                    or _valuation_signature(existing)
                    != _valuation_signature(valuation)
                ):
                    raise ValueError(
                        "referenced portfolio snapshot is immutable"
                    )
                return
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
                    base_currency,
                    holdings_as_of_date,
                    evidence_refs_json,
                    valuation_digest,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_id) DO UPDATE SET
                    total_market_value = excluded.total_market_value,
                    total_cost_value = excluded.total_cost_value,
                    unrealized_pnl = excluded.unrealized_pnl,
                    max_weight = excluded.max_weight,
                    concentration_hhi = excluded.concentration_hhi,
                    base_currency = excluded.base_currency,
                    holdings_as_of_date = excluded.holdings_as_of_date,
                    evidence_refs_json = excluded.evidence_refs_json,
                    valuation_digest = excluded.valuation_digest,
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
                    snapshot.base_currency,
                    snapshot.holdings_as_of_date,
                    json.dumps(
                        list(snapshot.evidence_refs),
                        ensure_ascii=False,
                    ),
                    portfolio_valuation_digest(valuation),
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

    def save_mandate(
        self,
        user_id: str,
        mandate: InvestmentMandate,
    ) -> None:
        """Persist one immutable, user-owned investment mandate."""

        payload_json = _canonical_json(mandate.to_dict())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, payload_json
                FROM investment_mandates
                WHERE mandate_id = ?
                """,
                [mandate.mandate_id],
            ).fetchone()
            if row is not None:
                if (
                    str(row["user_id"]) != user_id
                    or str(row["payload_json"]) != payload_json
                ):
                    raise ValueError(
                        "mandate_id already identifies a different "
                        "user or payload"
                    )
                return
            conn.execute(
                """
                INSERT INTO investment_mandates (
                    mandate_id, user_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    mandate.mandate_id,
                    user_id,
                    payload_json,
                    _utc_now(),
                ],
            )

    def get_mandate(
        self,
        user_id: str,
        mandate_id: str,
    ) -> InvestmentMandate | None:
        """Return one mandate scoped to a user."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM investment_mandates
                WHERE mandate_id = ? AND user_id = ?
                """,
                [mandate_id, user_id],
            ).fetchone()
        if row is None:
            return None
        return InvestmentMandate.from_dict(
            json.loads(str(row["payload_json"]))
        )

    def save_preference(self, preference: InvestmentPreference) -> None:
        """Append one preference version; re-saving the same content is a no-op."""

        payload_json = _canonical_json(preference.to_dict())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM investment_preferences WHERE user_id = ? AND version = ?",
                [preference.user_id, preference.version],
            ).fetchone()
            if row is not None:
                if str(row["payload_json"]) != payload_json:
                    raise ValueError("this preference version is already recorded with other content")
                return
            conn.execute(
                """
                INSERT INTO investment_preferences (
                    preference_id, user_id, version, recorded_on, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    preference.preference_id,
                    preference.user_id,
                    preference.version,
                    preference.recorded_on,
                    payload_json,
                    _utc_now(),
                ],
            )

    def list_preferences(self, user_id: str) -> list[InvestmentPreference]:
        """Every preference version of one user, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM investment_preferences WHERE user_id = ? ORDER BY version",
                [user_id],
            ).fetchall()
        return [InvestmentPreference.from_dict(json.loads(str(row["payload_json"]))) for row in rows]

    def save_trade(self, trade: TradeRecord) -> bool:
        """Append one trade; the same content again is a no-op and returns False."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO trade_records (
                    trade_id, user_id, asset_id, trade_date, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    trade.trade_id,
                    trade.user_id,
                    trade.asset_id,
                    trade.trade_date,
                    _canonical_json(trade.to_dict()),
                    _utc_now(),
                ],
            )
        return cursor.rowcount == 1

    def list_trades(self, user_id: str) -> list[TradeRecord]:
        """Every recorded trade of one user, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM trade_records WHERE user_id = ? ORDER BY trade_date, created_at, trade_id",
                [user_id],
            ).fetchall()
        return [TradeRecord.from_dict(json.loads(str(row["payload_json"]))) for row in rows]

    def list_mandates(self, user_id: str) -> list[InvestmentMandate]:
        """Return every mandate recorded for one user, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM investment_mandates
                WHERE user_id = ?
                ORDER BY created_at, mandate_id
                """,
                [user_id],
            ).fetchall()
        return [
            InvestmentMandate.from_dict(json.loads(str(row["payload_json"])))
            for row in rows
        ]

    def save_constraint_snapshot(
        self,
        snapshot: PortfolioConstraintSnapshot,
    ) -> None:
        """Persist one immutable portfolio-constraint snapshot."""

        payload_json = _canonical_json(snapshot.to_dict())
        with self._connect() as conn:
            mandate_row = conn.execute(
                """
                SELECT user_id, payload_json
                FROM investment_mandates
                WHERE mandate_id = ?
                """,
                [snapshot.mandate_id],
            ).fetchone()
            if (
                mandate_row is None
                or str(mandate_row["user_id"]) != snapshot.user_id
            ):
                raise ValueError(
                    "constraint mandate must belong to snapshot user"
                )
            mandate = InvestmentMandate.from_dict(
                json.loads(str(mandate_row["payload_json"]))
            )
            if (
                mandate.implementation_asset_id
                != snapshot.subject_asset_id
            ):
                raise ValueError(
                    "constraint subject must match mandate"
                )
            portfolio_row = conn.execute(
                """
                SELECT user_id, as_of_date, base_currency,
                       holdings_as_of_date, evidence_refs_json,
                       valuation_digest
                FROM portfolio_snapshots
                WHERE snapshot_id = ?
                """,
                [snapshot.portfolio_snapshot_id],
            ).fetchone()
            if (
                portfolio_row is None
                or str(portfolio_row["user_id"]) != snapshot.user_id
            ):
                raise ValueError(
                    "constraint portfolio must belong to snapshot "
                    "user"
                )
            if (
                str(portfolio_row["as_of_date"])
                != snapshot.as_of_date
                or str(portfolio_row["base_currency"])
                != snapshot.currency
            ):
                raise ValueError(
                    "constraint portfolio cutoff or currency mismatch"
                )
            if (
                str(portfolio_row["holdings_as_of_date"])
                != snapshot.as_of_date
                or not json.loads(
                    str(portfolio_row["evidence_refs_json"])
                )
            ):
                raise ValueError(
                    "constraint portfolio lacks cutoff provenance"
                )
            if (
                str(portfolio_row["valuation_digest"])
                != snapshot.portfolio_valuation_digest
            ):
                raise ValueError(
                    "constraint portfolio valuation digest mismatch"
                )
            row = conn.execute(
                """
                SELECT user_id, payload_json
                FROM portfolio_constraint_snapshots
                WHERE snapshot_id = ?
                """,
                [snapshot.snapshot_id],
            ).fetchone()
            if row is not None:
                if (
                    str(row["user_id"]) != snapshot.user_id
                    or str(row["payload_json"]) != payload_json
                ):
                    raise ValueError(
                        "snapshot_id already identifies a different "
                        "user or payload"
                    )
                return
            conn.execute(
                """
                INSERT INTO portfolio_constraint_snapshots (
                    snapshot_id,
                    user_id,
                    mandate_id,
                    portfolio_snapshot_id,
                    portfolio_valuation_digest,
                    subject_asset_id,
                    as_of_date,
                    payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.user_id,
                    snapshot.mandate_id,
                    snapshot.portfolio_snapshot_id,
                    snapshot.portfolio_valuation_digest,
                    snapshot.subject_asset_id,
                    snapshot.as_of_date,
                    payload_json,
                    snapshot.created_at,
                ],
            )

    def get_constraint_snapshot(
        self,
        user_id: str,
        snapshot_id: str,
    ) -> PortfolioConstraintSnapshot | None:
        """Return one constraint snapshot scoped to a user."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM portfolio_constraint_snapshots
                WHERE snapshot_id = ? AND user_id = ?
                """,
                [snapshot_id, user_id],
            ).fetchone()
        if row is None:
            return None
        return PortfolioConstraintSnapshot.from_dict(
            json.loads(str(row["payload_json"]))
        )

    def list_constraint_snapshots(
        self,
        user_id: str,
    ) -> list[PortfolioConstraintSnapshot]:
        """Return every constraint snapshot of one user, newest first."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM portfolio_constraint_snapshots
                WHERE user_id = ?
                ORDER BY as_of_date DESC, snapshot_id
                """,
                [user_id],
            ).fetchall()
        return [
            PortfolioConstraintSnapshot.from_dict(
                json.loads(str(row["payload_json"]))
            )
            for row in rows
        ]

    def get(self, snapshot_id: str, user_id: str) -> PortfolioValuation | None:
        """Return one snapshot scoped to a user."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT snapshot_id, user_id, as_of_date, total_market_value,
                       total_cost_value, unrealized_pnl, max_weight,
                       concentration_hhi, base_currency,
                       holdings_as_of_date, evidence_refs_json,
                       created_at
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
                       concentration_hhi, base_currency,
                       holdings_as_of_date, evidence_refs_json,
                       created_at
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
        base_currency=str(row["base_currency"]),
        holdings_as_of_date=(
            str(row["holdings_as_of_date"])
            if row["holdings_as_of_date"] is not None
            else None
        ),
        evidence_refs=tuple(
            str(item)
            for item in json.loads(
                str(row["evidence_refs_json"])
            )
        ),
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


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_column(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }
    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def _valuation_signature(
    valuation: PortfolioValuation,
) -> tuple[object, ...]:
    snapshot = valuation.snapshot
    return (
        snapshot.user_id,
        snapshot.as_of_date,
        snapshot.total_market_value,
        snapshot.total_cost_value,
        snapshot.unrealized_pnl,
        snapshot.max_weight,
        snapshot.concentration_hhi,
        snapshot.base_currency,
        snapshot.holdings_as_of_date,
        snapshot.evidence_refs,
        tuple(
            (
                item.asset_id,
                item.quantity,
                item.market_price,
                item.market_value,
                item.cost_value,
                item.unrealized_pnl,
                item.weight,
            )
            for item in valuation.positions
        ),
    )
