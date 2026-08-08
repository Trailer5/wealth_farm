"""First explainable research-rule catalog, one per horizon."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.research_workflow import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_HIGH_GROWTH_UNPROFITABLE,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
)

from .engine import StrategyExecutor
from .models import (
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    MetricCondition,
    StrategyInput,
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
    StrategySpec,
    WeightedCondition,
)
from .registry import StrategyRegistry


@dataclass(frozen=True)
class HorizonStrategyReport:
    results: tuple[StrategyResult, ...]
    conflicts: tuple[str, ...]
    aggregate_score: None = None


def default_strategy_specs() -> tuple[StrategySpec, ...]:
    stock_profiles = (
        PROFILE_ORDINARY_COMPANY,
        PROFILE_FINANCIAL_INSTITUTION,
        PROFILE_CYCLICAL_RESOURCE,
        PROFILE_HIGH_GROWTH_UNPROFITABLE,
    )
    return (
        StrategySpec(
            strategy_id="trend_timing",
            name="Short-term Trend Timing",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=stock_profiles
            + (PROFILE_EXCHANGE_FUND,),
            horizons=(ResearchHorizon.SHORT,),
            required_metrics=(
                "momentum_5",
                "return_1d",
                "annualized_volatility",
                "max_drawdown",
                "excess_total_return",
            ),
            required_evidence_types=(),
            data_window_days=60,
            valid_for_days=5,
            hard_filters=(
                MetricCondition(
                    "timing_drawdown_gate",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    Decimal("-0.20"),
                    "Drawdown must not exceed 20%",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "timing_volatility_veto",
                    "annualized_volatility",
                    ConditionOperator.GT,
                    Decimal("0.80"),
                    "Extreme annualized volatility",
                ),
            ),
            score_rules=(
                _weighted(
                    "timing_momentum",
                    "momentum_5",
                    ConditionOperator.GT,
                    "0",
                    "Positive five-day momentum",
                    "0.3",
                ),
                _weighted(
                    "timing_daily_return",
                    "return_1d",
                    ConditionOperator.GT,
                    "0",
                    "Positive latest daily return",
                    "0.2",
                ),
                _weighted(
                    "timing_relative_strength",
                    "excess_total_return",
                    ConditionOperator.GT,
                    "0",
                    "Outperforming benchmark",
                    "0.3",
                ),
                _weighted(
                    "timing_drawdown_score",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.10",
                    "Drawdown contained within 10%",
                    "0.2",
                ),
            ),
            passing_score=Decimal("0.6"),
            signal_on_pass="timing_supportive",
            signal_on_fail="timing_not_supportive",
            limitations=(
                "Short-term timing only; says nothing about intrinsic value",
                "Not backtested",
            ),
        ),
        StrategySpec(
            strategy_id="catalyst_repricing",
            name="Medium-short Catalyst Repricing",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(
                PROFILE_ORDINARY_COMPANY,
                PROFILE_CYCLICAL_RESOURCE,
            ),
            horizons=(ResearchHorizon.MEDIUM_SHORT,),
            required_metrics=(
                "pe_ttm",
                "momentum_5",
                "excess_total_return",
                "max_drawdown",
            ),
            required_evidence_types=("research_event",),
            data_window_days=180,
            valid_for_days=45,
            hard_filters=(
                MetricCondition(
                    "catalyst_valuation_gate",
                    "pe_ttm",
                    ConditionOperator.BETWEEN,
                    Decimal("0"),
                    "Valuation must remain in reviewable range",
                    upper_threshold=Decimal("40"),
                ),
            ),
            vetoes=(
                MetricCondition(
                    "catalyst_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.30"),
                    "Price damage exceeds catalyst-risk boundary",
                ),
            ),
            score_rules=(
                _weighted(
                    "catalyst_momentum",
                    "momentum_5",
                    ConditionOperator.GT,
                    "0",
                    "Catalyst has positive price confirmation",
                    "0.35",
                ),
                _weighted(
                    "catalyst_relative",
                    "excess_total_return",
                    ConditionOperator.GT,
                    "0",
                    "Catalyst outperforms benchmark",
                    "0.35",
                ),
                _weighted(
                    "catalyst_value",
                    "pe_ttm",
                    ConditionOperator.LTE,
                    "25",
                    "Valuation has not fully repriced",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="catalyst_research_supportive",
            signal_on_fail="catalyst_not_confirmed",
            limitations=(
                "Requires explicit catalyst evidence and expiry review",
                "Not backtested",
            ),
        ),
        StrategySpec(
            strategy_id="quality_value",
            name="Medium-long Quality and Value",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_ORDINARY_COMPANY,),
            horizons=(ResearchHorizon.MEDIUM_LONG,),
            required_metrics=(
                "roe",
                "roic",
                "fcf_yield",
                "debt_to_assets",
                "pe_ttm",
                "revenue_growth",
            ),
            required_evidence_types=("financial_statement",),
            data_window_days=730,
            valid_for_days=90,
            hard_filters=(
                ConditionGroup(
                    condition_id="quality_value_hard_gate",
                    logic=ConditionLogic.AND,
                    children=(
                        MetricCondition(
                            "quality_value_roic_gate",
                            "roic",
                            ConditionOperator.GTE,
                            Decimal("0.10"),
                            "ROIC at least 10%",
                        ),
                        MetricCondition(
                            "quality_value_fcf_gate",
                            "fcf_yield",
                            ConditionOperator.GT,
                            Decimal("0"),
                            "Free cash flow yield must be positive",
                        ),
                    ),
                    description="Quality and cash-flow hard gate",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "quality_value_leverage_veto",
                    "debt_to_assets",
                    ConditionOperator.GT,
                    Decimal("0.70"),
                    "Excessive leverage veto",
                ),
            ),
            score_rules=(
                _weighted(
                    "quality_value_roe",
                    "roe",
                    ConditionOperator.GTE,
                    "0.15",
                    "ROE at least 15%",
                    "0.25",
                ),
                _weighted(
                    "quality_value_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.15",
                    "ROIC at least 15%",
                    "0.25",
                ),
                _weighted(
                    "quality_value_growth",
                    "revenue_growth",
                    ConditionOperator.GT,
                    "0",
                    "Revenue is growing",
                    "0.20",
                ),
                _weighted(
                    "quality_value_pe",
                    "pe_ttm",
                    ConditionOperator.LTE,
                    "30",
                    "PE remains below 30",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="quality_value_research_supportive",
            signal_on_fail="quality_or_value_insufficient",
            limitations=(
                "Thresholds are initial research assumptions",
                "Not backtested or peer-normalized",
            ),
        ),
        StrategySpec(
            strategy_id="business_thesis_check",
            name="Long-term Business Thesis Readiness",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(
                PROFILE_ORDINARY_COMPANY,
                PROFILE_CYCLICAL_RESOURCE,
                PROFILE_HIGH_GROWTH_UNPROFITABLE,
            ),
            horizons=(ResearchHorizon.LONG,),
            required_metrics=(
                "roe",
                "roic",
                "fcf_yield",
                "debt_to_assets",
                "max_drawdown",
            ),
            required_evidence_types=(
                "financial_statement",
                "disclosure",
                "business_model_evidence",
                "management_evidence",
            ),
            data_window_days=1095,
            valid_for_days=180,
            hard_filters=(
                MetricCondition(
                    "thesis_fcf_gate",
                    "fcf_yield",
                    ConditionOperator.GT,
                    Decimal("0"),
                    "Long-term thesis requires positive FCF yield",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "thesis_leverage_veto",
                    "debt_to_assets",
                    ConditionOperator.GT,
                    Decimal("0.70"),
                    "Balance-sheet fragility veto",
                ),
                MetricCondition(
                    "thesis_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.60"),
                    "Extreme capital-loss history requires rejection",
                ),
            ),
            score_rules=(
                _weighted(
                    "thesis_roe",
                    "roe",
                    ConditionOperator.GTE,
                    "0.15",
                    "Sustained owner return potential",
                    "0.30",
                ),
                _weighted(
                    "thesis_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.15",
                    "Strong capital allocation economics",
                    "0.40",
                ),
                _weighted(
                    "thesis_fcf",
                    "fcf_yield",
                    ConditionOperator.GTE,
                    "0.03",
                    "Positive free-cash-flow yield",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.70"),
            signal_on_pass="thesis_evidence_ready",
            signal_on_fail="thesis_evidence_not_ready",
            limitations=(
                "This is thesis-readiness, not intrinsic-value proof",
                "Qualitative moat and management evidence require review",
                "Not backtested",
            ),
        ),
    )


def exchange_fund_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return profile-specific ETF research rules."""

    return (
        StrategySpec(
            strategy_id="etf_tracking_efficiency",
            name="Medium-long ETF Tracking Efficiency",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_EXCHANGE_FUND,),
            horizons=(ResearchHorizon.MEDIUM_LONG,),
            required_metrics=(
                "tracking_error",
                "premium_discount",
                "expense_ratio",
                "max_drawdown",
                "excess_total_return",
            ),
            required_evidence_types=(),
            data_window_days=730,
            valid_for_days=90,
            hard_filters=(
                MetricCondition(
                    "etf_premium_discount_gate",
                    "premium_discount",
                    ConditionOperator.BETWEEN,
                    Decimal("-0.02"),
                    "ETF premium or discount must remain within 2%",
                    upper_threshold=Decimal("0.02"),
                ),
                MetricCondition(
                    "etf_expense_gate",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    Decimal("0.01"),
                    "Annual comprehensive expense ratio at most 1%",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "etf_tracking_error_veto",
                    "tracking_error",
                    ConditionOperator.GT,
                    Decimal("0.15"),
                    "Annualized tracking error exceeds 15%",
                ),
            ),
            score_rules=(
                _weighted(
                    "etf_tracking_quality",
                    "tracking_error",
                    ConditionOperator.LTE,
                    "0.05",
                    "Annualized tracking error at most 5%",
                    "0.35",
                ),
                _weighted(
                    "etf_cost_quality",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    "0.005",
                    "Annual comprehensive expense ratio at most 0.5%",
                    "0.25",
                ),
                _weighted(
                    "etf_drawdown_quality",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.30",
                    "Input-window drawdown no worse than -30%",
                    "0.20",
                ),
                _weighted(
                    "etf_relative_quality",
                    "excess_total_return",
                    ConditionOperator.GTE,
                    "-0.10",
                    "Input-window excess return no worse than -10%",
                    "0.20",
                ),
            ),
            passing_score=Decimal("0.60"),
            signal_on_pass="etf_tracking_research_supportive",
            signal_on_fail="etf_tracking_efficiency_insufficient",
            limitations=(
                "Evaluates ETF vehicle efficiency, not underlying "
                "asset direction",
                "Premium/discount is a point-in-time snapshot",
                "Thresholds are research assumptions, not backtested",
            ),
        ),
    )


def financial_institution_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return bank-specific medium-long quality and risk rules."""

    return (
        StrategySpec(
            strategy_id="bank_quality_risk",
            name="Medium-long Bank Quality and Risk",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_FINANCIAL_INSTITUTION,),
            horizons=(ResearchHorizon.MEDIUM_LONG,),
            required_metrics=(
                "pb",
                "roe",
                "capital_adequacy",
                "net_interest_margin",
                "non_performing_loan_ratio",
                "max_drawdown",
            ),
            required_evidence_types=("financial_statement",),
            data_window_days=730,
            valid_for_days=90,
            hard_filters=(
                MetricCondition(
                    "bank_capital_gate",
                    "capital_adequacy",
                    ConditionOperator.GTE,
                    Decimal("0.10"),
                    "Capital adequacy must be at least 10%",
                ),
                MetricCondition(
                    "bank_npl_gate",
                    "non_performing_loan_ratio",
                    ConditionOperator.LTE,
                    Decimal("0.03"),
                    "Non-performing loan ratio at most 3%",
                ),
                MetricCondition(
                    "bank_pb_gate",
                    "pb",
                    ConditionOperator.BETWEEN,
                    Decimal("0"),
                    "PB must remain in a reviewable range",
                    upper_threshold=Decimal("3"),
                ),
            ),
            vetoes=(
                MetricCondition(
                    "bank_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.50"),
                    "Input-window drawdown exceeds 50%",
                ),
            ),
            score_rules=(
                _weighted(
                    "bank_roe_quality",
                    "roe",
                    ConditionOperator.GTE,
                    "0.08",
                    "Reported ROE at least 8%",
                    "0.25",
                ),
                _weighted(
                    "bank_margin_quality",
                    "net_interest_margin",
                    ConditionOperator.GTE,
                    "0.015",
                    "Net interest margin at least 1.5%",
                    "0.25",
                ),
                _weighted(
                    "bank_asset_quality",
                    "non_performing_loan_ratio",
                    ConditionOperator.LTE,
                    "0.02",
                    "Non-performing loan ratio at most 2%",
                    "0.30",
                ),
                _weighted(
                    "bank_valuation_quality",
                    "pb",
                    ConditionOperator.LTE,
                    "1.5",
                    "PB no higher than 1.5",
                    "0.20",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="bank_quality_research_supportive",
            signal_on_fail="bank_quality_or_risk_insufficient",
            limitations=(
                "Bank-specific research rule; excludes EV/EBITDA "
                "and free-cash-flow yield",
                "Reported quarter ROE may not be annualized",
                "Thresholds are research assumptions, not backtested",
            ),
        ),
    )


def cycle_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return long-horizon commodity-cycle research rules."""

    return (
        StrategySpec(
            strategy_id="resource_cycle_readiness",
            name="Long-term Resource Cycle Readiness",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_CYCLICAL_RESOURCE,),
            horizons=(ResearchHorizon.LONG,),
            required_metrics=(
                "pb",
                "roic",
                "net_debt",
                "commodity_sensitivity",
                "max_drawdown",
            ),
            required_evidence_types=(
                "financial_statement",
                "disclosure",
            ),
            data_window_days=1095,
            valid_for_days=180,
            hard_filters=(
                MetricCondition(
                    "resource_pb_gate",
                    "pb",
                    ConditionOperator.BETWEEN,
                    Decimal("0"),
                    "PB must remain in a reviewable range",
                    upper_threshold=Decimal("5"),
                ),
                MetricCondition(
                    "resource_sensitivity_gate",
                    "commodity_sensitivity",
                    ConditionOperator.GT,
                    Decimal("0"),
                    "Equity returns must retain positive commodity sensitivity",
                ),
            ),
            score_rules=(
                _weighted(
                    "resource_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.08",
                    "ROIC at least 8%",
                    "0.4",
                ),
                _weighted(
                    "resource_drawdown",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.40",
                    "Input-window drawdown no worse than -40%",
                    "0.3",
                ),
                _weighted(
                    "resource_sensitivity",
                    "commodity_sensitivity",
                    ConditionOperator.GTE,
                    "0.5",
                    "Commodity return beta at least 0.5",
                    "0.3",
                ),
            ),
            passing_score=Decimal("0.6"),
            limitations=(
                "Sensitivity is historical co-movement, not causality",
                "Copper/gold weights are explicit research assumptions",
                "Does not forecast commodity prices",
            ),
        ),
        StrategySpec(
            strategy_id="gold_etf_cycle_vehicle",
            name="Long-term Gold ETF Cycle Vehicle Readiness",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_EXCHANGE_FUND,),
            horizons=(ResearchHorizon.LONG,),
            required_metrics=(
                "commodity_sensitivity",
                "premium_discount",
                "expense_ratio",
                "max_drawdown",
            ),
            required_evidence_types=(),
            data_window_days=1095,
            valid_for_days=180,
            hard_filters=(
                MetricCondition(
                    "gold_etf_sensitivity_gate",
                    "commodity_sensitivity",
                    ConditionOperator.GT,
                    Decimal("0"),
                    "ETF must retain positive gold-price sensitivity",
                ),
                MetricCondition(
                    "gold_etf_premium_gate",
                    "premium_discount",
                    ConditionOperator.BETWEEN,
                    Decimal("-0.02"),
                    "Premium or discount must remain within 2%",
                    upper_threshold=Decimal("0.02"),
                ),
            ),
            score_rules=(
                _weighted(
                    "gold_etf_sensitivity",
                    "commodity_sensitivity",
                    ConditionOperator.GTE,
                    "0.5",
                    "Gold return beta at least 0.5",
                    "0.4",
                ),
                _weighted(
                    "gold_etf_expense",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    "0.01",
                    "Annual comprehensive expense ratio at most 1%",
                    "0.3",
                ),
                _weighted(
                    "gold_etf_drawdown",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.40",
                    "Input-window drawdown no worse than -40%",
                    "0.3",
                ),
            ),
            passing_score=Decimal("0.6"),
            limitations=(
                "Evaluates vehicle readiness, not future gold direction",
                "Macro and real-rate evidence still requires review",
            ),
        ),
    )


def default_strategy_registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    for spec in (
        *default_strategy_specs(),
        *exchange_fund_strategy_specs(),
        *financial_institution_strategy_specs(),
        *cycle_strategy_specs(),
    ):
        registry.register(spec)
    return registry


def execute_horizon_catalog(
    *,
    inputs: dict[ResearchHorizon, StrategyInput],
    executor: StrategyExecutor | None = None,
) -> HorizonStrategyReport:
    runner = executor or StrategyExecutor()
    specs_by_horizon = {
        spec.horizons[0]: spec
        for spec in default_strategy_specs()
    }
    results = tuple(
        runner.execute(specs_by_horizon[horizon], strategy_input)
        for horizon, strategy_input in sorted(
            inputs.items(),
            key=lambda item: list(ResearchHorizon).index(item[0]),
        )
    )
    pass_horizons = [
        result.horizon.value
        for result in results
        if result.status is StrategyRunStatus.PASS
    ]
    negative_horizons = [
        result.horizon.value
        for result in results
        if result.status
        in {
            StrategyRunStatus.FAIL,
            StrategyRunStatus.VETOED,
        }
    ]
    conflicts = ()
    if pass_horizons and negative_horizons:
        conflicts = (
            "Different horizons disagree: supportive="
            f"{','.join(pass_horizons)}; non_supportive="
            f"{','.join(negative_horizons)}. "
            "Do not aggregate scores across horizons.",
        )
    return HorizonStrategyReport(
        results=results,
        conflicts=conflicts,
        aggregate_score=None,
    )


def _weighted(
    condition_id: str,
    metric_name: str,
    operator: ConditionOperator,
    threshold: str,
    description: str,
    weight: str,
) -> WeightedCondition:
    return WeightedCondition(
        condition=MetricCondition(
            condition_id=condition_id,
            metric_name=metric_name,
            operator=operator,
            threshold=Decimal(threshold),
            description=description,
        ),
        weight=Decimal(weight),
    )
