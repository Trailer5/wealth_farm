"""Reviewed external evidence for the strategy templates (stage 9 slice 4).

Audit text for each record: docs/evidence/strategy_sources/ (ma_timing.md, etf_dual_momentum.md,
valuation_timing.md, factor_selection.md, rebalance_staged_entry.md). A template can only be
instantiated when its gate is implementation_ready.
"""

from __future__ import annotations

from .evidence import StrategyEvidenceAssessment, StrategyEvidenceGate, StrategyEvidenceRecord

BT_URL = "https://github.com/pmorissette/bt/tree/5dbfbedb24ec83d7a43c8d55741b72b1642a62a6"
BT_REF = "commit:5dbfbedb24ec83d7a43c8d55741b72b1642a62a6"

TEMPLATE_MECHANISMS: dict[str, tuple[str, ...]] = {
    "ma_timing": ("price_above_moving_average", "state_switch_to_cash", "next_bar_execution"),
    "dual_momentum_rotation": (
        "relative_momentum_ranking",
        "absolute_momentum_filter",
        "defensive_fallback",
        "lagged_periodic_rebalance",
        "inverse_volatility_weighting",
    ),
    "valuation_percentile_timing": (
        "valuation_mean_reversion",
        "trailing_percentile_rank",
        "hysteresis_thresholds",
        "point_in_time_valuation",
    ),
    "factor_selection": ("value_ranking", "quality_screen", "point_in_time_fundamentals", "top_n_equal_weight"),
    "fixed_weight_rebalance": ("constant_mix_rebalance", "calendar_schedule"),
    "staged_entry": ("staged_capital_deployment", "calendar_schedule"),
}


def _paper(template_id, evidence_id, title, doi, roles, mechanisms, borrow, prohibit, applicability, biases):
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=template_id,
        source_kind="paper",
        title=title,
        canonical_url=f"https://doi.org/{doi}",
        immutable_ref=f"doi:{doi}",
        retrieved_at="2026-10-29",
        license_spdx=None,
        license_status="restricted",
        reuse_policy="reference_only",
        evidence_roles=roles,
        mechanism_ids=mechanisms,
        reviewed_paths=(),
        borrowable_mechanisms=borrow,
        prohibited_transfers=prohibit,
        applicability=applicability,
        known_biases=biases,
    )


def _bt(template_id, evidence_id, mechanisms, paths, borrow, prohibit):
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=template_id,
        source_kind="repository",
        title="pmorissette/bt algos",
        canonical_url=BT_URL,
        immutable_ref=BT_REF,
        retrieved_at="2026-10-29",
        license_spdx="MIT",
        license_status="verified",
        reuse_policy="adapt_allowed",
        evidence_roles=("code",),
        mechanism_ids=mechanisms,
        reviewed_paths=(*paths, "LICENSE"),
        borrowable_mechanisms=borrow,
        prohibited_transfers=prohibit,
        applicability="Code reference for schedule, selection and rebalance layering; A-share rules are project-owned.",
        known_biases=("generic framework without price limits, suspensions or stamp duty",),
    )


_ZAKAMULIN = ("Real-life performance of market timing with moving average and time-series momentum rules", "10.1057/jam.2014.25")


def _records() -> tuple[StrategyEvidenceRecord, ...]:
    return (
        _paper(
            "ma_timing", "ma-timing:faber-qtaa", "A Quantitative Approach to Tactical Asset Allocation",
            "10.3905/jwm.2007.674809", ("mechanism", "validation"),
            ("price_above_moving_average", "state_switch_to_cash"),
            ("hold above the long moving average, cash below", "trade only on state changes"),
            ("10-month window and US asset classes",), "Mechanism origin of moving-average timing.",
            ("US sample", "low cost assumptions"),
        ),
        _bt(
            "ma_timing", "ma-timing:bt-selectwhere", ("state_switch_to_cash", "next_bar_execution"),
            ("bt/algos.py:SelectWhere", "bt/algos.py:Rebalance"),
            ("indicator state separated from execution", "close positions missing from targets"),
            ("same-bar execution",),
        ),
        _paper(
            "ma_timing", "ma-timing:zakamulin-2014", *_ZAKAMULIN, ("counterevidence",),
            ("price_above_moving_average", "next_bar_execution"),
            ("re-test with executable timing against buy and hold",), ("generalising one market to all rules",),
            "Requires out-of-sample and cost-aware comparison.", ("US indices",),
        ),
        _paper(
            "dual_momentum_rotation", "dual-momentum:antonacci", "Risk Premia Harvesting Through Dual Momentum",
            "10.2139/ssrn.2042750", ("mechanism", "validation"),
            ("relative_momentum_ranking", "absolute_momentum_filter", "defensive_fallback"),
            ("relative ranking then absolute filter with defensive fallback",), ("US asset modules and results",),
            "Mechanism origin of dual momentum.", ("market differences", "parameter selection"),
        ),
        _bt(
            "dual_momentum_rotation", "dual-momentum:bt-invvol",
            ("inverse_volatility_weighting", "lagged_periodic_rebalance", "relative_momentum_ranking"),
            ("bt/algos.py:WeighInvVol", "bt/algos.py:RunMonthly", "bt/algos.py:SelectMomentum", "bt/algos.py:Rebalance"),
            ("inverse volatility weights", "momentum ranking over lookbacks", "monthly schedule"),
            ("default windows", "same-bar execution"),
        ),
        _paper(
            "dual_momentum_rotation", "dual-momentum:zakamulin-2014", *_ZAKAMULIN, ("counterevidence",),
            ("absolute_momentum_filter", "lagged_periodic_rebalance"),
            ("separate executable signals from literature backtests",), ("generalising to all dual momentum",),
            "Requires lag-aware validation against buy and hold.", ("different asset set",),
        ),
        _paper(
            "valuation_percentile_timing", "valuation:campbell-shiller-1998",
            "Valuation Ratios and the Long-Run Stock Market Outlook", "10.3905/jpm.24.2.11",
            ("mechanism", "validation"), ("valuation_mean_reversion", "trailing_percentile_rank"),
            ("high valuation implies low long-run returns", "judge valuation against its own history"),
            ("US CAPE levels and coefficients", "monthly in-and-out trading"),
            "Supports valuation position for medium and long horizons.", ("US sample",),
        ),
        _bt(
            "valuation_percentile_timing", "valuation:bt-selectwhere",
            ("hysteresis_thresholds", "point_in_time_valuation"),
            ("bt/algos.py:SelectWhere", "bt/algos.py:RunMonthly", "bt/algos.py:Rebalance"),
            ("external indicator drives holdings", "point-in-time warning for fundamental signals"),
            ("publication dates are ignored by bt",),
        ),
        _paper(
            "valuation_percentile_timing", "valuation:goyal-welch-2008",
            "A Comprehensive Look at the Empirical Performance of Equity Premium Prediction", "10.1093/rfs/hhm014",
            ("counterevidence",), ("valuation_mean_reversion", "trailing_percentile_rank"),
            ("valuation predictors often fail out of sample",), ("claiming A-share valuation timing is useless",),
            "Requires rolling out-of-sample results not worse than buy and hold.", ("US sample", "regression forecasts"),
        ),
        _paper(
            "factor_selection", "factor:fama-french-1993", "Common Risk Factors in the Returns on Stocks and Bonds",
            "10.1016/0304-405X(93)90023-5", ("mechanism",), ("value_ranking",),
            ("cheap stocks by valuation ratios earn more over the long run",), ("US premia and breakpoints",),
            "Supports value ranking.", ("US sample",),
        ),
        _paper(
            "factor_selection", "factor:liu-stambaugh-yuan-2019", "Size and Value in China",
            "10.1016/j.jfineco.2019.03.008", ("mechanism", "validation"), ("value_ranking", "point_in_time_fundamentals"),
            ("earnings yield is the better A-share value measure", "use reports as announced"),
            ("reported factor returns",), "Direct A-share value evidence.", ("sample ends 2016", "shell value"),
        ),
        _paper(
            "factor_selection", "factor:asness-frazzini-pedersen-qmj", "Quality Minus Junk",
            "10.1007/s11142-018-9470-2", ("mechanism",), ("quality_screen",),
            ("quality as profitability, growth and safety",), ("composite score construction",),
            "Supports screening on quality before ranking on value.", ("US and developed markets",),
        ),
        _bt(
            "factor_selection", "factor:bt-selectn", ("top_n_equal_weight", "point_in_time_fundamentals"),
            ("bt/algos.py:SetStat", "bt/algos.py:SelectN", "bt/algos.py:WeighEqually", "bt/algos.py:SelectWhere"),
            ("drop missing values before ranking", "rank, select and weight as separate steps"),
            ("fixed lag instead of announcement dates",),
        ),
        _paper(
            "factor_selection", "factor:mclean-pontiff-2016", "Does Academic Research Destroy Stock Return Predictability?",
            "10.1111/jofi.12365", ("counterevidence",), ("value_ranking", "quality_screen"),
            ("published predictors earn less out of sample and after publication",), ("US discount ratios",),
            "Requires attempt counts and out-of-sample checks.", ("US anomalies",),
        ),
        *(
            record
            for template_id in ("fixed_weight_rebalance", "staged_entry")
            for record in (
                _paper(
                    template_id, f"{template_id}:perold-sharpe-1988", "Dynamic Strategies for Asset Allocation",
                    "10.2469/faj.v44.n1.16", ("mechanism", "counterevidence"),
                    ("constant_mix_rebalance", "calendar_schedule", "staged_capital_deployment"),
                    ("constant mix wins in oscillating markets and loses in trends",),
                    ("treating constant mix as always better",), "Mechanism and failure regime of rebalancing.",
                    ("theoretical analysis",),
                ),
                _bt(
                    template_id, f"{template_id}:bt-rebalance",
                    ("constant_mix_rebalance", "calendar_schedule", "staged_capital_deployment"),
                    ("bt/algos.py:RunMonthly", "bt/algos.py:RunQuarterly", "bt/algos.py:RunYearly",
                     "bt/algos.py:WeighSpecified", "bt/algos.py:RebalanceOverTime", "bt/algos.py:CapitalFlow"),
                    ("period-change schedule", "fixed weights", "reach targets over n periods"),
                    ("CapitalFlow accounting with its documented return defect",),
                ),
            )
        ),
        _paper(
            "staged_entry", "staged-entry:constantinides-1979",
            "A Note on the Suboptimality of Dollar-Cost Averaging as an Investment Policy", "10.2307/2330513",
            ("counterevidence",), ("staged_capital_deployment",),
            ("pre-committed staging is dominated by adaptive policies",), ("forbidding staged entry",),
            "Requires a lump-sum comparison alongside every staged-entry backtest.", ("theoretical result",),
        ),
    )


def template_evidence(template_id: str) -> tuple[StrategyEvidenceRecord, ...]:
    return tuple(record for record in _records() if record.strategy_id == template_id)


def assess_template(template_id: str) -> StrategyEvidenceAssessment:
    return StrategyEvidenceGate.assess(template_id, TEMPLATE_MECHANISMS[template_id], template_evidence(template_id))
