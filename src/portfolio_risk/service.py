"""Service for valuing current user holdings."""

from __future__ import annotations

from src.research_state import ResearchStateService
from src.user_space import UserContext

from .calculator import PortfolioCalculator
from .models import PortfolioSnapshot, PortfolioValuation
from .sqlite_repository import SQLitePortfolioRepository


class PortfolioRiskService:
    """Calculate, persist, and query current portfolio snapshots."""

    def __init__(
        self,
        repository: SQLitePortfolioRepository,
        research_state: ResearchStateService,
        calculator: PortfolioCalculator | None = None,
    ) -> None:
        self.repository = repository
        self.research_state = research_state
        self.calculator = calculator or PortfolioCalculator()
        self.repository.initialize()

    def value_current_holdings(
        self,
        context: UserContext,
        *,
        as_of_date: str,
        prices: dict[str, float],
    ) -> PortfolioValuation:
        """Calculate and save one current-portfolio snapshot."""

        holdings = self.research_state.list_holdings(context)
        valuation = self.calculator.calculate(
            user_id=context.user_id,
            as_of_date=as_of_date,
            holdings=holdings,
            prices=prices,
        )
        self.repository.save(valuation)
        return valuation

    def get_snapshot(
        self,
        context: UserContext,
        snapshot_id: str,
    ) -> PortfolioValuation | None:
        """Return one snapshot scoped to the current user."""

        return self.repository.get(snapshot_id, context.user_id)

    def list_snapshots(
        self,
        context: UserContext,
    ) -> list[PortfolioSnapshot]:
        """Return snapshot history for the current user."""

        self.research_state.list_holdings(context)
        return self.repository.list_snapshots(context.user_id)
