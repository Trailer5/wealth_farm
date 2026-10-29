"""Build a state archive (market or macro) and record its assessment in the ledger."""

from __future__ import annotations

from typing import Any, Protocol

from src.research_ledger import LedgerScope, ResearchLedgerService


class StateArchiveBuilder(Protocol):
    def build(self, as_of_date: str, *, allow_remote: bool = True, **kwargs: Any) -> Any: ...


class StateArchiveRecorder:
    def __init__(
        self,
        *,
        archive: StateArchiveBuilder,
        ledger: ResearchLedgerService,
        source_prefix: str,
    ) -> None:
        self.archive = archive
        self.ledger = ledger
        self.source_prefix = source_prefix

    def run(
        self,
        as_of_date: str,
        *,
        record: bool = True,
        allow_remote: bool = True,
        **build_kwargs: Any,
    ) -> dict[str, Any]:
        """Archive JSON; recording is idempotent because the ID hashes the content."""

        archive = self.archive.build(as_of_date, allow_remote=allow_remote, **build_kwargs)
        payload = archive.to_dict()
        payload["ledger_entry_id"] = None
        if record:
            (entry,) = self.ledger.record(
                (archive.assessment,),
                scope=LedgerScope.OBJECTIVE,
                user_id=None,
                source_ref=":".join(
                    (self.source_prefix, *map(str, build_kwargs.values()), as_of_date)
                ),
            )
            payload["ledger_entry_id"] = entry.entry_id
        return payload
