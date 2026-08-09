import tempfile
import unittest
from pathlib import Path

from src.master_data import (
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_acceptance import (
    AcceptanceStatus,
    AcceptanceStepResult,
    AcceptanceStepStatus,
    ClaimKind,
    CrossAssetAcceptanceService,
    InvalidationCondition,
    PLAN15_ACCEPTANCE_VERSION,
    PLAN15_AS_OF_DATE,
    REQUIRED_ACCEPTANCE_STEPS,
    RepresentativeResearchAssembler,
    ResearchAcceptanceReport,
    ResearchClaim,
    ResearchGap,
    ResearchScenario,
    ScenarioKind,
    build_plan15_tasks,
    plan15_etf_expense_schedules,
    register_plan15_assets,
)


class ResearchAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "acceptance.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.assets = register_plan15_assets(self.master)
        self.tasks = build_plan15_tasks(
            self.master,
            self.assets,
        )
        self.assembler = RepresentativeResearchAssembler()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_frozen_tasks_cover_five_assets_and_four_profiles(self):
        self.assertEqual(len(self.tasks), 5)
        self.assertEqual(
            len({task.profile_id for task in self.tasks.values()}),
            4,
        )
        self.assertEqual(
            self.assets["csi300_index"].exchange,
            "sh",
        )
        self.assertEqual(
            len(self.tasks["maotai"].peer_asset_ids),
            3,
        )
        schedules = plan15_etf_expense_schedules(self.assets)
        self.assertEqual(
            str(
                schedules[
                    "csi300_etf"
                ].comprehensive_expense_ratio
            ),
            "0.0020",
        )
        self.assertTrue(
            schedules["csi300_etf"].source.startswith("sse:")
        )
        for name, task in self.tasks.items():
            self.assertEqual(
                task.task_id,
                f"research:plan15:{name}",
            )
            self.assertEqual(task.as_of_date, PLAN15_AS_OF_DATE)
            self.assertEqual(
                task.benchmark_asset_id,
                self.assets["csi300_index"].asset_id,
            )

    def test_complete_report_round_trips_and_replays(self):
        report = self._report("maotai")

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.COMPLETE,
        )
        self.assertTrue(report.replay_verified)
        restored = ResearchAcceptanceReport.from_dict(
            report.to_dict()
        )
        self.assertEqual(restored.to_dict(), report.to_dict())

    def test_gap_and_unknown_are_partial_not_complete(self):
        report = self._report(
            "pingan_bank",
            step_overrides={
                "metrics": AcceptanceStepResult(
                    step_id="metrics",
                    status=AcceptanceStepStatus.GAP,
                    summary="Bank-specific metrics are unavailable",
                    limitations=("No capital adequacy metric",),
                )
            },
            gaps=(
                ResearchGap(
                    module="fundamental_analysis",
                    code="bank_metrics_missing",
                    message="Bank-specific metrics are not computed.",
                    blocking=False,
                ),
            ),
            unknown=True,
        )

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.PARTIAL,
        )

    def test_blocking_gap_is_blocked(self):
        report = self._report(
            "zijin",
            gaps=(
                ResearchGap(
                    module="data_access",
                    code="commodity_benchmark_missing",
                    message="Commodity benchmark dataset is absent.",
                    blocking=True,
                ),
            ),
        )

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.BLOCKED,
        )

    def test_duplicate_etf_tasks_count_as_one_complete_profile(self):
        reports = (
            self._report("maotai"),
            self._report("gold_etf"),
            self._report("csi300_etf"),
            self._report(
                "pingan_bank",
                gaps=(
                    ResearchGap(
                        module="fundamental_analysis",
                        code="bank_metrics_missing",
                        message="Missing bank metrics.",
                        blocking=False,
                    ),
                ),
            ),
            self._report(
                "zijin",
                gaps=(
                    ResearchGap(
                        module="data_access",
                        code="commodity_benchmark_missing",
                        message="Missing commodity benchmark.",
                        blocking=False,
                    ),
                ),
            ),
        )
        summary = CrossAssetAcceptanceService().summarize(
            acceptance_version=PLAN15_ACCEPTANCE_VERSION,
            as_of_date=PLAN15_AS_OF_DATE,
            reports=reports,
        )

        self.assertEqual(summary.complete_profile_count, 2)
        self.assertTrue(summary.consistency_verified)
        self.assertFalse(summary.agent_guidance_admitted)

    def _report(
        self,
        name,
        *,
        step_overrides=None,
        gaps=(),
        unknown=False,
    ):
        steps = {
            step_id: AcceptanceStepResult(
                step_id=step_id,
                status=AcceptanceStepStatus.PASS,
                summary=f"{step_id} passed",
                sources=(f"test:{step_id}",),
                metric_versions=(
                    ("test_v1",) if step_id == "metrics" else ()
                ),
            )
            for step_id in REQUIRED_ACCEPTANCE_STEPS
        }
        steps.update(step_overrides or {})
        scenario_claim_kind = (
            ClaimKind.UNKNOWN
            if unknown
            else ClaimKind.ASSUMPTION
        )
        scenarios = tuple(
            ResearchScenario(
                kind=kind,
                claims=(
                    ResearchClaim(
                        kind=scenario_claim_kind,
                        text=f"{kind.value} scenario",
                    ),
                ),
            )
            for kind in ScenarioKind
        )
        asset = self.assets[name]
        return self.assembler.assemble(
            acceptance_version=PLAN15_ACCEPTANCE_VERSION,
            task=self.tasks[name],
            asset_name=asset.display_name,
            asset_type=asset.asset_type,
            steps=tuple(steps.values()),
            scenarios=scenarios,
            strongest_counterargument=ResearchClaim(
                kind=(
                    ClaimKind.UNKNOWN
                    if unknown
                    else ClaimKind.INFERENCE
                ),
                text="Strongest counterargument",
                evidence_refs=("strategy:test",),
            ),
            invalidation_conditions=(
                InvalidationCondition(
                    condition="Research premise no longer holds",
                    evidence_required="Updated verified evidence",
                    severity="thesis_break",
                ),
            ),
            conclusions=(
                ResearchClaim(
                    kind=ClaimKind.FACT,
                    text="Acceptance steps were evaluated.",
                    evidence_refs=("acceptance:steps",),
                ),
            ),
            gaps=gaps,
        )


if __name__ == "__main__":
    unittest.main()
