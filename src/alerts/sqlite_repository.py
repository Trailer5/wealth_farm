"""SQLite persistence for local alert rules and reminders."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Alert, AlertRule, AlertRuleProvenance


class SQLiteAlertRepository:
    """Persist user-owned rules and idempotent alerts."""

    SCHEMA_VERSION = 34
    SCHEMA_DESCRIPTION = (
        "research-linked local alert rules and reminders"
    )

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        """Create alert tables."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS alert_rules (
                    rule_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    asset_id TEXT,
                    rule_type TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    provenance_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    asset_id TEXT,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'acknowledged')),
                    payload_json TEXT NOT NULL,
                    acknowledged_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (rule_id) REFERENCES alert_rules (rule_id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_alert_rules_user_type
                ON alert_rules (user_id, rule_type, enabled);

                CREATE INDEX IF NOT EXISTS idx_alerts_user_status
                ON alerts (user_id, status, triggered_at);
                """
            )
            _ensure_column(
                conn,
                table="alert_rules",
                column="provenance_json",
                definition="TEXT",
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def insert_rule(self, rule: AlertRule) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_rules (
                    rule_id, user_id, asset_id, rule_type, config_json,
                    provenance_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rule.rule_id,
                    rule.user_id,
                    rule.asset_id,
                    rule.rule_type,
                    _json(rule.config),
                    (
                        _json(rule.provenance.to_dict())
                        if rule.provenance is not None
                        else None
                    ),
                    1 if rule.enabled else 0,
                    rule.created_at,
                    rule.updated_at,
                ],
            )

    def get_rule(
        self,
        user_id: str,
        rule_id: str,
    ) -> AlertRule | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT rule_id, user_id, asset_id, rule_type, config_json,
                       provenance_json, enabled, created_at, updated_at
                FROM alert_rules
                WHERE rule_id = ? AND user_id = ?
                """,
                [rule_id, user_id],
            ).fetchone()
        return _rule_from_row(row) if row is not None else None

    def list_rules(
        self,
        user_id: str,
        *,
        rule_type: str | None = None,
        enabled_only: bool = False,
    ) -> list[AlertRule]:
        conditions = ["user_id = ?"]
        params: list[object] = [user_id]
        if rule_type is not None:
            conditions.append("rule_type = ?")
            params.append(rule_type)
        if enabled_only:
            conditions.append("enabled = 1")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT rule_id, user_id, asset_id, rule_type, config_json,
                       provenance_json, enabled, created_at, updated_at
                FROM alert_rules
                WHERE {" AND ".join(conditions)}
                ORDER BY created_at, rule_id
                """,
                params,
            ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def save_alert(self, alert: Alert) -> tuple[Alert, bool]:
        """Insert one alert or return the existing deduplicated alert."""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alerts (
                    alert_id, rule_id, user_id, asset_id, title, message,
                    triggered_at, dedupe_key, status, payload_json,
                    acknowledged_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    alert.alert_id,
                    alert.rule_id,
                    alert.user_id,
                    alert.asset_id,
                    alert.title,
                    alert.message,
                    alert.triggered_at,
                    alert.dedupe_key,
                    alert.status,
                    _json(alert.payload),
                    alert.acknowledged_at,
                    alert.created_at,
                ],
            )
            row = conn.execute(
                """
                SELECT alert_id, rule_id, user_id, asset_id, title, message,
                       triggered_at, dedupe_key, status, payload_json,
                       acknowledged_at, created_at
                FROM alerts
                WHERE dedupe_key = ?
                """,
                [alert.dedupe_key],
            ).fetchone()
        if row is None:
            raise RuntimeError("Alert was not persisted")
        return _alert_from_row(row), cursor.rowcount == 1

    def list_alerts(
        self,
        user_id: str,
        *,
        status: str | None = None,
    ) -> list[Alert]:
        conditions = ["user_id = ?"]
        params = [user_id]
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT alert_id, rule_id, user_id, asset_id, title, message,
                       triggered_at, dedupe_key, status, payload_json,
                       acknowledged_at, created_at
                FROM alerts
                WHERE {" AND ".join(conditions)}
                ORDER BY triggered_at DESC, alert_id
                """,
                params,
            ).fetchall()
        return [_alert_from_row(row) for row in rows]

    def acknowledge(
        self,
        user_id: str,
        alert_id: str,
        acknowledged_at: str,
    ) -> Alert:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE alerts
                SET status = 'acknowledged', acknowledged_at = ?
                WHERE alert_id = ? AND user_id = ?
                """,
                [acknowledged_at, alert_id, user_id],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown alert_id for user: {alert_id}")
            row = conn.execute(
                """
                SELECT alert_id, rule_id, user_id, asset_id, title, message,
                       triggered_at, dedupe_key, status, payload_json,
                       acknowledged_at, created_at
                FROM alerts
                WHERE alert_id = ?
                """,
                [alert_id],
            ).fetchone()
        return _alert_from_row(row)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _rule_from_row(row: sqlite3.Row) -> AlertRule:
    provenance_payload = (
        json.loads(row["provenance_json"])
        if row["provenance_json"] is not None
        else None
    )
    return AlertRule(
        rule_id=str(row["rule_id"]),
        user_id=str(row["user_id"]),
        asset_id=row["asset_id"],
        rule_type=str(row["rule_type"]),
        config=json.loads(row["config_json"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        provenance=(
            AlertRuleProvenance.from_dict(provenance_payload)
            if provenance_payload is not None
            else None
        ),
    )


def _alert_from_row(row: sqlite3.Row) -> Alert:
    return Alert(
        alert_id=str(row["alert_id"]),
        rule_id=str(row["rule_id"]),
        user_id=str(row["user_id"]),
        asset_id=row["asset_id"],
        title=str(row["title"]),
        message=str(row["message"]),
        triggered_at=str(row["triggered_at"]),
        dedupe_key=str(row["dedupe_key"]),
        status=str(row["status"]),
        payload=json.loads(row["payload_json"]),
        acknowledged_at=row["acknowledged_at"],
        created_at=str(row["created_at"]),
    )


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
