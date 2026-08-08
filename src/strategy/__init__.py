"""Horizon-aware, explainable research strategy contracts."""

from .engine import (
    ConditionEvaluation,
    StrategyExecutor,
    evaluate_condition,
)
from .catalog import (
    HorizonStrategyReport,
    cycle_strategy_specs,
    default_strategy_registry,
    default_strategy_specs,
    exchange_fund_strategy_specs,
    execute_horizon_catalog,
    financial_institution_strategy_specs,
)
from .input_builder import StrategyInputBuilder
from .models import (
    ConditionGroup,
    ConditionLogic,
    ConditionOperator,
    ConditionOutcome,
    MetricCondition,
    StrategyInput,
    StrategyMaturity,
    StrategyMetricInput,
    StrategyResult,
    StrategyRunStatus,
    StrategySpec,
    WeightedCondition,
    condition_from_dict,
    condition_to_dict,
)
from .registry import StrategyRegistry

__all__ = [
    "ConditionEvaluation",
    "ConditionGroup",
    "ConditionLogic",
    "ConditionOperator",
    "ConditionOutcome",
    "HorizonStrategyReport",
    "MetricCondition",
    "StrategyExecutor",
    "StrategyInput",
    "StrategyInputBuilder",
    "StrategyMaturity",
    "StrategyMetricInput",
    "StrategyRegistry",
    "StrategyResult",
    "StrategyRunStatus",
    "StrategySpec",
    "WeightedCondition",
    "condition_from_dict",
    "condition_to_dict",
    "cycle_strategy_specs",
    "default_strategy_registry",
    "default_strategy_specs",
    "exchange_fund_strategy_specs",
    "financial_institution_strategy_specs",
    "execute_horizon_catalog",
    "evaluate_condition",
]
