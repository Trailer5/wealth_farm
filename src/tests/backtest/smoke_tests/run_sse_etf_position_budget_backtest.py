"""Run real-ETF SSE position-budget backtests with transaction costs."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
START_DATE = "2005-01-01"
END_DATE = "2026-08-07"
SSE_ASSET_ID = "index:cn_a:sh:000001"
ETF_UNIVERSE = {
    "510210": {
        "asset_id": "asset:etf:510210",
        "role": "long_history_execution_anchor",
    },
    "510760": {
        "asset_id": "asset:etf:510760",
        "role": "post_2020_low_fee_candidate",
    },
}
ZERO = Decimal("0")
ONE = Decimal("1")


class RemoteDataUnavailable(RuntimeError):
    """All configured remote providers failed for a required dataset."""


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        BacktestBar,
        BacktestSample,
        BacktestSpec,
        FractionalExposureBacktestEngine,
        FractionalExposureSignal,
    )
    from src.data_src.eastmoney import EastmoneyExchangeFundDataSource
    from src.data_src.process_executor import run_provider_task
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.portfolio_risk import (
        InvestmentMandate,
        PositionBudgetCalculator,
        PositionBudgetInput,
    )
    from src.strategy import (
        SSEIndexBar,
        SSEIndexSeries,
        SSEMarketAuxPoint,
        SSEMarketAuxSeries,
        SSEMarketTimingParameters,
        SSEMarketTimingStrategy,
    )

    source_errors: dict[str, str] = {}
    raw_index_bars, index_source = _fetch_index_history(
        TencentExchangeFundDataSource,
        EastmoneyExchangeFundDataSource,
        run_provider_task,
        source_errors,
    )
    index_bars, excluded_index_bars = _normalize_index_bars(
        raw_index_bars,
        SSEIndexBar,
    )
    if (
        len(index_bars) < 1260
        or index_bars[0].trade_date[:4] != "2005"
        or index_bars[-1].trade_date != END_DATE
    ):
        raise RuntimeError(
            "SSE index data is incomplete for 2005 through 2026-08-07"
        )
    index_series = SSEIndexSeries(
        asset_id=SSE_ASSET_ID,
        bars=index_bars,
    )
    print(
        f"INDEX_READY source={index_source} rows={len(index_bars)}",
        flush=True,
    )

    pe_records = _required_aux_fetch(
        "Shanghai market PE",
        lambda: run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_shanghai_market_pe_history",
            {},
            timeout_seconds=60,
        ),
    )
    yield_records = _required_aux_fetch(
        "China ten-year yield",
        lambda: run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_china_ten_year_yield_history",
            {"start_date": START_DATE},
            timeout_seconds=60,
        ),
    )
    qvix_records = _required_aux_fetch(
        "50ETF QVIX",
        lambda: run_provider_task(
            "src.data_src.provider_tasks",
            "fetch_50etf_qvix_history",
            {},
            timeout_seconds=60,
        ),
    )
    if len(pe_records) < 48:
        raise RuntimeError("Shanghai market PE history is insufficient")
    aux_points = _merge_aux_points(
        pe_records,
        yield_records,
        qvix_records,
        SSEMarketAuxPoint,
    )
    aux_series = SSEMarketAuxSeries(aux_points)
    print(
        "AUX_READY "
        f"pe={len(pe_records)} yield={len(yield_records)} "
        f"qvix={len(qvix_records)}",
        flush=True,
    )

    timing_parameters = SSEMarketTimingParameters()
    timing_strategy = SSEMarketTimingStrategy()
    daily_scores = _daily_medium_long_scores(
        index_series,
        aux_series,
        timing_strategy,
        timing_parameters,
    )
    if not daily_scores:
        raise RuntimeError(
            "No usable daily medium_long directional scores were generated"
        )
    print(f"TIMING_READY usable_scores={len(daily_scores)}", flush=True)

    calculator = PositionBudgetCalculator()
    engine = FractionalExposureBacktestEngine()
    results = {}
    for symbol, identity in ETF_UNIVERSE.items():
        raw_etf_bars, etf_source = _fetch_etf_history(
            symbol,
            TencentExchangeFundDataSource,
            run_provider_task,
            source_errors,
        )
        normalized_etf_bars, excluded_etf_bars = _normalize_etf_bars(
            raw_etf_bars,
            BacktestBar,
        )
        etf_bars, adjustment_events = _forward_adjust_splits(
            normalized_etf_bars,
            BacktestBar,
        )
        if len(etf_bars) < 253:
            raise RuntimeError(
                f"ETF {symbol} has insufficient real price history "
                "for 60-session volatility and 252-session drawdown"
            )
        print(
            f"ETF_READY symbol={symbol} source={etf_source} "
            f"rows={len(etf_bars)} "
            f"split_adjustments={len(adjustment_events)}",
            flush=True,
        )

        common_bars = _common_etf_bars(etf_bars, index_series)
        month_ends = _completed_month_ends(
            [bar.trade_date for bar in common_bars]
        )
        schemes = _build_scheme_signals(
            symbol=symbol,
            asset_id=identity["asset_id"],
            common_bars=common_bars,
            month_ends=month_ends,
            daily_scores=daily_scores,
            calculator=calculator,
            InvestmentMandate=InvestmentMandate,
            PositionBudgetInput=PositionBudgetInput,
            FractionalExposureSignal=FractionalExposureSignal,
        )
        if (
            not schemes["timing_overlay"]
            or not schemes["drawdown_baseline"]
            or not schemes["volatility_baseline"]
        ):
            raise RuntimeError(f"ETF {symbol} generated no budget signals")

        scheme_results = {}
        for scheme_name, signals in schemes.items():
            spec = BacktestSpec(
                run_id=f"sse-position-budget:{symbol}:{scheme_name}",
                strategy_id="sse_market_turning_points_position_budget",
                strategy_version="v1",
                asset_id=identity["asset_id"],
                start_date=signals[0].as_of_date,
                end_date=common_bars[-1].trade_date,
                initial_cash=Decimal("1000000"),
                fee_rate=Decimal("0.0002"),
                slippage_rate=Decimal("0.0001"),
                sample=BacktestSample.FULL,
                signal_delay_sessions=1,
                benchmark_asset_id=identity["asset_id"],
                risk_free_rate=0.0,
                price_adjustment=(
                    "forward_split_adjusted"
                    if adjustment_events
                    else "none"
                ),
            )
            run = engine.run(
                spec,
                common_bars,
                signals,
                benchmark_bars=common_bars,
            )
            _assert_smoke_contract(run, signals, common_bars)
            scheme_results[scheme_name] = _run_summary(
                run,
                signals,
            )

        latest_overlay = schemes["timing_overlay"][-1]
        latest_drawdown = schemes["drawdown_baseline"][-1]
        latest_baseline = schemes["volatility_baseline"][-1]
        results[symbol] = {
            "asset_id": identity["asset_id"],
            "role": identity["role"],
            "price_source": etf_source,
            "price_is_real_etf": True,
            "price_adjustment": (
                "forward_split_adjusted"
                if adjustment_events
                else "none"
            ),
            "split_adjustment_events": adjustment_events,
            "fund_internal_fee_deduction": (
                "already_reflected_in_etf_price_no_extra_annual_fee"
            ),
            "source_period": [
                common_bars[0].trade_date,
                common_bars[-1].trade_date,
            ],
            "bar_count": len(common_bars),
            "excluded_invalid_bars": excluded_etf_bars,
            "schemes": scheme_results,
            "latest_formed_targets": {
                "timing_overlay": _signal_target(latest_overlay),
                "drawdown_baseline": _signal_target(
                    latest_drawdown
                ),
                "volatility_baseline": _signal_target(latest_baseline),
            },
        }

    print(
        json.dumps(
            {
                "status": "completed",
                "index": {
                    "asset_id": SSE_ASSET_ID,
                    "source": index_source,
                    "period": [
                        index_series.bars[0].trade_date,
                        index_series.bars[-1].trade_date,
                    ],
                    "bar_count": len(index_series.bars),
                    "excluded_invalid_bars": excluded_index_bars,
                },
                "aux": {
                    "pe_count": len(pe_records),
                    "ten_year_yield_count": len(yield_records),
                    "qvix_count": len(qvix_records),
                },
                "timing": {
                    "daily_usable_score_count": len(daily_scores),
                    "first_usable_score_date": daily_scores[0][0],
                    "last_usable_score_date": daily_scores[-1][0],
                    "directional_score": (
                        "medium_long low_position_score minus "
                        "high_position_score"
                    ),
                    "percentile_history": (
                        "expanding scores through each signal close; "
                        "no future scores"
                    ),
                    "neutral_rule": (
                        "use neutral multiplier when fewer than 252 "
                        "usable scores exist"
                    ),
                },
                "budget": {
                    "target_volatility": "0.10",
                    "max_drawdown": "0.15",
                    "max_risk_weight": "0.80",
                    "min_cash_weight": "0.05",
                    "defensive_asset_id": None,
                    "timing_multiplier_range": ["0.20", "1.00"],
                    "default_drawdown_governor_enabled": False,
                    "default_timing_overlay_enabled": False,
                    "volatility_window_returns": 60,
                    "drawdown_window_sessions": 252,
                    "baseline": (
                        "same volatility budget with current_drawdown=0 "
                        "and timing_score_percentile=1"
                    ),
                    "drawdown_baseline": (
                        "same volatility and current drawdown budget "
                        "with timing_score_percentile=1"
                    ),
                },
                "execution": {
                    "signal_frequency": "last common session of each month",
                    "execution": "next common session open",
                    "fee_rate": "0.0002",
                    "slippage_rate": "0.0001",
                },
                "source_errors": source_errors,
                "etfs": results,
                "assertions": [
                    "end-to-end run completed",
                    "cash remained non-negative",
                    "signals executed with one-common-session delay",
                    "required metrics exist",
                ],
                "maturity": "research_rule",
            },
            ensure_ascii=True,
            indent=2,
            default=str,
        )
    )
    return 0


def _fetch_index_history(
    tencent_type,
    eastmoney_type,
    run_provider_task,
    source_errors,
):
    attempts = 0
    try:
        attempts += 1
        bars = _fetch_tencent_chunks(
            tencent_type(),
            "index",
            "000001",
            START_DATE,
            END_DATE,
        )
        if bars:
            return bars, "tencent_segmented"
    except Exception as exc:
        source_errors["tencent_index"] = _error_text(exc)

    try:
        attempts += 1
        bars = eastmoney_type().get_index_daily_bars(
            "000001",
            START_DATE,
            END_DATE,
            "sh",
        )
        if bars:
            return bars, "eastmoney"
    except Exception as exc:
        source_errors["eastmoney_index"] = _error_text(exc)

    for source in ("akshare", "baostock", "sina"):
        try:
            attempts += 1
            bars = run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_index_daily_bars",
                {
                    "source": source,
                    "symbol": "000001",
                    "start_date": START_DATE,
                    "end_date": END_DATE,
                    "exchange": "sh",
                },
                timeout_seconds=60,
            )
            if bars:
                return bars, source
        except Exception as exc:
            source_errors[f"{source}_index"] = _error_text(exc)

    if len(source_errors) >= attempts:
        raise RemoteDataUnavailable(
            "Remote SSE index history is unavailable from all providers: "
            + json.dumps(source_errors, ensure_ascii=False)
        )
    raise RuntimeError("Remote SSE index providers returned no history")


def _fetch_etf_history(
    symbol,
    tencent_type,
    run_provider_task,
    source_errors,
):
    attempt_keys = []
    key = f"tencent_etf_{symbol}"
    attempt_keys.append(key)
    try:
        bars = _fetch_tencent_chunks(
            tencent_type(),
            "etf",
            symbol,
            START_DATE,
            END_DATE,
        )
        if bars:
            return bars, "tencent_segmented"
    except Exception as exc:
        source_errors[key] = _error_text(exc)

    for source in ("akshare", "baostock", "mootdx", "sina"):
        key = f"{source}_etf_{symbol}"
        attempt_keys.append(key)
        try:
            bars = run_provider_task(
                "src.data_src.provider_tasks",
                "fetch_etf_daily_bars",
                {
                    "source": source,
                    "symbol": symbol,
                    "start_date": START_DATE,
                    "end_date": END_DATE,
                },
                timeout_seconds=60,
            )
            if bars:
                return bars, source
        except Exception as exc:
            source_errors[key] = _error_text(exc)

    if all(key in source_errors for key in attempt_keys):
        details = {
            key: source_errors[key]
            for key in attempt_keys
            if key in source_errors
        }
        raise RemoteDataUnavailable(
            f"Remote ETF {symbol} history is unavailable from all "
            f"providers: {json.dumps(details, ensure_ascii=False)}"
        )
    raise RuntimeError(
        f"Remote ETF {symbol} providers returned no price history"
    )


def _fetch_tencent_chunks(
    provider,
    dataset,
    symbol,
    start_date,
    end_date,
):
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    by_date = {}
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, date(first_year, 1, 1))
        chunk_end = min(end, date(first_year + 1, 12, 31))
        if dataset == "index":
            bars = provider.get_index_daily_bars(
                symbol,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
                "sh",
            )
        else:
            bars = provider.get_etf_daily_bars(
                symbol,
                chunk_start.isoformat(),
                chunk_end.isoformat(),
            )
        for bar in bars:
            by_date[bar.trade_date] = bar
    return [by_date[trade_date] for trade_date in sorted(by_date)]


def _required_aux_fetch(name, fetch):
    try:
        records = fetch()
    except Exception as exc:
        raise RemoteDataUnavailable(
            f"Remote {name} history is unavailable: {_error_text(exc)}"
        ) from exc
    if not records:
        raise RuntimeError(f"Remote {name} provider returned no data")
    return records


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
        open_price, high, low, close = (
            Decimal(str(value)) for value in values
        )
        if (
            min(open_price, high, low, close) <= ZERO
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


def _normalize_etf_bars(raw_bars, bar_type):
    normalized = []
    excluded = 0
    by_date = {item.trade_date: item for item in raw_bars}
    for trade_date in sorted(by_date):
        item = by_date[trade_date]
        if item.open is None or item.close is None:
            excluded += 1
            continue
        open_price = Decimal(str(item.open))
        close = Decimal(str(item.close))
        if open_price <= ZERO or close <= ZERO:
            excluded += 1
            continue
        normalized.append(
            bar_type(
                trade_date=trade_date,
                open=open_price,
                close=close,
            )
        )
    return tuple(normalized), excluded


def _forward_adjust_splits(bars, bar_type):
    event_factors = {}
    events = []
    for index, (previous, current) in enumerate(
        zip(bars, bars[1:]),
        start=1,
    ):
        ratio = current.close / previous.close
        if Decimal("0.70") <= ratio <= Decimal("1.30"):
            continue
        magnitude = ONE / ratio if ratio < ONE else ratio
        nearest_integer = magnitude.to_integral_value(
            rounding=ROUND_HALF_UP
        )
        if (
            nearest_integer < Decimal("2")
            or abs(magnitude / nearest_integer - ONE)
            > Decimal("0.05")
        ):
            raise RuntimeError(
                "ETF price history contains a non-split discontinuity at "
                f"{current.trade_date}: close ratio={ratio}"
            )
        factor = (
            ONE / nearest_integer
            if ratio < ONE
            else nearest_integer
        )
        event_factors[index] = factor
        events.append(
            {
                "trade_date": current.trade_date,
                "raw_close_ratio": ratio,
                "prior_history_factor": factor,
                "inferred_split_ratio": nearest_integer,
            }
        )

    scales = [ONE] * len(bars)
    cumulative = ONE
    for index in range(len(bars) - 1, -1, -1):
        scales[index] = cumulative
        if index in event_factors:
            cumulative *= event_factors[index]
    adjusted = tuple(
        bar_type(
            trade_date=bar.trade_date,
            open=bar.open * scales[index],
            close=bar.close * scales[index],
        )
        for index, bar in enumerate(bars)
    )
    return adjusted, events


def _merge_aux_points(
    pe_records,
    yield_records,
    qvix_records,
    point_type,
):
    merged = {}
    for record in pe_records:
        merged.setdefault(str(record["trade_date"]), {})[
            "market_pe"
        ] = Decimal(str(record["market_pe"]))
    for record in yield_records:
        merged.setdefault(str(record["trade_date"]), {})[
            "ten_year_yield_percent"
        ] = Decimal(str(record["ten_year_yield_percent"]))
    for record in qvix_records:
        merged.setdefault(str(record["trade_date"]), {})[
            "qvix_close"
        ] = Decimal(str(record["qvix_close"]))
    return tuple(
        point_type(trade_date=trade_date, **values)
        for trade_date, values in sorted(merged.items())
    )


def _daily_medium_long_scores(
    series,
    aux_series,
    strategy,
    parameters,
):
    scores = []
    for index in range(len(series.bars) - 1):
        current = series.bars[index]
        following = series.bars[index + 1]
        result = strategy.evaluate(
            series,
            aux_series,
            current.trade_date,
            following.trade_date,
            parameters,
        )
        signal = next(
            item
            for item in result.signals
            if item.horizon == "medium_long"
        )
        low_score = signal.metrics.get("low_position_score")
        high_score = signal.metrics.get("high_position_score")
        if low_score is None or high_score is None:
            continue
        scores.append(
            (current.trade_date, low_score - high_score)
        )
    return tuple(scores)


def _common_etf_bars(etf_bars, index_series):
    index_dates = {bar.trade_date for bar in index_series.bars}
    common = tuple(
        bar for bar in etf_bars if bar.trade_date in index_dates
    )
    if len(common) < 253:
        raise RuntimeError(
            "ETF/index common history is insufficient for risk windows"
        )
    return common


def _completed_month_ends(trade_dates):
    last_by_month = {}
    for trade_date in trade_dates:
        last_by_month[trade_date[:7]] = trade_date
    dates_with_next = set(trade_dates[:-1])
    return tuple(
        last_by_month[month]
        for month in sorted(last_by_month)
        if last_by_month[month] in dates_with_next
    )


def _build_scheme_signals(
    *,
    symbol,
    asset_id,
    common_bars,
    month_ends,
    daily_scores,
    calculator,
    InvestmentMandate,
    PositionBudgetInput,
    FractionalExposureSignal,
):
    date_to_index = {
        bar.trade_date: index
        for index, bar in enumerate(common_bars)
    }
    next_date = {
        current.trade_date: following.trade_date
        for current, following in zip(common_bars, common_bars[1:])
    }
    scores_by_date = dict(daily_scores)
    score_history = []
    score_history_by_date = {}
    for score_date, score in daily_scores:
        score_history.append(score)
        score_history_by_date[score_date] = tuple(score_history)

    mandate = InvestmentMandate(
        mandate_id=f"sse-etf-position-budget:{symbol}",
        objective="absolute_return",
        implementation_asset_id=asset_id,
        benchmark_asset_id=asset_id,
        defensive_asset_id=None,
        target_volatility=Decimal("0.10"),
        max_drawdown=Decimal("0.15"),
        max_tracking_error=None,
        max_risk_weight=Decimal("0.80"),
        min_cash_weight=Decimal("0.05"),
        min_timing_multiplier=Decimal("0.20"),
        max_timing_multiplier=Decimal("1.00"),
        neutral_timing_multiplier=Decimal("0.60"),
    )
    timing_mandate = replace(
        mandate,
        drawdown_governor_enabled=True,
        timing_overlay_enabled=True,
    )
    drawdown_mandate = replace(
        mandate,
        drawdown_governor_enabled=True,
    )
    output = {
        "timing_overlay": [],
        "drawdown_baseline": [],
        "volatility_baseline": [],
    }
    for signal_date in month_ends:
        bar_index = date_to_index[signal_date]
        if bar_index < 251:
            continue
        closes = tuple(
            bar.close for bar in common_bars[: bar_index + 1]
        )
        realized_volatility = _annualized_volatility(closes, 60)
        current_drawdown = (
            closes[-1] / max(closes[-252:]) - ONE
        )
        current_score = scores_by_date.get(signal_date)
        historical_scores = score_history_by_date.get(signal_date, ())
        timing_percentile = (
            _rank_percentile(historical_scores, current_score)
            if current_score is not None
            and len(historical_scores) >= 252
            else None
        )
        overlay_budget = calculator.calculate(
            timing_mandate,
            PositionBudgetInput(
                as_of_date=signal_date,
                realized_volatility=realized_volatility,
                current_drawdown=current_drawdown,
                timing_score_percentile=timing_percentile,
            ),
        )
        baseline_budget = calculator.calculate(
            mandate,
            PositionBudgetInput(
                as_of_date=signal_date,
                realized_volatility=realized_volatility,
                current_drawdown=ZERO,
                timing_score_percentile=ONE,
            ),
        )
        drawdown_budget = calculator.calculate(
            drawdown_mandate,
            PositionBudgetInput(
                as_of_date=signal_date,
                realized_volatility=realized_volatility,
                current_drawdown=current_drawdown,
                timing_score_percentile=ONE,
            ),
        )
        effective_date = next_date[signal_date]
        output["timing_overlay"].append(
            FractionalExposureSignal(
                as_of_date=signal_date,
                effective_date=effective_date,
                target_exposure=overlay_budget.risk_weight,
                reason=(
                    "monthly SSE timing overlay; "
                    f"vol={realized_volatility}; "
                    f"drawdown={current_drawdown}; "
                    f"timing_percentile={timing_percentile}"
                ),
            )
        )
        output["volatility_baseline"].append(
            FractionalExposureSignal(
                as_of_date=signal_date,
                effective_date=effective_date,
                target_exposure=baseline_budget.risk_weight,
                reason=(
                    "monthly pure target-volatility baseline; "
                    f"vol={realized_volatility}; drawdown=0; "
                    "timing_percentile=1"
                ),
            )
        )
        output["drawdown_baseline"].append(
            FractionalExposureSignal(
                as_of_date=signal_date,
                effective_date=effective_date,
                target_exposure=drawdown_budget.risk_weight,
                reason=(
                    "monthly volatility and drawdown baseline; "
                    f"vol={realized_volatility}; "
                    f"drawdown={current_drawdown}; "
                    "timing_percentile=1"
                ),
            )
        )
    return {
        name: tuple(signals)
        for name, signals in output.items()
    }


def _annualized_volatility(closes, return_window):
    selected = closes[-(return_window + 1) :]
    returns = tuple(
        current / previous - ONE
        for previous, current in zip(selected, selected[1:])
    )
    mean = sum(returns, ZERO) / Decimal(len(returns))
    variance = sum(
        (value - mean) ** 2 for value in returns
    ) / Decimal(len(returns) - 1)
    return variance.sqrt() * Decimal("252").sqrt()


def _rank_percentile(values, current):
    less = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    if len(values) == 1:
        return Decimal("0.5")
    return (
        Decimal(less) + Decimal(equal - 1) / Decimal("2")
    ) / Decimal(len(values) - 1)


def _assert_smoke_contract(run, signals, bars):
    assert run.equity_curve and signals
    assert all(point.cash >= ZERO for point in run.equity_curve)
    next_date = {
        current.trade_date: following.trade_date
        for current, following in zip(bars, bars[1:])
    }
    assert all(
        signal.effective_date == next_date[signal.as_of_date]
        for signal in signals
    )
    required_metrics = {
        "total_return",
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "sharpe_ratio",
        "benchmark_total_return",
        "excess_total_return",
        "turnover",
        "total_fees",
        "average_exposure",
    }
    assert required_metrics <= run.metrics.keys()
    assert all(run.metrics[key] is not None for key in required_metrics)


def _run_summary(run, signals):
    metrics = run.metrics
    return {
        "period": [run.spec.start_date, run.spec.end_date],
        "signal_count": len(signals),
        "trade_count": len(run.trades),
        "total_return": metrics["total_return"],
        "annualized_return": metrics["annualized_return"],
        "annualized_volatility": metrics["annualized_volatility"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "benchmark_return": metrics["benchmark_total_return"],
        "excess_return": metrics["excess_total_return"],
        "turnover": metrics["turnover"],
        "fees": metrics["total_fees"],
        "slippage_cost": metrics["slippage_cost"],
        "total_transaction_cost": (
            metrics["total_fees"] + metrics["slippage_cost"]
        ),
        "average_exposure": metrics["average_exposure"],
        "latest_target_exposure": signals[-1].target_exposure,
        "latest_target_as_of": signals[-1].as_of_date,
        "latest_target_effective": signals[-1].effective_date,
    }


def _signal_target(signal):
    return {
        "as_of_date": signal.as_of_date,
        "effective_date": signal.effective_date,
        "target_exposure": signal.target_exposure,
        "target_cash": ONE - signal.target_exposure,
    }


def _error_text(exc):
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
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
