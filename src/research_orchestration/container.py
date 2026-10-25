"""Production dependency wiring for unified research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.alerts import AlertService, SQLiteAlertRepository
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
from src.fund_research import (
    ExchangeFundMetricService,
    FundResearchService,
    OpenFundBenchmarkResolver,
    SQLiteFundResearchRepository,
)
from src.fundamental_analysis import (
    FinancialInstitutionAnalysisService,
    FundamentalAnalysisService,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.peer_analysis import (
    PeerAnalysisService,
    SQLitePeerAnalysisRepository,
)
from src.portfolio_risk import (
    PortfolioRiskService,
    SQLitePortfolioRepository,
)
from src.research_acceptance import ResearchReportAssemblyService
from src.research_evidence import ResearchEvidenceService
from src.research_state import (
    ResearchStateService,
    SQLiteResearchStateRepository,
)
from src.research_visualization import ChartRenderer, ReportArtifactService
from src.research_workflow import (
    ETFResearchExecutionService,
    FinancialInstitutionResearchExecutionService,
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    CyclicalResourceResearchExecutionService,
    OpenFundResearchExecutionService,
    ResearchExecutionService,
    ResearchProfileAssignmentService,
    ResearchTaskFactory,
    SQLiteResearchProfileAssignmentRepository,
)
from src.strategy import default_strategy_registry
from src.user_space import (
    SQLiteUserSpaceRepository,
    UserSpaceService,
)

from .capabilities import (
    ResearchCapabilityRegistry,
    default_capability_registry,
)
from .conversation_acceptance import NewConversationAcceptanceRunner
from .dispatcher import ResearchExecutionDispatcher
from .entry import EntryPlanService
from .event_name_discovery import EventNameDiscoveryOrchestrationPipeline
from .exploration_persistence import (
    PersistentResearchExplorationService,
)
from .final_decision import FinalDecisionReportAssemblyService
from .final_decision_pipeline import FinalDecisionReportService
from .monitoring import MonitoringHandoffService
from .pipeline import ResearchOrchestrationPipeline
from .position import PositionPlanService
from .query_routing import (
    ResearchExplorationQueryService,
    default_query_router,
)
from .suitability import SuitabilityAssessmentService
from .request_planner import ResearchRequestPlanner
from .sqlite_repository import SQLiteResearchWorkflowRepository
from .workflow import ResearchWorkflowCoordinator


@dataclass(frozen=True)
class ResearchOrchestrationContainer:
    """Public production services needed to start one research run."""

    master_data: MasterDataService
    data_fetch: DataFetchService
    task_factory: ResearchTaskFactory
    request_planner: ResearchRequestPlanner
    new_conversation_acceptance: NewConversationAcceptanceRunner
    capability_registry: ResearchCapabilityRegistry
    workflow_coordinator: ResearchWorkflowCoordinator
    user_space: UserSpaceService
    exploration: PersistentResearchExplorationService
    queries: ResearchExplorationQueryService
    suitability: SuitabilityAssessmentService
    entry: EntryPlanService
    position: PositionPlanService
    final_decision: FinalDecisionReportService
    alerts: AlertService
    monitoring: MonitoringHandoffService
    portfolio_risk: PortfolioRiskService
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
    fund_repository = SQLiteFundResearchRepository(database_path)
    fund_research_service = FundResearchService(
        master_data=master_data,
        data_access=data_access,
        repository=fund_repository,
    )
    commodity_service = CommoditySensitivityService(factor_repository)

    benchmark_repository = SQLiteBenchmarkRepository(database_path)
    benchmark_service = BenchmarkAnalysisService(
        master_data=master_data,
        data_access=data_access,
        factor_repository=factor_repository,
        benchmark_repository=benchmark_repository,
    )
    open_fund_benchmark_resolver = OpenFundBenchmarkResolver(
        master_data=master_data,
        benchmark_repository=benchmark_repository,
        fund_repository=fund_repository,
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
    open_fund_execution_service = OpenFundResearchExecutionService(
        master_data=master_data,
        fund_research=fund_research_service,
        quality_service=quality_service,
        factor_service=factor_service,
        benchmark_service=benchmark_service,
        benchmark_resolver=open_fund_benchmark_resolver,
        chart_renderer=chart_renderer,
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
            fund_repository=fund_repository,
        ),
    )
    profile_assignment = ResearchProfileAssignmentService(
        master_data=master_data,
        repository=SQLiteResearchProfileAssignmentRepository(
            database_path
        ),
        profile_registry=task_factory.profile_registry,
    )
    request_planner = ResearchRequestPlanner(
        master_data=master_data,
        task_factory=task_factory,
        profile_assignment=profile_assignment,
        fund_identity_bootstrap=data_fetch,
    )
    new_conversation_acceptance = NewConversationAcceptanceRunner(
        planner=request_planner
    )
    capability_registry = default_capability_registry()
    user_space = UserSpaceService(
        SQLiteUserSpaceRepository(database_path)
    )
    research_state_repository = SQLiteResearchStateRepository(
        database_path
    )
    workflow_coordinator = ResearchWorkflowCoordinator(
        master_data=master_data,
        capability_registry=capability_registry,
        repository=SQLiteResearchWorkflowRepository(database_path),
        exploration_repository=research_state_repository,
    )
    alerts = AlertService(
        SQLiteAlertRepository(database_path),
        user_space,
        master_data,
        rule_authorizer=(
            workflow_coordinator.is_alert_rule_authorized
        ),
    )
    research_state = ResearchStateService(
        research_state_repository,
        user_space,
        master_data,
    )
    exploration = PersistentResearchExplorationService(
        repository=research_state_repository,
        workflow_coordinator=workflow_coordinator,
        user_space=user_space,
    )
    peer_analysis = PeerAnalysisService(
        master_data=master_data,
        metric_repository=fundamental_repository,
        peer_repository=SQLitePeerAnalysisRepository(
            database_path
        ),
    )
    portfolio_risk = PortfolioRiskService(
        SQLitePortfolioRepository(database_path),
        research_state,
    )
    queries = ResearchExplorationQueryService(
        capability_registry=capability_registry,
        router=default_query_router(
            data_access=data_access,
            research_evidence=evidence_service,
            benchmark_analysis=benchmark_service,
            open_fund_research=fund_research_service,
            open_fund_benchmark_resolver=(
                open_fund_benchmark_resolver
            ),
            event_knowledge=event_service,
            peer_analysis=peer_analysis,
            research_state=research_state,
            portfolio_risk=portfolio_risk,
        ),
        exploration=exploration,
    )
    suitability = SuitabilityAssessmentService()
    entry = EntryPlanService()
    position = PositionPlanService(portfolio_risk)
    report_publisher = ReportArtifactService(Path(report_root))
    final_decision = FinalDecisionReportService(
        publisher=report_publisher,
        assembly=FinalDecisionReportAssemblyService(
            portfolio_risk
        ),
        publication_recorder=workflow_coordinator,
    )
    monitoring = MonitoringHandoffService(
        alerts=alerts,
        workflow=workflow_coordinator,
        user_space=user_space,
    )
    pipeline = ResearchOrchestrationPipeline(
        dispatcher=ResearchExecutionDispatcher(
            {
                PROFILE_ORDINARY_COMPANY: ordinary_service,
                PROFILE_FINANCIAL_INSTITUTION: (
                    financial_execution_service
                ),
                PROFILE_EXCHANGE_FUND: etf_execution_service,
                PROFILE_OPEN_FUND: open_fund_execution_service,
                PROFILE_CYCLICAL_RESOURCE: cycle_execution_service,
            }
        ),
        report_assembly=ResearchReportAssemblyService(
            master_data=master_data
        ),
        publisher=report_publisher,
        workflow_coordinator=workflow_coordinator,
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
        data_fetch=data_fetch,
        task_factory=task_factory,
        request_planner=request_planner,
        new_conversation_acceptance=new_conversation_acceptance,
        capability_registry=capability_registry,
        workflow_coordinator=workflow_coordinator,
        user_space=user_space,
        exploration=exploration,
        queries=queries,
        suitability=suitability,
        entry=entry,
        position=position,
        final_decision=final_decision,
        alerts=alerts,
        monitoring=monitoring,
        portfolio_risk=portfolio_risk,
        pipeline=pipeline,
        event_name_discovery=event_name_discovery,
    )
