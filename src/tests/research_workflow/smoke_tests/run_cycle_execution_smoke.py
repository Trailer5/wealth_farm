"""Run formal three-year cyclical-resource research for Zijin Mining."""

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
        CommoditySensitivityService,
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
    from src.research_contracts import (
        PROFILE_CYCLICAL_RESOURCE,
        ResearchHorizon,
        ResearchQuestionType,
    )
    from src.research_workflow import (
        CycleResearchExecutionOptions,
        CyclicalResourceResearchExecutionService,
        ResearchTaskFactory,
    )
    from src.strategy import (
        StrategyRunStatus,
        default_strategy_registry,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "cycle-execution.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        zijin = _register(
            master,
            "stock:zijin",
            ASSET_TYPE_STOCK,
            "601899",
            "紫金矿业",
        )
        index = _register(
            master,
            "index:csi300",
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
        factors = SQLiteFactorRepository(db_path)
        service = CyclicalResourceResearchExecutionService(
            master_data=master,
            data_access=data_access,
            data_fetch=fetch,
            quality_service=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            factor_service=FactorService(factors, master),
            fundamental_service=FundamentalAnalysisService(
                master_data=master,
                repository=SQLiteFundamentalMetricRepository(
                    db_path
                ),
            ),
            commodity_service=CommoditySensitivityService(
                factors
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
            asset_id=zijin.asset_id,
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            question_type=ResearchQuestionType.CYCLE_READING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        orchestration = ResearchOrchestrationPipeline(
            dispatcher=ResearchExecutionDispatcher(
                {PROFILE_CYCLICAL_RESOURCE: service}
            ),
            report_assembly=ResearchReportAssemblyService(
                master_data=master
            ),
            publisher=ReportArtifactService(root / "report"),
        )
        run = orchestration.run(
            ResearchRunRequest(
                task=task,
                execution_options=CycleResearchExecutionOptions(
                    provider_timeout_seconds=120,
                    commodity_weights=(
                        ("sina:CU0", 0.7),
                        ("sge:Au99.99", 0.3),
                    ),
                ),
            ),
        )
        result = run.execution
        if result.status.value != "complete":
            raise AssertionError(
                f"Cycle execution incomplete: {result.gaps}"
            )
        if len(result.chart_artifacts) != 4:
            raise AssertionError("Expected four cycle charts")
        if (
            result.strategy_result is None
            or result.strategy_result.status
            is StrategyRunStatus.INSUFFICIENT_DATA
        ):
            raise AssertionError("Cycle strategy input incomplete")
        if run.report is None or run.report_path is None:
            raise AssertionError("Cycle report was not published")
        sensitivity = next(
            item
            for item in result.factor_values
            if item.factor_name == "commodity_sensitivity"
        )
        metrics = {
            item.metric_name: str(item.value)
            for item in result.fundamental_result.metrics
            if item.metric_name in {"pb", "roic", "net_debt"}
        }
        print(
            json.dumps(
                {
                    "status": result.status.value,
                    "window_days": (
                        result.windows.strategy_window_days
                    ),
                    "commodity_sensitivity": sensitivity.value,
                    "commodity_parameters": sensitivity.parameters,
                    "financial_metrics": metrics,
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
        source="cycle-execution:catalog",
        raw_symbol=symbol,
    )


if __name__ == "__main__":
    raise SystemExit(main())
