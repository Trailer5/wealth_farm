"""Versioned research-profile assignments for universe members."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.data_src import StockIndustryInfo
from src.master_data import MasterDataService

from .profiles import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    ResearchProfileRegistry,
    default_profile_registry,
)


@dataclass(frozen=True)
class ResearchProfileAssignment:
    asset_id: str
    profile_id: str
    industry_name: str
    source: str
    effective_date: str
    confidence: float
    reason: str
    manual_override: bool = False

    def __post_init__(self) -> None:
        date.fromisoformat(self.effective_date)
        if not all(
            value.strip()
            for value in (
                self.asset_id,
                self.profile_id,
                self.source,
                self.reason,
            )
        ):
            raise ValueError(
                "Profile assignment identity and reason are required"
            )
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")


class SQLiteResearchProfileAssignmentRepository:
    SCHEMA_VERSION = 23
    SCHEMA_DESCRIPTION = "versioned research profile assignments"

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
                CREATE TABLE IF NOT EXISTS research_profile_assignments (
                    asset_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    industry_name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    reason TEXT NOT NULL,
                    manual_override INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (asset_id, effective_date, source)
                );
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_versions (
                    version, description
                ) VALUES (?, ?)
                """,
                [self.SCHEMA_VERSION, self.SCHEMA_DESCRIPTION],
            )

    def save(
        self,
        assignment: ResearchProfileAssignment,
    ) -> ResearchProfileAssignment:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_profile_assignments (
                    asset_id, profile_id, industry_name, source,
                    effective_date, confidence, reason, manual_override
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (asset_id, effective_date, source)
                DO UPDATE SET
                    profile_id = excluded.profile_id,
                    industry_name = excluded.industry_name,
                    confidence = excluded.confidence,
                    reason = excluded.reason,
                    manual_override = excluded.manual_override
                """,
                [
                    assignment.asset_id,
                    assignment.profile_id,
                    assignment.industry_name,
                    assignment.source,
                    assignment.effective_date,
                    assignment.confidence,
                    assignment.reason,
                    1 if assignment.manual_override else 0,
                ],
            )
        return assignment

    def get(
        self,
        asset_id: str,
        *,
        as_of_date: str,
    ) -> ResearchProfileAssignment | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM research_profile_assignments
                WHERE asset_id = ? AND effective_date <= ?
                ORDER BY manual_override DESC,
                         effective_date DESC,
                         created_at DESC
                LIMIT 1
                """,
                [asset_id, as_of_date],
            ).fetchone()
        return _assignment(row) if row else None

    def _connect(self) -> sqlite3.Connection:
        if str(self.db_path) == ":memory:":
            if self._memory_connection is None:
                self._memory_connection = sqlite3.connect(":memory:")
                self._memory_connection.row_factory = sqlite3.Row
            return self._memory_connection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class ResearchProfileAssignmentService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        repository: SQLiteResearchProfileAssignmentRepository,
        profile_registry: ResearchProfileRegistry | None = None,
    ) -> None:
        self.master_data = master_data
        self.repository = repository
        self.profile_registry = (
            profile_registry or default_profile_registry()
        )
        self.repository.initialize()

    def classify_industries(
        self,
        records: list[StockIndustryInfo],
        *,
        asset_by_symbol: dict[str, str],
        effective_date: str,
    ) -> tuple[ResearchProfileAssignment, ...]:
        assignments = []
        for record in records:
            asset_id = asset_by_symbol.get(record.symbol)
            if asset_id is None:
                continue
            profile_id, confidence, reason = _classify(
                record.industry_name
            )
            assignment = ResearchProfileAssignment(
                asset_id=asset_id,
                profile_id=profile_id,
                industry_name=record.industry_name,
                source=record.source,
                effective_date=effective_date,
                confidence=confidence,
                reason=reason,
            )
            self._validate(assignment)
            assignments.append(self.repository.save(assignment))
        return tuple(assignments)

    def assign_manual(
        self,
        *,
        asset_id: str,
        profile_id: str,
        industry_name: str,
        effective_date: str,
        reason: str,
    ) -> ResearchProfileAssignment:
        assignment = ResearchProfileAssignment(
            asset_id=asset_id,
            profile_id=profile_id,
            industry_name=industry_name,
            source="manual_review",
            effective_date=effective_date,
            confidence=1.0,
            reason=reason,
            manual_override=True,
        )
        self._validate(assignment)
        return self.repository.save(assignment)

    def profile_map(
        self,
        asset_ids: list[str],
        *,
        as_of_date: str,
    ) -> dict[str, str]:
        output = {}
        for asset_id in asset_ids:
            assignment = self.repository.get(
                asset_id,
                as_of_date=as_of_date,
            )
            if assignment is not None:
                output[asset_id] = assignment.profile_id
        return output

    def _validate(
        self,
        assignment: ResearchProfileAssignment,
    ) -> None:
        asset = self.master_data.get_asset(assignment.asset_id)
        if asset is None:
            raise KeyError(
                f"Unknown asset_id: {assignment.asset_id}"
            )
        self.profile_registry.validate_for_asset(
            assignment.profile_id,
            asset,
        )


def _classify(industry_name: str) -> tuple[str, float, str]:
    value = industry_name.replace("Ⅱ", "").replace("Ⅲ", "")
    financial = ("银行", "保险", "证券", "多元金融")
    cyclical = (
        "工业金属",
        "贵金属",
        "能源金属",
        "小金属",
        "煤炭",
        "油气开采",
        "炼化",
        "钢铁",
    )
    if any(token in value for token in financial):
        return (
            PROFILE_FINANCIAL_INSTITUTION,
            0.95,
            "industry_rule:financial_institution",
        )
    if any(token in value for token in cyclical):
        return (
            PROFILE_CYCLICAL_RESOURCE,
            0.90,
            "industry_rule:cyclical_resource",
        )
    return (
        PROFILE_ORDINARY_COMPANY,
        0.70,
        "industry_default:non_specialized; "
        "high_growth_unprofitable_not_detected",
    )


def _assignment(row: sqlite3.Row) -> ResearchProfileAssignment:
    return ResearchProfileAssignment(
        asset_id=str(row["asset_id"]),
        profile_id=str(row["profile_id"]),
        industry_name=str(row["industry_name"]),
        source=str(row["source"]),
        effective_date=str(row["effective_date"]),
        confidence=float(row["confidence"]),
        reason=str(row["reason"]),
        manual_override=bool(row["manual_override"]),
    )
