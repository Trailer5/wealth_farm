"""Validate real ETF research bundles for two representative ETFs."""

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
    from src.fund_research import (
        FundResearchService,
        SQLiteFundResearchRepository,
    )
    from src.master_data import (
        ASSET_TYPE_EXCHANGE_FUND,
        ASSET_TYPE_INDEX,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "etf-bundle.sqlite3"
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = _register(
            master,
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
            "sh",
        )
        funds = {
            "gold": _register(
                master,
                "fund:gold-etf",
                ASSET_TYPE_EXCHANGE_FUND,
                "518880",
                "黄金ETF",
                "sh",
            ),
            "csi300": _register(
                master,
                "fund:csi300-etf",
                ASSET_TYPE_EXCHANGE_FUND,
                "510300",
                "沪深300ETF",
                "sh",
            ),
        }
        store = SQLiteDataStore(db_path)
        service = FundResearchService(
            master_data=master,
            data_access=DataAccessService(
                master_data=master,
                repository=SQLiteDataAccessRepository(store),
                fetch_service=DataFetchService(
                    store=store,
                    master_data_service=master,
                ),
            ),
            repository=SQLiteFundResearchRepository(db_path),
        )
        output = {}
        for name, fund in funds.items():
            service.add_underlying_index_relation(
                fund_asset_id=fund.asset_id,
                index_asset_id=index.asset_id,
                source="smoke:reviewed_relation",
                effective_date="2026-01-01",
            )
            bundle = service.get_etf_bundle(
                asset_id=fund.asset_id,
                start_date="2026-07-01",
                end_date="2026-07-24",
                as_of_date="2026-08-03",
                provider_timeout_seconds=60,
            )
            if not bundle.complete or not bundle.daily_bar_records:
                raise AssertionError(
                    f"Incomplete ETF bundle: {fund.standard_symbol}"
                )
            output[name] = {
                "symbol": fund.standard_symbol,
                "records": len(bundle.daily_bar_records),
                "sources": list(bundle.sources),
                "relations": len(bundle.relations),
            }
        print(json.dumps(output, ensure_ascii=True, indent=2))
    return 0


def _register(master, asset_id, asset_type, symbol, name, exchange):
    from src.master_data import Asset

    return master.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=symbol,
            display_name=name,
            market="cn_a",
            exchange=exchange,
        ),
        source="smoke:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
