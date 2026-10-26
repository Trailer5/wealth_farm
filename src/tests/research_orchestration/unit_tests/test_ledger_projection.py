import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.research_contracts import (
    ActionType,
    ClaimComparison,
    DecisionLane,
    PositionState,
    ProbabilityInterval,
    QuantityRuleKind,
    TriggerKind,
)
from src.research_ledger import (
    LedgerScope,
    ResearchLedgerService,
    SQLiteResearchLedgerRepository,
)
from src.research_orchestration import (
    FinalDecisionLedgerRecorder,
    FinalDecisionOutcome,
    FinalDecisionReportAssemblyService,
    FinalDecisionReportRenderer,
    FinalDecisionReportService,
    FinalDecisionResearchReport,
    LedgerRecordingStatus,
    PositionAction,
    final_report_source_ref,
    project_final_decision,
)
from src.research_orchestration.final_decision_models import (
    final_decision_report_id,
)
from src.research_orchestration.sqlite_repository import (
    SQLiteResearchWorkflowRepository,
)
from src.research_visualization import ReportArtifactService
from src.tests.research_orchestration.unit_tests.test_final_decision_report import (
    AS_OF,
    _FakePortfolioRisk,
    _inputs,
    _namespace,
)


class FinalDecisionProjectionTest(unittest.TestCase):
    def setUp(self):
        self.inputs = _inputs()
        self.report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**self.inputs)

    def _project(self, report=None, *, benchmark="index:test", **changes):
        objects = {
            "suitability": self.inputs["suitability"],
            "entry_plan": self.inputs["entry_plan"],
            "position_plan": self.inputs["position_plan"],
        }
        objects.update(changes)
        return project_final_decision(
            report=report or self.report,
            benchmark_asset_id=benchmark,
            **objects,
        )

    def test_consider_and_wait_become_relative_view_and_conditional_buy(self):
        judgements = self._project()

        (view,) = judgements.views
        (action,) = judgements.actions
        self.assertEqual(view.claim.comparison, ClaimComparison.OUTPERFORM)
        self.assertEqual(view.settlement_rule.benchmark_id, "index:test")
        self.assertEqual(view.expires_on, "2029-08-23")
        self.assertIn("核心收益来源连续两个复核期失效。", view.falsifiers)
        self.assertEqual(action.action_type, ActionType.BUY)
        self.assertEqual(action.trigger.kind, TriggerKind.CONDITIONAL)
        self.assertEqual(
            action.trigger.observability_key,
            "valuation_and_operations",
        )
        self.assertEqual(
            action.quantity_rule.kind,
            QuantityRuleKind.UNAVAILABLE,
        )

    def test_watch_and_missing_benchmark_stay_non_directional(self):
        watch = _with_outcomes(
            self.report,
            suitability=FinalDecisionOutcome.SUITABILITY_WATCH,
        )

        self.assertIsNone(self._project(watch).views[0].claim)
        self.assertIsNone(self._project(benchmark=None).views[0].claim)
        self.assertIsNotNone(
            self._project(watch).views[0].settlement_rule
        )

    def test_avoid_without_timing_records_only_the_view(self):
        avoid = _with_outcomes(
            self.report,
            suitability=FinalDecisionOutcome.SUITABILITY_AVOID,
            timing=FinalDecisionOutcome.TIMING_NOT_APPLICABLE,
        )

        judgements = self._project(avoid)

        self.assertEqual(
            judgements.views[0].claim.comparison,
            ClaimComparison.UNDERPERFORM,
        )
        self.assertEqual(judgements.actions, ())

    def test_pre_publication_probability_reaches_report_and_view(self):
        inputs = _inputs()
        inputs["narrative"] = replace(
            inputs["narrative"],
            claim_probability=ProbabilityInterval(
                Decimal("0.55"),
                Decimal("0.7"),
            ),
        )
        report = FinalDecisionReportAssemblyService(
            _FakePortfolioRisk()
        ).assemble(**inputs)

        markdown = FinalDecisionReportRenderer().render(report)
        (view,) = self._project(report).views

        self.assertIn("发布前概率区间：55%—70%", markdown)
        self.assertEqual(view.probability, report.claim_probability)
        with self.assertRaisesRegex(ValueError, "consider or avoid"):
            _with_outcomes(
                report,
                suitability=FinalDecisionOutcome.SUITABILITY_WATCH,
            )

    def test_act_on_held_position_adds_with_position_plan_quantity(self):
        report = _with_outcomes(
            self.report,
            timing=FinalDecisionOutcome.TIMING_ACT,
            position_sizing=FinalDecisionOutcome.POSITION_AVAILABLE,
        )
        held = _with_position_state(report, PositionState.HELD)

        (action,) = self._project(
            held,
            position_plan=_namespace(
                self.inputs["position_plan"],
                action=PositionAction.WAIT,
            ),
        ).actions

        self.assertEqual(action.action_type, ActionType.ADD)
        self.assertEqual(action.trigger.kind, TriggerKind.SCHEDULED)
        self.assertEqual(action.trigger.scheduled_on, AS_OF)
        self.assertEqual(
            action.quantity_rule.reference,
            report.position_plan_id,
        )


class FinalDecisionLedgerRecordingTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "测试标的-基础研究包-2026-08-24.md").write_text(
            "# 基础研究包\n",
            encoding="utf-8",
            newline="\n",
        )
        self.db_path = self.root / "ledger.sqlite3"
        self.ledger = ResearchLedgerService(
            SQLiteResearchLedgerRepository(self.db_path)
        )
        self.attestations = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def _publish(self, ledger):
        recorder = FinalDecisionLedgerRecorder(
            ledger=ledger,
            publications=SimpleNamespace(
                list_final_publications=lambda: tuple(self.attestations)
            ),
        )
        service = FinalDecisionReportService(
            publisher=ReportArtifactService(self.root),
            publication_recorder=SimpleNamespace(
                record_final_publication=self.attestations.append
            ),
            ledger_recorder=recorder,
        )
        return service.assemble_and_publish(**_inputs()), recorder

    def test_publication_is_recorded_once_per_report(self):
        result, recorder = self._publish(self.ledger)

        self.assertEqual(result.ledger.status, LedgerRecordingStatus.RECORDED)
        entries = self.ledger.list_entries(user_id="user:test")
        self.assertEqual(len(entries), 2)
        self.assertTrue(
            all(item.scope is LedgerScope.PERSONAL for item in entries)
        )
        self.assertEqual(
            {item.artifact for item in entries},
            set(self.attestations[0].judgements.artifacts),
        )
        self.assertEqual(recorder.replay(), ())

    def test_failed_recording_is_replayed_from_publication(self):
        failing = SimpleNamespace(
            record=_raise_locked,
            has_source=lambda source_ref: False,
        )
        result, _ = self._publish(failing)

        self.assertEqual(
            result.ledger.status,
            LedgerRecordingStatus.PENDING_REPLAY,
        )
        self.assertIsNotNone(result.report_path)
        replayed = FinalDecisionLedgerRecorder(
            ledger=self.ledger,
            publications=SimpleNamespace(
                list_final_publications=lambda: tuple(self.attestations)
            ),
        ).replay()

        self.assertEqual(
            [item.status for item in replayed],
            [LedgerRecordingStatus.RECORDED],
        )
        self.assertEqual(
            {
                item.artifact
                for item in self.ledger.list_entries(
                    user_id="user:test",
                    source_ref=final_report_source_ref(
                        result.report.report_id
                    ),
                )
            },
            set(self.attestations[0].judgements.artifacts),
        )

    def test_ledger_survives_workflow_run_deletion(self):
        SQLiteResearchWorkflowRepository(self.db_path).initialize()
        self._publish(self.ledger)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                """
                INSERT INTO research_workflow_runs VALUES (
                    'workflow:test', 'task:test', 'stock:test', NULL,
                    'complete', NULL, '{}', ?, ?
                )
                """,
                [AS_OF, AS_OF],
            )
            conn.execute(
                """
                INSERT INTO final_decision_publications VALUES (
                    'final-decision:test', 'workflow:test', '{}', ?
                )
                """,
                [AS_OF],
            )
            conn.execute(
                "DELETE FROM research_workflow_runs"
            )
            publications = conn.execute(
                "SELECT COUNT(*) FROM final_decision_publications"
            ).fetchone()[0]

        self.assertEqual(publications, 0)
        self.assertEqual(
            len(self.ledger.list_entries(user_id="user:test")),
            2,
        )


def _raise_locked(*args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def _with_outcomes(
    report: FinalDecisionResearchReport,
    **outcomes: FinalDecisionOutcome,
) -> FinalDecisionResearchReport:
    lanes = {
        DecisionLane.SUITABILITY.value: outcomes.get("suitability"),
        DecisionLane.TIMING.value: outcomes.get("timing"),
        DecisionLane.POSITION_SIZING.value: outcomes.get(
            "position_sizing"
        ),
    }
    payload = report.to_dict()
    for section in payload["decision_sections"]:
        outcome = lanes[section["lane"]]
        if outcome is not None:
            section["outcome"] = outcome.value
    payload["report_id"] = final_decision_report_id(payload)
    return FinalDecisionResearchReport.from_dict(payload)


def _with_position_state(
    report: FinalDecisionResearchReport,
    state: PositionState,
) -> FinalDecisionResearchReport:
    payload = report.to_dict()
    payload["user_boundary"]["position_state"] = state.value
    payload["report_id"] = final_decision_report_id(payload)
    return FinalDecisionResearchReport.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
