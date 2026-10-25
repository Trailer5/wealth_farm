"""SQLite persistence for watchlists, positions, and analysis reports."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.research_workflow import ResearchDecisionBrief

from .models import (
    AnalysisReport,
    ExplorationLedgerRecord,
    ExplorationSessionRecord,
    HoldingPosition,
    Watchlist,
    WatchlistItem,
)


class SQLiteResearchStateRepository:
    """Persist lightweight user research state in the shared database."""

    SCHEMA_VERSION = 32
    SCHEMA_DESCRIPTION = (
        "research state with persistent exploration ledger"
    )

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

                CREATE TABLE IF NOT EXISTS research_decision_briefs (
                    brief_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL,
                    decision_intent TEXT NOT NULL,
                    decision_lanes_json TEXT NOT NULL,
                    position_state TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    portfolio_role TEXT,
                    mandate_id TEXT,
                    portfolio_constraint_snapshot_id TEXT,
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    assumptions_json TEXT NOT NULL DEFAULT '[]',
                    missing_user_context_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id)
                        REFERENCES assets (asset_id) ON DELETE CASCADE,
                    UNIQUE (user_id, asset_id)
                );

                CREATE TABLE IF NOT EXISTS research_exploration_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    workflow_run_id TEXT NOT NULL UNIQUE,
                    handoff_id TEXT NOT NULL,
                    decision_brief_id TEXT,
                    asset_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id)
                        REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id)
                        REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS
                research_exploration_ledger_entries (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    entry_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (session_id)
                        REFERENCES research_exploration_sessions (
                            session_id
                        ) ON DELETE CASCADE,
                    UNIQUE (session_id, sequence_no)
                );

                CREATE TRIGGER IF NOT EXISTS
                prevent_exploration_ledger_update
                BEFORE UPDATE ON research_exploration_ledger_entries
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'exploration ledger is append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                prevent_exploration_ledger_delete
                BEFORE DELETE ON research_exploration_ledger_entries
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'exploration ledger is append-only'
                    );
                END;

                CREATE INDEX IF NOT EXISTS idx_watchlist_items_asset
                ON watchlist_items (asset_id);

                CREATE INDEX IF NOT EXISTS idx_holding_positions_user
                ON holding_positions (user_id);

                CREATE INDEX IF NOT EXISTS idx_analysis_reports_user_asset
                ON analysis_reports (user_id, asset_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_decision_briefs_user
                ON research_decision_briefs (user_id, updated_at);

                CREATE INDEX IF NOT EXISTS idx_exploration_user_asset
                ON research_exploration_sessions (
                    user_id,
                    asset_id,
                    updated_at
                );

                CREATE INDEX IF NOT EXISTS idx_exploration_ledger_session
                ON research_exploration_ledger_entries (
                    session_id,
                    sequence_no
                );
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

    def get_holding(
        self,
        user_id: str,
        asset_id: str,
    ) -> HoldingPosition | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, asset_id, quantity, average_cost, currency,
                       note, created_at, updated_at
                FROM holding_positions
                WHERE user_id = ? AND asset_id = ?
                """,
                [user_id, asset_id],
            ).fetchone()
        return _holding_from_row(row) if row is not None else None

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

    def upsert_decision_brief(
        self,
        brief: ResearchDecisionBrief,
    ) -> None:
        payload = brief.to_dict()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_decision_briefs (
                    brief_id,
                    user_id,
                    asset_id,
                    decision_intent,
                    decision_lanes_json,
                    position_state,
                    horizon,
                    as_of_date,
                    portfolio_role,
                    mandate_id,
                    portfolio_constraint_snapshot_id,
                    constraints_json,
                    assumptions_json,
                    missing_user_context_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user_id, asset_id) DO UPDATE SET
                    decision_intent = excluded.decision_intent,
                    decision_lanes_json = excluded.decision_lanes_json,
                    position_state = excluded.position_state,
                    horizon = excluded.horizon,
                    as_of_date = excluded.as_of_date,
                    portfolio_role = excluded.portfolio_role,
                    mandate_id = excluded.mandate_id,
                    portfolio_constraint_snapshot_id =
                        excluded.portfolio_constraint_snapshot_id,
                    constraints_json = excluded.constraints_json,
                    assumptions_json = excluded.assumptions_json,
                    missing_user_context_json =
                        excluded.missing_user_context_json,
                    updated_at = excluded.updated_at
                """,
                [
                    brief.brief_id,
                    brief.user_id,
                    brief.asset_id,
                    payload["decision_intent"],
                    json.dumps(payload["decision_lanes"]),
                    payload["position_state"],
                    payload["horizon"],
                    brief.as_of_date,
                    brief.portfolio_role,
                    brief.mandate_id,
                    brief.portfolio_constraint_snapshot_id,
                    json.dumps(
                        payload["constraints"],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        payload["assumptions"],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        payload["missing_user_context"],
                    ),
                    brief.created_at,
                    brief.updated_at,
                ],
            )

    def get_decision_brief(
        self,
        user_id: str,
        asset_id: str,
    ) -> ResearchDecisionBrief | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM research_decision_briefs
                WHERE user_id = ? AND asset_id = ?
                """,
                [user_id, asset_id],
            ).fetchone()
        return (
            _decision_brief_from_row(row)
            if row is not None
            else None
        )

    def insert_exploration_session(
        self,
        record: ExplorationSessionRecord,
        entry: ExplorationLedgerRecord,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_exploration_sessions (
                    session_id,
                    user_id,
                    workflow_run_id,
                    handoff_id,
                    decision_brief_id,
                    asset_id,
                    status,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _exploration_session_values(record),
            )
            self._insert_exploration_ledger_entry(
                conn,
                entry,
            )

    def save_exploration_mutation(
        self,
        record: ExplorationSessionRecord,
        entry: ExplorationLedgerRecord,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE research_exploration_sessions
                SET status = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE session_id = ? AND user_id = ?
                """,
                [
                    record.status,
                    json.dumps(
                        record.payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    record.updated_at,
                    record.session_id,
                    record.user_id,
                ],
            )
            if cursor.rowcount != 1:
                raise KeyError(
                    "Unknown exploration session for user: "
                    f"{record.session_id}"
                )
            self._insert_exploration_ledger_entry(
                conn,
                entry,
            )

    def get_exploration_session(
        self,
        user_id: str,
        session_id: str,
    ) -> ExplorationSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM research_exploration_sessions
                WHERE user_id = ? AND session_id = ?
                """,
                [user_id, session_id],
            ).fetchone()
        return (
            _exploration_session_from_row(row)
            if row is not None
            else None
        )

    def get_exploration_session_for_workflow(
        self,
        user_id: str,
        workflow_run_id: str,
    ) -> ExplorationSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM research_exploration_sessions
                WHERE user_id = ? AND workflow_run_id = ?
                """,
                [user_id, workflow_run_id],
            ).fetchone()
        return (
            _exploration_session_from_row(row)
            if row is not None
            else None
        )

    def list_exploration_ledger_entries(
        self,
        user_id: str,
        session_id: str,
    ) -> tuple[ExplorationLedgerRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry.*
                FROM research_exploration_ledger_entries AS entry
                JOIN research_exploration_sessions AS session
                  ON session.session_id = entry.session_id
                WHERE session.user_id = ?
                  AND entry.session_id = ?
                ORDER BY entry.sequence_no
                """,
                [user_id, session_id],
            ).fetchall()
        return tuple(
            _exploration_ledger_from_row(row)
            for row in rows
        )

    def _insert_exploration_ledger_entry(
        self,
        conn: sqlite3.Connection,
        entry: ExplorationLedgerRecord,
    ) -> None:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
            FROM research_exploration_ledger_entries
            WHERE session_id = ?
            """,
            [entry.session_id],
        ).fetchone()
        sequence_no = int(row["next_sequence"])
        conn.execute(
            """
            INSERT INTO research_exploration_ledger_entries (
                entry_id,
                session_id,
                sequence_no,
                entry_kind,
                payload_json,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                entry.entry_id,
                entry.session_id,
                sequence_no,
                entry.entry_kind,
                json.dumps(
                    entry.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                entry.recorded_at,
            ],
        )

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


def _decision_brief_from_row(
    row: sqlite3.Row,
) -> ResearchDecisionBrief:
    return ResearchDecisionBrief.from_dict(
        {
            "brief_id": row["brief_id"],
            "user_id": row["user_id"],
            "asset_id": row["asset_id"],
            "decision_intent": row["decision_intent"],
            "decision_lanes": json.loads(
                row["decision_lanes_json"]
            ),
            "position_state": row["position_state"],
            "horizon": row["horizon"],
            "as_of_date": row["as_of_date"],
            "portfolio_role": row["portfolio_role"],
            "mandate_id": row["mandate_id"],
            "portfolio_constraint_snapshot_id": (
                row["portfolio_constraint_snapshot_id"]
            ),
            "constraints": json.loads(row["constraints_json"]),
            "assumptions": json.loads(row["assumptions_json"]),
            "missing_user_context": json.loads(
                row["missing_user_context_json"]
            ),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


def _exploration_session_values(
    record: ExplorationSessionRecord,
) -> list[object]:
    return [
        record.session_id,
        record.user_id,
        record.workflow_run_id,
        record.handoff_id,
        record.decision_brief_id,
        record.asset_id,
        record.status,
        json.dumps(
            record.payload,
            ensure_ascii=False,
            sort_keys=True,
        ),
        record.created_at,
        record.updated_at,
    ]


def _exploration_session_from_row(
    row: sqlite3.Row,
) -> ExplorationSessionRecord:
    return ExplorationSessionRecord(
        session_id=str(row["session_id"]),
        user_id=str(row["user_id"]),
        workflow_run_id=str(row["workflow_run_id"]),
        handoff_id=str(row["handoff_id"]),
        decision_brief_id=(
            str(row["decision_brief_id"])
            if row["decision_brief_id"] is not None
            else None
        ),
        asset_id=str(row["asset_id"]),
        status=str(row["status"]),
        payload=json.loads(row["payload_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _exploration_ledger_from_row(
    row: sqlite3.Row,
) -> ExplorationLedgerRecord:
    return ExplorationLedgerRecord(
        entry_id=str(row["entry_id"]),
        session_id=str(row["session_id"]),
        sequence_no=int(row["sequence_no"]),
        entry_kind=str(row["entry_kind"]),
        payload=json.loads(row["payload_json"]),
        recorded_at=str(row["recorded_at"]),
    )
