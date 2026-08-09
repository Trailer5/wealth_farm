"""Production dependency wiring for unified research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.benchmark_analysis import (
    BenchmarkAnalysisService,
    DefaultBenchmarkResolver,
    SQLiteBenchmarkRepository,
)
from src.data_access import DataAccessService, SQLiteDataAccessRepository
from src.data_quality import DataQualityService, SQLiteDataQualityRepository
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore
from src.event_knowledge import EventKnowledgeService, SQLiteEventRepository
from src.event_name_discovery import (
    AssetNameSnapshotLoader,
    EventNameDiscoveryService,
    SQLiteEventNameDiscoveryRepository,
)
from src.factor_engineering import (
    CommoditySensitivityService,
    FactorService,
    SQLiteFactorRepository,
)
from src.financial_data import SQLiteFinancialDataRepository
from src.fund_research import ExchangeFundMetricService
from src.fundamental_analysis import (
    FinancialInstitutionAnalysisService,
    FundamentalAnalysisService,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.research_acceptance import ResearchReportAssemblyService
from src.research_evidence import ResearchEvidenceService
from src.research_visualization import ChartRenderer, ReportArtifactService
from src.research_workflow import (
    ETFResearchExecutionService,
    FinancialInstitutionResearchExecutionService,
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    CyclicalResourceResearchExecutionService,
    ResearchExecutionService,
    ResearchTaskFactory,
)
from src.strategy import default_strategy_registry

from .dispatcher import ResearchExecutionDispatcher
from .event_name_discovery import EventNameDiscoveryOrchestrationPipeline
from .pipeline import ResearchOrchestrationPipeline


@dataclass(frozen=True)
class ResearchOrchestrationContainer:
    """Public production services needed to start one research run."""

    master_data: MasterDataService
    task_factory: ResearchTaskFactory
    pipeline: ResearchOrchestrationPipeline
    event_name_discovery: EventNameDiscoveryOrchestrationPipeline


def create_default_container(
    db_path: str | Path = "data/database/wealth_farm.sqlite3",
    report_root: str | Path = "report",
) -> ResearchOrchestrationContainer:
    """Build the shared SQLite-backed production research graph."""

    database_path = Path(db_path)
    store = SQLiteDataStore(database_path)
    master_data = MasterDataService(
        SQLiteMasterDataRepository(database_path)
    )
    data_fetch = DataFetchService(
        store=store,
        master_data_service=master_data,
    )
    data_access = DataAccessService(
        master_data=master_data,
        repository=SQLiteDataAccessRepository(store),
        fetch_service=data_fetch,
        financial_repository=SQLiteFinancialDataRepository(database_path),
    )

    quality_service = DataQualityService(
        SQLiteDataQualityRepository(database_path)
    )
    factor_repository = SQLiteFactorRepository(database_path)
    factor_service = FactorService(factor_repository, master_data)
    fundamental_repository = SQLiteFundamentalMetricRepository(
        database_path
    )
    fundamental_service = FundamentalAnalysisService(
        master_data=master_data,
        repository=fundamental_repository,
    )
    financial_service = FinancialInstitutionAnalysisService(
        master_data=master_data,
        repository=fundamental_repository,
    )
    etf_metric_service = ExchangeFundMetricService(
        master_data=master_data,
        factor_repository=factor_repository,
    )
    commodity_service = CommoditySensitivityService(factor_repository)

    benchmark_repository = SQLiteBenchmarkRepository(database_path)
    benchmark_service = BenchmarkAnalysisService(
        master_data=master_data,
        data_access=data_access,
        factor_repository=factor_repository,
        benchmark_repository=benchmark_repository,
    )
    event_service = EventKnowledgeService(
        SQLiteEventRepository(database_path),
        master_data,
    )
    evidence_service = ResearchEvidenceService(
        master_data=master_data,
        data_access=data_access,
        event_knowledge=event_service,
        store=store,
    )

    strategy_registry = default_strategy_registry()
    chart_renderer = ChartRenderer(Path("temp/research"))
    common_execution_dependencies = {
        "master_data": master_data,
        "data_access": data_access,
        "quality_service": quality_service,
        "factor_service": factor_service,
        "benchmark_service": benchmark_service,
        "strategy_registry": strategy_registry,
        "chart_renderer": chart_renderer,
    }
    ordinary_service = ResearchExecutionService(
        **common_execution_dependencies,
        fundamental_service=fundamental_service,
        evidence_service=evidence_service,
    )
    financial_execution_service = (
        FinancialInstitutionResearchExecutionService(
            **common_execution_dependencies,
            financial_service=financial_service,
            evidence_service=evidence_service,
        )
    )
    etf_execution_service = ETFResearchExecutionService(
        **common_execution_dependencies,
        data_fetch=data_fetch,
        etf_metric_service=etf_metric_service,
        commodity_service=commodity_service,
    )
    cycle_execution_service = CyclicalResourceResearchExecutionService(
        **common_execution_dependencies,
        data_fetch=data_fetch,
        fundamental_service=fundamental_service,
        commodity_service=commodity_service,
        evidence_service=evidence_service,
    )

    task_factory = ResearchTaskFactory(
        master_data,
        benchmark_resolver=DefaultBenchmarkResolver(
            master_data=master_data,
            benchmark_repository=benchmark_repository,
        ),
    )
    pipeline = ResearchOrchestrationPipeline(
        dispatcher=ResearchExecutionDispatcher(
            {
                PROFILE_ORDINARY_COMPANY: ordinary_service,
                PROFILE_FINANCIAL_INSTITUTION: (
                    financial_execution_service
                ),
                PROFILE_EXCHANGE_FUND: etf_execution_service,
                PROFILE_CYCLICAL_RESOURCE: cycle_execution_service,
            }
        ),
        report_assembly=ResearchReportAssemblyService(
            master_data=master_data
        ),
        publisher=ReportArtifactService(Path(report_root)),
    )
    event_name_discovery = EventNameDiscoveryOrchestrationPipeline(
        discovery_service=EventNameDiscoveryService(
            task_factory=task_factory,
            repository=SQLiteEventNameDiscoveryRepository(database_path),
        ),
        name_loader=AssetNameSnapshotLoader(
            master_data=master_data,
        ),
    )
    return ResearchOrchestrationContainer(
        master_data=master_data,
        task_factory=task_factory,
        pipeline=pipeline,
        event_name_discovery=event_name_discovery,
    )
