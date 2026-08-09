"""Live actual-total-return comparison across ETFs and indices."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.data_src.eastmoney import EastmoneyExchangeFundDataSource
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.fund_research import (
        ETFComparisonSeries,
        ETFComparisonService,
        ETFMarketPricePoint,
        ETFTotalReturnCalculator,
        ReturnIndexPoint,
        official_etf_expense_schedule,
    )

    start_date = "2026-01-15"
    end_date = "2026-08-07"
    as_of_date = "2026-08-09"
    profiles = {
        "510210": "index:cn_a:sh:000001",
        "510760": "index:cn_a:sh:000001",
        "510300": "index:cn_a:sh:000300",
    }
    action_source = AkShareDataSource()
    distributions = action_source.get_etf_distribution_events_batch(
        symbols=tuple(profiles),
        start_date=start_date,
        end_date=end_date,
    )
    splits = action_source.get_etf_split_events_batch(
        symbols=tuple(profiles),
        start_date=start_date,
        end_date=end_date,
    )
    tencent = TencentExchangeFundDataSource()
    eastmoney = EastmoneyExchangeFundDataSource()
    csi_total_return_error = None
    try:
        csi_total_return = eastmoney.get_index_daily_bars(
            "H00300",
            start_date,
            end_date,
            "csi",
        )
    except Exception as exc:
        csi_total_return = []
        csi_total_return_error = type(exc).__name__
    benchmarks = {
        "index:cn_a:sh:000001": tencent.get_index_daily_bars(
            "000888",
            start_date,
            end_date,
            "sh",
        ),
        "index:cn_a:sh:000300": csi_total_return,
    }
    price_benchmarks = {
        "index:cn_a:sh:000001": tencent.get_index_daily_bars(
            "000001",
            start_date,
            end_date,
            "sh",
        ),
        "index:cn_a:sh:000300": tencent.get_index_daily_bars(
            "000300",
            start_date,
            end_date,
            "sh",
        ),
    }
    series = []
    for symbol, index_id in profiles.items():
        asset_id = f"asset:etf:{symbol}"
        navs = eastmoney.get_etf_nav_history_records(
            asset_id=asset_id,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        bars = tencent.get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
        if len(navs) < 100 or len(bars) < 100:
            raise RuntimeError(f"{symbol} live history is insufficient")
        prices = tuple(
            ETFMarketPricePoint(
                asset_id=asset_id,
                date=bar.trade_date,
                close=Decimal(str(bar.close)),
                available_at=bar.trade_date,
                source=bar.source,
            )
            for bar in bars
            if bar.close is not None and bar.close > 0
        )
        total_return = ETFTotalReturnCalculator().calculate(
            asset_id=asset_id,
            as_of_date=as_of_date,
            nav_points=tuple(navs),
            market_prices=prices,
            distributions=tuple(distributions[symbol]),
            splits=tuple(splits[symbol]),
            distribution_history_complete=True,
            split_history_complete=True,
        )
        benchmark_points = tuple(
            ReturnIndexPoint(
                observation_date=bar.trade_date,
                value=Decimal(str(bar.close)),
            )
            for bar in benchmarks[index_id]
            if bar.close is not None and bar.close > 0
        )
        price_benchmark_points = tuple(
            ReturnIndexPoint(
                observation_date=bar.trade_date,
                value=Decimal(str(bar.close)),
            )
            for bar in price_benchmarks[index_id]
            if bar.close is not None and bar.close > 0
        )
        expense = official_etf_expense_schedule(
            asset_id=asset_id,
            standard_symbol=symbol,
        )
        series.append(
            ETFComparisonSeries.from_total_return_result(
                result=total_return,
                standard_symbol=symbol,
                underlying_index_asset_id=index_id,
                available_at="2026-08-08",
                annual_expense_rate=(
                    expense.comprehensive_expense_ratio
                ),
                price_benchmark_return=price_benchmark_points,
                benchmark_total_return=benchmark_points,
                source_refs=(
                    "eastmoney:f10_lsjz",
                    "akshare:fund_fh_em",
                    "akshare:fund_cf_em",
                    "tencent:kline",
                    expense.source_url,
                    (
                        "tencent:000888"
                        if index_id.endswith("000001")
                        else (
                            "eastmoney:H00300"
                            if csi_total_return
                            else "H00300:remote_unavailable"
                        )
                    ),
                ),
            )
        )
    panel = ETFComparisonService().compare(
        as_of_date=as_of_date,
        series=tuple(series),
    )
    print(
        json.dumps(
            {
                "as_of_date": panel.as_of_date,
                "rows": [
                    {
                        "symbol": row.standard_symbol,
                        "index": row.underlying_index_asset_id,
                        "nav_total_return": float(row.nav_total_return),
                        "annual_expense_rate": float(
                            row.annual_expense_rate
                        ),
                        "max_drawdown": float(row.nav_max_drawdown),
                        "price_tracking_error": _optional_float(
                            row.price_tracking_error
                        ),
                        "nav_tracking_error": _optional_float(
                            row.nav_tracking_error
                        ),
                        "same_index_rank": row.same_index_rank,
                        "limitations": row.limitations,
                    }
                    for row in panel.rows
                ],
                "methodology": panel.methodology,
                "csi_total_return_error": csi_total_return_error,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _optional_float(value):
    return float(value) if value is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
