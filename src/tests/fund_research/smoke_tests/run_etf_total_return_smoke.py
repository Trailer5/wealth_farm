"""Live ETF NAV, distribution, split, and total-return validation."""

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
        ETFMarketPricePoint,
        ETFTotalReturnCalculator,
    )

    asset_id = "asset:etf:510300"
    symbol = "510300"
    start_date = "2026-01-15"
    end_date = "2026-08-07"
    as_of_date = "2026-08-09"
    navs = EastmoneyExchangeFundDataSource().get_etf_nav_history_records(
        asset_id=asset_id,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )
    bars = TencentExchangeFundDataSource().get_etf_daily_bars(
        symbol,
        start_date,
        end_date,
    )
    actions = AkShareDataSource()
    distributions = actions.get_etf_distribution_events(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )
    splits = actions.get_etf_split_events(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )
    if len(navs) < 100 or len(bars) < 100:
        raise RuntimeError("live ETF NAV or price history is insufficient")
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
    result = ETFTotalReturnCalculator().calculate(
        asset_id=asset_id,
        as_of_date=as_of_date,
        nav_points=tuple(navs),
        market_prices=prices,
        distributions=tuple(distributions),
        splits=tuple(splits),
        distribution_history_complete=True,
        split_history_complete=True,
    )
    if not result.coverage.complete:
        raise RuntimeError(
            f"incomplete total-return coverage: "
            f"{result.coverage.limitations}"
        )
    print(
        json.dumps(
            {
                "asset_id": asset_id,
                "start_date": result.points[0].date,
                "end_date": result.points[-1].date,
                "aligned_observations": (
                    result.coverage.aligned_observations
                ),
                "distribution_events": (
                    result.coverage.distribution_events
                ),
                "split_events": result.coverage.split_events,
                "market_price_return": float(
                    result.market_price_return[-1].value
                    / result.market_price_return[0].value
                    - Decimal("1")
                ),
                "nav_return": float(
                    result.nav_return[-1].value
                    / result.nav_return[0].value
                    - Decimal("1")
                ),
                "nav_total_return": float(
                    result.total_return[-1].value
                    / result.total_return[0].value
                    - Decimal("1")
                ),
                "latest_premium_discount": float(
                    result.points[-1].premium_discount
                ),
                "sources": sorted(
                    {
                        *(point.source for point in navs),
                        *(point.source for point in prices),
                        *(event.source for event in distributions),
                        *(event.source for event in splits),
                    }
                ),
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
