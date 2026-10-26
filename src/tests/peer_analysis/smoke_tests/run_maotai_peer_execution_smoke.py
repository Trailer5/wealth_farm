"""Run real Maotai peer metrics through the fundamental pipeline."""

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
    from src.financial_data import SQLiteFinancialDataRepository
    from src.fundamental_analysis import (
        FundamentalAnalysisService,
        SQLiteFundamentalMetricRepository,
    )
    from src.master_data import (
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.peer_analysis import (
        IndustryAssignment,
        PeerAnalysisService,
        PeerComparisonStatus,
        PeerMetricExecutionService,
        PeerSet,
        PeerSetMember,
        SQLitePeerAnalysisRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "peer-execution.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        assets = {
            symbol: _register(master, symbol, name)
            for symbol, name in (
                ("600519", "贵州茅台"),
                ("000858", "五粮液"),
                ("000568", "泸州老窖"),
                ("600809", "山西汾酒"),
                ("600600", "青岛啤酒"),
            )
        }
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=DataFetchService(
                store=store,
                master_data_service=master,
            ),
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        metric_repository = SQLiteFundamentalMetricRepository(
            db_path
        )
        peer_service = PeerAnalysisService(
            master_data=master,
            metric_repository=metric_repository,
            peer_repository=SQLitePeerAnalysisRepository(db_path),
        )
        peer_set = PeerSet(
            peer_set_id="peer-set:maotai:baijiu:v1",
            target_asset_id=assets["600519"].asset_id,
            name="贵州茅台白酒可比组",
            source="manual_review",
            version="v1",
            effective_date="2026-01-01",
            members=(
                PeerSetMember(
                    assets["000858"].asset_id,
                    True,
                    "全国高端白酒龙头",
                ),
                PeerSetMember(
                    assets["000568"].asset_id,
                    True,
                    "全国性浓香白酒",
                ),
                PeerSetMember(
                    assets["600809"].asset_id,
                    True,
                    "次高端白酒全国化",
                ),
                PeerSetMember(
                    assets["600600"].asset_id,
                    False,
                    "啤酒商业模式和利润结构不可比",
                ),
            ),
        )
        result = PeerMetricExecutionService(
            data_access=data_access,
            fundamental_service=FundamentalAnalysisService(
                master_data=master,
                repository=metric_repository,
            ),
            peer_service=peer_service,
        ).execute(
            peer_set=peer_set,
            industry=IndustryAssignment(
                asset_id=assets["600519"].asset_id,
                industry_code="baijiu",
                industry_name="白酒",
                classification_source="manual_review",
                classification_version="v1",
                effective_date="2026-01-01",
            ),
            as_of_date="2026-08-03",
            metric_names=("roe", "roic"),
            provider_timeout_seconds=120,
        )
        if any(
            item.status is not PeerComparisonStatus.AVAILABLE
            for item in result.comparison.comparisons
        ):
            raise AssertionError(
                f"Peer comparison unavailable: "
                f"{result.comparison.comparisons}"
            )
        print(
            json.dumps(
                {
                    "common_report_period": (
                        result.common_report_period
                    ),
                    "prepared_assets": len(
                        result.prepared_asset_ids
                    ),
                    "comparisons": {
                        item.metric_name: {
                            "target": str(item.target.value),
                            "peer_count": item.sample_count,
                            "median": str(item.median),
                            "target_percentile": str(
                                item.target_percentile
                            ),
                        }
                        for item in result.comparison.comparisons
                    },
                    "excluded_assets": (
                        result.comparison.comparisons[
                            0
                        ].excluded_assets
                    ),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


def _register(master, symbol, name):
    from src.master_data import ASSET_TYPE_STOCK, Asset

    return master.register_asset(
        Asset(
            asset_id=f"stock:{symbol}",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol=symbol,
            display_name=name,
            market="cn_a",
            exchange=(
                "sh" if symbol.startswith("6") else "sz"
            ),
        ),
        source="peer-execution:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
