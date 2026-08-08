"""SQLite persistence for discovery runs and every member outcome."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import DiscoveryMemberOutcome, DiscoveryRun


class SQLiteSecurityDiscoveryRepository:
    SCHEMA_VERSION = 22
    SCHEMA_DESCRIPTION = "security discovery funnel runs"

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

                CREATE TABLE IF NOT EXISTS discovery_runs (
                    run_id TEXT PRIMARY KEY,
                    discovery_id TEXT NOT NULL,
                    discovery_version TEXT NOT NULL,
                    universe_id TEXT NOT NULL,
                    universe_version TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    benchmark_asset_id TEXT NOT NULL,
                    member_count INTEGER NOT NULL,
                    candidate_count INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    run_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS discovery_member_outcomes (
                    run_id TEXT NOT NULL,
                    source_symbol TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    asset_id TEXT,
                    status TEXT NOT NULL,
                    last_layer INTEGER NOT NULL,
                    reasons_json TEXT NOT NULL,
                    missing_metrics_json TEXT NOT NULL,
                    matched_rules_json TEXT NOT NULL,
                    failed_rules_json TEXT NOT NULL,
                    matched_tracks_json TEXT NOT NULL,
                    selected_track TEXT,
                    PRIMARY KEY (run_id, source_symbol),
                    FOREIGN KEY (run_id)
                        REFERENCES discovery_runs (run_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_discovery_run_universe
                ON discovery_runs (
                    universe_id,
                    universe_version,
                    as_of_date
                );

                CREATE INDEX IF NOT EXISTS idx_discovery_outcome_status
                ON discovery_member_outcomes (run_id, status);
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

    def save(self, run: DiscoveryRun) -> DiscoveryRun:
        payload = _json(run.to_dict())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO discovery_runs (
                    run_id,
                    discovery_id,
                    discovery_version,
                    universe_id,
                    universe_version,
                    as_of_date,
                    horizon,
                    benchmark_asset_id,
                    member_count,
                    candidate_count,
                    started_at,
                    completed_at,
                    run_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id) DO UPDATE SET
                    completed_at = excluded.completed_at,
                    member_count = excluded.member_count,
                    candidate_count = excluded.candidate_count,
                    run_json = excluded.run_json
                """,
                [
                    run.run_id,
                    run.spec.discovery_id,
                    run.spec.version,
                    run.spec.universe_id,
                    run.universe_version,
                    run.spec.as_of_date,
                    run.spec.horizon.value,
                    run.benchmark_asset_id,
                    len(run.outcomes),
                    len(run.candidates),
                    run.started_at,
                    run.completed_at,
                    payload,
                ],
            )
            conn.execute(
                """
                DELETE FROM discovery_member_outcomes
                WHERE run_id = ?
                """,
                [run.run_id],
            )
            conn.executemany(
                """
                INSERT INTO discovery_member_outcomes (
                    run_id,
                    source_symbol,
                    source_name,
                    asset_id,
                    status,
                    last_layer,
                    reasons_json,
                    missing_metrics_json,
                    matched_rules_json,
                    failed_rules_json,
                    matched_tracks_json,
                    selected_track
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        run.run_id,
                        outcome.source_symbol,
                        outcome.source_name,
                        outcome.asset_id,
                        outcome.status.value,
                        outcome.last_layer,
                        _json(
                            [
                                reason.to_dict()
                                for reason in outcome.reasons
                            ]
                        ),
                        _json(list(outcome.missing_metrics)),
                        _json(list(outcome.matched_rules)),
                        _json(list(outcome.failed_rules)),
                        _json(list(outcome.matched_tracks)),
                        outcome.selected_track,
                    ]
                    for outcome in run.outcomes
                ],
            )
        return run

    def get(self, run_id: str) -> DiscoveryRun | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_json
                FROM discovery_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        if row is None:
            return None
        return DiscoveryRun.from_dict(
            json.loads(str(row["run_json"]))
        )

    def list_outcomes(
        self,
        run_id: str,
    ) -> tuple[DiscoveryMemberOutcome, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM discovery_member_outcomes
                WHERE run_id = ?
                ORDER BY source_symbol
                """,
                [run_id],
            ).fetchall()
        return tuple(
            DiscoveryMemberOutcome.from_dict(
                {
                    "source_symbol": row["source_symbol"],
                    "source_name": row["source_name"],
                    "asset_id": row["asset_id"],
                    "status": row["status"],
                    "last_layer": row["last_layer"],
                    "reasons": json.loads(row["reasons_json"]),
                    "missing_metrics": json.loads(
                        row["missing_metrics_json"]
                    ),
                    "matched_rules": json.loads(
                        row["matched_rules_json"]
                    ),
                    "failed_rules": json.loads(
                        row["failed_rules_json"]
                    ),
                    "matched_tracks": json.loads(
                        row["matched_tracks_json"]
                    ),
                    "selected_track": row["selected_track"],
                }
            )
            for row in rows
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


def _json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
