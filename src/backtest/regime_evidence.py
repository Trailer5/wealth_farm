"""Reviewed external evidence for market-state performance and the Sharpe difference test (stage 9 slice 7).

Audit text: docs/evidence/strategy_sources/market_regimes.md.
"""

from __future__ import annotations

from src.strategy import StrategyEvidenceAssessment, StrategyEvidenceGate, StrategyEvidenceRecord

METHOD_ID = "market_regime_performance"
MECHANISM_IDS = (
    "trend_state",
    "volatility_state",
    "valuation_state",
    "state_conditional_performance",
    "sharpe_difference_test",
)


def _paper(evidence_id, title, doi, roles, mechanisms, borrow, applicability, license_status="restricted"):
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=METHOD_ID,
        source_kind="paper",
        title=title,
        canonical_url=f"https://doi.org/{doi}",
        immutable_ref=f"doi:{doi}",
        retrieved_at="2026-10-29",
        license_spdx=None,
        license_status=license_status,
        reuse_policy="reference_only",
        evidence_roles=roles,
        mechanism_ids=mechanisms,
        reviewed_paths=(),
        borrowable_mechanisms=borrow,
        prohibited_transfers=("market-specific parameters and results",),
        applicability=applicability,
        known_biases=("non-A-share samples",),
    )


def reviewed_evidence() -> tuple[StrategyEvidenceRecord, ...]:
    return (
        _paper(
            "regime:faber-qtaa", "A Quantitative Approach to Tactical Asset Allocation", "10.3905/jwm.2007.674809",
            ("mechanism",), ("trend_state",), ("price against a long moving average separates trend states",),
            "Trend dimension.",
        ),
        _paper(
            "regime:moreira-muir-2017", "Volatility-Managed Portfolios", "10.1111/jofi.12513", ("mechanism",),
            ("volatility_state",), ("risk-return trade-off differs between high and low volatility",),
            "Volatility dimension.",
        ),
        _paper(
            "regime:campbell-shiller-1998", "Valuation Ratios and the Long-Run Stock Market Outlook",
            "10.3905/jpm.24.2.11", ("mechanism",), ("valuation_state",),
            ("valuation relative to its own history relates to later returns",), "Valuation dimension.",
        ),
        _paper(
            "regime:jobson-korkie-1981", "Performance Hypothesis Testing with the Sharpe and Treynor Measures",
            "10.1111/j.1540-6261.1981.tb04891.x", ("mechanism",), ("sharpe_difference_test",),
            ("asymptotic test of the Sharpe difference of two correlated return series",),
            "State-versus-benchmark and neighbour-versus-registered comparisons.",
        ),
        StrategyEvidenceRecord(
            evidence_id="regime:pyfolio-periods",
            strategy_id=METHOD_ID,
            source_kind="repository",
            title="quantopian/pyfolio period statistics",
            canonical_url="https://github.com/quantopian/pyfolio/tree/4b901f6d73aa02ceb6d04b7d83502e5c6f2e81aa",
            immutable_ref="commit:4b901f6d73aa02ceb6d04b7d83502e5c6f2e81aa",
            retrieved_at="2026-10-29",
            license_spdx="Apache-2.0",
            license_status="verified",
            reuse_policy="adapt_allowed",
            evidence_roles=("code",),
            mechanism_ids=("state_conditional_performance", "volatility_state"),
            reviewed_paths=("pyfolio/interesting_periods.py", "pyfolio/timeseries.py", "LICENSE"),
            borrowable_mechanisms=("group returns by period and summarise each", "annualised rolling volatility"),
            prohibited_transfers=("hand-picked ex-post date ranges",),
            applicability="Grouping code reference; states here come from daily point-in-time rules.",
            known_biases=("ex-post period selection",),
        ),
        _paper(
            "regime:ang-timmermann", "Regime Changes and Financial Markets", "10.3386/w17182", ("counterevidence",),
            ("state_conditional_performance", "trend_state", "volatility_state"),
            ("states are unobserved and few independent episodes make conditional results unstable",),
            "Report days and separate episodes; thin states are listed but not read.", license_status="unknown",
        ),
        _paper(
            "regime:ledoit-wolf-2008", "Robust performance hypothesis testing with the Sharpe ratio",
            "10.1016/j.jempfin.2008.03.002", ("counterevidence",), ("sharpe_difference_test",),
            ("Jobson-Korkie is unreliable under heavy tails or serial correlation",),
            "Significance is a ranking aid; reports state the test assumptions.",
        ),
    )


def assess_evidence() -> StrategyEvidenceAssessment:
    return StrategyEvidenceGate.assess(METHOD_ID, MECHANISM_IDS, reviewed_evidence())
