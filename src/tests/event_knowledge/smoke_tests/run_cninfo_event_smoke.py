"""Extract deterministic events from real CNINFO disclosures."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.cninfo import CninfoDataSource
    from src.event_knowledge import EventKnowledgeService, SQLiteEventRepository
    from src.master_data import MasterDataService, SQLiteMasterDataRepository

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "cninfo-event-smoke.sqlite3"
        master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
        asset = master_data.resolve_identifier(
            source="user:input",
            raw_symbol="600519",
            name="贵州茅台",
            asset_type_hint="股票",
        ).asset
        cninfo = CninfoDataSource()
        documents = cninfo.search_announcements(
            symbol="600519",
            start_date="2025-01-01",
            end_date="2026-07-26",
            category=CninfoDataSource.CATEGORY_ANNUAL_REPORT,
            page_size=10,
        )
        if not documents:
            raise RuntimeError("CNINFO returned no annual report disclosures")

        service = EventKnowledgeService(
            SQLiteEventRepository(db_path),
            master_data,
        )
        extracted = []
        for document in documents:
            extracted.extend(
                service.ingest_disclosure(
                    asset.asset_id,
                    document,
                )
            )
        if not extracted:
            raise RuntimeError("No event matched the real disclosure titles")
        persisted = service.list_events(asset.asset_id)
        result = {
            "asset_id": asset.asset_id,
            "document_count": len(documents),
            "event_count": len(persisted),
            "event_types": sorted({event.event_type for event in persisted}),
            "sample_title": persisted[0].title,
            "sample_source_record_id": persisted[0].source_record_id,
            "sample_source_url": persisted[0].source_url,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
