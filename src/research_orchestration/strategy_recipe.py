"""Adapter that lets the Q4 recipe (strategy_evaluation) reach the strategy entries (stage 9 slice 9)."""

from __future__ import annotations

from typing import Any

from .strategy_archive import StrategyArchiveService
from .strategy_evaluate import StrategyEvaluateService
from .strategy_track import StrategyTrackService


class StrategyRecipeRunner:
    def __init__(
        self,
        *,
        archives: StrategyArchiveService,
        evaluate: StrategyEvaluateService,
        track: StrategyTrackService,
        allow_remote: bool = True,
    ) -> None:
        self.archives = archives
        self.evaluate_service = evaluate
        self.track_service = track
        self.allow_remote = allow_remote

    def find(self, text: str, as_of_date: str) -> str | None:
        """A registered strategy by id or by exact name; free-text rules are not guessed."""

        key = text.strip()
        for row in self.archives.list_archives(as_of_date)["strategies"]:
            if key in (row["strategy_id"], row["name"]):
                return row["strategy_id"]
        return None

    def templates(self) -> dict[str, Any]:
        return self.archives.templates()

    def show(self, strategy_id: str, as_of_date: str) -> dict[str, Any]:
        return self.archives.show(strategy_id, as_of_date)

    def evaluate(self, strategy_id: str, as_of_date: str) -> dict[str, Any]:
        return self.evaluate_service.evaluate(strategy_id, as_of_date=as_of_date, allow_remote=self.allow_remote)

    def track(self, strategy_id: str, as_of_date: str) -> dict[str, Any]:
        return self.track_service.track(strategy_id, as_of_date=as_of_date, allow_remote=self.allow_remote)
