"""Service container for one local research API instance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.alerts import AlertService, SQLiteAlertRepository
from src.ai_harness import (
    AIHarnessService,
    ResearchContextAssembler,
    SQLiteAIHarnessRepository,
)
from src.data_access import (
    DataAccessService,
    DailyDataReader,
    SQLiteDataAccessRepository,
)
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore
from src.event_knowledge import EventKnowledgeService, SQLiteEventRepository
from src.data_quality import DataQualityService, SQLiteDataQualityRepository
from src.factor_engineering import FactorService, SQLiteFactorRepository
from src.master_data import MasterDataService, SQLiteMasterDataRepository
from src.portfolio_risk import PortfolioRiskService, SQLitePortfolioRepository
from src.research_state import (
    ResearchStateService,
    SQLiteResearchStateRepository,
)
from src.user_space import SQLiteUserSpaceRepository, UserSpaceService


@dataclass(frozen=True)
class AppServices:
    user_space: UserSpaceService
    master_data: MasterDataService
    research_state: ResearchStateService
    alerts: AlertService
    factors: FactorService
    events: EventKnowledgeService
    portfolio: PortfolioRiskService
    ai_harness: AIHarnessService
    data_quality: DataQualityService
    data_access: DataAccessService


def create_services(
    db_path: str | Path = "data/database/wealth_farm.sqlite3",
) -> AppServices:
    """Create services sharing one SQLite database."""

    user_space = UserSpaceService(SQLiteUserSpaceRepository(db_path))
    master_data = MasterDataService(SQLiteMasterDataRepository(db_path))
    data_store = SQLiteDataStore(db_path)
    data_access = DataAccessService(
        master_data=master_data,
        repository=SQLiteDataAccessRepository(data_store),
        fetch_service=DataFetchService(
            store=data_store,
            master_data_service=master_data,
        ),
        daily_data_reader=DailyDataReader(),
        # A missing calendar must not trigger remote access for local-only
        # Web queries. Explicit bar fallback still uses provider timeouts.
        trading_calendar=None,
    )
    research_state = ResearchStateService(
        SQLiteResearchStateRepository(db_path),
        user_space,
        master_data,
    )
    alerts = AlertService(
        SQLiteAlertRepository(db_path),
        user_space,
        master_data,
    )
    factors = FactorService(
        SQLiteFactorRepository(db_path),
        master_data,
    )
    events = EventKnowledgeService(
        SQLiteEventRepository(db_path),
        master_data,
    )
    portfolio = PortfolioRiskService(
        SQLitePortfolioRepository(db_path),
        research_state,
    )
    ai_harness = AIHarnessService(
        SQLiteAIHarnessRepository(db_path),
        user_space,
        ResearchContextAssembler(
            master_data,
            factors,
            events,
            research_state,
        ),
    )
    data_quality = DataQualityService(
        SQLiteDataQualityRepository(db_path)
    )
    return AppServices(
        user_space=user_space,
        master_data=master_data,
        research_state=research_state,
        alerts=alerts,
        factors=factors,
        events=events,
        portfolio=portfolio,
        ai_harness=ai_harness,
        data_quality=data_quality,
        data_access=data_access,
    )
