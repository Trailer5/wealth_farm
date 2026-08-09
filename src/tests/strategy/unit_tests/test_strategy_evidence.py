import unittest
from dataclasses import FrozenInstanceError, replace

from src.strategy import (
    StrategyEvidenceGate,
    StrategyEvidenceRecord,
)


STRATEGY_ID = "evidence_gate_test_v1"
MECHANISMS = ("relative_momentum", "absolute_momentum")


class StrategyEvidenceGateTest(unittest.TestCase):
    def test_complete_evidence_is_implementation_ready_and_frozen(self):
        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS,
            _ready_records(),
        )

        self.assertEqual(assessment.status, "implementation_ready")
        self.assertTrue(assessment.implementation_ready)
        self.assertEqual(assessment.blocking_reasons, ())
        self.assertEqual(
            assessment.reusable_repository_evidence_ids,
            ("repo-verified",),
        )
        with self.assertRaises(FrozenInstanceError):
            assessment.status = "blocked"

    def test_missing_paper_mechanism_source_is_blocked(self):
        records = tuple(
            record
            for record in _ready_records()
            if record.source_kind != "paper"
        )

        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS,
            records,
        )

        self.assertEqual(assessment.status, "blocked")
        self.assertIn(
            "paper_mechanism_source_missing",
            assessment.blocking_reasons,
        )

    def test_missing_reviewed_repository_source_is_blocked(self):
        records = tuple(
            record
            for record in _ready_records()
            if record.source_kind != "repository"
        )

        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS,
            records,
        )

        self.assertEqual(assessment.status, "blocked")
        self.assertIn(
            "reviewed_repository_source_missing",
            assessment.blocking_reasons,
        )

    def test_unlicensed_repository_can_inform_independent_implementation_only(
        self,
    ):
        reference_repository = _record(
            evidence_id="repo-unlicensed",
            source_kind="repository",
            license_status="unknown",
            reuse_policy="reference_only",
            license_spdx=None,
            evidence_roles=("code",),
            reviewed_paths=("strategy.py",),
            mechanism_ids=MECHANISMS,
        )
        records = (
            _ready_records()[0],
            reference_repository,
            _ready_records()[2],
        )

        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS,
            records,
        )

        self.assertEqual(assessment.status, "implementation_ready")
        self.assertEqual(
            assessment.repository_code_evidence_ids,
            ("repo-unlicensed",),
        )
        self.assertEqual(
            assessment.reusable_repository_evidence_ids,
            (),
        )
        with self.assertRaisesRegex(ValueError, "verified license"):
            replace(reference_repository, reuse_policy="adapt_allowed")

    def test_uncovered_mechanism_is_reported(self):
        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS + ("volatility_filter",),
            _ready_records(),
        )

        self.assertEqual(assessment.status, "blocked")
        self.assertEqual(
            assessment.missing_mechanism_ids,
            ("volatility_filter",),
        )
        self.assertIn("mechanisms_not_covered", assessment.blocking_reasons)

    def test_records_for_another_strategy_do_not_satisfy_gate(self):
        mismatched_repository = replace(
            _ready_records()[1],
            strategy_id="another_strategy_v1",
        )
        records = (
            _ready_records()[0],
            mismatched_repository,
            _ready_records()[2],
        )

        assessment = StrategyEvidenceGate.assess(
            STRATEGY_ID,
            MECHANISMS,
            records,
        )

        self.assertEqual(assessment.status, "blocked")
        self.assertEqual(
            assessment.mismatched_evidence_ids,
            ("repo-verified",),
        )
        self.assertIn(
            "reviewed_repository_source_missing",
            assessment.blocking_reasons,
        )

    def test_record_rejects_invalid_date_url_and_mutation(self):
        record = _ready_records()[0]

        with self.assertRaisesRegex(ValueError, "ISO date"):
            replace(record, retrieved_at="2026-02-30")
        with self.assertRaisesRegex(ValueError, "https URL"):
            replace(record, canonical_url="http://example.com/paper")
        with self.assertRaisesRegex(ValueError, "immutable_ref"):
            replace(record, immutable_ref=" ")
        with self.assertRaises(FrozenInstanceError):
            record.title = "changed"


def _ready_records() -> tuple[StrategyEvidenceRecord, ...]:
    return (
        _record(
            evidence_id="paper-mechanism",
            source_kind="paper",
            evidence_roles=("mechanism", "validation"),
            mechanism_ids=MECHANISMS,
        ),
        _record(
            evidence_id="repo-verified",
            source_kind="repository",
            license_spdx="MIT",
            license_status="verified",
            reuse_policy="adapt_allowed",
            evidence_roles=("code",),
            mechanism_ids=("absolute_momentum",),
            reviewed_paths=("src/strategy.py",),
            borrowable_mechanisms=("prior-bar signal execution",),
        ),
        _record(
            evidence_id="paper-counterevidence",
            source_kind="paper",
            evidence_roles=("counterevidence",),
            mechanism_ids=("absolute_momentum",),
            prohibited_transfers=("look-ahead simulation",),
        ),
    )


def _record(
    *,
    evidence_id: str,
    source_kind: str,
    license_spdx: str | None = None,
    license_status: str = "unknown",
    reuse_policy: str = "reference_only",
    evidence_roles: tuple[str, ...],
    mechanism_ids: tuple[str, ...],
    reviewed_paths: tuple[str, ...] = (),
    borrowable_mechanisms: tuple[str, ...] = (),
    prohibited_transfers: tuple[str, ...] = (),
) -> StrategyEvidenceRecord:
    return StrategyEvidenceRecord(
        evidence_id=evidence_id,
        strategy_id=STRATEGY_ID,
        source_kind=source_kind,
        title=evidence_id,
        canonical_url=f"https://example.com/{evidence_id}",
        immutable_ref=f"ref:{evidence_id}",
        retrieved_at="2026-08-09",
        license_spdx=license_spdx,
        license_status=license_status,
        reuse_policy=reuse_policy,
        evidence_roles=evidence_roles,
        mechanism_ids=mechanism_ids,
        reviewed_paths=reviewed_paths,
        borrowable_mechanisms=borrowable_mechanisms,
        prohibited_transfers=prohibited_transfers,
        applicability="Synthetic unit-test fixture only.",
        known_biases=("Not production evidence.",),
    )


if __name__ == "__main__":
    unittest.main()
