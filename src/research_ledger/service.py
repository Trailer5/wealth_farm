"""Record published judgements and their later settlements and reviews."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from src.research_contracts import Plan, View, ViewOrigin

from .models import (
    Artifact,
    LedgerAccessError,
    LedgerConflictError,
    LedgerEntry,
    LedgerEntryType,
    LedgerScope,
    Review,
    Settlement,
    SettlementStatus,
    build_entry,
)
from .sqlite_repository import SQLiteResearchLedgerRepository


class ResearchLedgerService:
    def __init__(
        self,
        repository: SQLiteResearchLedgerRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.repository.initialize()

    def record(
        self,
        artifacts: Sequence[Artifact],
        *,
        scope: LedgerScope,
        user_id: str | None,
        source_ref: str,
    ) -> tuple[LedgerEntry, ...]:
        """Append all artifacts of one source atomically and idempotently."""

        recorded_at = self._now()
        entries = []
        for artifact in artifacts:
            _check_ownership(artifact, scope=scope, user_id=user_id)
            entries.append(
                build_entry(
                    artifact,
                    scope=scope,
                    user_id=user_id,
                    source_ref=source_ref,
                    recorded_at=recorded_at,
                )
            )
        return self.repository.append_entries(entries)

    def record_user_view(
        self,
        view: View,
        *,
        source_ref: str,
    ) -> LedgerEntry:
        if view.origin is not ViewOrigin.USER:
            raise ValueError("record_user_view only accepts user views")
        (entry,) = self.record(
            (view,),
            scope=LedgerScope.PERSONAL,
            user_id=view.user_id,
            source_ref=source_ref,
        )
        return entry

    def record_revision(
        self,
        artifact: Artifact,
        *,
        supersedes_entry_id: str,
        user_id: str | None,
        source_ref: str,
    ) -> LedgerEntry:
        previous = self.get_entry(supersedes_entry_id, user_id=user_id)
        _check_ownership(artifact, scope=previous.scope, user_id=user_id)
        entry = build_entry(
            artifact,
            scope=previous.scope,
            user_id=previous.user_id,
            source_ref=source_ref,
            recorded_at=self._now(),
            supersedes_entry_id=previous.entry_id,
        )
        if entry.entry_type is not previous.entry_type:
            raise ValueError("a revision must keep the entry type")
        if entry.entry_id == previous.entry_id:
            raise LedgerConflictError("a revision needs a new artifact ID")
        (stored,) = self.repository.append_entries((entry,))
        return stored

    def get_entry(self, entry_id: str, *, user_id: str | None) -> LedgerEntry:
        entry = self.repository.get_entry(entry_id)
        if entry is None:
            raise KeyError(f"Unknown ledger entry: {entry_id}")
        if not entry.visible_to(user_id):
            raise LedgerAccessError(
                f"Ledger entry {entry_id} belongs to another user"
            )
        return entry

    def list_entries(
        self,
        *,
        user_id: str | None,
        entry_type: LedgerEntryType | None = None,
        subject_id: str | None = None,
        source_ref: str | None = None,
        active_on: str | None = None,
    ) -> tuple[LedgerEntry, ...]:
        """With active_on, keep unexpired entries that were not revised."""

        entries = self.repository.list_entries(
            user_id=user_id,
            entry_type=entry_type,
            subject_id=subject_id,
            source_ref=source_ref,
        )
        if active_on is None:
            return entries
        superseded = self.repository.superseded_entry_ids()
        return tuple(
            item
            for item in entries
            if item.as_of_date <= active_on <= item.effective_until
            and item.entry_id not in superseded
        )

    def has_source(self, source_ref: str) -> bool:
        return self.repository.has_source(source_ref)

    def append_settlement(self, settlement: Settlement) -> Settlement:
        entry = self.repository.get_entry(settlement.entry_id)
        if entry is None:
            raise KeyError(f"Unknown ledger entry: {settlement.entry_id}")
        if settlement.checkpoint_date < entry.as_of_date:
            raise ValueError("checkpoint precedes the ledger entry")
        stored, _ = self.repository.append_settlement(settlement)
        return stored

    def list_settlements(
        self,
        entry_id: str,
        *,
        user_id: str | None,
    ) -> tuple[Settlement, ...]:
        self.get_entry(entry_id, user_id=user_id)
        return self.repository.list_settlements(entry_id)

    def track_record(
        self,
        *,
        user_id: str | None,
        entry_type: LedgerEntryType | None = None,
        subject_id: str | None = None,
        active_on: str | None = None,
    ) -> dict[str, Any]:
        """Entries with the latest settlement of each checkpoint, plus counts."""

        superseded = self.repository.superseded_entry_ids()
        counts: Counter[str] = Counter()
        items = []
        for entry in self.list_entries(
            user_id=user_id,
            entry_type=entry_type,
            subject_id=subject_id,
            active_on=active_on,
        ):
            latest: dict[str, Settlement] = {}
            for item in self.repository.list_settlements(entry.entry_id):
                current = latest.get(item.checkpoint_date)
                if current is None or item.computed_at > current.computed_at:
                    latest[item.checkpoint_date] = item
            settlements = [latest[key] for key in sorted(latest)]
            for item in settlements:
                counts[
                    item.claim_result.value
                    if item.status is SettlementStatus.SETTLED
                    else item.status.value
                ] += 1
            items.append(
                {
                    "entry_id": entry.entry_id,
                    "entry_type": entry.entry_type.value,
                    "scope": entry.scope.value,
                    "subject_id": entry.subject_id,
                    "source_ref": entry.source_ref,
                    "as_of_date": entry.as_of_date,
                    "effective_until": entry.effective_until,
                    "superseded": entry.entry_id in superseded,
                    "artifact": entry.payload,
                    "settlements": [item.to_dict() for item in settlements],
                }
            )
        return {"entries": items, "checkpoint_counts": dict(counts)}

    def append_review(self, review: Review) -> Review:
        settlement = self.repository.get_settlement(review.settlement_id)
        if settlement is None or settlement.entry_id != review.entry_id:
            raise ValueError("a review must cite a settlement of its entry")
        if settlement.status is SettlementStatus.NOT_SETTLEABLE:
            raise ValueError("not_settleable checkpoints cannot be reviewed")
        return self.repository.append_review(review)

    def list_reviews(
        self,
        entry_id: str,
        *,
        user_id: str | None,
    ) -> tuple[Review, ...]:
        self.get_entry(entry_id, user_id=user_id)
        return self.repository.list_reviews(entry_id)

    def _now(self) -> str:
        return self.clock().isoformat()


def _check_ownership(
    artifact: Artifact,
    *,
    scope: LedgerScope,
    user_id: str | None,
) -> None:
    owner = None
    if isinstance(artifact, Plan):
        owner = artifact.user_id
    elif isinstance(artifact, View) and artifact.origin is ViewOrigin.USER:
        owner = artifact.user_id
    if owner is not None and (
        scope is not LedgerScope.PERSONAL or user_id != owner
    ):
        raise ValueError("user-owned artifacts must be personal entries")
