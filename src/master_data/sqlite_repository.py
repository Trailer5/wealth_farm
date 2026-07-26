"""SQLite persistence for canonical asset identities."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import (
    Asset,
    AssetIdentifier,
    AssetIdentifierRebinding,
    AssetMatchCandidate,
    AssetResolution,
    AssetSourceObservation,
    RESOLUTION_CONFIRMED,
    RESOLUTION_DISMISSED,
    StoredAssetObservation,
)


class IdentityMappingConflictError(ValueError):
    """Raised when one source identifier points at different assets."""


class SQLiteMasterDataRepository:
    """Persist master assets, identifiers, and source observations."""

    SCHEMA_VERSION = 3
    SCHEMA_DESCRIPTION = "master data identity review and explicit source identifier rebinding"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)
        self._memory_connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the master data schema if it does not exist."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    asset_type TEXT NOT NULL,
                    standard_symbol TEXT NOT NULL,
                    display_name TEXT,
                    market TEXT,
                    exchange TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_assets_standard_identity
                ON assets (standard_symbol, market, exchange, asset_type);

                CREATE INDEX IF NOT EXISTS idx_assets_display_name
                ON assets (display_name);

                CREATE TABLE IF NOT EXISTS asset_identifiers (
                    identifier_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    raw_symbol TEXT NOT NULL,
                    raw_symbol_key TEXT NOT NULL,
                    standard_symbol TEXT NOT NULL,
                    name TEXT,
                    asset_type TEXT,
                    market TEXT,
                    exchange TEXT,
                    identifier_type TEXT NOT NULL DEFAULT 'source_symbol',
                    confidence REAL NOT NULL DEFAULT 1.0
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (asset_id) REFERENCES assets (asset_id) ON DELETE CASCADE,
                    UNIQUE (source, raw_symbol_key)
                );

                CREATE INDEX IF NOT EXISTS idx_asset_identifiers_asset_id
                ON asset_identifiers (asset_id);

                CREATE INDEX IF NOT EXISTS idx_asset_identifiers_standard_symbol
                ON asset_identifiers (standard_symbol, asset_type, market, exchange);

                CREATE INDEX IF NOT EXISTS idx_asset_identifiers_name
                ON asset_identifiers (name);

                CREATE TABLE IF NOT EXISTS asset_source_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    raw_symbol TEXT NOT NULL,
                    raw_symbol_key TEXT NOT NULL,
                    standard_symbol TEXT,
                    name TEXT,
                    asset_type_hint TEXT,
                    market_hint TEXT,
                    exchange_hint TEXT,
                    raw_json TEXT NOT NULL,
                    resolution_status TEXT NOT NULL,
                    resolved_asset_id TEXT,
                    candidates_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
                    needs_review INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0, 1)),
                    reason TEXT NOT NULL DEFAULT '',
                    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (resolved_asset_id) REFERENCES assets (asset_id)
                );

                CREATE INDEX IF NOT EXISTS idx_asset_observations_lookup
                ON asset_source_observations (source, raw_symbol_key, observed_at);

                CREATE INDEX IF NOT EXISTS idx_asset_observations_review
                ON asset_source_observations (needs_review, resolution_status, observed_at);

                CREATE TABLE IF NOT EXISTS asset_identifier_rebindings (
                    rebinding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    raw_symbol TEXT NOT NULL,
                    raw_symbol_key TEXT NOT NULL,
                    old_asset_id TEXT NOT NULL,
                    new_asset_id TEXT NOT NULL,
                    observation_id INTEGER,
                    reason TEXT NOT NULL,
                    rebound_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (old_asset_id) REFERENCES assets (asset_id),
                    FOREIGN KEY (new_asset_id) REFERENCES assets (asset_id),
                    FOREIGN KEY (observation_id)
                        REFERENCES asset_source_observations (observation_id)
                );

                CREATE INDEX IF NOT EXISTS idx_asset_rebindings_identifier
                ON asset_identifier_rebindings (source, raw_symbol_key, rebound_at);
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (version, description)
                VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save_resolution(self, resolution: AssetResolution) -> int:
        """Persist one resolution and return its observation id."""

        with self._connect() as conn:
            if resolution.asset is not None:
                self._upsert_asset(conn, resolution.asset)
            if resolution.identifier is not None:
                self._upsert_identifier(conn, resolution.identifier)
            cursor = conn.execute(
                """
                INSERT INTO asset_source_observations (
                    source,
                    raw_symbol,
                    raw_symbol_key,
                    standard_symbol,
                    name,
                    asset_type_hint,
                    market_hint,
                    exchange_hint,
                    raw_json,
                    resolution_status,
                    resolved_asset_id,
                    candidates_json,
                    confidence,
                    needs_review,
                    reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    resolution.observation.source,
                    resolution.observation.raw_symbol,
                    _identifier_key(resolution.observation.raw_symbol),
                    resolution.observation.standard_symbol,
                    resolution.observation.name,
                    resolution.observation.asset_type_hint,
                    resolution.observation.market_hint,
                    resolution.observation.exchange_hint,
                    _json_dumps(resolution.observation.raw),
                    resolution.status,
                    resolution.asset.asset_id if resolution.asset is not None else None,
                    _json_dumps(
                        [
                            {
                                "asset_id": candidate.asset_id,
                                "score": candidate.score,
                                "reason": candidate.reason,
                            }
                            for candidate in resolution.candidates
                        ]
                    ),
                    resolution.confidence,
                    1 if resolution.needs_review else 0,
                    resolution.reason,
                ],
            )
            observation_id = cursor.lastrowid
        if observation_id is None:
            raise RuntimeError("SQLite did not return an observation id")
        return int(observation_id)

    def get_asset(self, asset_id: str) -> Asset | None:
        """Return one canonical asset by id."""

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT asset_id, asset_type, standard_symbol, display_name, market, exchange, status
                FROM assets
                WHERE asset_id = ?
                """,
                [asset_id],
            ).fetchone()
        return _asset_from_row(row) if row is not None else None

    def get_observation(self, observation_id: int) -> StoredAssetObservation | None:
        """Return one persisted source observation."""

        with self._connect() as conn:
            row = self._get_observation_row(conn, observation_id)
        return _stored_observation_from_row(row) if row is not None else None

    def confirm_observation(
        self,
        observation_id: int,
        asset_id: str,
        reason: str = "user_confirmed",
    ) -> AssetIdentifier:
        """Confirm an observation by binding it to an existing asset."""

        with self._connect() as conn:
            row = self._get_observation_row(conn, observation_id)
            if row is None:
                raise KeyError(f"Unknown observation_id: {observation_id}")
            if not bool(row["needs_review"]):
                raise ValueError(f"Observation {observation_id} does not require review")
            asset_row = conn.execute(
                """
                SELECT asset_id, asset_type, standard_symbol, display_name, market, exchange, status
                FROM assets
                WHERE asset_id = ?
                """,
                [asset_id],
            ).fetchone()
            if asset_row is None:
                raise KeyError(f"Unknown asset_id: {asset_id}")
            raw_symbol = str(row["raw_symbol"])
            if not raw_symbol.strip():
                raise ValueError("Cannot confirm an observation without a source symbol")
            asset = _asset_from_row(asset_row)
            identifier = AssetIdentifier(
                asset_id=asset.asset_id,
                source=str(row["source"]),
                raw_symbol=raw_symbol,
                standard_symbol=str(row["standard_symbol"] or asset.standard_symbol),
                name=row["name"] or asset.display_name,
                asset_type=asset.asset_type,
                market=asset.market,
                exchange=asset.exchange,
                confidence=1.0,
            )
            self._write_identifier(
                conn,
                identifier,
                allow_rebind=True,
                reason=reason,
                observation_id=observation_id,
            )
            conn.execute(
                """
                UPDATE asset_source_observations
                SET
                    resolution_status = ?,
                    resolved_asset_id = ?,
                    confidence = 1.0,
                    needs_review = 0,
                    reason = ?
                WHERE observation_id = ?
                """,
                [RESOLUTION_CONFIRMED, asset_id, reason, observation_id],
            )
        return identifier

    def dismiss_observation(
        self,
        observation_id: int,
        reason: str = "user_dismissed",
    ) -> StoredAssetObservation:
        """Dismiss an unresolved observation without creating an identifier."""

        with self._connect() as conn:
            row = self._get_observation_row(conn, observation_id)
            if row is None:
                raise KeyError(f"Unknown observation_id: {observation_id}")
            if not bool(row["needs_review"]):
                raise ValueError(f"Observation {observation_id} does not require review")
            conn.execute(
                """
                UPDATE asset_source_observations
                SET
                    resolution_status = ?,
                    resolved_asset_id = NULL,
                    needs_review = 0,
                    reason = ?
                WHERE observation_id = ?
                """,
                [RESOLUTION_DISMISSED, reason, observation_id],
            )
            updated_row = self._get_observation_row(conn, observation_id)
        if updated_row is None:
            raise RuntimeError("Observation disappeared after dismissal")
        return _stored_observation_from_row(updated_row)

    def rebind_identifier(
        self,
        source: str,
        raw_symbol: str,
        new_asset_id: str,
        reason: str,
    ) -> AssetIdentifier:
        """Explicitly move one existing source identifier to another asset."""

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT
                    asset_id,
                    source,
                    raw_symbol,
                    standard_symbol,
                    name,
                    asset_type,
                    market,
                    exchange,
                    identifier_type,
                    confidence,
                    active
                FROM asset_identifiers
                WHERE source = ? AND raw_symbol_key = ?
                """,
                [source, _identifier_key(raw_symbol)],
            ).fetchone()
            if existing is None:
                raise KeyError(f"Unknown source identifier: {source}:{raw_symbol}")
            asset_row = conn.execute(
                """
                SELECT asset_id, asset_type, standard_symbol, display_name, market, exchange, status
                FROM assets
                WHERE asset_id = ?
                """,
                [new_asset_id],
            ).fetchone()
            if asset_row is None:
                raise KeyError(f"Unknown asset_id: {new_asset_id}")
            asset = _asset_from_row(asset_row)
            identifier = AssetIdentifier(
                asset_id=asset.asset_id,
                source=str(existing["source"]),
                raw_symbol=str(existing["raw_symbol"]),
                standard_symbol=asset.standard_symbol,
                name=asset.display_name or existing["name"],
                asset_type=asset.asset_type,
                market=asset.market,
                exchange=asset.exchange,
                identifier_type=str(existing["identifier_type"]),
                confidence=1.0,
                active=bool(existing["active"]),
            )
            self._write_identifier(
                conn,
                identifier,
                allow_rebind=True,
                reason=reason,
            )
        return identifier

    def list_rebindings(self) -> list[AssetIdentifierRebinding]:
        """Return explicit source identifier corrections."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    rebinding_id,
                    source,
                    raw_symbol,
                    old_asset_id,
                    new_asset_id,
                    observation_id,
                    reason,
                    rebound_at
                FROM asset_identifier_rebindings
                ORDER BY rebinding_id
                """
            ).fetchall()
        return [
            AssetIdentifierRebinding(
                rebinding_id=int(row["rebinding_id"]),
                source=str(row["source"]),
                raw_symbol=str(row["raw_symbol"]),
                old_asset_id=str(row["old_asset_id"]),
                new_asset_id=str(row["new_asset_id"]),
                observation_id=row["observation_id"],
                reason=str(row["reason"]),
                rebound_at=str(row["rebound_at"]),
            )
            for row in rows
        ]

    def find_assets(
        self,
        *,
        standard_symbol: str | None = None,
        source: str | None = None,
        raw_symbol: str | None = None,
        name: str | None = None,
        asset_type: str | None = None,
    ) -> list[Asset]:
        """Find assets by canonical or source identity fields."""

        joins: list[str] = []
        conditions: list[str] = []
        params: list[Any] = []
        if source is not None or raw_symbol is not None:
            joins.append("JOIN asset_identifiers i ON i.asset_id = a.asset_id AND i.active = 1")
        if standard_symbol is not None:
            conditions.append("a.standard_symbol = ?")
            params.append(standard_symbol)
        if source is not None:
            conditions.append("i.source = ?")
            params.append(source)
        if raw_symbol is not None:
            conditions.append("i.raw_symbol_key = ?")
            params.append(_identifier_key(raw_symbol))
        if name is not None:
            conditions.append("(LOWER(a.display_name) = LOWER(?) OR a.asset_id IN (SELECT asset_id FROM asset_identifiers WHERE LOWER(name) = LOWER(?)))")
            params.extend([name, name])
        if asset_type is not None:
            conditions.append("a.asset_type = ?")
            params.append(asset_type)

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT DISTINCT
                a.asset_id,
                a.asset_type,
                a.standard_symbol,
                a.display_name,
                a.market,
                a.exchange,
                a.status
            FROM assets a
            {' '.join(joins)}
            {where_sql}
            ORDER BY a.asset_id
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_asset_from_row(row) for row in rows]

    def list_assets(self) -> list[Asset]:
        """Return all canonical assets."""

        return self.find_assets()

    def list_identifiers(self) -> list[AssetIdentifier]:
        """Return all source identifiers."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    asset_id,
                    source,
                    raw_symbol,
                    standard_symbol,
                    name,
                    asset_type,
                    market,
                    exchange,
                    identifier_type,
                    confidence,
                    active
                FROM asset_identifiers
                ORDER BY identifier_id
                """
            ).fetchall()
        return [_identifier_from_row(row) for row in rows]

    def list_unresolved_observations(self) -> list[StoredAssetObservation]:
        """Return observations that still require review."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    observation_id,
                    source,
                    raw_symbol,
                    standard_symbol,
                    name,
                    asset_type_hint,
                    market_hint,
                    exchange_hint,
                    raw_json,
                    resolution_status,
                    resolved_asset_id,
                    candidates_json,
                    confidence,
                    needs_review,
                    reason,
                    observed_at
                FROM asset_source_observations
                WHERE needs_review = 1
                ORDER BY observation_id
                """
            ).fetchall()
        return [_stored_observation_from_row(row) for row in rows]

    def count_observations(self) -> int:
        """Return the number of persisted source observations."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM asset_source_observations"
            ).fetchone()
        return int(row["count"])

    def _upsert_asset(self, conn: sqlite3.Connection, asset: Asset) -> None:
        conn.execute(
            """
            INSERT INTO assets (
                asset_id,
                asset_type,
                standard_symbol,
                display_name,
                market,
                exchange,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                asset_type = excluded.asset_type,
                standard_symbol = excluded.standard_symbol,
                display_name = COALESCE(excluded.display_name, assets.display_name),
                market = COALESCE(excluded.market, assets.market),
                exchange = COALESCE(excluded.exchange, assets.exchange),
                status = excluded.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                asset.asset_id,
                asset.asset_type,
                asset.standard_symbol,
                asset.display_name,
                asset.market,
                asset.exchange,
                asset.status,
            ],
        )

    def _upsert_identifier(self, conn: sqlite3.Connection, identifier: AssetIdentifier) -> None:
        self._write_identifier(conn, identifier, allow_rebind=False)

    def _write_identifier(
        self,
        conn: sqlite3.Connection,
        identifier: AssetIdentifier,
        *,
        allow_rebind: bool,
        reason: str = "",
        observation_id: int | None = None,
    ) -> None:
        raw_symbol_key = _identifier_key(identifier.raw_symbol)
        existing = conn.execute(
            """
            SELECT asset_id
            FROM asset_identifiers
            WHERE source = ? AND raw_symbol_key = ?
            """,
            [identifier.source, raw_symbol_key],
        ).fetchone()
        old_asset_id = str(existing["asset_id"]) if existing is not None else None
        if old_asset_id is not None and old_asset_id != identifier.asset_id and not allow_rebind:
            raise IdentityMappingConflictError(
                f"{identifier.source}:{identifier.raw_symbol} is already mapped "
                f"to {old_asset_id}, not {identifier.asset_id}"
            )
        if old_asset_id is not None and old_asset_id != identifier.asset_id:
            if not reason.strip():
                raise ValueError("Explicit identifier rebinding requires a reason")
            conn.execute(
                """
                INSERT INTO asset_identifier_rebindings (
                    source,
                    raw_symbol,
                    raw_symbol_key,
                    old_asset_id,
                    new_asset_id,
                    observation_id,
                    reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    identifier.source,
                    identifier.raw_symbol,
                    raw_symbol_key,
                    old_asset_id,
                    identifier.asset_id,
                    observation_id,
                    reason,
                ],
            )
        conn.execute(
            """
            INSERT INTO asset_identifiers (
                asset_id,
                source,
                raw_symbol,
                raw_symbol_key,
                standard_symbol,
                name,
                asset_type,
                market,
                exchange,
                identifier_type,
                confidence,
                active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, raw_symbol_key) DO UPDATE SET
                asset_id = excluded.asset_id,
                raw_symbol = excluded.raw_symbol,
                standard_symbol = excluded.standard_symbol,
                name = COALESCE(excluded.name, asset_identifiers.name),
                asset_type = COALESCE(excluded.asset_type, asset_identifiers.asset_type),
                market = COALESCE(excluded.market, asset_identifiers.market),
                exchange = COALESCE(excluded.exchange, asset_identifiers.exchange),
                identifier_type = excluded.identifier_type,
                confidence = excluded.confidence,
                active = excluded.active,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                identifier.asset_id,
                identifier.source,
                identifier.raw_symbol,
                raw_symbol_key,
                identifier.standard_symbol,
                identifier.name,
                identifier.asset_type,
                identifier.market,
                identifier.exchange,
                identifier.identifier_type,
                identifier.confidence,
                1 if identifier.active else 0,
            ],
        )

    def _get_observation_row(
        self,
        conn: sqlite3.Connection,
        observation_id: int,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT
                observation_id,
                source,
                raw_symbol,
                standard_symbol,
                name,
                asset_type_hint,
                market_hint,
                exchange_hint,
                raw_json,
                resolution_status,
                resolved_asset_id,
                candidates_json,
                confidence,
                needs_review,
                reason,
                observed_at
            FROM asset_source_observations
            WHERE observation_id = ?
            """,
            [observation_id],
        ).fetchone()

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
                self._memory_connection.execute("PRAGMA foreign_keys = ON")
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def _identifier_key(raw_symbol: str) -> str:
    return raw_symbol.strip().lower()


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(
        asset_id=str(row["asset_id"]),
        asset_type=str(row["asset_type"]),
        standard_symbol=str(row["standard_symbol"]),
        display_name=row["display_name"],
        market=row["market"],
        exchange=row["exchange"],
        status=str(row["status"]),
    )


def _identifier_from_row(row: sqlite3.Row) -> AssetIdentifier:
    return AssetIdentifier(
        asset_id=str(row["asset_id"]),
        source=str(row["source"]),
        raw_symbol=str(row["raw_symbol"]),
        standard_symbol=str(row["standard_symbol"]),
        name=row["name"],
        asset_type=row["asset_type"],
        market=row["market"],
        exchange=row["exchange"],
        identifier_type=str(row["identifier_type"]),
        confidence=float(row["confidence"]),
        active=bool(row["active"]),
    )


def _stored_observation_from_row(row: sqlite3.Row) -> StoredAssetObservation:
    candidates = tuple(
        AssetMatchCandidate(
            asset_id=str(candidate["asset_id"]),
            score=float(candidate["score"]),
            reason=str(candidate["reason"]),
        )
        for candidate in json.loads(row["candidates_json"])
    )
    observation = AssetSourceObservation(
        source=str(row["source"]),
        raw_symbol=str(row["raw_symbol"]),
        standard_symbol=row["standard_symbol"],
        name=row["name"],
        asset_type_hint=row["asset_type_hint"],
        market_hint=row["market_hint"],
        exchange_hint=row["exchange_hint"],
        raw=json.loads(row["raw_json"]),
    )
    return StoredAssetObservation(
        observation_id=int(row["observation_id"]),
        observation=observation,
        resolution_status=str(row["resolution_status"]),
        resolved_asset_id=row["resolved_asset_id"],
        candidates=candidates,
        confidence=float(row["confidence"]),
        needs_review=bool(row["needs_review"]),
        reason=str(row["reason"]),
        observed_at=str(row["observed_at"]),
    )
