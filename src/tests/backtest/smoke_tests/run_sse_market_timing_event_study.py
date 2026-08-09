"""Run a real-data event study for the SSE multi-horizon timing rule."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SSE_COMPOSITE_ASSET_ID = "index:cn_a:sh:000001"


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        MarketTimingEventStudyEngine,
        MarketTimingEventStudyParameters,
        MarketTimingOOSEngine,
        MarketTimingValidationEngine,
    )
    from src.data_src.eastmoney import EastmoneyExchangeFundDataSource
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

    start_date = "2005-01-01"
    end_date = "2026-08-07"
    source_errors: dict[str, str] = {}
    try:
        raw_bars = _fetch_tencent_index_chunks(
            TencentExchangeFundDataSource(),
            start_date,
            end_date,
        )
        index_source = "tencent"
    except Exception as exc:
        source_errors["tencent_index"] = (
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        try:
            raw_bars = EastmoneyExchangeFundDataSource().get_index_daily_bars(
                "000001",
                start_date,
                end_date,
                "sh",
            )
            index_source = "eastmoney"
        except Exception as direct_exc:
            source_errors["eastmoney_index"] = (
                f"{type(direct_exc).__name__}: {str(direct_exc)[:160]}"
            )
            try:
                raw_bars = run_provider_task(
                    "src.data_src.provider_tasks",
                    "fetch_index_daily_bars",
                    {
                        "source": "akshare",
                        "symbol": "000001",
                        "start_date": start_date,
                        "end_date": end_date,
                        "exchange": "sh",
                    },
                    timeout_seconds=30,
                )
                index_source = "akshare"
            except Exception as fallback_exc:
                raise RuntimeError(
                    "Remote SSE index history is unavailable from "
                    "Tencent, Eastmoney direct, and AkShare"
                ) from fallback_exc
    if len(raw_bars) < 1260:
        raise RuntimeError(
            "SSE index history is too short for a four-year study"
        )
    print(
        f"INDEX_READY source={index_source} rows={len(raw_bars)}",
        flush=True,
    )

    normalized_bars, excluded_invalid_ohlc = _normalize_index_bars(
        raw_bars,
        SSEIndexBar,
    )
    series = SSEIndexSeries(
        asset_id=SSE_COMPOSITE_ASSET_ID,
        bars=normalized_bars,
    )
    try:
        pe_records = run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_shanghai_market_pe_history",
            {},
            timeout_seconds=45,
        )
    except Exception as exc:
        raise RuntimeError(
            "Remote Shanghai market PE history is unavailable"
        ) from exc
    if len(pe_records) < 48:
        raise RuntimeError(
            "Shanghai market PE history is too short for four-year signals"
        )
    print(f"PE_READY rows={len(pe_records)}", flush=True)
    yield_records = _optional_fetch(
        source_errors,
        "ten_year_yield",
        lambda: run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_china_ten_year_yield_history",
            {"start_date": start_date},
            timeout_seconds=45,
        ),
    )
    qvix_records = _optional_fetch(
        source_errors,
        "qvix",
        lambda: run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_50etf_qvix_history",
            {},
            timeout_seconds=45,
        ),
    )
    print(
        "AUX_READY "
        f"yield={len(yield_records)} qvix={len(qvix_records)}",
        flush=True,
    )
    aux_points = _merge_aux_points(
        pe_records,
        yield_records,
        qvix_records,
        SSEMarketAuxPoint,
    )
    aux_series = SSEMarketAuxSeries(aux_points)

    timing_parameters = SSEMarketTimingParameters()
    strategy = SSEMarketTimingStrategy()
    results = []
    first_index = max(
        timing_parameters.short_channel,
        timing_parameters.rsi_window + 1,
        timing_parameters.short_reversal_window + 1,
    ) - 1
    for index in range(first_index, len(series.bars) - 1):
        results.append(
            strategy.evaluate(
                series,
                aux_series,
                series.bars[index].trade_date,
                series.bars[index + 1].trade_date,
                timing_parameters,
            )
        )
    print(f"SIGNALS_READY rows={len(results)}", flush=True)

    study = MarketTimingEventStudyEngine.run(
        series,
        results,
        MarketTimingEventStudyParameters(),
    )
    continuous_validation = MarketTimingValidationEngine.run(
        series,
        results,
    )
    oos_validation = MarketTimingOOSEngine.run(
        series.asset_id,
        continuous_validation.medium_long,
    )
    signal_counts = Counter(
        f"{signal.horizon}:{signal.direction}:{signal.stage}"
        for result in results
        for signal in result.signals
    )
    latest = results[-1]
    next_session_assumption = _next_weekday(
        series.bars[-1].trade_date
    )
    current_snapshot = strategy.evaluate(
        series,
        aux_series,
        series.bars[-1].trade_date,
        next_session_assumption,
        timing_parameters,
    )
    print(
        json.dumps(
            {
                "asset_id": SSE_COMPOSITE_ASSET_ID,
                "period": [
                    series.bars[0].trade_date,
                    series.bars[-1].trade_date,
                ],
                "bar_count": len(series.bars),
                "excluded_invalid_ohlc": excluded_invalid_ohlc,
                "index_source": index_source,
                "pe_observation_count": len(pe_records),
                "yield_observation_count": len(yield_records),
                "qvix_observation_count": len(qvix_records),
                "source_errors": source_errors,
                "signal_counts": dict(sorted(signal_counts.items())),
                "event_study_metrics": _json_values(study.metrics),
                "continuous_validation": {
                    horizon: _validation_summary(validation)
                    for horizon, validation
                    in continuous_validation.horizons.items()
                },
                "medium_long_oos": _oos_summary(oos_validation),
                "latest": {
                    "as_of_date": latest.as_of_date,
                    "effective_date": latest.effective_date,
                    "conflicts": list(latest.conflicts),
                    "signals": [
                        {
                            "horizon": signal.horizon,
                            "direction": signal.direction,
                            "stage": signal.stage,
                            "confidence": str(signal.confidence),
                            "evidence": list(signal.evidence),
                            "missing_inputs": list(
                                signal.missing_inputs
                            ),
                            "metrics": _json_values(signal.metrics),
                        }
                        for signal in latest.signals
                    ],
                },
                "current_snapshot": {
                    "as_of_date": current_snapshot.as_of_date,
                    "effective_date_assumption": (
                        current_snapshot.effective_date
                    ),
                    "signals": [
                        {
                            "horizon": signal.horizon,
                            "direction": signal.direction,
                            "stage": signal.stage,
                            "confidence": str(signal.confidence),
                            "evidence": list(signal.evidence),
                            "missing_inputs": list(
                                signal.missing_inputs
                            ),
                            "metrics": _json_values(signal.metrics),
                        }
                        for signal in current_snapshot.signals
                    ],
                },
                "maturity": "research_rule",
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _optional_fetch(source_errors, name, fetch):
    try:
        return fetch()
    except Exception as exc:
        source_errors[name] = (
            f"{type(exc).__name__}: {str(exc)[:160]}"
        )
        return []


def _next_weekday(trade_date):
    candidate = date.fromisoformat(trade_date) + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _normalize_index_bars(raw_bars, bar_type):
    normalized = []
    excluded = 0
    for item in sorted(raw_bars, key=lambda value: value.trade_date):
        if any(
            value is None
            for value in (item.open, item.high, item.low, item.close)
        ):
            excluded += 1
            continue
        open_price = Decimal(str(item.open))
        high = Decimal(str(item.high))
        low = Decimal(str(item.low))
        close = Decimal(str(item.close))
        if not low <= open_price <= high or not low <= close <= high:
            excluded += 1
            continue
        normalized.append(
            bar_type(
                trade_date=item.trade_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(normalized), excluded


def _fetch_tencent_index_chunks(provider, start_date, end_date):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    by_date = {}
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, date(first_year, 1, 1))
        chunk_end = min(end, date(first_year + 1, 12, 31))
        for bar in provider.get_index_daily_bars(
            "000001",
            chunk_start.isoformat(),
            chunk_end.isoformat(),
            "sh",
        ):
            by_date[bar.trade_date] = bar
    return [by_date[trade_date] for trade_date in sorted(by_date)]


def _merge_aux_points(
    pe_records,
    yield_records,
    qvix_records,
    point_type,
):
    merged = {}
    for record in pe_records:
        merged.setdefault(record["trade_date"], {})["market_pe"] = Decimal(
            str(record["market_pe"])
        )
    for record in yield_records:
        merged.setdefault(
            record["trade_date"],
            {},
        )["ten_year_yield_percent"] = Decimal(
            str(record["ten_year_yield_percent"])
        )
    for record in qvix_records:
        merged.setdefault(record["trade_date"], {})["qvix_close"] = Decimal(
            str(record["qvix_close"])
        )
    return tuple(
        point_type(trade_date=trade_date, **merged[trade_date])
        for trade_date in sorted(merged)
    )


def _json_values(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            key: _json_values(item)
            for key, item in value.items()
        }
    return value


def _validation_summary(validation):
    return {
        "count": validation.count,
        "spearman_correlation": (
            str(validation.spearman_correlation)
            if validation.spearman_correlation is not None
            else None
        ),
        "bootstrap_ci": (
            [str(value) for value in validation.bootstrap_ci]
            if validation.bootstrap_ci is not None
            else None
        ),
        "quantiles": [
            {
                "quantile": item.quantile,
                "count": item.count,
                "score_min": (
                    str(item.score_min)
                    if item.score_min is not None
                    else None
                ),
                "score_max": (
                    str(item.score_max)
                    if item.score_max is not None
                    else None
                ),
                "mean_forward_return": (
                    str(item.mean_forward_return)
                    if item.mean_forward_return is not None
                    else None
                ),
                "hit_rate": (
                    str(item.hit_rate)
                    if item.hit_rate is not None
                    else None
                ),
            }
            for item in validation.quantiles
        ],
        "walk_forward": [
            {
                "fold": item.fold,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "count": item.count,
                "spearman_correlation": (
                    str(item.spearman_correlation)
                    if item.spearman_correlation is not None
                    else None
                ),
            }
            for item in validation.walk_forward_folds
        ],
    }


def _oos_summary(run):
    return {
        "holdout_scope": run.holdout_scope,
        "fold_count": len(run.folds),
        "median_spearman": (
            str(run.median_spearman)
            if run.median_spearman is not None
            else None
        ),
        "positive_fold_ratio": (
            str(run.positive_fold_ratio)
            if run.positive_fold_ratio is not None
            else None
        ),
        "median_q5_minus_q1": (
            str(run.median_spread)
            if run.median_spread is not None
            else None
        ),
        "folds": [
            {
                "fold": item.fold,
                "train_count": item.train_count,
                "test_start_date": item.test_start_date,
                "test_end_date": item.test_end_date,
                "test_count": item.test_count,
                "purged_count": item.purged_count,
                "spearman_correlation": (
                    str(item.spearman_correlation)
                    if item.spearman_correlation is not None
                    else None
                ),
                "q5_minus_q1": (
                    str(item.q5_minus_q1)
                    if item.q5_minus_q1 is not None
                    else None
                ),
            }
            for item in run.folds
        ],
        "holdout": {
            "test_start_date": run.holdout.test_start_date,
            "test_end_date": run.holdout.test_end_date,
            "test_count": run.holdout.test_count,
            "spearman_correlation": (
                str(run.holdout.spearman_correlation)
                if run.holdout.spearman_correlation is not None
                else None
            ),
            "q5_minus_q1": (
                str(run.holdout.q5_minus_q1)
                if run.holdout.q5_minus_q1 is not None
                else None
            ),
            "evaluated_once": run.holdout.evaluated_once,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
