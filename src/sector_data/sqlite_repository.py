"""SQLite storage for the Shenwan catalog, memberships and valuation snapshots."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import IndustryMember, IndustryValuationPoint, SwIndustry


class SQLiteSectorDataRepository:
    SCHEMA_VERSION = 38
    SCHEMA_DESCRIPTION = "shenwan industry catalog, members and valuations"

    def __init__(
        self,
        db_path: str | Path = "data/database/wealth_farm.sqlite3",
    ) -> None:
        self.db_path = Path(db_path)

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

                CREATE TABLE IF NOT EXISTS sw_industries (
                    code TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    parent_code TEXT,
                    constituent_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sw_industry_members (
                    industry_code TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    included_on TEXT,
                    PRIMARY KEY (industry_code, symbol)
                );

                CREATE INDEX IF NOT EXISTS idx_sw_industry_members_symbol
                ON sw_industry_members (symbol);

                CREATE TABLE IF NOT EXISTS sw_industry_valuations (
                    code TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    pe REAL,
                    pb REAL,
                    dividend_yield_percent REAL,
                    amount_share_percent REAL,
                    turnover_rate_percent REAL,
                    float_market_cap REAL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (code, trade_date)
                );

                CREATE TABLE IF NOT EXISTS sector_fetch_log (
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sector_fetch_log_lookup
                ON sector_fetch_log (kind, key, fetched_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_versions (version, description) "
                "VALUES (?, ?)",
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def replace_industries(self, industries: list[SwIndustry]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sw_industries")
            conn.executemany(
                "INSERT INTO sw_industries VALUES (?, ?, ?, ?, ?)",
                [
                    [item.code, item.name, item.level, item.parent_code, item.constituent_count]
                    for item in industries
                ],
            )

    def list_industries(self) -> list[SwIndustry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sw_industries ORDER BY level, code"
            ).fetchall()
        return [
            SwIndustry(
                code=str(row["code"]),
                name=str(row["name"]),
                level=int(row["level"]),
                parent_code=row["parent_code"],
                constituent_count=int(row["constituent_count"]),
            )
            for row in rows
        ]

    def replace_members(self, industry_code: str, members: list[IndustryMember]) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sw_industry_members WHERE industry_code = ?",
                [industry_code],
            )
            conn.executemany(
                "INSERT OR REPLACE INTO sw_industry_members VALUES (?, ?, ?, ?)",
                [
                    [industry_code, item.symbol, item.name, item.included_on]
                    for item in members
                ],
            )

    def list_members(self, industry_codes: list[str]) -> list[IndustryMember]:
        if not industry_codes:
            return []
        marks = ",".join("?" * len(industry_codes))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM sw_industry_members WHERE industry_code IN ({marks}) "
                "ORDER BY industry_code, symbol",
                list(industry_codes),
            ).fetchall()
        return [_member(row) for row in rows]

    def memberships(self, symbol: str) -> list[IndustryMember]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sw_industry_members WHERE symbol = ?",
                [symbol],
            ).fetchall()
        return [_member(row) for row in rows]

    def upsert_valuations(self, points: list[IndustryValuationPoint]) -> int:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO sw_industry_valuations
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    [
                        item.code,
                        item.trade_date,
                        item.pe,
                        item.pb,
                        item.dividend_yield_percent,
                        item.amount_share_percent,
                        item.turnover_rate_percent,
                        item.float_market_cap,
                        item.source,
                    ]
                    for item in points
                ],
            )
        return len(points)

    def list_valuations(
        self,
        code: str,
        start_date: str,
        end_date: str,
    ) -> list[IndustryValuationPoint]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sw_industry_valuations
                WHERE code = ? AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
                """,
                [code, start_date, end_date],
            ).fetchall()
        return [
            IndustryValuationPoint(
                code=str(row["code"]),
                trade_date=str(row["trade_date"]),
                pe=row["pe"],
                pb=row["pb"],
                dividend_yield_percent=row["dividend_yield_percent"],
                amount_share_percent=row["amount_share_percent"],
                turnover_rate_percent=row["turnover_rate_percent"],
                float_market_cap=row["float_market_cap"],
                source=str(row["source"]),
            )
            for row in rows
        ]

    def log_fetch(
        self,
        kind: str,
        key: str,
        *,
        fetched_at: str,
        record_count: int,
        error: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sector_fetch_log VALUES (?, ?, ?, ?, ?)",
                [kind, key, fetched_at, record_count, error],
            )

    def last_success(self, kind: str, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(fetched_at) AS fetched_at FROM sector_fetch_log
                WHERE kind = ? AND key = ? AND error IS NULL AND record_count > 0
                """,
                [kind, key],
            ).fetchone()
        return None if row is None else row["fetched_at"]

    def successful_keys(self, kind: str) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT key FROM sector_fetch_log
                WHERE kind = ? AND error IS NULL AND record_count > 0
                """,
                [kind],
            ).fetchall()
        return {str(row["key"]) for row in rows}

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def _member(row: sqlite3.Row) -> IndustryMember:
    return IndustryMember(
        symbol=str(row["symbol"]),
        name=str(row["name"]),
        industry_code=str(row["industry_code"]),
        included_on=row["included_on"],
    )
