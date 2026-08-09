"""Run formal financial-institution research for Ping An Bank."""

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
        FinancialInstitutionAnalysisService,
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
    from src.research_acceptance import ResearchReportAssemblyService
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
        FinancialInstitutionResearchExecutionService,
        PROFILE_FINANCIAL_INSTITUTION,
        ResearchExecutionOptions,
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
        db_path = root / "financial-execution.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        bank = _register(
            master,
            "stock:pingan-bank",
            ASSET_TYPE_STOCK,
            "000001",
            "平安银行",
            "sz",
        )
        index = _register(
            master,
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
            "sh",
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
        factors = SQLiteFactorRepository(db_path)
        service = FinancialInstitutionResearchExecutionService(
            master_data=master,
            data_access=data_access,
            quality_service=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            factor_service=FactorService(factors, master),
            financial_service=FinancialInstitutionAnalysisService(
                master_data=master,
                repository=SQLiteFundamentalMetricRepository(
                    db_path
                ),
            ),
            benchmark_service=BenchmarkAnalysisService(
                master_data=master,
                data_access=data_access,
                factor_repository=factors,
                benchmark_repository=SQLiteBenchmarkRepository(
                    db_path
                ),
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
        task = ResearchTaskFactory(master).create(
            asset_id=bank.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            question_type=ResearchQuestionType.RISK_REVIEW,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        orchestration = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {PROFILE_FINANCIAL_INSTITUTION: service}
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
        if result.status.value != "complete":
            raise AssertionError(
                f"Financial execution incomplete: {result.gaps}"
            )
        if len(result.profile_metrics) != 5:
            raise AssertionError("Expected five bank metrics")
        if len(result.chart_artifacts) != 4:
            raise AssertionError("Expected four bank charts")
        if (
            result.strategy_result is None
            or result.strategy_result.status
            is StrategyRunStatus.INSUFFICIENT_DATA
        ):
            raise AssertionError("Bank strategy input incomplete")
        if run.report is None or run.report_path is None:
            raise AssertionError("Bank report was not published")
        print(
            json.dumps(
                {
                    "status": result.status.value,
                    "window_days": (
                        result.windows.strategy_window_days
                    ),
                    "metrics": {
                        item.metric_name: (
                            str(item.value)
                            if item.value is not None
                            else None
                        )
                        for item in result.profile_metrics
                    },
                    "chart_count": len(result.chart_artifacts),
                    "strategy": result.strategy_result.strategy_id,
                    "strategy_status": (
                        result.strategy_result.status.value
                    ),
                    "phases": {
                        item.phase_id: item.status.value
                        for item in result.phases
                    },
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


def _register(master, asset_id, asset_type, symbol, name, exchange):
    from src.master_data import Asset

    return master.register_asset(
        Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=symbol,
            display_name=name,
            market="cn_a",
            exchange=exchange,
        ),
        source="financial-execution:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
