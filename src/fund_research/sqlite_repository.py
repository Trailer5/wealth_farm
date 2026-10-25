"""SQLite persistence for fund manager and underlying relations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from pathlib import Path

from .models import (
    DisclosureExtractionStatus,
    FundAssetRelation,
    FundRelationType,
    OpenFundAssetAllocation,
    OpenFundHolding,
    OpenFundIndustryAllocation,
    OpenFundManagerTenure,
    OpenFundPerformanceComparison,
    OpenFundPeriodicSnapshot,
    OpenFundProfileSnapshot,
    OpenFundShareClassSnapshot,
    OpenFundShareFlow,
)


class SQLiteFundResearchRepository:
    SCHEMA_VERSION = 28
    SCHEMA_DESCRIPTION = "open fund official disclosure snapshots"

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

                CREATE TABLE IF NOT EXISTS fund_asset_relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fund_asset_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    related_asset_id TEXT NOT NULL DEFAULT '',
                    related_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        fund_asset_id,
                        relation_type,
                        related_asset_id,
                        related_name,
                        source,
                        effective_date
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_fund_relations_lookup
                ON fund_asset_relations (
                    fund_asset_id,
                    relation_type,
                    effective_date
                );

                CREATE TABLE IF NOT EXISTS open_fund_profile_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fund_asset_id TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    announcement_date TEXT NOT NULL,
                    source_document_id TEXT NOT NULL,
                    extraction_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        fund_asset_id,
                        effective_date,
                        source_document_id
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_open_fund_profile_lookup
                ON open_fund_profile_snapshots (
                    fund_asset_id,
                    announcement_date,
                    effective_date
                );

                CREATE TABLE IF NOT EXISTS open_fund_periodic_snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fund_asset_id TEXT NOT NULL,
                    report_period_start TEXT NOT NULL,
                    report_period_end TEXT NOT NULL,
                    announcement_date TEXT NOT NULL,
                    source_document_id TEXT NOT NULL,
                    extraction_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (
                        fund_asset_id,
                        report_period_end,
                        source_document_id
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_open_fund_periodic_lookup
                ON open_fund_periodic_snapshots (
                    fund_asset_id,
                    announcement_date,
                    report_period_end
                );
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

    def save_relation(
        self,
        relation: FundAssetRelation,
    ) -> FundAssetRelation:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fund_asset_relations (
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date,
                    confidence,
                    raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date
                ) DO UPDATE SET
                    confidence = excluded.confidence,
                    raw_json = excluded.raw_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    relation.fund_asset_id,
                    relation.relation_type.value,
                    relation.related_asset_id or "",
                    relation.related_name,
                    relation.source,
                    relation.effective_date,
                    relation.confidence,
                    json.dumps(
                        relation.raw,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                ],
            )
        return relation

    def list_relations(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
        relation_type: FundRelationType | None = None,
    ) -> list[FundAssetRelation]:
        conditions = [
            "fund_asset_id = ?",
            "effective_date <= ?",
        ]
        params: list[object] = [fund_asset_id, as_of_date]
        if relation_type is not None:
            conditions.append("relation_type = ?")
            params.append(relation_type.value)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    fund_asset_id,
                    relation_type,
                    related_asset_id,
                    related_name,
                    source,
                    effective_date,
                    confidence,
                    raw_json
                FROM fund_asset_relations
                WHERE {" AND ".join(conditions)}
                ORDER BY relation_type, effective_date DESC, related_name
                """,
                params,
            ).fetchall()
        return [_relation_from_row(row) for row in rows]

    def save_profile_snapshot(
        self,
        snapshot: OpenFundProfileSnapshot,
    ) -> OpenFundProfileSnapshot:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM open_fund_profile_snapshots
                WHERE fund_asset_id = ?
                  AND source_document_id = ?
                  AND effective_date <> ?
                """,
                [
                    snapshot.fund_asset_id,
                    snapshot.source_document_id,
                    snapshot.effective_date,
                ],
            )
            conn.execute(
                """
                INSERT INTO open_fund_profile_snapshots (
                    fund_asset_id,
                    effective_date,
                    announcement_date,
                    source_document_id,
                    extraction_status,
                    source,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    fund_asset_id,
                    effective_date,
                    source_document_id
                ) DO UPDATE SET
                    announcement_date = excluded.announcement_date,
                    extraction_status = excluded.extraction_status,
                    source = excluded.source,
                    payload_json = excluded.payload_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    snapshot.fund_asset_id,
                    snapshot.effective_date,
                    snapshot.announcement_date,
                    snapshot.source_document_id,
                    snapshot.extraction_status.value,
                    snapshot.source,
                    _snapshot_json(snapshot),
                ],
            )
        return snapshot

    def list_profile_snapshots(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
    ) -> list[OpenFundProfileSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM open_fund_profile_snapshots
                WHERE fund_asset_id = ?
                  AND effective_date <= ?
                  AND announcement_date <= ?
                ORDER BY effective_date DESC,
                         announcement_date DESC,
                         source_document_id
                """,
                [fund_asset_id, as_of_date, as_of_date],
            ).fetchall()
        return [
            _profile_snapshot_from_json(str(row["payload_json"]))
            for row in rows
        ]

    def get_latest_profile_snapshot(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
    ) -> OpenFundProfileSnapshot | None:
        snapshots = self.list_profile_snapshots(
            fund_asset_id,
            as_of_date=as_of_date,
        )
        return snapshots[0] if snapshots else None

    def save_periodic_snapshot(
        self,
        snapshot: OpenFundPeriodicSnapshot,
    ) -> OpenFundPeriodicSnapshot:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO open_fund_periodic_snapshots (
                    fund_asset_id,
                    report_period_start,
                    report_period_end,
                    announcement_date,
                    source_document_id,
                    extraction_status,
                    source,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    fund_asset_id,
                    report_period_end,
                    source_document_id
                ) DO UPDATE SET
                    report_period_start = excluded.report_period_start,
                    announcement_date = excluded.announcement_date,
                    extraction_status = excluded.extraction_status,
                    source = excluded.source,
                    payload_json = excluded.payload_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [
                    snapshot.fund_asset_id,
                    snapshot.report_period_start,
                    snapshot.report_period_end,
                    snapshot.announcement_date,
                    snapshot.source_document_id,
                    snapshot.extraction_status.value,
                    snapshot.source,
                    _snapshot_json(snapshot),
                ],
            )
        return snapshot

    def list_periodic_snapshots(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
    ) -> list[OpenFundPeriodicSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json
                FROM open_fund_periodic_snapshots
                WHERE fund_asset_id = ?
                  AND report_period_end <= ?
                  AND announcement_date <= ?
                ORDER BY report_period_end DESC,
                         announcement_date DESC,
                         source_document_id
                """,
                [fund_asset_id, as_of_date, as_of_date],
            ).fetchall()
        return [
            _periodic_snapshot_from_json(str(row["payload_json"]))
            for row in rows
        ]

    def get_latest_periodic_snapshot(
        self,
        fund_asset_id: str,
        *,
        as_of_date: str,
    ) -> OpenFundPeriodicSnapshot | None:
        snapshots = self.list_periodic_snapshots(
            fund_asset_id,
            as_of_date=as_of_date,
        )
        return snapshots[0] if snapshots else None

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _relation_from_row(row: sqlite3.Row) -> FundAssetRelation:
    return FundAssetRelation(
        fund_asset_id=str(row["fund_asset_id"]),
        relation_type=FundRelationType(row["relation_type"]),
        related_asset_id=(
            str(row["related_asset_id"])
            if row["related_asset_id"]
            else None
        ),
        related_name=str(row["related_name"]),
        source=str(row["source"]),
        effective_date=str(row["effective_date"]),
        confidence=float(row["confidence"]),
        raw=json.loads(str(row["raw_json"])),
    )


def _snapshot_json(snapshot: object) -> str:
    return json.dumps(
        asdict(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported snapshot value: {type(value)}")


def _profile_snapshot_from_json(
    payload_json: str,
) -> OpenFundProfileSnapshot:
    payload = json.loads(payload_json)
    return OpenFundProfileSnapshot(
        fund_asset_id=str(payload["fund_asset_id"]),
        effective_date=str(payload["effective_date"]),
        announcement_date=str(payload["announcement_date"]),
        fund_name=str(payload["fund_name"]),
        share_class_name=str(payload["share_class_name"]),
        share_class_code=str(payload["share_class_code"]),
        manager_name=str(payload["manager_name"]),
        custodian_name=str(payload["custodian_name"]),
        fund_managers=tuple(
            OpenFundManagerTenure(
                name=str(item["name"]),
                start_date=str(item["start_date"]),
                end_date=(
                    str(item["end_date"])
                    if item.get("end_date") is not None
                    else None
                ),
                page_number=int(item["page_number"]),
            )
            for item in payload["fund_managers"]
        ),
        benchmark_text=str(payload["benchmark_text"]),
        management_fee_rate=_optional_decimal(
            payload.get("management_fee_rate")
        ),
        custody_fee_rate=_optional_decimal(
            payload.get("custody_fee_rate")
        ),
        sales_service_fee_rate=_optional_decimal(
            payload.get("sales_service_fee_rate")
        ),
        source_document_id=str(payload["source_document_id"]),
        source_title=str(payload["source_title"]),
        source_url=str(payload["source_url"]),
        source=str(payload["source"]),
        parser_version=str(payload["parser_version"]),
        extraction_status=DisclosureExtractionStatus(
            payload["extraction_status"]
        ),
        page_numbers={
            str(key): int(value)
            for key, value in payload.get("page_numbers", {}).items()
        },
        extraction_errors=tuple(payload.get("extraction_errors", ())),
        raw=dict(payload.get("raw", {})),
    )


def _periodic_snapshot_from_json(
    payload_json: str,
) -> OpenFundPeriodicSnapshot:
    payload = json.loads(payload_json)
    return OpenFundPeriodicSnapshot(
        fund_asset_id=str(payload["fund_asset_id"]),
        report_period_start=str(payload["report_period_start"]),
        report_period_end=str(payload["report_period_end"]),
        announcement_date=str(payload["announcement_date"]),
        share_classes=tuple(
            OpenFundShareClassSnapshot(
                share_class_name=str(item["share_class_name"]),
                share_class_code=str(item["share_class_code"]),
                ending_shares=Decimal(str(item["ending_shares"])),
                unit_nav=_optional_decimal(item.get("unit_nav")),
                net_asset_value=_optional_decimal(
                    item.get("net_asset_value")
                ),
                page_number=int(item["page_number"]),
            )
            for item in payload["share_classes"]
        ),
        share_flows=tuple(
            OpenFundShareFlow(
                share_class_name=str(item["share_class_name"]),
                share_class_code=str(item["share_class_code"]),
                opening_shares=Decimal(str(item["opening_shares"])),
                subscriptions=Decimal(str(item["subscriptions"])),
                redemptions=Decimal(str(item["redemptions"])),
                ending_shares=Decimal(str(item["ending_shares"])),
                page_number=int(item["page_number"]),
            )
            for item in payload["share_flows"]
        ),
        asset_allocations=tuple(
            OpenFundAssetAllocation(
                category=str(item["category"]),
                amount=Decimal(str(item["amount"])),
                ratio=Decimal(str(item["ratio"])),
                basis=str(item["basis"]),
                page_number=int(item["page_number"]),
            )
            for item in payload["asset_allocations"]
        ),
        industry_allocations=tuple(
            OpenFundIndustryAllocation(
                category=str(item["category"]),
                fair_value=Decimal(str(item["fair_value"])),
                net_asset_ratio=Decimal(
                    str(item["net_asset_ratio"])
                ),
                market_scope=str(item["market_scope"]),
                portfolio_scope=str(item["portfolio_scope"]),
                page_number=int(item["page_number"]),
            )
            for item in payload["industry_allocations"]
        ),
        top_holdings=tuple(
            OpenFundHolding(
                rank=int(item["rank"]),
                security_code=str(item["security_code"]),
                security_name=str(item["security_name"]),
                quantity=Decimal(str(item["quantity"])),
                fair_value=Decimal(str(item["fair_value"])),
                net_asset_ratio=Decimal(
                    str(item["net_asset_ratio"])
                ),
                page_number=int(item["page_number"]),
            )
            for item in payload["top_holdings"]
        ),
        source_document_id=str(payload["source_document_id"]),
        source_title=str(payload["source_title"]),
        source_url=str(payload["source_url"]),
        source=str(payload["source"]),
        parser_version=str(payload["parser_version"]),
        extraction_status=DisclosureExtractionStatus(
            payload["extraction_status"]
        ),
        performance_comparisons=tuple(
            OpenFundPerformanceComparison(
                period_label=str(item["period_label"]),
                fund_return=Decimal(str(item["fund_return"])),
                benchmark_return=Decimal(
                    str(item["benchmark_return"])
                ),
                tracking_difference=Decimal(
                    str(item["tracking_difference"])
                ),
                page_number=int(item["page_number"]),
                share_class_name=str(
                    item.get("share_class_name") or ""
                ),
                share_class_code=str(
                    item.get("share_class_code") or ""
                ),
            )
            for item in payload.get("performance_comparisons", ())
        ),
        extraction_errors=tuple(payload.get("extraction_errors", ())),
        raw=dict(payload.get("raw", {})),
    )


def _optional_decimal(value: object | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None
