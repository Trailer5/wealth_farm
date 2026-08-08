"""Classify every current CSI 300 member into a research profile."""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
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
        ResearchUniverseService,
        SQLiteResearchUniverseRepository,
    )
    from src.research_workflow import (
        ResearchProfileAssignmentService,
        SQLiteResearchProfileAssignmentRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "universe-profiles.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = master.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_a",
                exchange="sh",
            ),
            source="smoke:catalog",
            raw_symbol="000300",
        )
        fetch = DataFetchService(
            store=store,
            master_data_service=master,
        )
        universe = ResearchUniverseService(
            master_data=master,
            data_fetch=fetch,
            repository=SQLiteResearchUniverseRepository(db_path),
        )
        snapshot = universe.refresh_index_universe(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id=index.asset_id,
            provider_timeout_seconds=120,
        )
        asset_by_symbol = {
            item.source_symbol: item.asset_id
            for item in snapshot.memberships
            if item.asset_id is not None
        }
        industries = fetch.fetch_stock_industries(
            list(asset_by_symbol)
        )
        service = ResearchProfileAssignmentService(
            master_data=master,
            repository=SQLiteResearchProfileAssignmentRepository(
                db_path
            ),
        )
        assignments = service.classify_industries(
            industries,
            asset_by_symbol=asset_by_symbol,
            effective_date=snapshot.universe.effective_date,
        )
        profile_map = service.profile_map(
            list(asset_by_symbol.values()),
            as_of_date="2026-08-03",
        )
        if len(profile_map) != len(snapshot.memberships):
            missing = set(asset_by_symbol.values()) - set(profile_map)
            raise AssertionError(
                f"Unclassified CSI 300 members: {len(missing)}"
            )
        counts = Counter(
            item.profile_id for item in assignments
        )
        confidence = Counter(
            str(item.confidence) for item in assignments
        )
        print(
            json.dumps(
                {
                    "universe_version": snapshot.universe.version,
                    "members": len(snapshot.memberships),
                    "industry_records": len(industries),
                    "classified": len(profile_map),
                    "profile_counts": dict(sorted(counts.items())),
                    "confidence_counts": dict(
                        sorted(confidence.items())
                    ),
                    "ordinary_default_limitation": (
                        "high_growth_unprofitable_not_detected"
                    ),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
