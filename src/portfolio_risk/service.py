"""Service for valuing current user holdings."""

from __future__ import annotations

from src.research_state import ResearchStateService
from src.user_space import UserContext

from .budget import InvestmentMandate
from .calculator import PortfolioCalculator
from .models import (
    PortfolioConstraintSnapshot,
    PortfolioSnapshot,
    PortfolioValuation,
    portfolio_valuation_digest,
)
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
        holdings_as_of_date: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> PortfolioValuation:
        """Calculate and save one current-portfolio snapshot."""

        if (holdings_as_of_date is None) != (not evidence_refs):
            raise ValueError(
                "holdings_as_of_date and evidence_refs must be "
                "provided together"
            )
        if (
            holdings_as_of_date is not None
            and holdings_as_of_date != as_of_date
        ):
            raise ValueError(
                "current holdings must be explicitly confirmed at "
                "the valuation cutoff"
            )
        holdings = self.research_state.list_holdings(context)
        valuation = self.calculator.calculate(
            user_id=context.user_id,
            as_of_date=as_of_date,
            holdings=holdings,
            prices=prices,
            holdings_as_of_date=holdings_as_of_date,
            evidence_refs=evidence_refs,
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

    def save_mandate(
        self,
        context: UserContext,
        mandate: InvestmentMandate,
    ) -> None:
        """Save one immutable mandate for the current user."""

        self.research_state.list_holdings(context)
        self.repository.save_mandate(context.user_id, mandate)

    def get_mandate(
        self,
        context: UserContext,
        mandate_id: str,
    ) -> InvestmentMandate | None:
        """Return one mandate scoped to the current user."""

        self.research_state.list_holdings(context)
        return self.repository.get_mandate(
            context.user_id,
            mandate_id,
        )

    def save_constraint_snapshot(
        self,
        context: UserContext,
        snapshot: PortfolioConstraintSnapshot,
    ) -> None:
        """Validate and save one frozen decision constraint set."""

        self.research_state.list_holdings(context)
        if snapshot.user_id != context.user_id:
            raise ValueError(
                "constraint snapshot user_id must match context"
            )
        mandate = self.repository.get_mandate(
            context.user_id,
            snapshot.mandate_id,
        )
        if mandate is None:
            raise ValueError(
                "constraint snapshot mandate is not recorded "
                "for this user"
            )
        if (
            snapshot.subject_asset_id
            != mandate.implementation_asset_id
        ):
            raise ValueError(
                "constraint subject must match mandate "
                "implementation asset"
            )
        valuation = self.repository.get(
            snapshot.portfolio_snapshot_id,
            context.user_id,
        )
        if valuation is None:
            raise ValueError(
                "constraint snapshot portfolio valuation is not "
                "recorded for this user"
            )
        if valuation.snapshot.as_of_date != snapshot.as_of_date:
            raise ValueError(
                "constraint and portfolio snapshots must share "
                "as_of_date"
            )
        if valuation.snapshot.base_currency != snapshot.currency:
            raise ValueError(
                "constraint currency must match portfolio "
                "valuation base currency"
            )
        if (
            valuation.snapshot.holdings_as_of_date
            != snapshot.as_of_date
            or not valuation.snapshot.evidence_refs
        ):
            raise ValueError(
                "constraint snapshot requires portfolio holdings "
                "and prices confirmed at the cutoff"
            )
        if (
            portfolio_valuation_digest(valuation)
            != snapshot.portfolio_valuation_digest
        ):
            raise ValueError(
                "constraint snapshot portfolio valuation digest "
                "mismatch"
            )
        self.repository.save_constraint_snapshot(snapshot)

    def get_constraint_snapshot(
        self,
        context: UserContext,
        snapshot_id: str,
    ) -> PortfolioConstraintSnapshot | None:
        """Return one constraint snapshot scoped to this user."""

        self.research_state.list_holdings(context)
        return self.repository.get_constraint_snapshot(
            context.user_id,
            snapshot_id,
        )
