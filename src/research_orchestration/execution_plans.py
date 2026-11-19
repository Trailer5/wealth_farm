"""Agent-facing execution registry, calendar and follow-up routing.

Design notes: docs/modules/4-个人数据/用户空间与研究状态模块.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from src.user_space import UserSpaceService
from src.user_space.plan_registry import PlanRegistryService

from .research_rules import audit_rules

_CLI = "python -m src.research_orchestration.cli"


class ResearchRuleChanged(ValueError):
    code = "research_rule_changed"
    retryable = False


class ExecutionPlanService:
    """Public orchestration entry; validate user ownership before every operation.

    Registration takes a structured plan, not extracted prose. See plan-register
    --help for its schema. No operation fetches remote data or places trades.
    """

    def __init__(self, *, registry: PlanRegistryService, users: UserSpaceService,
                 rule_source: Callable[[str], list[dict[str, Any]]] | None = None):
        self.registry = registry
        self.users = users
        self.rule_source = rule_source or (lambda user: [])

    def _authorize(self, user_id: str) -> None:
        self.users.context_for_user(user_id)

    @staticmethod
    def _actions(user_id: str, plan_ids: list[str]) -> list[dict[str, str]]:
        return [{"command": f"{_CLI} plan-show --user-id '{user_id.replace(chr(39), chr(39) * 2)}' --plan-id {plan_id}",
                 "operation": "plan-show", "arguments": {"user_id": user_id, "plan_id": plan_id},
                 "reason": "读取完整计划、来源、修订与执行记录，再核实条件并处理当前节点。"}
                for plan_id in dict.fromkeys(plan_ids)]

    def register(self, *, user_id: str, registration_key: str, plan: dict[str, Any],
                 expected_revision: int | None = None) -> dict[str, Any]:
        """Register a full plan snapshot; revisions require the last observed revision."""

        self._authorize(user_id)
        audit = audit_rules(plan, self.rule_source(user_id))
        if audit["gaps"]:
            raise ResearchRuleChanged("research rule references changed: " + "; ".join(audit["gaps"]))
        result = self.registry.register(user_id=user_id, registration_key=registration_key, plan=plan,
                                        expected_revision=expected_revision)
        return {**result, "user_id": user_id, "gaps": [],
                "next_actions": self._actions(user_id, [result["plan_id"]])}

    def show(self, *, user_id: str, plan_id: str, known_at: str | None = None,
             task_id: str | None = None, history: bool = True, include_audit: bool = True) -> dict[str, Any]:
        """Inspect the plan and all history, including tasks removed by a later revision."""

        self._authorize(user_id)
        result = self.registry.show(user_id=user_id, plan_id=plan_id, known_at=known_at)
        audit = audit_rules(result["plan"], self.rule_source(user_id)) if known_at is None else {
            "tasks": [], "uncovered_rules": [], "gaps": [], "state": "historical_not_evaluated",
            "notes": ["历史视图仅显示当时计划；不以当前研究规则冒充当时可得规则。"]}
        if task_id:
            tasks = [task for task in result["plan"]["tasks"] if task["task_id"] == task_id]
            if not tasks:
                raise KeyError("task not found")
            result["plan"] = {**result["plan"], "tasks": tasks}
            audit = {**audit, "tasks": [task for task in audit["tasks"] if task["task_id"] == task_id]}
        if not history:
            result = {key: value for key, value in result.items() if key not in ("revisions", "records")}
        return {**result, **({"rule_audit": audit} if include_audit else {"rule_links": audit["tasks"]}), "gaps": audit["gaps"],
                "next_actions": [{"command": f"{_CLI} task-resolve --help",
                                  "reason": "核实任务条件后，记录结论和证据；未完成必须安排下一检查时间。"}]}

    def context(self, *, user_id: str, as_of_date: str) -> dict[str, Any]:
        """Effective plan-scoped constraints; never overwrite the user's generic preference.

        Past dates use that UTC day's knowledge cutoff. Current/future dates use only
        information recorded now. Free-text constraints remain Agent-checked rules.
        """

        self._authorize(user_id)
        today = self.registry.clock().astimezone(timezone.utc).date().isoformat()
        known_at = f"{as_of_date}T23:59:59.999999Z" if as_of_date < today else None
        plans = self.registry.list_plans(user_id=user_id, known_at=known_at)
        active = [{"plan_id": row["plan_id"], "revision": row["revision"], "title": row["plan"]["title"],
                   "constraints": row["plan"].get("constraints", []),
                   "uncertainties": row["plan"].get("uncertainties", []),
                   "source_refs": row["plan"]["source_refs"]}
                  for row in plans if row["plan"]["status"] == "active"]
        return {"plans": active, "notes": ["计划例外仅在所属计划范围生效，不覆盖通用偏好；行动前核对最新状态。"],
                "next_actions": self._actions(user_id, [row["plan_id"] for row in active]), "gaps": []}

    def calendar(self, *, user_id: str, at: str | None = None, through: str | None = None,
                 known_at: str | None = None, include_closed: bool = False, details: bool = False,
                 fields: list[str] | None = None, limit: int = 100, offset: int = 0,
                 plan_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        """Read overdue/current/future work without running research or changing its state."""

        self._authorize(user_id)
        if not 1 <= limit <= 500 or offset < 0:
            raise ValueError("limit must be 1..500 and offset must be nonnegative")
        result = self.registry.calendar(user_id=user_id, at=at or datetime.now(timezone.utc).isoformat(),
                                        through=through, known_at=known_at, include_closed=include_closed)
        plans = self.registry.list_plans(user_id=user_id, known_at=known_at)
        rules = self.rule_source(user_id)
        audits = {plan["plan_id"]: audit_rules(plan["plan"], rules) for plan in plans}
        for group in ("overdue", "due", "upcoming", "blocked", "closed"):
            for row in list(result[group]):
                audit = audits[row["plan_id"]]
                checked = next(task for task in audit["tasks"] if task["task_id"] == row["task_id"]) if known_at is None else {
                    "state": "historical_not_evaluated", "rules": [], "issues": []}
                row["rule_state"], row["linked_rules"] = checked["state"], checked["rules"]
                if checked["issues"]:
                    result["gaps"].extend(f"{row['plan_id']} / {row['task_id']}: {issue['code']}" for issue in checked["issues"])
                    if group not in ("closed", "blocked"):
                        result[group].remove(row)
                        result["blocked"].append(row)
        groups = ("overdue", "due", "blocked", "upcoming", "closed")
        for group in groups:
            result[group] = [row for row in result[group] if (plan_id is None or row["plan_id"] == plan_id)
                             and (task_id is None or row["task_id"] == task_id)]
        result["counts"] = {key: len(result[key]) for key in groups}
        all_rows = [(group, row) for group in groups for row in result[group]]
        selected = all_rows[offset:offset + limit]
        shared = {row["plan_id"]: {"plan_id": row["plan_id"], "title": row["plan_title"], "revision": row["revision"],
                                   "constraints": row["plan_constraints"], "uncertainties": row["plan_uncertainties"],
                                   "source_refs": row["plan_source_refs"]} for _, row in selected}
        compact_fields = {"plan_id", "revision", "task_id", "title", "kind", "priority", "occurrence", "ready_at",
                          "deadline_at", "status", "plan_status", "next_check", "blocked_by", "overdue", "rule_state"}
        allowed = set().union(*(row.keys() for _, row in all_rows)) if all_rows else compact_fields
        if fields is not None and set(fields) - allowed:
            raise ValueError("unknown calendar fields: " + ", ".join(sorted(set(fields) - allowed)))
        for group in groups:
            result[group] = []
        actions = []
        for group, row in selected:
            keep = set(fields) | {"plan_id", "task_id", "occurrence"} if fields is not None else set(row) if details else compact_fields
            result[group].append({key: value for key, value in row.items() if key in keep})
            if group != "closed":
                actions.append({"operation": "plan-show", "arguments": {"user_id": user_id, "plan_id": row["plan_id"],
                    "task_id": row["task_id"]}, "reason": "读取本节点的完整条件、研究规则及证据；不必重读整份计划。"})
        return {**result, "plans": list(shared.values()), "next_actions": actions,
                "page": {"offset": offset, "limit": limit, "total": len(all_rows),
                         "next_offset": offset + limit if offset + limit < len(all_rows) else None}}

    def resolve(self, *, user_id: str, **handling: Any) -> dict[str, Any]:
        """Record one occurrence, with evidence and explicit confirmation for execution."""

        self._authorize(user_id)
        if handling.get("outcome") in {"completed", "executed"}:
            plan = self.registry.show(user_id=user_id, plan_id=handling["plan_id"])
            checked = next(task for task in audit_rules(plan["plan"], self.rule_source(user_id))["tasks"]
                           if task["task_id"] == handling["task_id"])
            if checked["issues"]:
                raise ResearchRuleChanged("research rule changed; inspect plan-show and revise the node before closing")
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