"""Run a real-data medium-long timing panel across three mainland indexes."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
START_DATE = "2005-01-01"
END_DATE = "2026-08-07"
INDEX_SPECS = (
    {
        "symbol": "000016",
        "name": "上证50",
        "asset_id": "index:cn_a:sh:000016",
        "history_start": "2005-01-01",
    },
    {
        "symbol": "000300",
        "name": "沪深300",
        "asset_id": "index:cn_a:sh:000300",
        "history_start": "2005-04-08",
    },
    {
        "symbol": "000905",
        "name": "中证500",
        "asset_id": "index:cn_a:sh:000905",
        "history_start": "2007-01-15",
    },
)
ZERO = Decimal("0")


class RemoteDataUnavailable(RuntimeError):
    """A required remote provider call failed."""


class BusinessDataUnavailable(RuntimeError):
    """A remote call succeeded but returned insufficient usable history."""


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        MarketTimingOOSEngine,
        MarketTimingValidationEngine,
    )
    from src.data_src.process_executor import run_provider_task
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.strategy import (
        SSEIndexBar,
        SSEIndexSeries,
        SSEMarketAuxPoint,
        SSEMarketAuxSeries,
        SSEMarketTimingParameters,
        SSEMarketTimingStrategy,
    )

    yield_records, excluded_future_yield = _fetch_shared_aux(
        run_provider_task,
        "China ten-year yield",
        "fetch_china_ten_year_yield_history",
        {"start_date": START_DATE},
        "ten_year_yield_percent",
    )
    qvix_records, excluded_future_qvix = _fetch_shared_aux(
        run_provider_task,
        "50ETF QVIX",
        "fetch_50etf_qvix_history",
        {},
        "qvix_close",
    )
    print(
        "SHARED_AUX_READY "
        f"yield={len(yield_records)} qvix={len(qvix_records)}",
        flush=True,
    )

    strategy = SSEMarketTimingStrategy()
    parameters = SSEMarketTimingParameters()
    panel_results: dict[str, dict[str, object]] = {}
    valuation_bindings: dict[str, str] = {}
    for spec in INDEX_SPECS:
        symbol = str(spec["symbol"])
        try:
            result = _run_index(
                spec=spec,
                provider=TencentExchangeFundDataSource(),
                run_provider_task=run_provider_task,
                strategy=strategy,
                parameters=parameters,
                yield_records=yield_records,
                qvix_records=qvix_records,
                SSEIndexBar=SSEIndexBar,
                SSEIndexSeries=SSEIndexSeries,
                SSEMarketAuxPoint=SSEMarketAuxPoint,
                SSEMarketAuxSeries=SSEMarketAuxSeries,
                MarketTimingValidationEngine=MarketTimingValidationEngine,
                MarketTimingOOSEngine=MarketTimingOOSEngine,
            )
            valuation_bindings[symbol] = str(spec["name"])
            panel_results[symbol] = result
        except BusinessDataUnavailable as exc:
            panel_results[symbol] = {
                "status": "business_no_data",
                "asset_id": spec["asset_id"],
                "index_name": spec["name"],
                "reason": str(exc),
            }

    assert len({spec["asset_id"] for spec in INDEX_SPECS}) == 3
    assert len({spec["symbol"] for spec in INDEX_SPECS}) == 3
    assert len(set(valuation_bindings.values())) == len(valuation_bindings)
    signs = {
        symbol: result["current"]["directional_score_sign"]
        for symbol, result in panel_results.items()
        if result.get("status") == "completed"
    }
    sign_counts = Counter(signs.values())
    consistency_count = max(sign_counts.values(), default=0)

    output = {
        "status": (
            "completed"
            if all(
                result.get("status") == "completed"
                for result in panel_results.values()
            )
            else "completed_with_business_no_data"
        ),
        "as_of_date": END_DATE,
        "panel": panel_results,
        "shared_market_proxies": {
            "ten_year_yield": {
                "role": "China sovereign-yield market proxy shared by indexes",
                "observation_count": len(yield_records),
                "future_observations_excluded": excluded_future_yield,
            },
            "qvix": {
                "role": "50ETF volatility market proxy shared by indexes",
                "observation_count": len(qvix_records),
                "future_observations_excluded": excluded_future_qvix,
            },
            "join_semantics": "same_trade_date_only_no_forward_fill",
        },
        "direction_sign_consistency": {
            "signs_by_symbol": signs,
            "eligible_index_count": len(signs),
            "largest_same_sign_count": consistency_count,
            "all_three_same_sign": len(signs) == 3 and consistency_count == 3,
        },
        "pooling": {
            "raw_observations_pooled": False,
            "method": "independent_per_index_validation_then_sign_summary",
        },
        "assertions": [
            "canonical index identities are distinct",
            "each index valuation query is bound to its own index name",
            "only each index own AkShare ttm_pe is mapped to market_pe",
            "all used source observations are at or before 2026-08-07",
            "OOS was completed or explicitly classified as business_no_data",
            "no strategy or validation parameters were adjusted",
            "no return pass threshold was applied",
        ],
    }
    print(json.dumps(output, ensure_ascii=True, indent=2, default=str))
    return 0


def _run_index(
    *,
    spec,
    provider,
    run_provider_task,
    strategy,
    parameters,
    yield_records,
    qvix_records,
    SSEIndexBar,
    SSEIndexSeries,
    SSEMarketAuxPoint,
    SSEMarketAuxSeries,
    MarketTimingValidationEngine,
    MarketTimingOOSEngine,
):
    symbol = str(spec["symbol"])
    index_name = str(spec["name"])
    asset_id = str(spec["asset_id"])
    raw_bars = _fetch_tencent_index_chunks(
        provider,
        symbol,
        str(spec["history_start"]),
        END_DATE,
    )
    bars, excluded_invalid_ohlc = _normalize_index_bars(
        raw_bars,
        SSEIndexBar,
    )
    if not bars:
        raise BusinessDataUnavailable(
            f"{index_name} Tencent history returned no usable OHLC"
        )
    if bars[-1].trade_date != END_DATE:
        raise BusinessDataUnavailable(
            f"{index_name} Tencent history ends at {bars[-1].trade_date}, "
            f"not {END_DATE}"
        )
    if len(bars) <= parameters.long_window + 252:
        raise BusinessDataUnavailable(
            f"{index_name} price history is too short for medium_long validation"
        )
    series = SSEIndexSeries(asset_id=asset_id, bars=bars)
    print(
        f"INDEX_READY symbol={symbol} rows={len(bars)} "
        f"excluded_invalid_ohlc={excluded_invalid_ohlc}",
        flush=True,
    )

    valuation_records, valuation_diagnostics = _fetch_index_valuation(
        run_provider_task,
        index_name,
    )
    if not valuation_records:
        raise BusinessDataUnavailable(
            f"{index_name} has no positive own-index ttm_pe through {END_DATE}"
        )
    aux_points = _merge_aux_points(
        valuation_records,
        yield_records,
        qvix_records,
        SSEMarketAuxPoint,
    )
    assert aux_points
    assert all(point.trade_date <= END_DATE for point in aux_points)
    aux_series = SSEMarketAuxSeries(aux_points)
    print(
        f"VALUATION_READY symbol={symbol} "
        f"ttm_pe={len(valuation_records)}",
        flush=True,
    )

    first_result_index = max(
        parameters.short_channel,
        parameters.medium_window,
        parameters.long_window + 1,
    ) - 1
    timing_results = tuple(
        strategy.evaluate(
            series,
            aux_series,
            series.bars[index].trade_date,
            series.bars[index + 1].trade_date,
            parameters,
        )
        for index in range(first_result_index, len(series.bars) - 1)
    )
    if not timing_results:
        raise BusinessDataUnavailable(
            f"{index_name} generated no point-in-time timing results"
        )
    current_snapshot = strategy.evaluate(
        series,
        aux_series,
        series.bars[-1].trade_date,
        _next_weekday(series.bars[-1].trade_date),
        parameters,
    )
    current_signal = _medium_long_signal(current_snapshot)
    current_score = _directional_score(current_signal)

    validation = MarketTimingValidationEngine.run(series, timing_results)
    medium_long = validation.medium_long
    if medium_long.count == 0:
        raise BusinessDataUnavailable(
            f"{index_name} has no complete medium_long continuous observations"
        )
    oos_summary = _run_oos(
        asset_id,
        medium_long,
        MarketTimingOOSEngine,
    )

    assert series.asset_id == asset_id
    assert validation.asset_id == asset_id
    assert all(result.asset_id == asset_id for result in timing_results)
    assert all(
        result.as_of_date <= END_DATE and result.effective_date <= END_DATE
        for result in timing_results
    )
    assert all(
        record["index_name"] == index_name
        and record["valuation_metric"] == "ttm_pe"
        and str(record["source"]).startswith("akshare:")
        and record["trade_date"] <= END_DATE
        for record in valuation_records
    )
    assert oos_summary["status"] in ("completed", "business_no_data")

    return {
        "status": "completed",
        "asset_id": asset_id,
        "index_name": index_name,
        "index_symbol": symbol,
        "period": [bars[0].trade_date, bars[-1].trade_date],
        "bars": len(bars),
        "excluded_invalid_ohlc": excluded_invalid_ohlc,
        "price_source": "tencent_segmented_two_calendar_years",
        "valuation": {
            "observations": len(valuation_records),
            "query_symbol": index_name,
            "metric": "ttm_pe",
            "mapped_strategy_field": "market_pe",
            "source": "akshare:stock_index_pe_lg",
            **valuation_diagnostics,
        },
        "current": {
            "as_of_date": current_snapshot.as_of_date,
            "effective_date_assumption": current_snapshot.effective_date,
            "direction": current_signal.direction,
            "stage": current_signal.stage,
            "directional_score": current_score,
            "directional_score_sign": _score_sign(current_score),
            "low_position_score": current_signal.metrics.get(
                "low_position_score"
            ),
            "high_position_score": current_signal.metrics.get(
                "high_position_score"
            ),
        },
        "continuous_medium_long": {
            "count": medium_long.count,
            "spearman_correlation": medium_long.spearman_correlation,
            "bootstrap_ci": medium_long.bootstrap_ci,
        },
        "oos_medium_long": oos_summary,
        "point_in_time_result_count": len(timing_results),
        "generated_horizons": ["short", "medium_short", "medium_long"],
        "evaluated_continuous_horizon": "medium_long",
        "aux_join": {
            "own_index_ttm_pe": True,
            "ten_year_yield_role": "shared_market_proxy",
            "qvix_role": "shared_50etf_market_proxy",
            "same_trade_date_only": True,
        },
    }


def _fetch_tencent_index_chunks(
    provider,
    symbol: str,
    start_date: str,
    end_date: str,
):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    by_date = {}
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, date(first_year, 1, 1))
        chunk_end = min(end, date(first_year + 1, 12, 31))
        try:
            chunk = provider.get_index_daily_bars(
                symbol,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                "sh",
            )
        except Exception as exc:
            raise RemoteDataUnavailable(
                f"Remote Tencent index history failed for {symbol} "
                f"{chunk_start.isoformat()}..{chunk_end.isoformat()}: "
                f"{_error_text(exc)}"
            ) from exc
        for bar in chunk:
            by_date[bar.trade_date] = bar
    return tuple(by_date[trade_date] for trade_date in sorted(by_date))


def _normalize_index_bars(raw_bars, bar_type):
    normalized = []
    excluded = 0
    by_date = {item.trade_date: item for item in raw_bars}
    for trade_date in sorted(by_date):
        item = by_date[trade_date]
        values = (item.open, item.high, item.low, item.close)
        if any(value is None for value in values):
            excluded += 1
            continue
        try:
            open_price, high, low, close = (
                Decimal(str(value)) for value in values
            )
        except InvalidOperation:
            excluded += 1
            continue
        if (
            any(not value.is_finite() for value in (open_price, high, low, close))
            or min(open_price, high, low, close) <= ZERO
            or not low <= open_price <= high
            or not low <= close <= high
        ):
            excluded += 1
            continue
        normalized.append(
            bar_type(
                trade_date=trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(normalized), excluded


def _fetch_index_valuation(run_provider_task, index_name: str):
    try:
        raw_records = run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_index_valuation_history",
            {"symbol": index_name},
            timeout_seconds=90,
        )
    except Exception as exc:
        raise RemoteDataUnavailable(
            f"Remote AkShare own-index valuation failed for {index_name}: "
            f"{_error_text(exc)}"
        ) from exc

    by_date = {}
    future_count = 0
    invalid_count = 0
    for record in raw_records:
        trade_date = str(record.get("trade_date") or "")
        if trade_date > END_DATE:
            future_count += 1
            continue
        if trade_date < START_DATE:
            continue
        if record.get("index_name") != index_name:
            raise AssertionError(
                f"valuation identity mismatch for {index_name}: "
                f"{record.get('index_name')}"
            )
        try:
            ttm_pe = Decimal(str(record.get("ttm_pe")))
        except (InvalidOperation, TypeError):
            invalid_count += 1
            continue
        if not ttm_pe.is_finite() or ttm_pe <= ZERO:
            invalid_count += 1
            continue
        by_date[trade_date] = {
            "trade_date": trade_date,
            "index_name": index_name,
            "ttm_pe": ttm_pe,
            "valuation_metric": "ttm_pe",
            "source": str(record.get("source") or ""),
        }
    return (
        tuple(by_date[trade_date] for trade_date in sorted(by_date)),
        {
            "future_observations_excluded": future_count,
            "invalid_ttm_pe_excluded": invalid_count,
        },
    )


def _fetch_shared_aux(
    run_provider_task,
    display_name: str,
    function_name: str,
    kwargs,
    value_field: str,
):
    try:
        raw_records = run_provider_task(
            "src.data_src.provider_tasks",
            function_name,
            kwargs,
            timeout_seconds=90,
        )
    except Exception as exc:
        raise RemoteDataUnavailable(
            f"Remote {display_name} history failed: {_error_text(exc)}"
        ) from exc
    filtered = []
    future_count = 0
    for record in raw_records:
        trade_date = str(record.get("trade_date") or "")
        if trade_date > END_DATE:
            future_count += 1
            continue
        if trade_date < START_DATE:
            continue
        try:
            value = Decimal(str(record.get(value_field)))
        except (InvalidOperation, TypeError):
            continue
        if not value.is_finite() or value < ZERO:
            continue
        if value_field == "qvix_close" and value == ZERO:
            continue
        filtered.append(
            {
                "trade_date": trade_date,
                value_field: value,
                "source": str(record.get("source") or ""),
            }
        )
    if not filtered:
        raise BusinessDataUnavailable(
            f"{display_name} returned no usable observations through {END_DATE}"
        )
    assert all(record["trade_date"] <= END_DATE for record in filtered)
    return tuple(filtered), future_count


def _merge_aux_points(
    valuation_records,
    yield_records,
    qvix_records,
    point_type,
):
    merged = {}
    for record in valuation_records:
        merged.setdefault(record["trade_date"], {})["market_pe"] = record[
            "ttm_pe"
        ]
    for record in yield_records:
        merged.setdefault(record["trade_date"], {})[
            "ten_year_yield_percent"
        ] = record["ten_year_yield_percent"]
    for record in qvix_records:
        merged.setdefault(record["trade_date"], {})["qvix_close"] = record[
            "qvix_close"
        ]
    return tuple(
        point_type(trade_date=trade_date, **values)
        for trade_date, values in sorted(merged.items())
        if trade_date <= END_DATE
    )


def _run_oos(asset_id, validation, engine):
    try:
        run = engine.run(asset_id, validation)
    except ValueError as exc:
        if "insufficient" not in str(exc).lower():
            raise
        return {
            "status": "business_no_data",
            "reason": str(exc),
            "fold_count": 0,
            "median_correlation": None,
            "positive_fold_ratio": None,
            "retrospective_holdout_correlation": None,
        }
    assert run.asset_id == asset_id
    assert run.folds
    assert run.holdout_scope == "retrospective"
    assert run.holdout.evaluated_once is True
    assert all(
        fold.train_end_date < fold.test_start_date
        for fold in (*run.folds, run.holdout)
    )
    return {
        "status": "completed",
        "fold_count": len(run.folds),
        "median_correlation": run.median_spearman,
        "positive_fold_ratio": run.positive_fold_ratio,
        "retrospective_holdout_correlation": (
            run.holdout.spearman_correlation
        ),
        "holdout_scope": run.holdout_scope,
        "holdout_evaluated_once": run.holdout.evaluated_once,
    }


def _medium_long_signal(result):
    return next(
        signal
        for signal in result.signals
        if signal.horizon == "medium_long"
    )


def _directional_score(signal):
    low_score = signal.metrics.get("low_position_score")
    high_score = signal.metrics.get("high_position_score")
    if low_score is None or high_score is None:
        return None
    return low_score - high_score


def _score_sign(value):
    if value is None:
        return None
    if value > ZERO:
        return 1
    if value < ZERO:
        return -1
    return 0


def _next_weekday(trade_date: str) -> str:
    candidate = date.fromisoformat(trade_date) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:240]}"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteDataUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "remote_unavailable",
                    "error": str(exc),
                },
                ensure_ascii=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
    except BusinessDataUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "business_no_data",
                    "error": str(exc),
                },
                ensure_ascii=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(3)
