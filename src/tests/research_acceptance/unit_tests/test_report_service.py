import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_acceptance import (
    AcceptanceStatus,
    ResearchReportAssemblyService,
    ScenarioKind,
)
from src.research_workflow import (
    PROFILE_ORDINARY_COMPANY,
    ResearchExecutionGap,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchHorizon,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchQuestionType,
    ResearchTaskFactory,
    ResearchWindows,
)
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
)


class ResearchReportAssemblyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "report.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = self._register(
            "stock:company",
            ASSET_TYPE_STOCK,
            "600519",
        )
        index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        self.service = ResearchReportAssemblyService(
            master_data=self.master
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_execution_generates_replayable_report(self):
        report = self.service.assemble(_execution(self.task))

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.COMPLETE,
        )
        self.assertTrue(report.replay_verified)
        self.assertEqual(
            {item.kind for item in report.scenarios},
            set(ScenarioKind),
        )

    def test_blocking_execution_gap_blocks_report(self):
        execution = _execution(self.task)
        phases = tuple(
            replace(
                phase,
                status=ResearchPhaseStatus.FAILED,
                error="provider down",
            )
            if phase.phase_id == "bars"
            else phase
            for phase in execution.phases
        )
        execution = replace(
            execution,
            status=ResearchExecutionStatus.BLOCKED,
            phases=phases,
            gaps=(
                ResearchExecutionGap(
                    "data_access",
                    "daily_bars_unavailable",
                    "provider down",
                    True,
                ),
            ),
        )
        report = self.service.assemble(execution)

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.BLOCKED,
        )
        self.assertEqual(
            report.gaps[0].code,
            "daily_bars_unavailable",
        )

    def _register(self, asset_id, asset_type, symbol):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=asset_id,
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol=symbol,
        )


def _execution(task):
    phases = tuple(
        ResearchPhaseResult(
            phase_id=phase_id,
            status=ResearchPhaseStatus.SUCCESS,
            summary=f"{phase_id} complete",
            sources=(f"unit:{phase_id}",),
        )
        for phase_id in (
            "task",
            "bars",
            "quality",
            "factors",
            "fundamental",
            "benchmark",
            "evidence",
            "charts",
            "strategy",
        )
    )
    return ResearchExecutionResult(
        task=task,
        windows=ResearchWindows(
            bar_start="2024-08-03",
            bar_end="2026-08-03",
            financial_start="2023-08-04",
            financial_end="2026-08-03",
            evidence_start="2025-02-01",
            evidence_end="2026-08-03",
            strategy_window_days=730,
        ),
        status=ResearchExecutionStatus.COMPLETE,
        phases=phases,
        gaps=(),
        strategy_result=StrategyResult(
            strategy_id="quality_value",
            strategy_version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            asset_id=task.asset_id,
            horizon=task.horizon,
            as_of_date=task.as_of_date,
            valid_until="2026-11-01",
            status=StrategyRunStatus.PASS,
            signal="research_pass",
            score=Decimal("1"),
            confidence=Decimal("1"),
            input_completeness=Decimal("1"),
            matched_conditions=("quality_gate",),
            failed_conditions=(),
            vetoed_conditions=(),
            missing_metrics=(),
            missing_evidence_types=(),
            metric_versions={"roe": "fundamental_v1"},
            counterevidence=(),
            limitations=("Thresholds are research assumptions",),
            input_effective_dates={"roe": "2026-08-03"},
        ),
    )


if __name__ == "__main__":
    unittest.main()
