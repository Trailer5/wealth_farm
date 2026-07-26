"""Service boundary for user-owned manual research state."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from src.master_data import MasterDataService
from src.user_space import UserContext, UserSpaceService

from .models import AnalysisReport, HoldingPosition, Watchlist, WatchlistItem
from .sqlite_repository import SQLiteResearchStateRepository


class ResearchStateService:
    """Manage watchlists, current positions, and saved analysis reports."""

    def __init__(
        self,
        repository: SQLiteResearchStateRepository,
        user_space: UserSpaceService,
        master_data: MasterDataService,
    ) -> None:
        self.repository = repository
        self.user_space = user_space
        self.master_data = master_data
        self.repository.initialize()

    def get_or_create_default_watchlist(self, context: UserContext) -> Watchlist:
        """Return the user's default watchlist."""

        self._require_user(context)
        existing = self.repository.get_default_watchlist(context.user_id)
        if existing is not None:
            return existing
        return self.create_watchlist(
            context,
            name="默认自选",
            description="",
            is_default=True,
        )

    def create_watchlist(
        self,
        context: UserContext,
        *,
        name: str,
        description: str = "",
        is_default: bool = False,
    ) -> Watchlist:
        """Create one watchlist for the current user."""

        self._require_user(context)
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("watchlist name must not be empty")
        now = _now()
        watchlist = Watchlist(
            watchlist_id=f"watchlist:{uuid4()}",
            user_id=context.user_id,
            name=normalized_name,
            description=description.strip(),
            is_default=is_default,
            created_at=now,
            updated_at=now,
        )
        self.repository.insert_watchlist(watchlist)
        return watchlist

    def list_watchlists(self, context: UserContext) -> list[Watchlist]:
        self._require_user(context)
        return self.repository.list_watchlists(context.user_id)

    def add_watchlist_item(
        self,
        context: UserContext,
        *,
        asset_id: str,
        watchlist_id: str | None = None,
        reason: str = "",
        tags: tuple[str, ...] = (),
        status: str = "active",
    ) -> WatchlistItem:
        """Add or update one asset in a user watchlist."""

        self._require_asset(asset_id)
        watchlist = (
            self.get_or_create_default_watchlist(context)
            if watchlist_id is None
            else self._require_watchlist(context, watchlist_id)
        )
        now = _now()
        item = WatchlistItem(
            watchlist_id=watchlist.watchlist_id,
            asset_id=asset_id,
            reason=reason.strip(),
            tags=tuple(sorted({tag.strip() for tag in tags if tag.strip()})),
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.repository.upsert_watchlist_item(item)
        return item

    def list_watchlist_items(
        self,
        context: UserContext,
        watchlist_id: str | None = None,
    ) -> list[WatchlistItem]:
        watchlist = (
            self.get_or_create_default_watchlist(context)
            if watchlist_id is None
            else self._require_watchlist(context, watchlist_id)
        )
        return self.repository.list_watchlist_items(
            context.user_id,
            watchlist.watchlist_id,
        )

    def remove_watchlist_item(
        self,
        context: UserContext,
        *,
        asset_id: str,
        watchlist_id: str | None = None,
    ) -> bool:
        watchlist = (
            self.get_or_create_default_watchlist(context)
            if watchlist_id is None
            else self._require_watchlist(context, watchlist_id)
        )
        return self.repository.remove_watchlist_item(
            watchlist.watchlist_id,
            asset_id,
        )

    def upsert_holding(
        self,
        context: UserContext,
        *,
        asset_id: str,
        quantity: float,
        average_cost: float | None = None,
        currency: str = "CNY",
        note: str = "",
    ) -> HoldingPosition:
        """Save the current position snapshot for one asset."""

        self._require_user(context)
        self._require_asset(asset_id)
        if average_cost is not None and average_cost < 0:
            raise ValueError("average_cost must not be negative")
        now = _now()
        position = HoldingPosition(
            user_id=context.user_id,
            asset_id=asset_id,
            quantity=float(quantity),
            average_cost=(
                float(average_cost)
                if average_cost is not None
                else None
            ),
            currency=currency.strip().upper() or "CNY",
            note=note.strip(),
            created_at=now,
            updated_at=now,
        )
        self.repository.upsert_holding(position)
        return position

    def list_holdings(self, context: UserContext) -> list[HoldingPosition]:
        self._require_user(context)
        return self.repository.list_holdings(context.user_id)

    def remove_holding(
        self,
        context: UserContext,
        *,
        asset_id: str,
    ) -> bool:
        """Remove one current position from the user's holdings."""

        self._require_user(context)
        return self.repository.remove_holding(
            context.user_id,
            asset_id,
        )

    def save_report(
        self,
        context: UserContext,
        *,
        asset_id: str,
        title: str,
        summary: str,
        content_markdown: str,
        status: str = "draft",
    ) -> AnalysisReport:
        """Save a new analysis report revision."""

        self._require_user(context)
        self._require_asset(asset_id)
        if status not in {"draft", "final"}:
            raise ValueError("report status must be draft or final")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("report title must not be empty")
        if not content_markdown.strip():
            raise ValueError("report content must not be empty")
        now = _now()
        report = AnalysisReport(
            report_id=f"report:{uuid4()}",
            user_id=context.user_id,
            asset_id=asset_id,
            title=normalized_title,
            summary=summary.strip(),
            content_markdown=content_markdown,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.repository.insert_report(report)
        return report

    def get_report(
        self,
        context: UserContext,
        report_id: str,
    ) -> AnalysisReport | None:
        self._require_user(context)
        return self.repository.get_report(report_id, context.user_id)

    def list_reports(
        self,
        context: UserContext,
        asset_id: str | None = None,
    ) -> list[AnalysisReport]:
        self._require_user(context)
        return self.repository.list_reports(context.user_id, asset_id)

    def _require_user(self, context: UserContext) -> None:
        if self.user_space.get_user(context.user_id) is None:
            raise KeyError(f"Unknown user_id: {context.user_id}")

    def _require_asset(self, asset_id: str) -> None:
        if self.master_data.get_asset(asset_id) is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")

    def _require_watchlist(
        self,
        context: UserContext,
        watchlist_id: str,
    ) -> Watchlist:
        self._require_user(context)
        watchlist = self.repository.get_watchlist(
            watchlist_id,
            context.user_id,
        )
        if watchlist is None:
            raise KeyError(f"Unknown watchlist_id for user: {watchlist_id}")
        return watchlist


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
