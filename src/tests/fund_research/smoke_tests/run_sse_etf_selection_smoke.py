"""Select a tradable SSE Composite ETF from live market histories."""

from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.benchmark_analysis import (
        BenchmarkKind,
        BenchmarkMetricCalculator,
    )
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.fund_research import (
        ETFInstrumentProfile,
        ETFInstrumentSelectionRequest,
        ETFInstrumentSelectionService,
        ETFSelectionObservation,
        official_etf_expense_schedule,
    )

    source = TencentExchangeFundDataSource()
    start_date = "2024-08-01"
    end_date = "2026-08-07"
    index_bars = source.get_index_daily_bars(
        "000001",
        start_date,
        end_date,
        "sh",
    )
    candidates = {
        "510210": {
            "name": "富国上证综指ETF",
            "manager": "富国基金",
            "inception": "2011-01-30",
            "aum": Decimal("18394000000"),
        },
        "510760": {
            "name": "国泰上证综合ETF",
            "manager": "国泰基金",
            "inception": "2020-08-21",
            "aum": Decimal("5002000000"),
        },
    }
    profiles = []
    observations = []
    for symbol, item in candidates.items():
        asset_id = f"asset:etf:{symbol}"
        bars = source.get_etf_daily_bars(
            symbol,
            start_date,
            end_date,
        )
        if len(bars) < 252:
            raise RuntimeError(
                f"{symbol} has insufficient live history"
            )
        amounts = [
            Decimal(
                str(
                    bar.amount
                    if bar.amount is not None
                    else bar.volume * bar.close
                )
            )
            for bar in bars[-60:]
            if (
                (bar.amount is not None and bar.amount > 0)
                or (
                    bar.amount is None
                    and bar.volume is not None
                    and bar.volume > 0
                    and bar.close is not None
                    and bar.close > 0
                )
            )
        ]
        if len(amounts) < 50:
            raise RuntimeError(
                f"{symbol} has insufficient amount coverage"
            )
        comparison = BenchmarkMetricCalculator().calculate(
            asset_id=asset_id,
            benchmark_asset_id="index:cn_a:sh:000001",
            benchmark_kind=BenchmarkKind.INDEX,
            asset_records=[
                {
                    "trade_date": bar.trade_date,
                    "close": bar.close,
                }
                for bar in bars
            ],
            benchmark_records=[
                {
                    "trade_date": bar.trade_date,
                    "close": bar.close,
                }
                for bar in index_bars
            ],
            asset_dataset="tencent:etf_daily",
            benchmark_dataset="tencent:index_daily",
        )
        tracking_error = next(
            Decimal(str(metric.value))
            for metric in comparison.metrics
            if metric.factor_name == "tracking_error"
        )
        expense = official_etf_expense_schedule(
            asset_id=asset_id,
            standard_symbol=symbol,
        )
        profiles.append(
            ETFInstrumentProfile(
                asset_id=asset_id,
                standard_symbol=symbol,
                legal_name=item["name"],
                exchange="sh",
                manager=item["manager"],
                underlying_index_asset_id=(
                    "index:cn_a:sh:000001"
                ),
                inception_date=item["inception"],
            )
        )
        observations.append(
            ETFSelectionObservation(
                asset_id=asset_id,
                as_of_date=end_date,
                available_at=end_date,
                history_days=len(bars),
                median_amount_60d=Decimal(
                    str(statistics.median(amounts))
                ),
                aum=item["aum"],
                annual_expense_rate=(
                    expense.comprehensive_expense_ratio
                ),
                price_tracking_error=tracking_error,
                data_coverage=(
                    Decimal(len(amounts)) / Decimal("60")
                ),
                source_refs=(
                    "tencent:etf_daily",
                    "estimated_amount:volume_times_close",
                    expense.source_url,
                    "sina:2026-08-07:same-index-etf-snapshot",
                ),
            )
        )

    service = ETFInstrumentSelectionService()
    decisions = {
        holding: service.select(
            ETFInstrumentSelectionRequest(
                benchmark_asset_id="index:cn_a:sh:000001",
                as_of_date=end_date,
                expected_holding_days=days,
                order_notional=Decimal("10000000"),
            ),
            tuple(profiles),
            tuple(observations),
        )
        for holding, days in (
            ("short_execution", 30),
            ("long_holding", 365),
        )
    }
    print(
        json.dumps(
            {
                name: {
                    "selected_symbol": decision.selected_symbol,
                    "selection_basis": list(
                        decision.selection_basis
                    ),
                }
                for name, decision in decisions.items()
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
