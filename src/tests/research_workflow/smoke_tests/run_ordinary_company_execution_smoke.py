"""Run formal ordinary-company research without Plan15 fixtures."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.benchmark_analysis import (
        BenchmarkAnalysisService,
        DefaultBenchmarkResolver,
        SQLiteBenchmarkRepository,
    )
    from src.data_access import (
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_quality import (
        DataQualityService,
        SQLiteDataQualityRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.event_knowledge import (
        EventKnowledgeService,
        SQLiteEventRepository,
    )
    from src.factor_engineering import (
        FactorService,
        SQLiteFactorRepository,
    )
    from src.financial_data import SQLiteFinancialDataRepository
    from src.fundamental_analysis import (
        FundamentalAnalysisService,
        SQLiteFundamentalMetricRepository,
    )
    from src.master_data import (
        ASSET_TYPE_INDEX,
        ASSET_TYPE_STOCK,
        Asset,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_evidence import ResearchEvidenceService
    from src.research_acceptance import (
        ResearchReportAssemblyService,
    )
    from src.research_orchestration import (
        ResearchExecutionDispatcher,
        ResearchOrchestrationPipeline,
        ResearchRunRequest,
    )
    from src.research_visualization import (
        ChartRenderer,
        ReportArtifactService,
    )
    from src.research_workflow import (
        PROFILE_ORDINARY_COMPANY,
        ResearchExecutionOptions,
        ResearchExecutionService,
        ResearchHorizon,
        ResearchQuestionType,
        ResearchTaskFactory,
    )
    from src.strategy import (
        StrategyRunStatus,
        default_strategy_registry,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "ordinary-execution.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        maotai = _register(
            master,
            "asset:ordinary-execution-maotai",
            ASSET_TYPE_STOCK,
            "600519",
            "贵州茅台",
        )
        csi300 = _register(
            master,
            "asset:ordinary-execution-csi300",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
        )
        fetch = DataFetchService(
            store=store,
            master_data_service=master,
        )
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch,
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        factor_repository = SQLiteFactorRepository(db_path)
        benchmark_repository = SQLiteBenchmarkRepository(db_path)
        service = ResearchExecutionService(
            master_data=master,
            data_access=data_access,
            quality_service=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            factor_service=FactorService(
                factor_repository,
                master,
            ),
            fundamental_service=FundamentalAnalysisService(
                master_data=master,
                repository=SQLiteFundamentalMetricRepository(
                    db_path
                ),
            ),
            benchmark_service=BenchmarkAnalysisService(
                master_data=master,
                data_access=data_access,
                factor_repository=factor_repository,
                benchmark_repository=benchmark_repository,
            ),
            evidence_service=ResearchEvidenceService(
                master_data=master,
                data_access=data_access,
                event_knowledge=EventKnowledgeService(
                    SQLiteEventRepository(db_path),
                    master,
                ),
                store=store,
            ),
            strategy_registry=default_strategy_registry(),
            chart_renderer=ChartRenderer(root / "research"),
        )
        task = ResearchTaskFactory(
            master,
            benchmark_resolver=DefaultBenchmarkResolver(
                master_data=master,
                benchmark_repository=benchmark_repository,
            ),
        ).create(
            asset_id=maotai.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
        )
        if task.benchmark_asset_id != csi300.asset_id:
            raise AssertionError("Broad-market benchmark not resolved")
        orchestration = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {PROFILE_ORDINARY_COMPANY: service}
            ),
            report_assembly=ResearchReportAssemblyService(
                master_data=master
            ),
            publisher=ReportArtifactService(root / "report"),
        )
        run = orchestration.run(
            ResearchRunRequest(
                task=task,
                execution_options=ResearchExecutionOptions(
                provider_timeout_seconds=120,
                ),
            ),
        )
        result = run.execution
        if result.status.value == "blocked":
            raise AssertionError(
                f"Formal execution blocked: {result.gaps}"
            )
        if len(result.chart_artifacts) != 6:
            raise AssertionError(
                "Ordinary-company execution requires six charts"
            )
        if (
            result.strategy_result is None
            or result.strategy_result.status
            is StrategyRunStatus.INSUFFICIENT_DATA
        ):
            raise AssertionError(
                "Formal execution strategy input is incomplete"
            )
        report = run.report
        if report is None or run.report_path is None:
            raise AssertionError(
                "Orchestration did not publish the user report"
            )
        if report.overall_status.value != "complete":
            raise AssertionError(
                f"Formal narrative report incomplete: {report.gaps}"
            )
        print(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "status": result.status.value,
                    "windows": result.windows.__dict__,
                    "phases": {
                        phase.phase_id: phase.status.value
                        for phase in result.phases
                    },
                    "chart_count": len(result.chart_artifacts),
                    "strategy": {
                        "id": result.strategy_result.strategy_id,
                        "status": result.strategy_result.status.value,
                    },
                    "gaps": [
                        {
                            "code": gap.code,
                            "blocking": gap.blocking,
                        }
                        for gap in result.gaps
                    ],
                    "report": {
                        "status": report.overall_status.value,
                        "scenario_count": len(report.scenarios),
                        "replay_verified": report.replay_verified,
                    },
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


def _register(master, asset_id, asset_type, symbol, name):
    from src.master_data import Asset

    return master.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=symbol,
            display_name=name,
            market="cn_a",
            exchange="sh",
        ),
        source="ordinary-execution:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
