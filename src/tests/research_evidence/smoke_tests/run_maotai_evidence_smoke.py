"""Fetch real Maotai disclosures and research metadata."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_access import (
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.event_knowledge import (
        EventKnowledgeService,
        SQLiteEventRepository,
    )
    from src.master_data import (
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_evidence.service import (
        ResearchEvidenceService,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "evidence-smoke.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = master_data.register_asset(
            Asset(
                asset_id="asset:smoke-maotai",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="evidence-smoke",
            raw_symbol="600519",
        )
        store = SQLiteDataStore(db_path)
        data_access = DataAccessService(
            master_data=master_data,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master_data,
            ),
        )
        bundle = ResearchEvidenceService(
            master_data=master_data,
            data_access=data_access,
            event_knowledge=EventKnowledgeService(
                SQLiteEventRepository(db_path),
                master_data,
            ),
            store=store,
        ).get_bundle(
            asset_id=asset.asset_id,
            start_date="2025-01-01",
            end_date="2026-08-03",
            as_of_date="2026-08-03",
            allow_remote=True,
            disclosure_sources=("cninfo",),
            research_sources=("eastmoney",),
            provider_timeout_seconds=60,
        )
        disclosure_count = sum(
            item.evidence_type == "disclosure"
            for item in bundle.evidence
        )
        research_count = sum(
            item.evidence_type == "research_report"
            for item in bundle.evidence
        )
        if disclosure_count == 0:
            raise RuntimeError(
                "Real evidence smoke returned no disclosures"
            )
        if research_count == 0:
            raise RuntimeError(
                "Real evidence smoke returned no research reports"
            )
        print(
            json.dumps(
                {
                    "asset_id": asset.asset_id,
                    "complete": bundle.complete,
                    "disclosure_count": disclosure_count,
                    "research_report_count": research_count,
                    "event_count": len(bundle.events),
                    "document_states": {
                        state.status.value
                        for state in bundle.document_states
                    },
                    "sources": bundle.sources,
                },
                ensure_ascii=True,
                indent=2,
                default=list,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
