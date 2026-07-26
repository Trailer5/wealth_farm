"""Validate live cross-source identity resolution with gentle requests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate master identity matching with live provider data."
    )
    parser.add_argument(
        "--stock-symbol",
        default="600519",
        help="One A-share symbol checked through BaoStock and CNINFO.",
    )
    parser.add_argument(
        "--include-fund",
        action="store_true",
        help="Also fetch the full public fund name table and resolve one fund.",
    )
    parser.add_argument(
        "--fund-symbol",
        default="000001",
        help="Fund symbol selected when --include-fund is enabled.",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.data_src.baostock import BaoStockDataSource
    from src.data_src.cninfo import CninfoDataSource
    from src.data_src.common import infer_a_share_exchange, normalize_a_share_code
    from src.master_data import MasterDataService, SQLiteMasterDataRepository

    service = MasterDataService(SQLiteMasterDataRepository(":memory:"))
    normalized_stock = normalize_a_share_code(args.stock_symbol)

    with BaoStockDataSource() as baostock:
        baostock_records = baostock.get_stock_basic_records(normalized_stock)
    if not baostock_records:
        raise RuntimeError(f"BaoStock returned no basic record for {normalized_stock}")
    baostock_resolution = service.ingest_security_info(baostock_records[0])

    cninfo_rows = CninfoDataSource().search_securities(normalized_stock)
    cninfo_row = next(
        (
            row
            for row in cninfo_rows
            if str(row.get("code", "")).strip() == normalized_stock
        ),
        None,
    )
    if cninfo_row is None:
        raise RuntimeError(f"CNINFO returned no exact mapping for {normalized_stock}")
    cninfo_resolution = service.resolve_identifier(
        source="cninfo:stock_list",
        raw_symbol=str(cninfo_row.get("code", "")),
        name=str(cninfo_row.get("zwjc") or "") or None,
        asset_type_hint="股票",
        exchange_hint=infer_a_share_exchange(normalized_stock),
        raw=cninfo_row,
    )
    if cninfo_resolution.asset is None or baostock_resolution.asset is None:
        raise RuntimeError("Stock identity resolution did not return an asset")
    if cninfo_resolution.asset.asset_id != baostock_resolution.asset.asset_id:
        raise RuntimeError("BaoStock and CNINFO resolved to different stock assets")

    result: dict[str, object] = {
        "stock": {
            "symbol": normalized_stock,
            "asset_id": baostock_resolution.asset.asset_id,
            "baostock_status": baostock_resolution.status,
            "cninfo_status": cninfo_resolution.status,
        },
        "fund": None,
        "unresolved_count": len(service.list_unresolved_observations()),
    }

    if args.include_fund:
        normalized_fund = normalize_a_share_code(args.fund_symbol)
        fund_record = next(
            (
                record
                for record in AkShareDataSource().get_fund_names()
                if record.symbol.strip() == normalized_fund
            ),
            None,
        )
        if fund_record is None:
            raise RuntimeError(f"AkShare returned no fund name for {normalized_fund}")
        fund_resolution = service.ingest_security_info(fund_record)
        if fund_resolution.asset is None:
            raise RuntimeError("Fund identity resolution did not return an asset")
        result["fund"] = {
            "symbol": normalized_fund,
            "asset_id": fund_resolution.asset.asset_id,
            "status": fund_resolution.status,
            "market": fund_resolution.asset.market,
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
