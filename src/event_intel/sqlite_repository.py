"""SQLite storage for intelligence items, searches, observables and append-only observations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import IntelItem, Observable, Observation


class SQLiteEventIntelRepository:
    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS intel_items (
                    item_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_on TEXT NOT NULL,
                    published_at TEXT,
                    publisher TEXT,
                    summary TEXT,
                    language TEXT,
                    raw_json TEXT,
                    first_seen_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intel_items_published
                ON intel_items (published_on);

                CREATE TABLE IF NOT EXISTS intel_searches (
                    search_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    searched_at TEXT NOT NULL,
                    item_ids_json TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS tracked_observables (
                    observable_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observable_observations (
                    observation_id TEXT PRIMARY KEY,
                    observable_id TEXT NOT NULL
                        REFERENCES tracked_observables (observable_id),
                    period_end TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_observable_observations
                ON observable_observations (observable_id, period_end);

                CREATE TABLE IF NOT EXISTS intel_pages (
                    url TEXT PRIMARY KEY,
                    final_url TEXT,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS prevent_observable_observations_update
                BEFORE UPDATE ON observable_observations
                BEGIN
                    SELECT RAISE(ABORT, 'observations are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS prevent_observable_observations_delete
                BEFORE DELETE ON observable_observations
                BEGIN
                    SELECT RAISE(ABORT, 'observations are append-only');
                END;
                """
            )

    def save_items(self, items: Iterable[IntelItem], seen_at: str) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO intel_items (
                    item_id, source, kind, title, url, published_on, published_at,
                    publisher, summary, language, raw_json, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.item_id,
                        item.source,
                        item.kind,
                        item.title,
                        item.url,
                        item.published_on,
                        item.published_at,
                        item.publisher,
                        item.summary,
                        item.language,
                        json.dumps(item.raw, ensure_ascii=False, default=str),
                        seen_at,
                    )
                    for item in items
                ],
            )

    def record_search(
        self,
        source: str,
        params: dict[str, Any],
        searched_at: str,
        item_ids: list[str],
        error: str | None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO intel_searches (source, params_json, searched_at, item_ids_json, error) VALUES (?, ?, ?, ?, ?)",
                (source, json.dumps(params, ensure_ascii=False, sort_keys=True), searched_at, json.dumps(item_ids), error),
            )
            return int(cursor.lastrowid)

    def get_items(self, item_ids: Iterable[str]) -> dict[str, IntelItem]:
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM intel_items WHERE item_id IN ({','.join('?' for _ in ids)})", ids
            ).fetchall()
        return {row["item_id"]: _item(row) for row in rows}

    def get_search(self, search_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intel_searches WHERE search_id = ?", (search_id,)).fetchone()
        if row is None:
            return None
        return {
            "search_id": row["search_id"],
            "source": row["source"],
            "params": json.loads(row["params_json"]),
            "searched_at": row["searched_at"],
            "item_ids": json.loads(row["item_ids_json"]),
            "error": row["error"],
        }

    def find_items(
        self,
        *,
        source: str | None = None,
        text: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> list[IntelItem]:
        conditions, params = [], []
        if source:
            conditions.append("source = ?")
            params.append(source)
        if text:
            for word in text.split():
                conditions.append("LOWER(title) LIKE ?")
                params.append("%" + word.strip('"').lower() + "%")
        if since:
            conditions.append("published_on >= ?")
            params.append(since[:10])
        if until:
            conditions.append("published_on <= ?")
            params.append(until[:10])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM intel_items {where} ORDER BY published_on DESC, item_id LIMIT ?",
                [*params, limit],
            ).fetchall()
        return [_item(row) for row in rows]

    def save_observable(self, observable: Observable, created_at: str) -> bool:
        """True when newly stored; the same definition again is a no-op, a different one an error."""

        payload = json.dumps(observable.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM tracked_observables WHERE observable_id = ?", (observable.observable_id,)
            ).fetchone()
            if row is not None:
                if json.loads(row["payload_json"]) != observable.to_dict():
                    raise ValueError(
                        f"{observable.observable_id} 已登记为不同的定义；定义不可修改，换一个 observable_id"
                    )
                return False
            conn.execute(
                "INSERT INTO tracked_observables (observable_id, payload_json, created_at) VALUES (?, ?, ?)",
                (observable.observable_id, payload, created_at),
            )
        return True

    def get_observable(self, observable_id: str) -> Observable | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM tracked_observables WHERE observable_id = ?", (observable_id,)
            ).fetchone()
        return Observable.from_dict(json.loads(row["payload_json"])) if row is not None else None

    def list_observables(self) -> list[Observable]:
        with self._connect() as conn:
            rows = conn.execute("SELECT payload_json FROM tracked_observables ORDER BY observable_id").fetchall()
        return [Observable.from_dict(json.loads(row["payload_json"])) for row in rows]

    def append_observation(self, observation: Observation) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO observable_observations (
                    observation_id, observable_id, period_end, available_at, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.observable_id,
                    observation.period_end,
                    observation.available_at,
                    json.dumps(observation.to_dict(), ensure_ascii=False, sort_keys=True),
                    observation.recorded_at,
                ),
            )
            return cursor.rowcount > 0

    def list_observations(self, observable_id: str) -> list[Observation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM observable_observations WHERE observable_id = ? ORDER BY period_end, recorded_at",
                (observable_id,),
            ).fetchall()
        return [Observation.from_dict(json.loads(row["payload_json"])) for row in rows]

    def save_page(self, url: str, *, final_url: str | None, title: str, text: str, fetched_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO intel_pages (url, final_url, title, text, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (url, final_url, title, text, fetched_at),
            )

    def get_page(self, url: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intel_pages WHERE url = ?", (url,)).fetchone()
        return dict(row) if row else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _item(row: sqlite3.Row) -> IntelItem:
    return IntelItem(
        item_id=row["item_id"],
        source=row["source"],
        kind=row["kind"],
        title=row["title"],
        url=row["url"],
        published_on=row["published_on"],
        published_at=row["published_at"],
        publisher=row["publisher"],
        summary=row["summary"],
        language=row["language"],
        raw=json.loads(row["raw_json"] or "{}"),
    )
