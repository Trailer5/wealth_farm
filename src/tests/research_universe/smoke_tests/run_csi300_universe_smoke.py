"""Fetch and persist the real current CSI 300 universe."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.master_data import (
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_universe import (
        MembershipStatus,
        ResearchUniverseService,
        SQLiteResearchUniverseRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "csi300-universe.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = master_data.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_index",
                exchange="csi",
            ),
            source="project:index_catalog",
            raw_symbol="000300",
        )
        service = ResearchUniverseService(
            master_data=master_data,
            data_fetch=DataFetchService(
                store=SQLiteDataStore(db_path),
                master_data_service=master_data,
            ),
            repository=SQLiteResearchUniverseRepository(db_path),
        )
        snapshot = service.refresh_index_universe(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id=index.asset_id,
            provider_timeout_seconds=60,
        )
        if snapshot.quality.total_source_records != 300:
            raise AssertionError(
                "Expected 300 CSI 300 source records, got "
                f"{snapshot.quality.total_source_records}"
            )
        if snapshot.quality.unresolved_count:
            raise AssertionError(
                "CSI 300 snapshot contains unresolved identities: "
                f"{snapshot.quality.unresolved_count}"
            )
        stored = service.get(
            "csi300",
            version=snapshot.universe.version,
        )
        if stored is None:
            raise AssertionError("Persisted universe was not found")
        resolved = [
            member
            for member in stored.memberships
            if member.status is MembershipStatus.RESOLVED
        ]
        print(
            json.dumps(
                {
                    "universe_id": stored.universe.universe_id,
                    "version": stored.universe.version,
                    "source": stored.universe.source,
                    "effective_date": (
                        stored.universe.effective_date
                    ),
                    "historical_membership": (
                        stored.universe.historical_membership
                    ),
                    "quality": {
                        "source_records": (
                            stored.quality.total_source_records
                        ),
                        "unique_symbols": (
                            stored.quality.unique_source_symbols
                        ),
                        "resolved": stored.quality.resolved_count,
                        "unresolved": (
                            stored.quality.unresolved_count
                        ),
                        "duplicates": (
                            stored.quality.duplicate_count
                        ),
                    },
                    "sample": [
                        {
                            "symbol": member.source_symbol,
                            "name": member.source_name,
                            "asset_id": member.asset_id,
                        }
                        for member in resolved[:3]
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
