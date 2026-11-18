"""Agent-facing execution registry, calendar and follow-up routing.

Design notes: docs/modules/4-个人数据/用户空间与研究状态模块.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.user_space import UserSpaceService
from src.user_space.plan_registry import PlanRegistryService

_CLI = "python -m src.research_orchestration.cli"


class ExecutionPlanService:
    """Public orchestration entry; validate user ownership before every operation.

    Registration takes a structured plan, not extracted prose. See plan-register
    --help for its schema. No operation fetches remote data or places trades.
    """

    def __init__(self, *, registry: PlanRegistryService, users: UserSpaceService):
        self.registry = registry
        self.users = users

    def _authorize(self, user_id: str) -> None:
        self.users.context_for_user(user_id)

    @staticmethod
    def _actions(user_id: str, plan_ids: list[str]) -> list[dict[str, str]]:
        return [{"command": f"{_CLI} plan-show --user-id '{user_id.replace(chr(39), chr(39) * 2)}' --plan-id {plan_id}",
                 "reason": "读取完整计划、来源、修订与执行记录，再核实条件并处理当前节点。"}
                for plan_id in dict.fromkeys(plan_ids)]

    def register(self, *, user_id: str, registration_key: str, plan: dict[str, Any],
                 expected_revision: int | None = None) -> dict[str, Any]:
        """Register a full plan snapshot; revisions require the last observed revision."""

        self._authorize(user_id)
        result = self.registry.register(user_id=user_id, registration_key=registration_key, plan=plan,
                                        expected_revision=expected_revision)
        return {**result, "user_id": user_id, "gaps": [],
                "next_actions": self._actions(user_id, [result["plan_id"]])}

    def show(self, *, user_id: str, plan_id: str, known_at: str | None = None) -> dict[str, Any]:
        """Inspect the plan and all history, including tasks removed by a later revision."""

        self._authorize(user_id)
        return {**self.registry.show(user_id=user_id, plan_id=plan_id, known_at=known_at), "gaps": [],
                "next_actions": [{"command": f"{_CLI} task-resolve --help",
                                  "reason": "核实任务条件后，记录结论和证据；未完成必须安排下一检查时间。"}]}

    def calendar(self, *, user_id: str, at: str | None = None, through: str | None = None,
                 known_at: str | None = None, include_closed: bool = False) -> dict[str, Any]:
        """Read overdue/current/future work without running research or changing its state."""

        self._authorize(user_id)
        result = self.registry.calendar(user_id=user_id, at=at or datetime.now(timezone.utc).isoformat(),
                                        through=through, known_at=known_at, include_closed=include_closed)
        pending = [row for group in ("overdue", "due", "blocked", "upcoming") for row in result[group]]
        return {**result, "next_actions": self._actions(user_id, [row["plan_id"] for row in pending])}

    def resolve(self, *, user_id: str, **handling: Any) -> dict[str, Any]:
        """Record one occurrence, with evidence and explicit confirmation for execution."""

        self._authorize(user_id)
        result = self.registry.resolve(user_id=user_id, **handling)
        return {**result, "user_id": user_id, "plan_id": handling["plan_id"], "gaps": [],
                "next_actions": self._actions(user_id, [handling["plan_id"]])}

    def review(self, *, user_id: str, plan_id: str, at: str | None = None,
               known_at: str | None = None) -> dict[str, Any]:
        """Return execution history separately from investment-judgement settlement."""

        self._authorize(user_id)
        return {**self.registry.review(user_id=user_id, plan_id=plan_id,
                                       at=at or datetime.now(timezone.utc).isoformat(), known_at=known_at),
            "next_actions": self._actions(user_id, [plan_id])}