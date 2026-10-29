"""Reviewed external evidence for the backtest significance method (stage 9 slice 5).

Audit text: docs/evidence/strategy_sources/deflated_sharpe.md.
"""

from __future__ import annotations

from src.strategy import StrategyEvidenceAssessment, StrategyEvidenceGate, StrategyEvidenceRecord

METHOD_ID = "deflated_sharpe_significance"
MECHANISM_IDS = (
    "probabilistic_sharpe_ratio",
    "non_normal_sharpe_variance",
    "expected_maximum_sharpe",
    "deflated_sharpe_ratio",
    "minimum_track_record_length",
    "minimum_backtest_length",
)


def _paper(evidence_id, title, doi, roles, mechanisms, borrow, applicability, biases):
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=METHOD_ID,
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
        prohibited_transfers=("numerical examples",),
        applicability=applicability,
        known_biases=biases,
    )


def reviewed_evidence() -> tuple[StrategyEvidenceRecord, ...]:
    return (
        _paper(
            "dsr:bailey-lopez-de-prado-2014",
            "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality",
            "10.3905/jpm.2014.40.5.094",
            ("mechanism",),
            ("expected_maximum_sharpe", "deflated_sharpe_ratio", "non_normal_sharpe_variance"),
            ("expected maximum Sharpe of N skill-less trials", "PSR against that maximum"),
            "Main significance method.",
            ("only registered trials can be counted",),
        ),
        _paper(
            "dsr:bailey-lopez-de-prado-2012",
            "The Sharpe Ratio Efficient Frontier",
            "10.21314/JOR.2012.255",
            ("mechanism",),
            ("probabilistic_sharpe_ratio", "non_normal_sharpe_variance", "minimum_track_record_length"),
            ("Sharpe estimator variance with skew and kurtosis", "minimum track record length"),
            "Formula source for PSR and minTRL.",
            ("iid returns assumed",),
        ),
        _paper(
            "dsr:bailey-borwein-lopez-de-prado-zhu-2014",
            "Pseudo-Mathematics and Financial Charlatanism",
            "10.1090/noti1105",
            ("mechanism", "validation"),
            ("minimum_backtest_length", "expected_maximum_sharpe"),
            ("minimum backtest length grows with the number of trials",),
            "Judges whether the sample is long enough for the trial count.",
            ("independent trials and zero true Sharpe under the null",),
        ),
        StrategyEvidenceRecord(
            evidence_id="dsr:rubenbriones-psr",
            strategy_id=METHOD_ID,
            source_kind="repository",
            title="rubenbriones/Probabilistic-Sharpe-Ratio",
            canonical_url="https://github.com/rubenbriones/Probabilistic-Sharpe-Ratio/tree/7c65ea513df575a93a3a474b6e3c2a0a9e724607",
            immutable_ref="commit:7c65ea513df575a93a3a474b6e3c2a0a9e724607",
            retrieved_at="2026-10-29",
            license_spdx=None,
            license_status="unknown",
            reuse_policy="reference_only",
            evidence_roles=("code",),
            mechanism_ids=MECHANISM_IDS[:5],
            reviewed_paths=("src/sharpe_ratio_stats.py",),
            borrowable_mechanisms=("cross-check formulas against an independent implementation",),
            prohibited_transfers=("copying or adapting unlicensed code",),
            applicability="Formula cross-check only; the project implementation is independent.",
            known_biases=("example repository with limited tests",),
        ),
        _paper(
            "dsr:harvey-liu-zhu-2016",
            "... and the Cross-Section of Expected Returns",
            "10.1093/rfs/hhv059",
            ("counterevidence",),
            ("expected_maximum_sharpe", "deflated_sharpe_ratio"),
            ("multiple-testing thresholds far above conventional ones",),
            "The deflation is a lower bound: unregistered trials are not counted.",
            ("cross-sectional factor setting",),
        ),
    )


def assess_evidence() -> StrategyEvidenceAssessment:
    return StrategyEvidenceGate.assess(METHOD_ID, MECHANISM_IDS, reviewed_evidence())
