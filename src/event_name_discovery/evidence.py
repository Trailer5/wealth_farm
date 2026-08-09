"""Reviewed external evidence for event-name attention discovery."""

from __future__ import annotations

from src.strategy import (
    StrategyEvidenceAssessment,
    StrategyEvidenceGate,
    StrategyEvidenceRecord,
)


STRATEGY_ID = "event_name_attention_discovery_v1"
MECHANISM_IDS = (
    "similar_name_attention_spillover",
    "salient_event_abnormal_demand",
    "consensus_coordination",
    "short_horizon_reversal",
    "chinese_phonetic_matching",
    "cross_language_acronym_attention",
    "brand_name_association",
)


def reviewed_evidence() -> tuple[StrategyEvidenceRecord, ...]:
    common = {
        "strategy_id": STRATEGY_ID,
        "retrieved_at": "2026-08-09",
    }
    return (
        StrategyEvidenceRecord(
            evidence_id="event-name:balashov-nikiforov-2019",
            source_kind="paper",
            title=(
                "How much do investors trade because of "
                "name/ticker confusion?"
            ),
            canonical_url=(
                "https://doi.org/10.1016/j.finmar.2019.06.002"
            ),
            immutable_ref="doi:10.1016/j.finmar.2019.06.002",
            license_spdx=None,
            license_status="restricted",
            reuse_policy="reference_only",
            evidence_roles=("mechanism", "validation"),
            mechanism_ids=MECHANISM_IDS[:4],
            reviewed_paths=(),
            borrowable_mechanisms=(
                "similar-name candidate pairs",
                "turnover co-movement around salient events",
                "separate short-window price response and reversal",
            ),
            prohibited_transfers=(
                "US-market similarity thresholds",
                "reported effect sizes as A-share priors",
            ),
            applicability=(
                "Supports auditable name-confusion and event-window "
                "mechanisms, not A-share parameter values."
            ),
            known_biases=(
                "US market structure",
                "selected highly similar pairs",
                "intraday data unavailable in the current project",
            ),
            **common,
        ),
        StrategyEvidenceRecord(
            evidence_id="event-name:qiu-wu-zhang-2021",
            source_kind="paper",
            title=(
                "In name only: Information spillovers among Chinese "
                "firms with similar stock names"
            ),
            canonical_url=(
                "https://doi.org/10.1016/j.jcorpfin.2021.102015"
            ),
            immutable_ref="doi:10.1016/j.jcorpfin.2021.102015",
            license_spdx=None,
            license_status="restricted",
            reuse_policy="reference_only",
            evidence_roles=("mechanism", "validation"),
            mechanism_ids=MECHANISM_IDS[:3],
            reviewed_paths=(),
            borrowable_mechanisms=(
                "Chinese stock names as salient characteristics",
                "exclude genuine economic relations",
                "event study around the attention source",
            ),
            prohibited_transfers=(
                "earnings-announcement results to arbitrary world events",
            ),
            applicability=(
                "Direct China-market evidence for attention spillover "
                "between similarly named but unrelated firms."
            ),
            known_biases=(
                "earnings announcements only",
                "firm-to-firm names rather than event puns",
            ),
            **common,
        ),
        StrategyEvidenceRecord(
            evidence_id="event-name:python-pinyin-v0.55.0",
            source_kind="repository",
            title="python-pinyin",
            canonical_url="https://github.com/mozillazg/python-pinyin",
            immutable_ref="tag:v0.55.0",
            license_spdx="MIT",
            license_status="verified",
            reuse_policy="dependency_allowed",
            evidence_roles=("code",),
            mechanism_ids=(
                "chinese_phonetic_matching",
                "cross_language_acronym_attention",
            ),
            reviewed_paths=(
                "pypinyin/core.py",
                "pypinyin/standard.py",
                "LICENSE",
            ),
            borrowable_mechanisms=(
                "phrase-aware pinyin conversion",
                "tone-free forms",
                "first-letter forms",
            ),
            prohibited_transfers=(
                "treating phonetic equality as investment evidence",
                "using conversion output as a black-box candidate score",
            ),
            applicability=(
                "Optional deterministic phonetic candidate generation; "
                "selection remains project-owned and evidence-gated."
            ),
            known_biases=(
                "polyphonic character ambiguity",
                "spoken dialects not represented",
            ),
            **common,
        ),
        StrategyEvidenceRecord(
            evidence_id="event-name:corona-sales-2022",
            source_kind="paper",
            title=(
                "Product sales and unintentional name association "
                "with the coronavirus pandemic"
            ),
            canonical_url="https://doi.org/10.1002/jaa2.18",
            immutable_ref="doi:10.1002/jaa2.18",
            license_spdx=None,
            license_status="restricted",
            reuse_policy="reference_only",
            evidence_roles=("mechanism", "validation", "counterevidence"),
            mechanism_ids=("brand_name_association",),
            reviewed_paths=(),
            borrowable_mechanisms=(
                "separate attention and sentiment from realized sales",
                "use scanner data rather than media survey claims",
            ),
            prohibited_transfers=(
                "assuming negative brand buzz implies sales decline",
            ),
            applicability=(
                "Extends name association beyond securities and supplies "
                "counterevidence for the Corona beer sales narrative."
            ),
            known_biases=(
                "consumer product setting",
                "pandemic demand shocks affect the whole category",
            ),
            **common,
        ),
        StrategyEvidenceRecord(
            evidence_id="event-name:davies-hillier-thamm-2007",
            source_kind="paper",
            title="Investor confusion and similarly identified securities",
            canonical_url=(
                "https://doi.org/10.1111/j.1467-629x.2007.00223.x"
            ),
            immutable_ref="doi:10.1111/j.1467-629x.2007.00223.x",
            license_spdx=None,
            license_status="restricted",
            reuse_policy="reference_only",
            evidence_roles=("counterevidence",),
            mechanism_ids=MECHANISM_IDS[:4],
            reviewed_paths=(),
            borrowable_mechanisms=(
                "test whether effects are confined to very short horizons",
            ),
            prohibited_transfers=(
                "assuming similar names systematically create alpha",
            ),
            applicability=(
                "Counterevidence requiring candidate confirmation and "
                "market-specific validation."
            ),
            known_biases=("UK sample", "limited number of similar pairs"),
            **common,
        ),
    )


def assess_evidence() -> StrategyEvidenceAssessment:
    return StrategyEvidenceGate.assess(
        STRATEGY_ID,
        MECHANISM_IDS,
        reviewed_evidence(),
    )
