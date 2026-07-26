"""Service for extracting and querying source-backed events."""

from __future__ import annotations

from src.data_src.models import DisclosureDocument
from src.master_data import MasterDataService

from .extractor import DisclosureEventExtractor
from .models import ResearchEvent
from .sqlite_repository import SQLiteEventRepository


class EventKnowledgeService:
    """Extract deterministic events and persist them idempotently."""

    def __init__(
        self,
        repository: SQLiteEventRepository,
        master_data: MasterDataService,
        extractor: DisclosureEventExtractor | None = None,
    ) -> None:
        self.repository = repository
        self.master_data = master_data
        self.extractor = extractor or DisclosureEventExtractor()
        self.repository.initialize()

    def ingest_disclosure(
        self,
        asset_id: str,
        document: DisclosureDocument,
        *,
        include_other: bool = False,
    ) -> list[ResearchEvent]:
        """Extract and persist events from one disclosure."""

        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        events = self.extractor.extract(
            asset_id,
            document,
            include_other=include_other,
        )
        self.repository.upsert_events(events)
        return events

    def list_events(
        self,
        asset_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        event_type: str | None = None,
    ) -> list[ResearchEvent]:
        """Query events for one asset."""

        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        return self.repository.list_events(
            asset_id,
            start_date=start_date,
            end_date=end_date,
            event_type=event_type,
        )
