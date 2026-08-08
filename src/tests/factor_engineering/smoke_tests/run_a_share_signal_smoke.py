"""Validate A-share margin, ETF-flow, and event factor source wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.factor_engineering import (
        MarginFactorCalculator,
        MarginFactorParameters,
        MarginTradingRecord,
        MarketActivityRecord,
        MarketEventFactorCalculator,
    )
    from src.fund_research import (
        ExchangeFundFlowService,
        ExchangeFundNavRecord,
        ExchangeFundShareRecord,
    )

    provider = AkShareDataSource()
    previous_date = "2026-08-06"
    trade_date = "2026-08-07"

    margin_records = []
    for value_date in (previous_date, trade_date):
        record = next(
            record
            for record in provider.get_stock_margin_details(
                exchange="sh",
                trade_date=value_date,
            )
            if record["symbol"] == "600519"
        )
        margin_records.append(MarginTradingRecord(**record))
    margin_values = MarginFactorCalculator().calculate(
        asset_id="asset:stock:600519",
        records=margin_records,
        daily_amount_by_date={},
        as_of_date=trade_date,
        parameters=MarginFactorParameters(window=1),
    )

    share_records = []
    for value_date in (previous_date, trade_date):
        record = next(
            record
            for record in provider.get_exchange_fund_share_records(
                exchange="sh",
                start_date=value_date,
                end_date=value_date,
            )
            if record["symbol"] == "510300"
        )
        share_records.append(
            ExchangeFundShareRecord(
                asset_id="asset:etf:510300",
                stat_date=record["stat_date"],
                shares=_decimal(record["shares"]),
                source=record["source"],
                split_adjusted=record["split_adjusted"],
            )
        )
    nav_records = [
        ExchangeFundNavRecord(
            asset_id="asset:etf:510300",
            nav_date=record["nav_date"],
            unit_nav=_decimal(record["unit_nav"]),
            source=record["source"],
            estimated=record["estimated"],
        )
        for record in provider.get_exchange_fund_nav_records()
        if record["symbol"] == "510300"
    ]
    etf_values = ExchangeFundFlowService().calculate(
        "asset:etf:510300",
        trade_date,
        share_records,
        nav_records,
    )

    activity_records = [
        *provider.get_lhb_activity_records(
            start_date=trade_date,
            end_date=trade_date,
        ),
        *provider.get_block_trade_activity_records(
            start_date=trade_date,
            end_date=trade_date,
        ),
    ]
    first_symbol = activity_records[0]["symbol"]
    event_values = MarketEventFactorCalculator().calculate(
        asset_id=f"asset:stock:{first_symbol}",
        records=[
            MarketActivityRecord(
                asset_id=f"asset:stock:{record['symbol']}",
                **{
                    key: value
                    for key, value in record.items()
                    if key != "symbol"
                },
            )
            for record in activity_records
            if record["symbol"] == first_symbol
        ],
        as_of_date=trade_date,
        window_days=1,
    )

    print(
        json.dumps(
            {
                "trade_date": trade_date,
                "margin": {
                    value.factor_name: value.value
                    for value in margin_values
                },
                "etf_flow": {
                    value.factor_name: value.value
                    for value in etf_values
                },
                "event_symbol": first_symbol,
                "event": {
                    value.factor_name: value.value
                    for value in event_values
                },
                "identity_inference": False,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _decimal(value):
    from decimal import Decimal

    return Decimal(str(value))


if __name__ == "__main__":
    raise SystemExit(main())
