"""SQLite persistence for watchlists, positions, and analysis reports."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import AnalysisReport, HoldingPosition, Watchlist, WatchlistItem


class SQLiteResearchStateRepository:
    """Persist lightweight user research state in the shared database."""

    SCHEMA_VERSION = 8
    SCHEMA_DESCRIPTION = "watchlists, current holdings, and analysis reports"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        """Create research state tables."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS watchlists (
                    watchlist_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    UNIQUE (user_id, name)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlists_user_default
                ON watchlists (user_id)
                WHERE is_default = 1;

                CREATE TABLE IF NOT EXISTS watchlist_items (
                    watchlist_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (watchlist_id, asset_id),
                    FOREIGN KEY (watchlist_id)
                        REFERENCES watchlists (watchlist_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS holding_positions (
                    user_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    average_cost REAL,
                    currency TEXT NOT NULL DEFAULT 'CNY',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, asset_id),
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS analysis_reports (
                    report_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    content_markdown TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('draft', 'final')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_watchlist_items_asset
                ON watchlist_items (asset_id);

                CREATE INDEX IF NOT EXISTS idx_holding_positions_user
                ON holding_positions (user_id);

                CREATE INDEX IF NOT EXISTS idx_analysis_reports_user_asset
                ON analysis_reports (user_id, asset_id, created_at);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def insert_watchlist(self, watchlist: Watchlist) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlists (
                    watchlist_id,
                    user_id,
                    name,
                    description,
                    is_default,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    watchlist.watchlist_id,
                    watchlist.user_id,
                    watchlist.name,
                    watchlist.description,
                    1 if watchlist.is_default else 0,
                    watchlist.created_at,
                    watchlist.updated_at,
                ],
            )

    def get_watchlist(self, watchlist_id: str, user_id: str) -> Watchlist | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT watchlist_id, user_id, name, description, is_default,
                       created_at, updated_at
                FROM watchlists
                WHERE watchlist_id = ? AND user_id = ?
                """,
                [watchlist_id, user_id],
            ).fetchone()
        return _watchlist_from_row(row) if row is not None else None

    def get_default_watchlist(self, user_id: str) -> Watchlist | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT watchlist_id, user_id, name, description, is_default,
                       created_at, updated_at
                FROM watchlists
                WHERE user_id = ? AND is_default = 1
                """,
                [user_id],
            ).fetchone()
        return _watchlist_from_row(row) if row is not None else None

    def list_watchlists(self, user_id: str) -> list[Watchlist]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT watchlist_id, user_id, name, description, is_default,
                       created_at, updated_at
                FROM watchlists
                WHERE user_id = ?
                ORDER BY is_default DESC, name
                """,
                [user_id],
            ).fetchall()
        return [_watchlist_from_row(row) for row in rows]

    def upsert_watchlist_item(self, item: WatchlistItem) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watchlist_items (
                    watchlist_id,
                    asset_id,
                    reason,
                    tags_json,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (watchlist_id, asset_id) DO UPDATE SET
                    reason = excluded.reason,
                    tags_json = excluded.tags_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                [
                    item.watchlist_id,
                    item.asset_id,
                    item.reason,
                    json.dumps(item.tags, ensure_ascii=False),
                    item.status,
                    item.created_at,
                    item.updated_at,
                ],
            )

    def remove_watchlist_item(
        self,
        watchlist_id: str,
        asset_id: str,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM watchlist_items
                WHERE watchlist_id = ? AND asset_id = ?
                """,
                [watchlist_id, asset_id],
            )
        return cursor.rowcount == 1

    def list_watchlist_items(
        self,
        user_id: str,
        watchlist_id: str,
    ) -> list[WatchlistItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.watchlist_id, i.asset_id, i.reason, i.tags_json,
                       i.status, i.created_at, i.updated_at
                FROM watchlist_items i
                JOIN watchlists w ON w.watchlist_id = i.watchlist_id
                WHERE w.user_id = ? AND i.watchlist_id = ?
                ORDER BY i.created_at, i.asset_id
                """,
                [user_id, watchlist_id],
            ).fetchall()
        return [_watchlist_item_from_row(row) for row in rows]

    def upsert_holding(self, position: HoldingPosition) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO holding_positions (
                    user_id,
                    asset_id,
                    quantity,
                    average_cost,
                    currency,
                    note,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, asset_id) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_cost = excluded.average_cost,
                    currency = excluded.currency,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                [
                    position.user_id,
                    position.asset_id,
                    position.quantity,
                    position.average_cost,
                    position.currency,
                    position.note,
                    position.created_at,
                    position.updated_at,
                ],
            )

    def remove_holding(self, user_id: str, asset_id: str) -> bool:
        """Remove one current holding from a user space."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM holding_positions
                WHERE user_id = ? AND asset_id = ?
                """,
                [user_id, asset_id],
            )
        return cursor.rowcount == 1

    def list_holdings(self, user_id: str) -> list[HoldingPosition]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, asset_id, quantity, average_cost, currency,
                       note, created_at, updated_at
                FROM holding_positions
                WHERE user_id = ?
                ORDER BY asset_id
                """,
                [user_id],
            ).fetchall()
        return [_holding_from_row(row) for row in rows]

    def insert_report(self, report: AnalysisReport) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_reports (
                    report_id,
                    user_id,
                    asset_id,
                    title,
                    summary,
                    content_markdown,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    report.report_id,
                    report.user_id,
                    report.asset_id,
                    report.title,
                    report.summary,
                    report.content_markdown,
                    report.status,
                    report.created_at,
                    report.updated_at,
                ],
            )

    def get_report(self, report_id: str, user_id: str) -> AnalysisReport | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT report_id, user_id, asset_id, title, summary,
                       content_markdown, status, created_at, updated_at
                FROM analysis_reports
                WHERE report_id = ? AND user_id = ?
                """,
                [report_id, user_id],
            ).fetchone()
        return _report_from_row(row) if row is not None else None

    def list_reports(
        self,
        user_id: str,
        asset_id: str | None = None,
    ) -> list[AnalysisReport]:
        conditions = ["user_id = ?"]
        params = [user_id]
        if asset_id is not None:
            conditions.append("asset_id = ?")
            params.append(asset_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT report_id, user_id, asset_id, title, summary,
                       content_markdown, status, created_at, updated_at
                FROM analysis_reports
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at DESC, report_id DESC
                """,
                params,
            ).fetchall()
        return [_report_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _watchlist_from_row(row: sqlite3.Row) -> Watchlist:
    return Watchlist(
        watchlist_id=str(row["watchlist_id"]),
        user_id=str(row["user_id"]),
        name=str(row["name"]),
        description=str(row["description"]),
        is_default=bool(row["is_default"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _watchlist_item_from_row(row: sqlite3.Row) -> WatchlistItem:
    return WatchlistItem(
        watchlist_id=str(row["watchlist_id"]),
        asset_id=str(row["asset_id"]),
        reason=str(row["reason"]),
        tags=tuple(json.loads(row["tags_json"])),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _holding_from_row(row: sqlite3.Row) -> HoldingPosition:
    return HoldingPosition(
        user_id=str(row["user_id"]),
        asset_id=str(row["asset_id"]),
        quantity=float(row["quantity"]),
        average_cost=(
            float(row["average_cost"])
            if row["average_cost"] is not None
            else None
        ),
        currency=str(row["currency"]),
        note=str(row["note"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _report_from_row(row: sqlite3.Row) -> AnalysisReport:
    return AnalysisReport(
        report_id=str(row["report_id"]),
        user_id=str(row["user_id"]),
        asset_id=str(row["asset_id"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        content_markdown=str(row["content_markdown"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
