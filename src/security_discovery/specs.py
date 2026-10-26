"""Default research rules for the first discovery funnel."""

from __future__ import annotations

from decimal import Decimal

from src.research_contracts import (
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.strategy import ConditionOperator

from .models import (
    DiscoveryCandidateTrack,
    DiscoveryRankMetric,
    DiscoveryRule,
    DiscoverySpec,
)


def default_quality_value_discovery_spec(
    *,
    universe_id: str,
    as_of_date: str,
    universe_version: str | None = None,
    benchmark_asset_id: str | None = None,
    max_candidates: int = 6,
    max_per_track: int = 2,
) -> DiscoverySpec:
    """Build the first medium/long quality-value discovery contract."""

    horizon = ResearchHorizon.MEDIUM_LONG
    hard_rules = (
        _rule(
            "quality_roe_floor",
            "roe",
            ConditionOperator.GTE,
            "0.08",
            "ROE must be at least 8%",
            horizon,
        ),
        _rule(
            "quality_leverage_cap",
            "debt_to_assets",
            ConditionOperator.LTE,
            "0.75",
            "Debt-to-assets must not exceed 75%",
            horizon,
        ),
        DiscoveryRule(
            rule_id="valuation_pe_range",
            metric_name="pe_ttm",
            operator=ConditionOperator.BETWEEN,
            threshold=Decimal("1"),
            upper_threshold=Decimal("40"),
            description="Positive PE must not exceed 40",
            horizon=horizon,
        ),
        _rule(
            "valuation_fcf_nonnegative",
            "fcf_yield",
            ConditionOperator.GTE,
            "0",
            "Free-cash-flow yield must not be negative",
            horizon,
        ),
        _rule(
            "relative_return_floor",
            "excess_total_return",
            ConditionOperator.GTE,
            "-0.30",
            "Relative return must not trail benchmark by over 30%",
            horizon,
        ),
    )
    tracks = (
        DiscoveryCandidateTrack(
            track_id="certainty",
            name="财务确定性",
            rules=(
                _rule(
                    "certainty_roe",
                    "roe",
                    ConditionOperator.GTE,
                    "0.15",
                    "ROE at least 15%",
                    horizon,
                ),
                _rule(
                    "certainty_roic",
                    "roic",
                    ConditionOperator.GTE,
                    "0.10",
                    "ROIC at least 10%",
                    horizon,
                ),
                _rule(
                    "certainty_leverage",
                    "debt_to_assets",
                    ConditionOperator.LTE,
                    "0.55",
                    "Debt-to-assets at most 55%",
                    horizon,
                ),
            ),
            rank_by=(
                DiscoveryRankMetric("roe", True),
                DiscoveryRankMetric("roic", True),
            ),
        ),
        DiscoveryCandidateTrack(
            track_id="valuation_elasticity",
            name="估值弹性",
            rules=(
                _rule(
                    "elasticity_pe",
                    "pe_ttm",
                    ConditionOperator.LTE,
                    "20",
                    "PE no higher than 20",
                    horizon,
                ),
                _rule(
                    "elasticity_fcf",
                    "fcf_yield",
                    ConditionOperator.GTE,
                    "0.03",
                    "Free-cash-flow yield at least 3%",
                    horizon,
                ),
                _rule(
                    "elasticity_not_extended",
                    "excess_total_return",
                    ConditionOperator.LTE,
                    "0.15",
                    "Relative return is not already overextended",
                    horizon,
                ),
            ),
            rank_by=(
                DiscoveryRankMetric("fcf_yield", True),
                DiscoveryRankMetric("pe_ttm", False),
            ),
        ),
        DiscoveryCandidateTrack(
            track_id="risk_resilience",
            name="风险韧性",
            rules=(
                _rule(
                    "resilience_drawdown",
                    "max_drawdown",
                    ConditionOperator.GTE,
                    "-0.25",
                    "Maximum drawdown no worse than -25%",
                    horizon,
                ),
                _rule(
                    "resilience_volatility",
                    "annualized_volatility",
                    ConditionOperator.LTE,
                    "0.40",
                    "Annualized volatility no higher than 40%",
                    horizon,
                ),
                _rule(
                    "resilience_relative",
                    "excess_total_return",
                    ConditionOperator.GTE,
                    "-0.10",
                    "Relative return no worse than -10%",
                    horizon,
                ),
            ),
            rank_by=(
                DiscoveryRankMetric("max_drawdown", True),
                DiscoveryRankMetric(
                    "annualized_volatility",
                    False,
                ),
            ),
        ),
    )
    return DiscoverySpec(
        discovery_id="quality_value_relative_funnel",
        name="质量—估值—相对表现发现漏斗",
        version="discovery_v1",
        universe_id=universe_id,
        universe_version=universe_version,
        as_of_date=as_of_date,
        horizon=horizon,
        question_type=ResearchQuestionType.INVESTMENT,
        profile_id=PROFILE_ORDINARY_COMPANY,
        required_metrics=(
            "roe",
            "roic",
            "debt_to_assets",
            "pe_ttm",
            "fcf_yield",
            "excess_total_return",
            "max_drawdown",
            "annualized_volatility",
        ),
        hard_rules=hard_rules,
        candidate_tracks=tracks,
        data_window_days=730,
        max_candidates=max_candidates,
        max_per_track=max_per_track,
        benchmark_asset_id=benchmark_asset_id,
        limitations=(
            "Discovery candidates are research leads, not buy advice.",
            "The funnel only uses the current universe snapshot.",
            "Banks and insurers need dedicated profile metrics before use.",
            "Thresholds are research rules and are not historically validated.",
        ),
    )


def _rule(
    rule_id: str,
    metric_name: str,
    operator: ConditionOperator,
    threshold: str,
    description: str,
    horizon: ResearchHorizon,
) -> DiscoveryRule:
    return DiscoveryRule(
        rule_id=rule_id,
        metric_name=metric_name,
        operator=operator,
        threshold=Decimal(threshold),
        description=description,
        horizon=horizon,
    )
