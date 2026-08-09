"""Compare cash and defensive-ETF implementations of SSE risk budgets."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
END_DATE = "2026-08-07"
ZERO = Decimal("0")
ONE = Decimal("1")
MIN_CASH_WEIGHT = Decimal("0.05")
MAX_INVESTED_WEIGHT = ONE - MIN_CASH_WEIGHT
MAX_RISK_WEIGHT = Decimal("0.80")
VOLATILITY_TOLERANCE = Decimal("0.03")
MAX_DRAWDOWN = Decimal("0.15")
INITIAL_CASH = Decimal("1000000")
PAIRS = {
    "long_history": {
        "start_date": "2005-01-01",
        "risk_symbol": "510210",
        "defensive_symbol": "511010",
        "defensive_role": "five_year_treasury_etf",
    },
    "post_2020": {
        "start_date": "2020-01-01",
        "risk_symbol": "510760",
        "defensive_symbol": "511360",
        "defensive_role": "short_term_financing_etf",
    },
}
SCHEMES = {
    "risk_only_vol10_cash": {
        "target_volatility": Decimal("0.10"),
        "uses_defensive": False,
    },
    "risk_bond_vol10": {
        "target_volatility": Decimal("0.10"),
        "uses_defensive": True,
    },
    "risk_only_vol8_cash": {
        "target_volatility": Decimal("0.08"),
        "uses_defensive": False,
    },
    "risk_bond_vol8": {
        "target_volatility": Decimal("0.08"),
        "uses_defensive": True,
    },
    "risk_only_vol7": {
        "target_volatility": Decimal("0.07"),
        "uses_defensive": False,
    },
    "risk_bond_vol7": {
        "target_volatility": Decimal("0.07"),
        "uses_defensive": True,
    },
    "risk_bond_vol10_trend": {
        "target_volatility": Decimal("0.10"),
        "uses_defensive": True,
        "trend_filter": True,
    },
    "risk_bond_vol8_trend": {
        "target_volatility": Decimal("0.08"),
        "uses_defensive": True,
        "trend_filter": True,
    },
}


class RemoteDataUnavailable(RuntimeError):
    """Tencent failed to provide a required real-price dataset."""


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.backtest import (
        BacktestBar,
        ETFRotationBacktestBar,
        ETFRotationBacktestEngine,
        ETFRotationBacktestSpec,
        PortfolioTargetSignal,
    )
    from src.data_src.tencent import TencentExchangeFundDataSource
    from src.factor_engineering import RiskMetricCalculator, RiskMetricParameters
    from src.fund_research import official_etf_expense_schedule
    from src.portfolio_risk import (
        InvestmentMandate,
        PositionBudgetCalculator,
        PositionBudgetInput,
    )
    from src.tests.backtest.smoke_tests.run_sse_etf_position_budget_backtest import (
        _annualized_volatility,
        _completed_month_ends,
        _error_text,
        _fetch_tencent_chunks,
        _forward_adjust_splits,
        _normalize_etf_bars,
    )

    helper_types = {
        "BacktestBar": BacktestBar,
        "ETFRotationBacktestBar": ETFRotationBacktestBar,
        "ETFRotationBacktestSpec": ETFRotationBacktestSpec,
        "PortfolioTargetSignal": PortfolioTargetSignal,
        "InvestmentMandate": InvestmentMandate,
        "PositionBudgetInput": PositionBudgetInput,
        "RiskMetricParameters": RiskMetricParameters,
    }
    provider = TencentExchangeFundDataSource()
    budget_calculator = PositionBudgetCalculator()
    engine = ETFRotationBacktestEngine()
    risk_calculator = RiskMetricCalculator()
    source_errors: dict[str, str] = {}
    results = {}

    for pair_name, pair in PAIRS.items():
        normalized = {}
        source_details = {}
        for role in ("risk", "defensive"):
            symbol = pair[f"{role}_symbol"]
            raw_bars = _fetch_required_tencent_history(
                provider=provider,
                symbol=symbol,
                start_date=pair["start_date"],
                fetch_tencent_chunks=_fetch_tencent_chunks,
                error_text=_error_text,
                source_errors=source_errors,
            )
            clean_bars, excluded = _normalize_etf_bars(
                raw_bars,
                BacktestBar,
            )
            adjusted_bars, split_events = _forward_adjust_splits(
                clean_bars,
                BacktestBar,
            )
            if len(adjusted_bars) < 253:
                raise RuntimeError(
                    f"ETF {symbol} has insufficient real history for "
                    "252-session common-date initialization"
                )
            normalized[role] = adjusted_bars
            source_details[role] = {
                "symbol": symbol,
                "source": "tencent_segmented",
                "raw_bar_count": len(raw_bars),
                "normalized_bar_count": len(adjusted_bars),
                "excluded_invalid_bars": excluded,
                "price_adjustment": (
                    "forward_split_adjusted" if split_events else "none"
                ),
                "split_adjustment_events": split_events,
            }

        common_dates = tuple(
            sorted(
                {bar.trade_date for bar in normalized["risk"]}
                & {bar.trade_date for bar in normalized["defensive"]}
            )
        )
        if len(common_dates) < 253:
            raise RuntimeError(
                f"ETF pair {pair_name} has insufficient common real history"
            )
        common_date_set = set(common_dates)
        risk_asset_id = f"asset:etf:{pair['risk_symbol']}"
        defensive_asset_id = f"asset:etf:{pair['defensive_symbol']}"
        pair_asset_ids = {
            "risk": risk_asset_id,
            "defensive": defensive_asset_id,
        }
        common_by_role = {
            role: tuple(
                bar
                for bar in normalized[role]
                if bar.trade_date in common_date_set
            )
            for role in ("risk", "defensive")
        }
        rotation_bars = tuple(
            ETFRotationBacktestBar(
                asset_id=pair_asset_ids[role],
                trade_date=bar.trade_date,
                open=bar.open,
                close=bar.close,
            )
            for role in ("risk", "defensive")
            for bar in common_by_role[role]
        )
        benchmark_bars = tuple(
            bar for bar in rotation_bars if bar.asset_id == risk_asset_id
        )
        month_ends = _completed_month_ends(common_dates)
        signals_by_scheme = _build_scheme_signals(
            pair_name=pair_name,
            risk_asset_id=risk_asset_id,
            defensive_asset_id=defensive_asset_id,
            risk_bars=common_by_role["risk"],
            common_dates=common_dates,
            month_ends=month_ends,
            calculator=budget_calculator,
            types=helper_types,
            annualized_volatility=_annualized_volatility,
        )
        _assert_common_signal_schedule(signals_by_scheme)

        first_effective_date = next(iter(signals_by_scheme.values()))[
            0
        ].effective_date
        scheme_results = {}
        for scheme_name, signals in signals_by_scheme.items():
            run = engine.run(
                ETFRotationBacktestSpec(
                    run_id=f"sse-defensive-budget:{pair_name}:{scheme_name}",
                    start_date=first_effective_date,
                    end_date=common_dates[-1],
                    initial_cash=INITIAL_CASH,
                    fee_rate=Decimal("0.0002"),
                    slippage_rate=Decimal("0.0001"),
                    benchmark_asset_id=risk_asset_id,
                ),
                rotation_bars,
                signals,
                benchmark_bars,
            )
            summary = _run_summary(
                run=run,
                signals=signals,
                risk_asset_id=risk_asset_id,
                risk_bars=common_by_role["risk"],
                risk_calculator=risk_calculator,
                RiskMetricParameters=RiskMetricParameters,
            )
            target_volatility = SCHEMES[scheme_name][
                "target_volatility"
            ]
            summary["constraint_assessment"] = _constraint_assessment(
                summary,
                target_volatility,
            )
            _assert_smoke_contract(
                run=run,
                signals=signals,
                common_dates=common_dates,
                summary=summary,
            )
            scheme_results[scheme_name] = summary

        buy_and_hold = _buy_and_hold_summary(
            risk_asset_id=risk_asset_id,
            risk_bars=common_by_role["risk"],
            start_date=first_effective_date,
            end_date=common_dates[-1],
            risk_calculator=risk_calculator,
            RiskMetricParameters=RiskMetricParameters,
        )
        qualified = [
            name
            for name, summary in scheme_results.items()
            if summary["constraint_assessment"]["meets_both"]
        ]
        expense_schedules = {}
        for role, symbol in (
            ("risk", pair["risk_symbol"]),
            ("defensive", pair["defensive_symbol"]),
        ):
            expense = official_etf_expense_schedule(
                asset_id=pair_asset_ids[role],
                standard_symbol=symbol,
            )
            expense_schedules[role] = {
                "symbol": symbol,
                "effective_date": expense.effective_date,
                "management_fee_rate": expense.management_fee_rate,
                "custody_fee_rate": expense.custody_fee_rate,
                "comprehensive_expense_ratio": (
                    expense.comprehensive_expense_ratio
                ),
                "source": expense.source,
                "source_url": expense.source_url,
                "backtest_treatment": (
                    "already_reflected_in_real_etf_price_no_extra_deduction"
                ),
            }

        results[pair_name] = {
            "risk_asset": {
                "symbol": pair["risk_symbol"],
                "asset_id": risk_asset_id,
            },
            "defensive_asset": {
                "symbol": pair["defensive_symbol"],
                "asset_id": defensive_asset_id,
                "role": pair["defensive_role"],
            },
            "source_period": [common_dates[0], common_dates[-1]],
            "backtest_period": [first_effective_date, common_dates[-1]],
            "common_bar_count": len(common_dates),
            "source_details": source_details,
            "official_expenses": expense_schedules,
            "signal_schedule": {
                "frequency": "last_common_session_of_month",
                "execution": "next_common_session_open",
                "signal_count": len(
                    next(iter(signals_by_scheme.values()))
                ),
                "first_as_of_date": next(
                    iter(signals_by_scheme.values())
                )[0].as_of_date,
                "last_as_of_date": next(
                    iter(signals_by_scheme.values())
                )[-1].as_of_date,
                "identical_dates_across_schemes": True,
            },
            "buy_and_hold_risk_etf": buy_and_hold,
            "schemes": scheme_results,
            "parameter_decision": {
                "qualified_schemes": qualified,
                "qualification_rule": (
                    "max_drawdown_at_most_15_percent_and_realized_"
                    "volatility_within_target_plus_or_minus_3_points"
                ),
                "automatic_return_maximization": False,
                "selection": (
                    "no_automatic_winner_report_all_constraint_results"
                ),
            },
        }

    decision_summary = {
        pair_name: {
            scheme_name: {
                "annualized_return": pair_result["schemes"][
                    scheme_name
                ]["annualized_return"],
                "annualized_volatility": pair_result["schemes"][
                    scheme_name
                ]["annualized_volatility"],
                "max_drawdown": pair_result["schemes"][
                    scheme_name
                ]["max_drawdown"],
                "sharpe_ratio": pair_result["schemes"][
                    scheme_name
                ]["sharpe_ratio"],
                "latest_target": pair_result["schemes"][
                    scheme_name
                ]["latest_target"],
            }
            for scheme_name in pair_result["parameter_decision"][
                "qualified_schemes"
            ]
        }
        for pair_name, pair_result in results.items()
    }
    output = {
        "status": "completed",
        "decision_summary": decision_summary,
        "data": {
            "source": "tencent_segmented_real_daily_bars",
            "end_date": END_DATE,
            "split_policy": (
                "infer_and_forward_adjust_only_obvious_near_integer_splits"
            ),
            "all_schemes_use_pairwise_common_dates": True,
        },
        "budget": {
            "model": "pure_target_volatility",
            "volatility_window_returns": 60,
            "initialization_sessions": 252,
            "max_risk_weight": MAX_RISK_WEIGHT,
            "minimum_cash_weight": MIN_CASH_WEIGHT,
            "maximum_risk_plus_defensive_weight": MAX_INVESTED_WEIGHT,
            "drawdown_governor_enabled": False,
            "timing_overlay_enabled": False,
        },
        "execution": {
            "signal_frequency": "month_end",
            "execution": "next_common_trading_session_open",
            "fee_rate": Decimal("0.0002"),
            "slippage_rate": Decimal("0.0001"),
        },
        "comparison_policy": {
            "max_drawdown_limit": MAX_DRAWDOWN,
            "target_volatility_tolerance": VOLATILITY_TOLERANCE,
            "parameter_choice_not_based_on_highest_return": True,
        },
        "pairs": results,
        "source_errors": source_errors,
        "assertions": [
            "T+1 on next common session",
            "cash remained non-negative",
            "risk plus defensive weight did not exceed 0.95",
            "target weights plus residual cash equal 1",
            "required metrics exist",
        ],
        "maturity": "research_rule",
    }
    print(
        json.dumps(
            output,
            ensure_ascii=True,
            indent=2,
            default=str,
        )
    )
    return 0


def _fetch_required_tencent_history(
    *,
    provider,
    symbol,
    start_date,
    fetch_tencent_chunks,
    error_text,
    source_errors,
):
    key = f"tencent_etf_{symbol}"
    try:
        bars = fetch_tencent_chunks(
            provider,
            "etf",
            symbol,
            start_date,
            END_DATE,
        )
    except Exception as exc:
        source_errors[key] = error_text(exc)
        raise RemoteDataUnavailable(
            f"Remote real ETF {symbol} history is unavailable from "
            f"Tencent segmented fetch: {source_errors[key]}"
        ) from exc
    if not bars:
        raise RuntimeError(
            f"Tencent returned no real ETF history for {symbol}"
        )
    return bars


def _build_scheme_signals(
    *,
    pair_name,
    risk_asset_id,
    defensive_asset_id,
    risk_bars,
    common_dates,
    month_ends,
    calculator,
    types,
    annualized_volatility,
):
    date_to_index = {
        bar.trade_date: index for index, bar in enumerate(risk_bars)
    }
    next_date = {
        current: following
        for current, following in zip(common_dates, common_dates[1:])
    }
    output = {name: [] for name in SCHEMES}
    for scheme_name, config in SCHEMES.items():
        defensive_id = (
            defensive_asset_id if config["uses_defensive"] else None
        )
        mandate = types["InvestmentMandate"](
            mandate_id=f"sse-defensive-budget:{pair_name}:{scheme_name}",
            objective="absolute_return",
            implementation_asset_id=risk_asset_id,
            benchmark_asset_id=risk_asset_id,
            defensive_asset_id=defensive_id,
            target_volatility=config["target_volatility"],
            max_drawdown=MAX_DRAWDOWN,
            max_tracking_error=None,
            max_risk_weight=MAX_RISK_WEIGHT,
            min_cash_weight=MIN_CASH_WEIGHT,
            drawdown_governor_enabled=False,
            timing_overlay_enabled=False,
        )
        for signal_date in month_ends:
            bar_index = date_to_index[signal_date]
            if bar_index < 251:
                continue
            closes = tuple(
                bar.close for bar in risk_bars[: bar_index + 1]
            )
            realized_volatility = annualized_volatility(closes, 60)
            budget = calculator.calculate(
                mandate,
                types["PositionBudgetInput"](
                    as_of_date=signal_date,
                    realized_volatility=realized_volatility,
                    current_drawdown=ZERO,
                    timing_score_percentile=ONE,
                ),
            )
            risk_weight = budget.risk_weight
            trend_reduced = False
            if (
                config.get("trend_filter", False)
                and closes[-1]
                < sum(closes[-200:], ZERO) / Decimal("200")
            ):
                risk_weight = ZERO
                trend_reduced = True
            weights = {risk_asset_id: risk_weight}
            if config["uses_defensive"]:
                weights[defensive_asset_id] = (
                    MAX_INVESTED_WEIGHT - risk_weight
                )
            output[scheme_name].append(
                types["PortfolioTargetSignal"](
                    as_of_date=signal_date,
                    effective_date=next_date[signal_date],
                    target_weights=weights,
                    reason=(
                        "monthly pure target-volatility budget; "
                        f"target_volatility={config['target_volatility']}; "
                        f"realized_risk_volatility={realized_volatility}; "
                        "drawdown_governor=off; timing_overlay=off; "
                        f"trend_filter={config.get('trend_filter', False)}; "
                        f"trend_reduced={trend_reduced}"
                    ),
                )
            )
    result = {
        name: tuple(signals) for name, signals in output.items()
    }
    if any(not signals for signals in result.values()):
        raise RuntimeError(
            f"ETF pair {pair_name} generated no defensive-budget signals"
        )
    return result


def _assert_common_signal_schedule(signals_by_scheme):
    schedules = {
        tuple(
            (signal.as_of_date, signal.effective_date)
            for signal in signals
        )
        for signals in signals_by_scheme.values()
    }
    assert len(schedules) == 1


def _run_summary(
    *,
    run,
    signals,
    risk_asset_id,
    risk_bars,
    risk_calculator,
    RiskMetricParameters,
):
    calculated = _calculate_risk_metrics(
        asset_id=run.spec.run_id,
        records=[
            {
                "trade_date": point.trade_date,
                "close": float(point.equity),
            }
            for point in run.equity_curve
        ],
        risk_calculator=risk_calculator,
        RiskMetricParameters=RiskMetricParameters,
    )
    total_notional = sum(
        (trade.notional for trade in run.trades),
        ZERO,
    )
    fees = sum((trade.fee for trade in run.trades), ZERO)
    slippage = sum(
        (trade.slippage_cost for trade in run.trades),
        ZERO,
    )
    latest = signals[-1]
    latest_invested = sum(latest.target_weights.values(), ZERO)
    return {
        "period": [run.spec.start_date, run.spec.end_date],
        "signal_count": len(signals),
        "trade_count": len(run.trades),
        "total_return": run.metrics["total_return"],
        "annualized_return": calculated["annualized_return"],
        "annualized_volatility": calculated["annualized_volatility"],
        "max_drawdown": calculated["max_drawdown"],
        "sharpe_ratio": calculated["sharpe_ratio"],
        "average_risk_weight": _average_actual_risk_weight(
            run,
            risk_asset_id,
            risk_bars,
        ),
        "turnover": total_notional / run.spec.initial_cash,
        "fees": fees,
        "slippage_cost": slippage,
        "total_transaction_cost": fees + slippage,
        "latest_target": {
            "as_of_date": latest.as_of_date,
            "effective_date": latest.effective_date,
            "risk_weight": latest.target_weights.get(
                risk_asset_id,
                ZERO,
            ),
            "defensive_weight": sum(
                (
                    weight
                    for asset_id, weight in latest.target_weights.items()
                    if asset_id != risk_asset_id
                ),
                ZERO,
            ),
            "cash_weight": ONE - latest_invested,
        },
        "skipped_order_count": len(run.skipped_orders),
    }


def _calculate_risk_metrics(
    *,
    asset_id,
    records,
    risk_calculator,
    RiskMetricParameters,
):
    result = risk_calculator.calculate(
        asset_id=asset_id,
        records=records,
        source_dataset="defensive_budget_backtest_equity",
        parameters=RiskMetricParameters(
            annualization_days=252,
            risk_free_rate=0.0,
            rolling_windows=(),
            price_adjustment="portfolio_equity",
        ),
    )
    values = {
        metric.factor_name: Decimal(str(metric.value))
        for metric in result.metrics
    }
    required = {
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "sharpe_ratio",
    }
    missing = required - values.keys()
    if missing:
        raise RuntimeError(
            f"Required risk metrics are missing: {sorted(missing)}"
        )
    return values


def _average_actual_risk_weight(run, risk_asset_id, risk_bars):
    risk_close_by_date = {
        bar.trade_date: bar.close for bar in risk_bars
    }
    trades_by_date = {}
    for trade in run.trades:
        trades_by_date.setdefault(trade.trade_date, []).append(trade)
    risk_quantity = ZERO
    daily_weights = []
    for point in run.equity_curve:
        for trade in trades_by_date.get(point.trade_date, ()):
            if trade.asset_id != risk_asset_id:
                continue
            if trade.side == "buy":
                risk_quantity += trade.quantity
            else:
                risk_quantity -= trade.quantity
        daily_weights.append(
            risk_quantity
            * risk_close_by_date[point.trade_date]
            / point.equity
        )
    return sum(daily_weights, ZERO) / Decimal(len(daily_weights))


def _buy_and_hold_summary(
    *,
    risk_asset_id,
    risk_bars,
    start_date,
    end_date,
    risk_calculator,
    RiskMetricParameters,
):
    selected = tuple(
        bar
        for bar in risk_bars
        if start_date <= bar.trade_date <= end_date
    )
    metrics = _calculate_risk_metrics(
        asset_id=f"{risk_asset_id}:buy_and_hold",
        records=[
            {
                "trade_date": bar.trade_date,
                "close": float(bar.close),
            }
            for bar in selected
        ],
        risk_calculator=risk_calculator,
        RiskMetricParameters=RiskMetricParameters,
    )
    return {
        "period": [selected[0].trade_date, selected[-1].trade_date],
        "method": "close_to_close_price_return_without_transaction_costs",
        "total_return": selected[-1].close / selected[0].close - ONE,
        "annualized_return": metrics["annualized_return"],
        "annualized_volatility": metrics["annualized_volatility"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "average_risk_weight": ONE,
        "turnover": ZERO,
        "fees": ZERO,
    }


def _constraint_assessment(summary, target_volatility):
    realized_volatility = summary["annualized_volatility"]
    max_drawdown = summary["max_drawdown"]
    volatility_gap = realized_volatility - target_volatility
    meets_drawdown = abs(max_drawdown) <= MAX_DRAWDOWN
    meets_volatility = abs(volatility_gap) <= VOLATILITY_TOLERANCE
    return {
        "target_volatility": target_volatility,
        "realized_volatility": realized_volatility,
        "volatility_gap": volatility_gap,
        "volatility_tolerance": VOLATILITY_TOLERANCE,
        "meets_target_volatility_band": meets_volatility,
        "max_drawdown_limit": MAX_DRAWDOWN,
        "meets_max_drawdown_limit": meets_drawdown,
        "meets_both": meets_drawdown and meets_volatility,
    }


def _assert_smoke_contract(
    *,
    run,
    signals,
    common_dates,
    summary,
):
    assert run.equity_curve and signals
    assert all(point.cash >= ZERO for point in run.equity_curve)
    next_date = {
        current: following
        for current, following in zip(common_dates, common_dates[1:])
    }
    assert all(
        signal.effective_date == next_date[signal.as_of_date]
        for signal in signals
    )
    assert all(
        trade.trade_date == next_date[trade.signal_date]
        for trade in run.trades
    )
    for signal in signals:
        invested = sum(signal.target_weights.values(), ZERO)
        cash = ONE - invested
        assert invested <= MAX_INVESTED_WEIGHT
        assert cash >= MIN_CASH_WEIGHT
        assert invested + cash == ONE
    required_metrics = {
        "total_return",
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "sharpe_ratio",
        "average_risk_weight",
        "turnover",
        "fees",
        "latest_target",
    }
    assert required_metrics <= summary.keys()
    assert all(summary[key] is not None for key in required_metrics)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RemoteDataUnavailable as exc:
        print(
            json.dumps(
                {
                    "status": "remote_unavailable",
                    "source": "tencent_segmented_real_daily_bars",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
