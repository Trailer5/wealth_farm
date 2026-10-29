"""First explainable research-rule catalog, one per horizon."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.research_contracts import (
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
            name="短期趋势择时规则",
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
                    "区间回撤不超过 20%",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "timing_volatility_veto",
                    "annualized_volatility",
                    ConditionOperator.GT,
                    Decimal("0.80"),
                    "年化波动率极端",
                ),
            ),
            score_rules=(
                _weighted(
                    "timing_momentum",
                    "momentum_5",
                    ConditionOperator.GT,
                    "0",
                    "5 日动量为正",
                    "0.3",
                ),
                _weighted(
                    "timing_daily_return",
                    "return_1d",
                    ConditionOperator.GT,
                    "0",
                    "最近一日收益为正",
                    "0.2",
                ),
                _weighted(
                    "timing_relative_strength",
                    "excess_total_return",
                    ConditionOperator.GT,
                    "0",
                    "跑赢比较基准",
                    "0.3",
                ),
                _weighted(
                    "timing_drawdown_score",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.10",
                    "区间回撤控制在 10% 以内",
                    "0.2",
                ),
            ),
            passing_score=Decimal("0.6"),
            signal_on_pass="timing_supportive",
            signal_on_fail="timing_not_supportive",
            limitations=(
                "只用于短期择时，不说明内在价值",
                "未经回测",
            ),
        ),
        StrategySpec(
            strategy_id="catalyst_repricing",
            name="中短期催化重估规则",
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
                    "估值须处于可评估区间",
                    upper_threshold=Decimal("40"),
                ),
            ),
            vetoes=(
                MetricCondition(
                    "catalyst_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.30"),
                    "价格损伤超出催化风险边界",
                ),
            ),
            score_rules=(
                _weighted(
                    "catalyst_momentum",
                    "momentum_5",
                    ConditionOperator.GT,
                    "0",
                    "催化获得价格正向确认",
                    "0.35",
                ),
                _weighted(
                    "catalyst_relative",
                    "excess_total_return",
                    ConditionOperator.GT,
                    "0",
                    "催化期间跑赢基准",
                    "0.35",
                ),
                _weighted(
                    "catalyst_value",
                    "pe_ttm",
                    ConditionOperator.LTE,
                    "25",
                    "估值尚未充分重估",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="catalyst_research_supportive",
            signal_on_fail="catalyst_not_confirmed",
            limitations=(
                "需要明确的催化证据并复查到期时间",
                "未经回测",
            ),
        ),
        StrategySpec(
            strategy_id="quality_value",
            name="中长期质量与估值规则",
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
                            "ROIC 不低于 10%",
                        ),
                        MetricCondition(
                            "quality_value_fcf_gate",
                            "fcf_yield",
                            ConditionOperator.GT,
                            Decimal("0"),
                            "自由现金流收益率须为正",
                        ),
                    ),
                    description="质量与现金流硬性门槛",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "quality_value_leverage_veto",
                    "debt_to_assets",
                    ConditionOperator.GT,
                    Decimal("0.70"),
                    "杠杆过高否决",
                ),
            ),
            score_rules=(
                _weighted(
                    "quality_value_roe",
                    "roe",
                    ConditionOperator.GTE,
                    "0.15",
                    "ROE 不低于 15%",
                    "0.25",
                ),
                _weighted(
                    "quality_value_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.15",
                    "ROIC 不低于 15%",
                    "0.25",
                ),
                _weighted(
                    "quality_value_growth",
                    "revenue_growth",
                    ConditionOperator.GT,
                    "0",
                    "营业收入增长",
                    "0.20",
                ),
                _weighted(
                    "quality_value_pe",
                    "pe_ttm",
                    ConditionOperator.LTE,
                    "30",
                    "市盈率不高于 30 倍",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="quality_value_research_supportive",
            signal_on_fail="quality_or_value_insufficient",
            limitations=(
                "阈值是初始研究假设",
                "未经回测，也未按同行标准化",
            ),
        ),
        StrategySpec(
            strategy_id="business_thesis_check",
            name="长期商业论点就绪度规则",
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
                    "长期论点要求自由现金流收益率为正",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "thesis_leverage_veto",
                    "debt_to_assets",
                    ConditionOperator.GT,
                    Decimal("0.70"),
                    "资产负债表脆弱否决",
                ),
                MetricCondition(
                    "thesis_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.60"),
                    "历史资本损失极端，需否决",
                ),
            ),
            score_rules=(
                _weighted(
                    "thesis_roe",
                    "roe",
                    ConditionOperator.GTE,
                    "0.15",
                    "股东回报具备持续潜力",
                    "0.30",
                ),
                _weighted(
                    "thesis_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.15",
                    "资本配置回报较强",
                    "0.40",
                ),
                _weighted(
                    "thesis_fcf",
                    "fcf_yield",
                    ConditionOperator.GTE,
                    "0.03",
                    "自由现金流收益率为正且不低于 3%",
                    "0.30",
                ),
            ),
            passing_score=Decimal("0.70"),
            signal_on_pass="thesis_evidence_ready",
            signal_on_fail="thesis_evidence_not_ready",
            limitations=(
                "只检查论点证据是否就绪，不证明内在价值",
                "护城河与管理层等定性证据仍需人工复查",
                "未经回测",
            ),
        ),
    )


def exchange_fund_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return profile-specific ETF research rules."""

    return (
        StrategySpec(
            strategy_id="etf_tracking_efficiency",
            name="中长期 ETF 跟踪效率规则",
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
                    "ETF 折溢价须在 2% 以内",
                    upper_threshold=Decimal("0.02"),
                ),
                MetricCondition(
                    "etf_expense_gate",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    Decimal("0.01"),
                    "年综合费率不高于 1%",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "etf_tracking_error_veto",
                    "tracking_error",
                    ConditionOperator.GT,
                    Decimal("0.15"),
                    "年化跟踪误差超过 15%",
                ),
            ),
            score_rules=(
                _weighted(
                    "etf_tracking_quality",
                    "tracking_error",
                    ConditionOperator.LTE,
                    "0.05",
                    "年化跟踪误差不高于 5%",
                    "0.35",
                ),
                _weighted(
                    "etf_cost_quality",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    "0.005",
                    "年综合费率不高于 0.5%",
                    "0.25",
                ),
                _weighted(
                    "etf_drawdown_quality",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.30",
                    "区间回撤不差于 -30%",
                    "0.20",
                ),
                _weighted(
                    "etf_relative_quality",
                    "excess_total_return",
                    ConditionOperator.GTE,
                    "-0.10",
                    "区间超额收益不差于 -10%",
                    "0.20",
                ),
            ),
            passing_score=Decimal("0.60"),
            signal_on_pass="etf_tracking_research_supportive",
            signal_on_fail="etf_tracking_efficiency_insufficient",
            limitations=(
                "只评估 ETF 工具效率，不判断标的资产方向",
                "折溢价是单一时点快照",
                "阈值是研究假设，未经回测",
            ),
        ),
    )


def financial_institution_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return bank-specific medium-long quality and risk rules."""

    return (
        StrategySpec(
            strategy_id="bank_quality_risk",
            name="中长期银行质量与风险规则",
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
                    "资本充足率不低于 10%",
                ),
                MetricCondition(
                    "bank_npl_gate",
                    "non_performing_loan_ratio",
                    ConditionOperator.LTE,
                    Decimal("0.03"),
                    "不良贷款率不高于 3%",
                ),
                MetricCondition(
                    "bank_pb_gate",
                    "pb",
                    ConditionOperator.BETWEEN,
                    Decimal("0"),
                    "市净率须处于可评估区间",
                    upper_threshold=Decimal("3"),
                ),
            ),
            vetoes=(
                MetricCondition(
                    "bank_drawdown_veto",
                    "max_drawdown",
                    ConditionOperator.LT,
                    Decimal("-0.50"),
                    "区间回撤超过 50%",
                ),
            ),
            score_rules=(
                _weighted(
                    "bank_roe_quality",
                    "roe",
                    ConditionOperator.GTE,
                    "0.08",
                    "报告期 ROE 不低于 8%",
                    "0.25",
                ),
                _weighted(
                    "bank_margin_quality",
                    "net_interest_margin",
                    ConditionOperator.GTE,
                    "0.015",
                    "净息差不低于 1.5%",
                    "0.25",
                ),
                _weighted(
                    "bank_asset_quality",
                    "non_performing_loan_ratio",
                    ConditionOperator.LTE,
                    "0.02",
                    "不良贷款率不高于 2%",
                    "0.30",
                ),
                _weighted(
                    "bank_valuation_quality",
                    "pb",
                    ConditionOperator.LTE,
                    "1.5",
                    "市净率不高于 1.5 倍",
                    "0.20",
                ),
            ),
            passing_score=Decimal("0.65"),
            signal_on_pass="bank_quality_research_supportive",
            signal_on_fail="bank_quality_or_risk_insufficient",
            limitations=(
                "银行专用研究规则，不使用 EV/EBITDA 与自由现金流收益率",
                "中期 ROE 按披露的年初累计值 × 12 ÷ 月数年化，四季度常低于前三季度年化值",
                "阈值是研究假设，未经回测",
            ),
        ),
    )


def high_growth_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return the medium-long rule for high-growth unprofitable companies.

    Design notes: docs/evidence/strategy_sources/growth_runway.md.
    Runway is expressed as cash_burn_ratio (burn / cash) rather than cash_runway_years because the
    engine treats any missing metric as a blocking gap, and runway years is not applicable when the
    company does not burn cash; burn ratio is always computable (negative when cash flow is positive).
    """

    return (
        StrategySpec(
            strategy_id="growth_runway_check",
            name="中长期高成长未盈利就绪度规则",
            version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            supported_profiles=(PROFILE_HIGH_GROWTH_UNPROFITABLE,),
            horizons=(ResearchHorizon.MEDIUM_LONG,),
            required_metrics=(
                "revenue_growth",
                "gross_margin",
                "cash_burn_ratio",
                "ps_ttm",
                "debt_to_assets",
            ),
            required_evidence_types=("financial_statement",),
            data_window_days=730,
            valid_for_days=90,
            hard_filters=(
                MetricCondition(
                    "growth_revenue_gate",
                    "revenue_growth",
                    ConditionOperator.GTE,
                    Decimal("0.20"),
                    "营业收入同比不低于 20%",
                ),
                MetricCondition(
                    "growth_runway_gate",
                    "cash_burn_ratio",
                    ConditionOperator.LTE,
                    Decimal("0.67"),
                    "年度现金消耗不超过货币资金的 67%（现金跑道约 1.5 年以上）",
                ),
            ),
            vetoes=(
                MetricCondition(
                    "growth_runway_veto",
                    "cash_burn_ratio",
                    ConditionOperator.GT,
                    Decimal("1"),
                    "年度现金消耗超过货币资金（现金跑道不足一年）",
                ),
                MetricCondition(
                    "growth_leverage_veto",
                    "debt_to_assets",
                    ConditionOperator.GT,
                    Decimal("0.70"),
                    "杠杆过高否决",
                ),
            ),
            score_rules=(
                _weighted(
                    "growth_revenue_score",
                    "revenue_growth",
                    ConditionOperator.GTE,
                    "0.30",
                    "营业收入同比不低于 30%",
                    "0.30",
                ),
                _weighted(
                    "growth_margin_score",
                    "gross_margin",
                    ConditionOperator.GTE,
                    "0.40",
                    "毛利率不低于 40%",
                    "0.25",
                ),
                _weighted(
                    "growth_runway_score",
                    "cash_burn_ratio",
                    ConditionOperator.LTE,
                    "0.33",
                    "现金跑道约 3 年以上",
                    "0.25",
                ),
                _weighted(
                    "growth_valuation_score",
                    "ps_ttm",
                    ConditionOperator.LTE,
                    "10",
                    "市销率不高于 10 倍",
                    "0.20",
                ),
            ),
            passing_score=Decimal("0.6"),
            signal_on_pass="growth_runway_research_supportive",
            signal_on_fail="growth_or_runway_insufficient",
            limitations=(
                "阈值是初始研究假设，未经回测",
                "现金消耗按自由现金流计，货币资金不含理财类金融资产，跑道偏保守",
                "营收为零或上年同期为零的公司无法计算增速，规则不适用",
            ),
        ),
    )


def cycle_strategy_specs() -> tuple[StrategySpec, ...]:
    """Return long-horizon commodity-cycle research rules."""

    return (
        StrategySpec(
            strategy_id="resource_cycle_readiness",
            name="长期资源周期就绪度规则",
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
                    "市净率须处于可评估区间",
                    upper_threshold=Decimal("5"),
                ),
                MetricCondition(
                    "resource_sensitivity_gate",
                    "commodity_sensitivity",
                    ConditionOperator.GT,
                    Decimal("0"),
                    "股价收益须保持对商品价格的正向敏感",
                ),
            ),
            score_rules=(
                _weighted(
                    "resource_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.08",
                    "ROIC 不低于 8%",
                    "0.4",
                ),
                _weighted(
                    "resource_drawdown",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.40",
                    "区间回撤不差于 -40%",
                    "0.3",
                ),
                _weighted(
                    "resource_sensitivity",
                    "commodity_sensitivity",
                    ConditionOperator.GTE,
                    "0.5",
                    "商品收益 Beta 不低于 0.5",
                    "0.3",
                ),
            ),
            passing_score=Decimal("0.6"),
            limitations=(
                "敏感度是历史联动，不代表因果",
                "铜/金权重是显式研究假设",
                "不预测商品价格",
            ),
        ),
        StrategySpec(
            strategy_id="gold_etf_cycle_vehicle",
            name="长期黄金 ETF 周期工具就绪度规则",
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
                    "ETF 须保持对金价的正向敏感",
                ),
                MetricCondition(
                    "gold_etf_premium_gate",
                    "premium_discount",
                    ConditionOperator.BETWEEN,
                    Decimal("-0.02"),
                    "折溢价须在 2% 以内",
                    upper_threshold=Decimal("0.02"),
                ),
            ),
            score_rules=(
                _weighted(
                    "gold_etf_sensitivity",
                    "commodity_sensitivity",
                    ConditionOperator.GTE,
                    "0.5",
                    "金价收益 Beta 不低于 0.5",
                    "0.4",
                ),
                _weighted(
                    "gold_etf_expense",
                    "expense_ratio",
                    ConditionOperator.LTE,
                    "0.01",
                    "年综合费率不高于 1%",
                    "0.3",
                ),
                _weighted(
                    "gold_etf_drawdown",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.40",
                    "区间回撤不差于 -40%",
                    "0.3",
                ),
            ),
            passing_score=Decimal("0.6"),
            limitations=(
                "只评估工具就绪度，不判断未来金价方向",
                "宏观与实际利率证据仍需复查",
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
        *high_growth_strategy_specs(),
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
