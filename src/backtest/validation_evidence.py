"""Reviewed external evidence for rolling out-of-sample and parameter-neighbourhood validation (stage 9 slice 6).

Audit text: docs/evidence/strategy_sources/rolling_oos_neighbourhood.md.
"""

from __future__ import annotations

from src.strategy import StrategyEvidenceAssessment, StrategyEvidenceGate, StrategyEvidenceRecord

METHOD_ID = "rolling_oos_neighbourhood"
MECHANISM_IDS = (
    "expanding_window_oos",
    "embargo_gap",
    "oos_benchmark_comparison",
    "selection_overfitting",
    "parameter_neighbourhood",
    "sharpe_difference_test",
)


def _paper(evidence_id, title, doi, roles, mechanisms, borrow, applicability, biases, license_status="restricted"):
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
        prohibited_transfers=("models and numerical results",),
        applicability=applicability,
        known_biases=biases,
    )


def _repository(evidence_id, title, url, commit, spdx, policy, mechanisms, paths, borrow, prohibit):
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=METHOD_ID,
        source_kind="repository",
        title=title,
        canonical_url=f"{url}/tree/{commit}",
        immutable_ref=f"commit:{commit}",
        retrieved_at="2026-10-29",
        license_spdx=spdx,
        license_status="verified",
        reuse_policy=policy,
        evidence_roles=("code",),
        mechanism_ids=mechanisms,
        reviewed_paths=paths,
        borrowable_mechanisms=borrow,
        prohibited_transfers=prohibit,
        applicability="Implementation cross-check; the project code is independent and dependency-free.",
        known_biases=("not designed for trading costs or position carry-over",),
    )


def reviewed_evidence() -> tuple[StrategyEvidenceRecord, ...]:
    return (
        _paper(
            "oos:bergmeir-benitez-2012", "On the use of cross-validation for time series predictor evaluation",
            "10.1016/j.ins.2011.12.028", ("mechanism",), ("expanding_window_oos", "embargo_gap"),
            ("time-ordered splits", "gaps between adjacent segments"), "Source of expanding windows and gaps.",
            ("forecast errors rather than trading returns",),
        ),
        _paper(
            "oos:bailey-et-al-pbo", "The Probability of Backtest Overfitting", "10.2139/ssrn.2326253",
            ("mechanism", "validation"), ("selection_overfitting", "oos_benchmark_comparison"),
            ("in-sample optimum tends to fall below median out of sample",),
            "Why only out-of-sample segments against a simple benchmark are judged.",
            ("needs many configurations",), license_status="unknown",
        ),
        _paper(
            "oos:white-2000", "A Reality Check for Data Snooping", "10.1111/1468-0262.00152",
            ("mechanism", "validation"), ("oos_benchmark_comparison", "selection_overfitting", "parameter_neighbourhood"),
            ("judge the best rule against a benchmark and the whole specification set",),
            "Supports benchmark comparison and looking at the neighbourhood rather than a point.",
            ("specification set defined by the researcher",),
        ),
        _repository(
            "oos:sklearn-timeseriessplit", "scikit-learn TimeSeriesSplit", "https://github.com/scikit-learn/scikit-learn",
            "d685424bec8f8585d8017c1aab1db7535d3fa03d", "BSD-3-Clause", "adapt_allowed",
            ("expanding_window_oos", "embargo_gap"), ("sklearn/model_selection/_split.py", "COPYING"),
            ("equal test segments", "training ends gap samples before the test"), ("numpy dependency",),
        ),
        _repository(
            "oos:backtesting-py-heatmap", "backtesting.py optimisation heatmaps", "https://github.com/kernc/backtesting.py",
            "ca2e2611621e472542ba90f7243a1fa06a7d7108", "AGPL-3.0", "reference_only",
            ("parameter_neighbourhood",), ("backtesting/lib.py", "LICENSE.md"),
            ("inspect results around the optimum, not only the optimum",), ("copying or depending on AGPL code",),
        ),
        _paper(
            "oos:jobson-korkie-1981", "Performance Hypothesis Testing with the Sharpe and Treynor Measures",
            "10.1111/j.1540-6261.1981.tb04891.x", ("mechanism",), ("sharpe_difference_test", "parameter_neighbourhood"),
            ("paired Sharpe difference test for correlated series",),
            "A neighbour is worse only when its Sharpe is significantly lower than the registered parameters'.",
            ("iid normal returns assumed",),
        ),
        _paper(
            "oos:ledoit-wolf-2008", "Robust performance hypothesis testing with the Sharpe ratio",
            "10.1016/j.jempfin.2008.03.002", ("counterevidence",), ("sharpe_difference_test",),
            ("Jobson-Korkie is unreliable under heavy tails or serial correlation",),
            "The sensitivity flag is a screen, not a proof.", ("heavier computation for robust tests",),
        ),
        _paper(
            "oos:inoue-kilian-2005", "In-Sample or Out-of-Sample Tests of Predictability: Which One Should We Use?",
            "10.1081/etc-200040785", ("counterevidence",), ("expanding_window_oos", "oos_benchmark_comparison"),
            ("out-of-sample tests have lower power; split points matter",),
            "Out-of-sample is a not-worse-than-benchmark gate, reported per segment with its split settings.",
            ("regression forecasts",),
        ),
    )


def assess_evidence() -> StrategyEvidenceAssessment:
    return StrategyEvidenceGate.assess(METHOD_ID, MECHANISM_IDS, reviewed_evidence())
