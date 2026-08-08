"""Research evidence items assembled for one canonical asset."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.event_knowledge import ResearchEvent
from src.master_data import Asset


class EvidenceTier(str, Enum):
    OFFICIAL = "official"
    THIRD_PARTY = "third_party"
    DERIVED = "derived"


class DocumentContentStatus(str, Enum):
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOADED_NO_TEXT = "downloaded_no_text"
    TEXT_AVAILABLE = "text_available"
    EXTRACTION_FAILED = "extraction_failed"


@dataclass(frozen=True)
class ResearchEvidenceItem:
    evidence_id: str
    asset_id: str
    evidence_type: str
    tier: EvidenceTier
    title: str
    event_date: str
    source: str
    source_record_id: str
    source_url: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentEvidenceState:
    document_id: str
    status: DocumentContentStatus
    extractor: str | None = None
    page_count: int | None = None
    error: str | None = None
    financial_field_count: int = 0


@dataclass(frozen=True)
class ResearchEvidenceBundle:
    asset: Asset
    start_date: str
    end_date: str
    as_of_date: str
    evidence: tuple[ResearchEvidenceItem, ...]
    document_states: tuple[DocumentEvidenceState, ...]
    events: tuple[ResearchEvent, ...]
    sources: tuple[str, ...]
    complete: bool
    errors: dict[str, str] = field(default_factory=dict)
