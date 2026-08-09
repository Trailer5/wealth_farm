"""Run a real-data monthly ETF dual-momentum research backtest."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
RISK_ETFS = {
    "510300": "asset:etf:510300",
    "510500": "asset:etf:510500",
    "512100": "asset:etf:512100",
    "518880": "asset:etf:518880",
}
DEFENSIVE_SYMBOL = "511010"
DEFENSIVE_ASSET_ID = "asset:etf:511010"


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        ETFRotationBacktestBar,
        ETFRotationBacktestEngine,
        ETFRotationBacktestSpec,
    )
    from src.data_src.sina import SinaExchangeFundDataSource
    from src.strategy import (
        ETFDualMomentumTrendStrategy,
        ETFPriceSeries,
        ETFRotationParameters,
    )

    provider = SinaExchangeFundDataSource()
    start_date = "2023-01-01"
    end_date = "2026-08-07"
    symbol_to_asset = {
        **RISK_ETFS,
        DEFENSIVE_SYMBOL: DEFENSIVE_ASSET_ID,
    }
    bars_by_asset = {
        asset_id: sorted(
            provider.get_etf_daily_bars(
                symbol,
                start_date,
                end_date,
            ),
            key=lambda item: item.trade_date,
        )
        for symbol, asset_id in symbol_to_asset.items()
    }
    if any(len(values) < 253 for values in bars_by_asset.values()):
        raise RuntimeError("ETF history is insufficient for long momentum")

    series = tuple(
        ETFPriceSeries(
            asset_id=asset_id,
            dates=tuple(item.trade_date for item in values),
            closes=tuple(
                Decimal(str(item.close)) for item in values
            ),
        )
        for asset_id, values in bars_by_asset.items()
    )
    common_dates = sorted(
        set.intersection(
            *(
                {item.trade_date for item in values}
                for values in bars_by_asset.values()
            )
        )
    )
    month_ends = _month_end_dates(common_dates)
    next_date = {
        current: following
        for current, following in zip(
            common_dates,
            common_dates[1:],
        )
    }
    parameters = ETFRotationParameters(
        top_n=2,
        defensive_asset_id=DEFENSIVE_ASSET_ID,
    )
    evaluated_signals = [
        ETFDualMomentumTrendStrategy.evaluate(
            series,
            signal_date,
            next_date[signal_date],
            parameters,
        )
        for signal_date in month_ends
        if signal_date in next_date
    ]
    risk_asset_ids = set(RISK_ETFS.values())
    signals = [
        signal
        for signal in evaluated_signals
        if all(
            metric.return_long is not None
            for metric in signal.metrics
            if metric.asset_id in risk_asset_ids
        )
    ]
    if not signals:
        raise RuntimeError("No ETF rotation signals were generated")
    backtest_start_date = signals[0].effective_date

    backtest_bars = [
        ETFRotationBacktestBar(
            asset_id=asset_id,
            trade_date=item.trade_date,
            open=Decimal(str(item.open)),
            close=Decimal(str(item.close)),
        )
        for asset_id, values in bars_by_asset.items()
        for item in values
    ]
    benchmark_asset_id = RISK_ETFS["510300"]
    benchmark_bars = [
        item
        for item in backtest_bars
        if item.asset_id == benchmark_asset_id
    ]
    run = ETFRotationBacktestEngine().run(
        ETFRotationBacktestSpec(
            run_id="etf-dual-momentum:real-smoke",
            start_date=backtest_start_date,
            end_date=common_dates[-1],
            initial_cash=Decimal("1000000"),
            fee_rate=Decimal("0.0002"),
            slippage_rate=Decimal("0.0001"),
            benchmark_asset_id=benchmark_asset_id,
        ),
        backtest_bars,
        signals,
        benchmark_bars,
    )
    status_counts = {
        status: sum(
            signal.status == status for signal in signals
        )
        for status in ("risk_on", "defensive", "cash")
    }
    print(
        json.dumps(
            {
                "period": [backtest_start_date, common_dates[-1]],
                "asset_count": len(series),
                "signal_count": len(signals),
                "status_counts": status_counts,
                "metrics": {
                    key: (
                        str(value)
                        if isinstance(value, Decimal)
                        else value
                    )
                    for key, value in run.metrics.items()
                },
                "skipped_order_count": len(run.skipped_orders),
                "maturity": "research_rule",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _month_end_dates(trade_dates):
    result = {}
    for trade_date in trade_dates:
        result[trade_date[:7]] = trade_date
    return [result[key] for key in sorted(result)]


if __name__ == "__main__":
    raise SystemExit(main())
