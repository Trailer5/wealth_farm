"""SQLite persistence for normalized market data."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from src.data_src.models import (
    DailyBar,
    DataSourceStatus,
    DisclosureDocument,
    DisclosureSourceCheck,
    DocumentTable,
    DocumentText,
    FinancialField,
    DownloadedFile,
    FundEstimate,
    FundNav,
    FundPurchaseStatus,
    FundReportRecord,
    OCRQueueItem,
    ResearchReport,
    SecurityInfo,
)


class SQLiteDataStore:
    """Small SQLite store for project market data."""

    SCHEMA_VERSION = 1
    SCHEMA_DESCRIPTION = "initial data source, disclosure, document, OCR, and research schema"

    def __init__(self, db_path: str | Path = "data/database/wealth_farm.sqlite3") -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        """Create required tables if they do not exist."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS securities (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    security_type TEXT,
                    exchange TEXT,
                    source TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS stock_daily_bars (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, trade_date, source)
                );

                CREATE TABLE IF NOT EXISTS fund_exchange_daily_bars (
                    symbol TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, trade_date, source)
                );

                CREATE TABLE IF NOT EXISTS fund_nav_daily (
                    symbol TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    unit_nav REAL,
                    accumulated_nav REAL,
                    daily_growth_rate REAL,
                    source TEXT NOT NULL,
                    estimated INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, nav_date, source, estimated)
                );

                CREATE TABLE IF NOT EXISTS fund_estimates (
                    symbol TEXT NOT NULL,
                    estimate_date TEXT NOT NULL,
                    estimate_time TEXT,
                    estimated_nav REAL,
                    estimated_growth_rate REAL,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, estimate_date, source)
                );

                CREATE TABLE IF NOT EXISTS fund_purchase_status (
                    symbol TEXT NOT NULL,
                    name TEXT,
                    fund_type TEXT,
                    purchase_status TEXT,
                    redemption_status TEXT,
                    next_open_date TEXT,
                    min_purchase_amount REAL,
                    daily_limit_amount REAL,
                    fee_rate REAL,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, source)
                );

                CREATE TABLE IF NOT EXISTS fund_report_records (
                    symbol TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    item_value REAL,
                    ratio REAL,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (symbol, report_type, report_date, item_name, source)
                );

                CREATE TABLE IF NOT EXISTS source_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    check_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    trade_date TEXT,
                    field_name TEXT NOT NULL,
                    left_source TEXT NOT NULL,
                    right_source TEXT NOT NULL,
                    left_value REAL,
                    right_value REAL,
                    diff_value REAL,
                    diff_pct REAL,
                    passed INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS data_source_status (
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    field_completeness REAL,
                    record_count INTEGER,
                    error TEXT,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, category)
                );

                CREATE TABLE IF NOT EXISTS disclosure_documents (
                    document_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    title TEXT NOT NULL,
                    announcement_date TEXT,
                    category TEXT,
                    source TEXT NOT NULL,
                    file_url TEXT NOT NULL,
                    page_url TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS disclosure_source_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    left_source TEXT NOT NULL,
                    right_source TEXT NOT NULL,
                    left_document_id TEXT NOT NULL,
                    right_document_id TEXT,
                    title_match INTEGER NOT NULL,
                    date_match INTEGER NOT NULL,
                    report_period_match INTEGER,
                    hash_match INTEGER,
                    left_title TEXT NOT NULL,
                    right_title TEXT,
                    left_date TEXT,
                    right_date TEXT,
                    left_sha256 TEXT,
                    right_sha256 TEXT,
                    raw_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS document_files (
                    document_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS document_texts (
                    document_id TEXT PRIMARY KEY,
                    text_content TEXT NOT NULL,
                    extractor TEXT NOT NULL,
                    page_count INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS document_tables (
                    document_id TEXT NOT NULL,
                    table_index INTEGER NOT NULL,
                    page_number INTEGER,
                    rows_json TEXT NOT NULL,
                    extractor TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (document_id, table_index, extractor)
                );

                CREATE TABLE IF NOT EXISTS financial_fields (
                    document_id TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    value REAL,
                    unit TEXT,
                    source TEXT NOT NULL,
                    table_index INTEGER,
                    row_index INTEGER,
                    column_index INTEGER,
                    page_number INTEGER,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (document_id, field_name, source, table_index, row_index)
                );

                CREATE TABLE IF NOT EXISTS ocr_queue (
                    document_id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at TEXT,
                    completed_at TEXT,
                    source TEXT NOT NULL,
                    error TEXT,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS research_reports (
                    report_id TEXT PRIMARY KEY,
                    symbol TEXT,
                    title TEXT NOT NULL,
                    publish_date TEXT,
                    institution TEXT,
                    rating TEXT,
                    industry TEXT,
                    pdf_url TEXT,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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

    def upsert_stock_daily_bars(self, bars: Iterable[DailyBar]) -> int:
        """Insert or update stock daily bars."""

        return self._upsert_daily_bars("stock_daily_bars", bars)

    def get_schema_versions(self) -> list[dict[str, Any]]:
        """Read applied SQLite schema versions."""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT version, description, applied_at
                FROM schema_versions
                ORDER BY version ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_securities(self, securities: Iterable[SecurityInfo]) -> int:
        """Insert or update security and fund basic information."""

        rows = [
            {
                "symbol": security.symbol,
                "name": security.name,
                "security_type": security.security_type,
                "exchange": security.exchange,
                "source": security.source,
                "raw_json": json.dumps(security.raw, ensure_ascii=False, sort_keys=True),
            }
            for security in securities
            if security.symbol
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO securities (
                    symbol, name, security_type, exchange, source, raw_json
                ) VALUES (
                    :symbol, :name, :security_type, :exchange, :source, :raw_json
                )
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    security_type = excluded.security_type,
                    exchange = excluded.exchange,
                    source = excluded.source,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_fund_exchange_daily_bars(self, bars: Iterable[DailyBar]) -> int:
        """Insert or update exchange-traded fund daily bars."""

        return self._upsert_daily_bars("fund_exchange_daily_bars", bars)

    def upsert_fund_nav_daily(self, navs: Iterable[FundNav]) -> int:
        """Insert or update open-end fund NAV records."""

        rows = [
            {
                "symbol": nav.symbol,
                "nav_date": nav.nav_date,
                "unit_nav": nav.unit_nav,
                "accumulated_nav": nav.accumulated_nav,
                "daily_growth_rate": nav.daily_growth_rate,
                "source": nav.source,
                "estimated": 1 if nav.estimated else 0,
                "raw_json": json.dumps(nav.raw, ensure_ascii=False, sort_keys=True),
            }
            for nav in navs
        ]
        if not rows:
            return 0

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO fund_nav_daily (
                    symbol, nav_date, unit_nav, accumulated_nav,
                    daily_growth_rate, source, estimated, raw_json
                ) VALUES (
                    :symbol, :nav_date, :unit_nav, :accumulated_nav,
                    :daily_growth_rate, :source, :estimated, :raw_json
                )
                ON CONFLICT(symbol, nav_date, source, estimated) DO UPDATE SET
                    unit_nav = excluded.unit_nav,
                    accumulated_nav = excluded.accumulated_nav,
                    daily_growth_rate = excluded.daily_growth_rate,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_fund_estimates(self, estimates: Iterable[FundEstimate]) -> int:
        """Insert or update platform-estimated fund values."""

        rows = [
            {
                "symbol": estimate.symbol,
                "estimate_date": estimate.estimate_date,
                "estimate_time": estimate.estimate_time,
                "estimated_nav": estimate.estimated_nav,
                "estimated_growth_rate": estimate.estimated_growth_rate,
                "source": estimate.source,
                "raw_json": json.dumps(estimate.raw, ensure_ascii=False, sort_keys=True),
            }
            for estimate in estimates
            if estimate.symbol and estimate.estimate_date
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO fund_estimates (
                    symbol, estimate_date, estimate_time, estimated_nav,
                    estimated_growth_rate, source, raw_json
                ) VALUES (
                    :symbol, :estimate_date, :estimate_time, :estimated_nav,
                    :estimated_growth_rate, :source, :raw_json
                )
                ON CONFLICT(symbol, estimate_date, source) DO UPDATE SET
                    estimate_time = excluded.estimate_time,
                    estimated_nav = excluded.estimated_nav,
                    estimated_growth_rate = excluded.estimated_growth_rate,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_fund_purchase_status(self, statuses: Iterable[FundPurchaseStatus]) -> int:
        """Insert or update fund purchase/redemption statuses."""

        rows = [
            {
                "symbol": status.symbol,
                "name": status.name,
                "fund_type": status.fund_type,
                "purchase_status": status.purchase_status,
                "redemption_status": status.redemption_status,
                "next_open_date": status.next_open_date,
                "min_purchase_amount": status.min_purchase_amount,
                "daily_limit_amount": status.daily_limit_amount,
                "fee_rate": status.fee_rate,
                "source": status.source,
                "raw_json": json.dumps(status.raw, ensure_ascii=False, sort_keys=True),
            }
            for status in statuses
            if status.symbol
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO fund_purchase_status (
                    symbol, name, fund_type, purchase_status, redemption_status,
                    next_open_date, min_purchase_amount, daily_limit_amount,
                    fee_rate, source, raw_json
                ) VALUES (
                    :symbol, :name, :fund_type, :purchase_status, :redemption_status,
                    :next_open_date, :min_purchase_amount, :daily_limit_amount,
                    :fee_rate, :source, :raw_json
                )
                ON CONFLICT(symbol, source) DO UPDATE SET
                    name = excluded.name,
                    fund_type = excluded.fund_type,
                    purchase_status = excluded.purchase_status,
                    redemption_status = excluded.redemption_status,
                    next_open_date = excluded.next_open_date,
                    min_purchase_amount = excluded.min_purchase_amount,
                    daily_limit_amount = excluded.daily_limit_amount,
                    fee_rate = excluded.fee_rate,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_fund_report_records(self, records: Iterable[FundReportRecord]) -> int:
        """Insert or update public fund scale, holding, or allocation records."""

        rows = [
            {
                "symbol": record.symbol,
                "report_type": record.report_type,
                "report_date": record.report_date,
                "item_name": record.item_name,
                "item_value": record.item_value,
                "ratio": record.ratio,
                "source": record.source,
                "raw_json": json.dumps(record.raw, ensure_ascii=False, sort_keys=True),
            }
            for record in records
            if record.symbol and record.report_date and record.item_name
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO fund_report_records (
                    symbol, report_type, report_date, item_name,
                    item_value, ratio, source, raw_json
                ) VALUES (
                    :symbol, :report_type, :report_date, :item_name,
                    :item_value, :ratio, :source, :raw_json
                )
                ON CONFLICT(symbol, report_type, report_date, item_name, source) DO UPDATE SET
                    item_value = excluded.item_value,
                    ratio = excluded.ratio,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def get_stock_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read stock daily bars from SQLite."""

        return self._get_daily_bars("stock_daily_bars", symbol, start_date, end_date, source)

    def get_security(self, symbol: str) -> dict[str, Any] | None:
        """Read one security or fund basic information record."""

        sql = """
            SELECT symbol, name, security_type, exchange, source, raw_json
            FROM securities
            WHERE symbol = ?
        """
        with self._connect() as conn:
            row = conn.execute(sql, [symbol]).fetchone()
        return dict(row) if row else None

    def get_fund_exchange_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read exchange-traded fund daily bars from SQLite."""

        return self._get_daily_bars("fund_exchange_daily_bars", symbol, start_date, end_date, source)

    def get_fund_nav_daily(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
        estimated: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Read open-end fund NAV records from SQLite."""

        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start_date is not None:
            conditions.append("nav_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("nav_date <= ?")
            params.append(end_date)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        if estimated is not None:
            conditions.append("estimated = ?")
            params.append(1 if estimated else 0)

        sql = f"""
            SELECT symbol, nav_date, unit_nav, accumulated_nav, daily_growth_rate,
                   source, estimated, raw_json
            FROM fund_nav_daily
            WHERE {" AND ".join(conditions)}
            ORDER BY nav_date ASC, source ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_fund_estimates(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read platform-estimated fund values from SQLite."""

        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start_date is not None:
            conditions.append("estimate_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("estimate_date <= ?")
            params.append(end_date)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)

        sql = f"""
            SELECT symbol, estimate_date, estimate_time, estimated_nav,
                   estimated_growth_rate, source, raw_json
            FROM fund_estimates
            WHERE {" AND ".join(conditions)}
            ORDER BY estimate_date ASC, source ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_fund_purchase_status(self, symbol: str) -> list[dict[str, Any]]:
        """Read fund purchase/redemption status records from SQLite."""

        sql = """
            SELECT symbol, name, fund_type, purchase_status, redemption_status,
                   next_open_date, min_purchase_amount, daily_limit_amount,
                   fee_rate, source, raw_json
            FROM fund_purchase_status
            WHERE symbol = ?
            ORDER BY source ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, [symbol]).fetchall()
        return [dict(row) for row in rows]

    def get_fund_report_records(
        self,
        symbol: str,
        report_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read public fund scale, holding, or allocation records."""

        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if report_type is not None:
            conditions.append("report_type = ?")
            params.append(report_type)
        if start_date is not None:
            conditions.append("report_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("report_date <= ?")
            params.append(end_date)
        sql = f"""
            SELECT symbol, report_type, report_date, item_name,
                   item_value, ratio, source, raw_json
            FROM fund_report_records
            WHERE {" AND ".join(conditions)}
            ORDER BY report_date DESC, report_type ASC, item_name ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def insert_source_checks(self, checks: Iterable[dict[str, Any]]) -> int:
        """Insert source check records."""

        rows = list(checks)
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO source_checks (
                    check_type, symbol, trade_date, field_name,
                    left_source, right_source, left_value, right_value,
                    diff_value, diff_pct, passed
                ) VALUES (
                    :check_type, :symbol, :trade_date, :field_name,
                    :left_source, :right_source, :left_value, :right_value,
                    :diff_value, :diff_pct, :passed
                )
                """,
                rows,
            )
        return len(rows)

    def upsert_data_source_status(self, statuses: Iterable[DataSourceStatus]) -> int:
        """Insert or update data source status records."""

        rows = [
            {
                "source": status.source,
                "category": status.category,
                "status": status.status,
                "checked_at": status.checked_at,
                "field_completeness": status.field_completeness,
                "record_count": status.record_count,
                "error": status.error,
                "success_delta": 1 if status.status == "ok" else 0,
                "failure_delta": 1 if status.status != "ok" else 0,
                "raw_json": json.dumps(status.raw, ensure_ascii=False, sort_keys=True),
            }
            for status in statuses
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO data_source_status (
                    source, category, status, checked_at, field_completeness,
                    record_count, error, success_count, failure_count, raw_json
                ) VALUES (
                    :source, :category, :status, :checked_at, :field_completeness,
                    :record_count, :error, :success_delta, :failure_delta, :raw_json
                )
                ON CONFLICT(source, category) DO UPDATE SET
                    status = excluded.status,
                    checked_at = excluded.checked_at,
                    field_completeness = excluded.field_completeness,
                    record_count = excluded.record_count,
                    error = excluded.error,
                    success_count = data_source_status.success_count + excluded.success_count,
                    failure_count = data_source_status.failure_count + excluded.failure_count,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def get_data_source_status(self, category: str | None = None) -> list[dict[str, Any]]:
        """Read data source status records."""

        conditions: list[str] = []
        params: list[Any] = []
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT source, category, status, checked_at, field_completeness,
                   record_count, error, success_count, failure_count, raw_json
            FROM data_source_status
            {where_clause}
            ORDER BY category ASC, source ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def upsert_disclosure_documents(self, documents: Iterable[DisclosureDocument]) -> int:
        """Insert or update official disclosure document metadata."""

        rows = [
            {
                "document_id": document.document_id,
                "symbol": document.symbol,
                "title": document.title,
                "announcement_date": document.announcement_date,
                "category": document.category,
                "source": document.source,
                "file_url": document.file_url,
                "page_url": document.page_url,
                "raw_json": json.dumps(document.raw, ensure_ascii=False, sort_keys=True),
            }
            for document in documents
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO disclosure_documents (
                    document_id, symbol, title, announcement_date, category,
                    source, file_url, page_url, raw_json
                ) VALUES (
                    :document_id, :symbol, :title, :announcement_date, :category,
                    :source, :file_url, :page_url, :raw_json
                )
                ON CONFLICT(document_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    title = excluded.title,
                    announcement_date = excluded.announcement_date,
                    category = excluded.category,
                    source = excluded.source,
                    file_url = excluded.file_url,
                    page_url = excluded.page_url,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def insert_disclosure_source_checks(self, checks: Iterable[DisclosureSourceCheck]) -> int:
        """Insert cross-source disclosure check results."""

        rows = [
            {
                "symbol": check.symbol,
                "left_source": check.left_source,
                "right_source": check.right_source,
                "left_document_id": check.left_document_id,
                "right_document_id": check.right_document_id,
                "title_match": 1 if check.title_match else 0,
                "date_match": 1 if check.date_match else 0,
                "report_period_match": None
                if check.report_period_match is None
                else 1 if check.report_period_match else 0,
                "hash_match": None if check.hash_match is None else 1 if check.hash_match else 0,
                "left_title": check.left_title,
                "right_title": check.right_title,
                "left_date": check.left_date,
                "right_date": check.right_date,
                "left_sha256": check.left_sha256,
                "right_sha256": check.right_sha256,
                "raw_json": json.dumps(check.raw, ensure_ascii=False, sort_keys=True),
            }
            for check in checks
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO disclosure_source_checks (
                    symbol, left_source, right_source, left_document_id, right_document_id,
                    title_match, date_match, report_period_match, hash_match,
                    left_title, right_title, left_date, right_date,
                    left_sha256, right_sha256, raw_json
                ) VALUES (
                    :symbol, :left_source, :right_source, :left_document_id, :right_document_id,
                    :title_match, :date_match, :report_period_match, :hash_match,
                    :left_title, :right_title, :left_date, :right_date,
                    :left_sha256, :right_sha256, :raw_json
                )
                """,
                rows,
            )
        return len(rows)

    def upsert_document_files(self, files: Iterable[DownloadedFile]) -> int:
        """Insert or update downloaded official disclosure file metadata."""

        rows = [
            {
                "document_id": file.document_id,
                "file_path": file.file_path,
                "file_url": file.file_url,
                "sha256": file.sha256,
                "size_bytes": file.size_bytes,
                "source": file.source,
                "status": file.status,
                "error": file.error,
            }
            for file in files
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO document_files (
                    document_id, file_path, file_url, sha256, size_bytes,
                    source, status, error
                ) VALUES (
                    :document_id, :file_path, :file_url, :sha256, :size_bytes,
                    :source, :status, :error
                )
                ON CONFLICT(document_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    file_url = excluded.file_url,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    source = excluded.source,
                    status = excluded.status,
                    error = excluded.error,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_document_texts(self, texts: Iterable[DocumentText]) -> int:
        """Insert or update extracted document text records."""

        rows = [
            {
                "document_id": text.document_id,
                "text_content": text.text_content,
                "extractor": text.extractor,
                "page_count": text.page_count,
                "status": text.status,
                "error": text.error,
                "raw_json": json.dumps(text.raw, ensure_ascii=False, sort_keys=True),
            }
            for text in texts
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO document_texts (
                    document_id, text_content, extractor, page_count,
                    status, error, raw_json
                ) VALUES (
                    :document_id, :text_content, :extractor, :page_count,
                    :status, :error, :raw_json
                )
                ON CONFLICT(document_id) DO UPDATE SET
                    text_content = excluded.text_content,
                    extractor = excluded.extractor,
                    page_count = excluded.page_count,
                    status = excluded.status,
                    error = excluded.error,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_document_tables(self, tables: Iterable[DocumentTable]) -> int:
        """Insert or update extracted document table records."""

        rows = [
            {
                "document_id": table.document_id,
                "table_index": table.table_index,
                "page_number": table.page_number,
                "rows_json": json.dumps(table.rows, ensure_ascii=False),
                "extractor": table.extractor,
                "status": table.status,
                "error": table.error,
                "raw_json": json.dumps(table.raw, ensure_ascii=False, sort_keys=True),
            }
            for table in tables
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO document_tables (
                    document_id, table_index, page_number, rows_json,
                    extractor, status, error, raw_json
                ) VALUES (
                    :document_id, :table_index, :page_number, :rows_json,
                    :extractor, :status, :error, :raw_json
                )
                ON CONFLICT(document_id, table_index, extractor) DO UPDATE SET
                    page_number = excluded.page_number,
                    rows_json = excluded.rows_json,
                    status = excluded.status,
                    error = excluded.error,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_research_reports(self, reports: Iterable[ResearchReport]) -> int:
        """Insert or update third-party research report metadata."""

        rows = [
            {
                "report_id": report.report_id,
                "symbol": report.symbol,
                "title": report.title,
                "publish_date": report.publish_date,
                "institution": report.institution,
                "rating": report.rating,
                "industry": report.industry,
                "pdf_url": report.pdf_url,
                "source": report.source,
                "raw_json": json.dumps(report.raw, ensure_ascii=False, sort_keys=True),
            }
            for report in reports
            if report.report_id
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO research_reports (
                    report_id, symbol, title, publish_date, institution,
                    rating, industry, pdf_url, source, raw_json
                ) VALUES (
                    :report_id, :symbol, :title, :publish_date, :institution,
                    :rating, :industry, :pdf_url, :source, :raw_json
                )
                ON CONFLICT(report_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    title = excluded.title,
                    publish_date = excluded.publish_date,
                    institution = excluded.institution,
                    rating = excluded.rating,
                    industry = excluded.industry,
                    pdf_url = excluded.pdf_url,
                    source = excluded.source,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_financial_fields(self, fields: Iterable[FinancialField]) -> int:
        """Insert or update extracted financial fields."""

        rows = [
            {
                "document_id": field.document_id,
                "field_name": field.field_name,
                "field_label": field.field_label,
                "value": field.value,
                "unit": field.unit,
                "source": field.source,
                "table_index": field.table_index,
                "row_index": field.row_index,
                "column_index": field.column_index,
                "page_number": field.page_number,
                "raw_json": json.dumps(field.raw, ensure_ascii=False, sort_keys=True),
            }
            for field in fields
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO financial_fields (
                    document_id, field_name, field_label, value, unit, source,
                    table_index, row_index, column_index, page_number, raw_json
                ) VALUES (
                    :document_id, :field_name, :field_label, :value, :unit, :source,
                    :table_index, :row_index, :column_index, :page_number, :raw_json
                )
                ON CONFLICT(document_id, field_name, source, table_index, row_index) DO UPDATE SET
                    field_label = excluded.field_label,
                    value = excluded.value,
                    unit = excluded.unit,
                    column_index = excluded.column_index,
                    page_number = excluded.page_number,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def upsert_ocr_queue_items(self, items: Iterable[OCRQueueItem]) -> int:
        """Insert or update OCR queue items."""

        rows = [
            {
                "document_id": item.document_id,
                "file_path": item.file_path,
                "reason": item.reason,
                "status": item.status,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts,
                "next_attempt_at": item.next_attempt_at,
                "completed_at": item.completed_at,
                "source": item.source,
                "error": item.error,
                "raw_json": json.dumps(item.raw, ensure_ascii=False, sort_keys=True),
            }
            for item in items
            if item.document_id and item.file_path
        ]
        if not rows:
            return 0
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO ocr_queue (
                    document_id, file_path, reason, status, attempts,
                    max_attempts, next_attempt_at, completed_at,
                    source, error, raw_json
                ) VALUES (
                    :document_id, :file_path, :reason, :status, :attempts,
                    :max_attempts, :next_attempt_at, :completed_at,
                    :source, :error, :raw_json
                )
                ON CONFLICT(document_id) DO UPDATE SET
                    file_path = excluded.file_path,
                    reason = excluded.reason,
                    status = excluded.status,
                    attempts = excluded.attempts,
                    max_attempts = excluded.max_attempts,
                    next_attempt_at = excluded.next_attempt_at,
                    completed_at = excluded.completed_at,
                    source = excluded.source,
                    error = excluded.error,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def get_disclosure_documents(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read official disclosure document metadata from SQLite."""

        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start_date is not None:
            conditions.append("announcement_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("announcement_date <= ?")
            params.append(end_date)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)

        sql = f"""
            SELECT document_id, symbol, title, announcement_date, category,
                   source, file_url, page_url, raw_json
            FROM disclosure_documents
            WHERE {" AND ".join(conditions)}
            ORDER BY announcement_date DESC, document_id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_disclosure_source_checks(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Read cross-source disclosure check results."""

        conditions: list[str] = []
        params: list[Any] = []
        if symbol is not None:
            conditions.append("symbol = ?")
            params.append(symbol)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT symbol, left_source, right_source, left_document_id, right_document_id,
                   title_match, date_match, report_period_match, hash_match,
                   left_title, right_title, left_date, right_date,
                   left_sha256, right_sha256, raw_json
            FROM disclosure_source_checks
            {where_clause}
            ORDER BY created_at ASC, id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_document_text(self, document_id: str) -> dict[str, Any] | None:
        """Read extracted text for one disclosure document."""

        sql = """
            SELECT document_id, text_content, extractor, page_count,
                   status, error, raw_json
            FROM document_texts
            WHERE document_id = ?
        """
        with self._connect() as conn:
            row = conn.execute(sql, [document_id]).fetchone()
        return dict(row) if row else None

    def get_document_tables(self, document_id: str) -> list[dict[str, Any]]:
        """Read extracted tables for one disclosure document."""

        sql = """
            SELECT document_id, table_index, page_number, rows_json,
                   extractor, status, error, raw_json
            FROM document_tables
            WHERE document_id = ?
            ORDER BY table_index ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, [document_id]).fetchall()
        return [dict(row) for row in rows]

    def get_research_reports(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read third-party research report metadata from SQLite."""

        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start_date is not None:
            conditions.append("publish_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("publish_date <= ?")
            params.append(end_date)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)
        sql = f"""
            SELECT report_id, symbol, title, publish_date, institution,
                   rating, industry, pdf_url, source, raw_json
            FROM research_reports
            WHERE {" AND ".join(conditions)}
            ORDER BY publish_date DESC, report_id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_ocr_queue_items(
        self,
        status: str | None = None,
        due_at: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read OCR queue items."""

        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if due_at is not None:
            conditions.append("(next_attempt_at IS NULL OR next_attempt_at <= ?)")
            params.append(due_at)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT document_id, file_path, reason, status, attempts,
                   max_attempts, next_attempt_at, completed_at,
                   source, error, raw_json
            FROM ocr_queue
            {where_clause}
            ORDER BY updated_at ASC, document_id ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_financial_fields(self, document_id: str) -> list[dict[str, Any]]:
        """Read extracted financial fields for one document."""

        sql = """
            SELECT document_id, field_name, field_label, value, unit, source,
                   table_index, row_index, column_index, page_number, raw_json
            FROM financial_fields
            WHERE document_id = ?
            ORDER BY field_name ASC, table_index ASC, row_index ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, [document_id]).fetchall()
        return [dict(row) for row in rows]

    def update_ocr_queue_item(
        self,
        document_id: str,
        status: str,
        attempts: int | None = None,
        error: str | None = None,
        next_attempt_at: str | None = None,
        completed_at: str | None = None,
    ) -> int:
        """Update OCR queue status for one document."""

        assignments = ["status = ?", "error = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: list[Any] = [status, error]
        if attempts is not None:
            assignments.append("attempts = ?")
            params.append(attempts)
        assignments.append("next_attempt_at = ?")
        params.append(next_attempt_at)
        assignments.append("completed_at = ?")
        params.append(completed_at)
        params.append(document_id)
        sql = f"UPDATE ocr_queue SET {', '.join(assignments)} WHERE document_id = ?"
        with self._connect() as conn:
            cursor = conn.execute(sql, params)
        return cursor.rowcount

    def _upsert_daily_bars(self, table: str, bars: Iterable[DailyBar]) -> int:
        rows = [
            {
                "symbol": bar.symbol,
                "trade_date": bar.trade_date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "source": bar.source,
                "raw_json": json.dumps(bar.raw, ensure_ascii=False, sort_keys=True),
            }
            for bar in bars
        ]
        if not rows:
            return 0

        with self._connect() as conn:
            conn.executemany(
                f"""
                INSERT INTO {table} (
                    symbol, trade_date, open, high, low, close,
                    volume, amount, source, raw_json
                ) VALUES (
                    :symbol, :trade_date, :open, :high, :low, :close,
                    :volume, :amount, :source, :raw_json
                )
                ON CONFLICT(symbol, trade_date, source) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
        return len(rows)

    def _get_daily_bars(
        self,
        table: str,
        symbol: str,
        start_date: str | None,
        end_date: str | None,
        source: str | None,
    ) -> list[dict[str, Any]]:
        conditions = ["symbol = ?"]
        params: list[Any] = [symbol]
        if start_date is not None:
            conditions.append("trade_date >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("trade_date <= ?")
            params.append(end_date)
        if source is not None:
            conditions.append("source = ?")
            params.append(source)

        sql = f"""
            SELECT symbol, trade_date, open, high, low, close, volume, amount, source, raw_json
            FROM {table}
            WHERE {" AND ".join(conditions)}
            ORDER BY trade_date ASC, source ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
