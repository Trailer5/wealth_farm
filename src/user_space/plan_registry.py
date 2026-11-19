"""Execution commitments and append-only history.

Design: docs/modules/4-个人数据/用户空间与研究状态模块.md, execution registry.
Research judgements and trade records remain in their original owning modules.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .plan_schedule import boundary, instant, normalize_schedule, occurrences

TERMINAL = frozenset({"completed", "not_triggered", "cancelled", "executed"})
OUTCOMES = TERMINAL | {"deferred", "blocked", "awaiting_confirmation"}


class PlanRevisionConflict(ValueError):
    code = "revision_conflict"
    retryable = False


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value.strip()


def _texts(value: Any, label: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"{label} must be a list" + (" with at least one reference" if required else ""))
    return list(dict.fromkeys(_text(item, label) for item in value))


def normalize_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a full plan snapshot; task IDs stay stable within its plan.

    Each task needs task_id, title, instruction, completion and schedule. Optional
    kind is observe/review/action, priority is high/normal, depends_on names other
    nonrepeating tasks. A condition requires on_met/on_unmet branches. Source refs
    point to reports, debates, ledger entries, observables or other research facts.
    """

    allowed = {"title", "objective", "status", "source_refs", "tasks", "reason", "constraints", "uncertainties", "rule_exclusions"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid plan fields")
    result = {key: _text(payload.get(key), key) for key in ("title", "objective")}
    result["status"] = payload.get("status", "draft")
    if result["status"] not in {"draft", "active", "paused", "completed", "cancelled"}:
        raise ValueError("invalid plan status")
    result["source_refs"] = _texts(payload.get("source_refs"), "source_refs", required=True)
    result["reason"] = _text(payload.get("reason"), "reason")
    for key in ("constraints", "uncertainties"):
        if key in payload:
            result[key] = _texts(payload[key], key)
    if "rule_exclusions" in payload:
        if not isinstance(payload["rule_exclusions"], dict):
            raise ValueError("rule_exclusions must map rule IDs to reasons")
        result["rule_exclusions"] = {_text(key, "rule_id"): _text(reason, "exclusion reason")
                                     for key, reason in payload["rule_exclusions"].items()}
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or (result["status"] == "active" and not tasks):
        raise ValueError("active plan needs tasks")
    result["tasks"] = []
    task_fields = {"task_id", "title", "instruction", "completion", "schedule", "kind", "priority",
                   "condition", "on_met", "on_unmet", "depends_on", "source_refs", "source_excerpt", "rule_refs"}
    for raw in tasks:
        if not isinstance(raw, dict) or set(raw) - task_fields:
            raise ValueError("invalid task fields")
        task = {key: _text(raw.get(key), key) for key in ("task_id", "title", "instruction", "completion")}
        task["kind"] = raw.get("kind", "observe")
        task["priority"] = raw.get("priority", "normal")
        if task["kind"] not in {"observe", "review", "action"} or task["priority"] not in {"high", "normal"}:
            raise ValueError("invalid task kind or priority")
        task["schedule"] = normalize_schedule(raw["schedule"])
        task["depends_on"] = _texts(raw.get("depends_on", []), "depends_on")
        task["source_refs"] = _texts(raw.get("source_refs", []), "source_refs")
        if "source_excerpt" in raw:
            task["source_excerpt"] = _text(raw["source_excerpt"], "source_excerpt")
        if "rule_refs" in raw:
            if not isinstance(raw["rule_refs"], list):
                raise ValueError("rule_refs must be a list of rule_id/content_hash pairs")
            task["rule_refs"] = []
            for ref in raw["rule_refs"]:
                if not isinstance(ref, dict) or set(ref) != {"rule_id", "content_hash"}:
                    raise ValueError("rule_refs need rule_id and content_hash")
                digest = _text(ref["content_hash"], "content_hash")
                if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                    raise ValueError("content_hash must be a lowercase SHA256 digest")
                task["rule_refs"].append({"rule_id": _text(ref["rule_id"], "rule_id"), "content_hash": digest})
        for key in ("condition", "on_met", "on_unmet"):
            task[key] = _text(raw[key], key) if raw.get(key) else None
        if task["condition"] and not (task["on_met"] and task["on_unmet"]):
            raise ValueError("conditional tasks need on_met and on_unmet")
        result["tasks"].append(task)
    indexed = {task["task_id"]: task for task in result["tasks"]}
    if len(indexed) != len(tasks):
        raise ValueError("duplicate task_id")

    def visit(task_id: str, ancestors: set[str]) -> None:
        if task_id in ancestors:
            raise ValueError("cyclic task dependency")
        for dependency in indexed[task_id]["depends_on"]:
            if dependency not in indexed or indexed[dependency]["schedule"].get("repeat_days"):
                raise ValueError("dependency must name an existing nonrepeating task")
            visit(dependency, ancestors | {task_id})

    for task_id in indexed:
        visit(task_id, set())
    return result


class SQLitePlanRegistryRepository:
    """Own execution registry tables and atomic compare-and-append transactions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._memory = sqlite3.connect(":memory:") if str(db_path) == ":memory:" else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS execution_plans (
                    user_id TEXT NOT NULL, plan_id TEXT NOT NULL, registration_key TEXT NOT NULL,
                    PRIMARY KEY (user_id, plan_id), UNIQUE (user_id, registration_key)
                );
                CREATE TABLE IF NOT EXISTS execution_plan_revisions (
                    user_id TEXT NOT NULL, plan_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    body TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, plan_id, revision)
                );
                CREATE TABLE IF NOT EXISTS execution_task_records (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL, plan_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    body TEXT NOT NULL, recorded_at TEXT NOT NULL,
                    UNIQUE (user_id, plan_id, request_id)
                );
            """)

    @contextmanager
    def connect(self):
        conn = self._memory or sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            if self._memory is None:
                conn.close()

    def read(self, user_id: str, *, known_at: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return self.read_on(conn, user_id, known_at=known_at)

    @staticmethod
    def read_on(conn, user_id: str, *, known_at: str) -> list[dict[str, Any]]:
        roots = conn.execute("SELECT * FROM execution_plans WHERE user_id = ?", (user_id,)).fetchall()
        result = []
        for root in roots:
            revisions = conn.execute(
                "SELECT * FROM execution_plan_revisions WHERE user_id=? AND plan_id=? AND recorded_at<=? ORDER BY revision",
                (user_id, root["plan_id"], known_at),
            ).fetchall()
            if not revisions:
                continue
            records = conn.execute(
                "SELECT * FROM execution_task_records WHERE user_id=? AND plan_id=? AND recorded_at<=? ORDER BY sequence",
                (user_id, root["plan_id"], known_at),
            ).fetchall()
            result.append({
                **dict(root), "revision": revisions[-1]["revision"], "plan": json.loads(revisions[-1]["body"]),
                "revisions": [{"revision": row["revision"], "recorded_at": row["recorded_at"],
                               "plan": json.loads(row["body"])} for row in revisions],
                "records": [{**json.loads(row["body"]), "recorded_at": row["recorded_at"]} for row in records],
            })
        return result


class PlanRegistryService:
    """Public personal execution registry; no network, automatic trade or judgement settlement.

    All writes use current UTC recorded_at, stable request keys and optimistic revision
    checks. Historical queries may supply known_at to exclude information recorded later.
    Calendar defaults to current known information, even when previewing another date.
    """

    def __init__(self, repository: SQLitePlanRegistryRepository, *, clock: Callable[[], datetime] | None = None):
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat()

    def _known(self, known_at: str | None) -> str:
        now = self._now()
        if known_at is None:
            return now
        cutoff = instant(known_at).isoformat()
        if cutoff > now:
            raise ValueError("known_at cannot be in the future")
        return cutoff

    def register(self, *, user_id: str, registration_key: str, plan: dict[str, Any],
                 expected_revision: int | None = None) -> dict[str, Any]:
        """Register a full snapshot; same key/content is a no-op, changed content needs expected_revision.

        Tasks with handling history are immutable: give changed commitments a new task_id.
        Removed tasks disappear from future calendars but remain in revision/record history.
        """

        user_id, registration_key = _text(user_id, "user_id"), _text(registration_key, "registration_key")
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("expected_revision must be a positive integer")
        body = normalize_plan(plan)
        now = self._now()
        with self.repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = next((row for row in self.repository.read_on(conn, user_id, known_at=now)
                            if row["registration_key"] == registration_key), None)
            if current and current["plan"] == body:
                return {"plan_id": current["plan_id"], "revision": current["revision"], "changed": False}
            if current:
                if expected_revision != current["revision"]:
                    raise PlanRevisionConflict("plan revision conflict; reload plan-show before revising")
                old = {task["task_id"]: task for task in current["plan"]["tasks"]}
                handled = {record["task_id"] for record in current["records"]}
                for task in body["tasks"]:
                    if task["task_id"] in handled and task != old.get(task["task_id"]):
                        raise ValueError("handled task is immutable; register a new task_id")
                plan_id, revision = current["plan_id"], current["revision"] + 1
            else:
                if expected_revision is not None:
                    raise ValueError("cannot revise a missing plan")
                plan_id, revision = f"execution-plan:{uuid4().hex}", 1
                conn.execute("INSERT INTO execution_plans VALUES (?,?,?)", (user_id, plan_id, registration_key))
            conn.execute("INSERT INTO execution_plan_revisions VALUES (?,?,?,?,?)",
                         (user_id, plan_id, revision, _json(body), now))
        return {"plan_id": plan_id, "revision": revision, "changed": True}

    def show(self, *, user_id: str, plan_id: str, known_at: str | None = None) -> dict[str, Any]:
        """Return the complete plan, revisions and handling evidence owned by this user."""

        return self._find(self.repository.read(user_id, known_at=self._known(known_at)), plan_id)

    def list_plans(self, *, user_id: str, known_at: str | None = None) -> list[dict[str, Any]]:
        """Current snapshots at a knowledge cutoff, without repeating revision/handling history."""

        return [{key: row[key] for key in ("user_id", "plan_id", "registration_key", "revision", "plan")}
                for row in self.repository.read(user_id, known_at=self._known(known_at))]

    @staticmethod
    def _find(plans: list[dict[str, Any]], plan_id: str) -> dict[str, Any]:
        for plan in plans:
            if plan["plan_id"] == plan_id:
                return plan
        raise ValueError("plan not found for this user and knowledge cutoff")

    def calendar(self, *, user_id: str, at: str, through: str | None = None,
                 known_at: str | None = None, include_closed: bool = False) -> dict[str, Any]:
        """Read-only global calendar: overdue, due, upcoming, blocked and optionally closed.

        Dates mean the entire local day of each task; offset timestamps are exact.
        through includes future readiness but never removes older unresolved items.
        Conditions are instructions for Agent verification, not evaluated expressions.
        """

        known = self._known(known_at)
        boundary(at, ZoneInfo("UTC"), end=True)
        if through is not None:
            boundary(through, ZoneInfo("UTC"), end=True)
        gaps = []
        groups: dict[str, list[dict[str, Any]]] = {key: [] for key in ("overdue", "due", "upcoming", "blocked", "closed")}
        for plan in self.repository.read(user_id, known_at=known):
            if plan["plan"]["status"] != "active" and not include_closed:
                continue
            latest = {(record["task_id"], record["occurrence"]): record for record in plan["records"]}
            successful = {record["task_id"] for record in latest.values() if record["outcome"] in {"completed", "executed"}}
            for task in plan["plan"]["tasks"]:
                zone = ZoneInfo(task["schedule"]["timezone"])
                current = boundary(at, zone, end=True)
                cutoff = boundary(through or at, zone, end=True)
                if cutoff < current:
                    raise ValueError("through precedes at")
                try:
                    scheduled = occurrences(task["schedule"], through or at)
                except (ValueError, KeyError) as exc:
                    gaps.append(f"{plan['plan_id']} / {task['task_id']} 日程不可展开：{exc}")
                    continue
                for occurrence in scheduled:
                    record = latest.get((task["task_id"], occurrence["occurrence"]))
                    outcome = record["outcome"] if record else "pending"
                    closed = outcome in TERMINAL or plan["plan"]["status"] in {"completed", "cancelled"}
                    if closed and not include_closed:
                        continue
                    next_check = record.get("next_check") if record else None
                    ready = instant(next_check) if next_check else instant(occurrence["ready_at"])
                    if ready > cutoff:
                        continue
                    missing = [key for key in task["depends_on"] if key not in successful]
                    if closed:
                        group = "closed"
                    elif plan["plan"]["status"] != "active" or missing or outcome in {"blocked", "awaiting_confirmation"}:
                        group = "blocked"
                    elif ready > current:
                        group = "upcoming"
                    else:
                        group = "overdue" if instant(occurrence["deadline_at"]) < current else "due"
                    groups[group].append({
                        "plan_id": plan["plan_id"], "plan_title": plan["plan"]["title"], "revision": plan["revision"],
                        **task, **occurrence, "status": outcome, "plan_status": plan["plan"]["status"],
                        "next_check": next_check, "blocked_by": missing,
                        "overdue": not closed and instant(occurrence["deadline_at"]) < current,
                        "last_record": record, "plan_source_refs": plan["plan"]["source_refs"],
                        "plan_constraints": plan["plan"].get("constraints", []),
                        "plan_uncertainties": plan["plan"].get("uncertainties", []),
                    })
        for rows in groups.values():
            rows.sort(key=lambda row: (row["priority"] != "high", row["next_check"] or row["ready_at"], row["plan_id"], row["task_id"]))
        return {"user_id": user_id, "at": at, "through": through or at, "known_at": known, **groups,
                "counts": {key: len(rows) for key, rows in groups.items()}, "gaps": gaps,
                "notes": ["日期查询覆盖任务所在时区的全天；精确检查请传带 UTC 偏移的时刻。",
                          "条件尚未自动核实；行动前必须复核最新持仓、现金、跨计划约束及用户确认。",
                          "known_at 是信息截止时刻；回放历史时显式提供，避免使用后来修订。"]}

    def resolve(self, *, user_id: str, plan_id: str, task_id: str, occurrence: str,
                expected_revision: int, request_id: str, outcome: str, note: str,
                evidence_refs: list[str], next_check: str | None = None,
                execution_ref: str | None = None) -> dict[str, Any]:
        """Append handling evidence, never infer a trade from a recommendation.

        Action tasks cannot be completed without an executed outcome and a trade/user
        confirmation reference. Blocked, deferred and awaiting_confirmation require a
        future next_check. not_triggered closes just this occurrence; use deferred to
        keep watching. Every retry must reuse request_id and identical arguments.
        """

        if outcome not in OUTCOMES:
            raise ValueError("invalid outcome")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        body = {"task_id": _text(task_id, "task_id"), "occurrence": _text(occurrence, "occurrence"),
                "revision": expected_revision, "request_id": _text(request_id, "request_id"), "outcome": outcome,
                "note": _text(note, "note"), "evidence_refs": _texts(evidence_refs, "evidence_refs", required=True),
                "next_check": instant(next_check).isoformat() if next_check else None,
                "execution_ref": _text(execution_ref, "execution_ref") if execution_ref else None}
        now = self._now()
        with self.repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            plan = self._find(self.repository.read_on(conn, user_id, known_at=now), plan_id)
            duplicate = next((record for record in plan["records"] if record["request_id"] == body["request_id"]), None)
            if duplicate:
                if {key: duplicate[key] for key in body} != body:
                    raise ValueError("request_id reused with different content")
                return {**duplicate, "changed": False}
            if plan["revision"] != expected_revision or plan["plan"]["status"] != "active":
                raise ValueError("plan is inactive or revision changed; reload plan-show")
            task = next((task for task in plan["plan"]["tasks"] if task["task_id"] == task_id), None)
            if task is None:
                raise ValueError("task not found")
            item = next((item for item in occurrences(task["schedule"], now) if item["occurrence"] == occurrence), None)
            if item is None:
                raise ValueError("occurrence is not yet due or does not exist")
            previous = next((record for record in reversed(plan["records"])
                             if record["task_id"] == task_id and record["occurrence"] == occurrence), None)
            if previous and previous["outcome"] in TERMINAL:
                raise ValueError("occurrence already closed")
            successful = {record["task_id"] for record in plan["records"] if record["outcome"] in {"completed", "executed"}}
            if outcome in {"completed", "executed"} and set(task["depends_on"]) - successful:
                raise ValueError("task dependencies are not completed")
            if (outcome == "completed" and task["kind"] == "action") or (
                outcome == "executed" and (task["kind"] != "action" or not body["execution_ref"])
            ):
                raise ValueError("action execution needs executed outcome and execution_ref; a recommendation is not a trade")
            if outcome in {"deferred", "blocked", "awaiting_confirmation"}:
                if not body["next_check"] or body["next_check"] <= now:
                    raise ValueError("unfinished handling requires a future next_check")
            elif body["next_check"]:
                raise ValueError("closed occurrence cannot have next_check")
            body["original_deadline_at"] = item["deadline_at"]
            conn.execute("INSERT INTO execution_task_records (user_id,plan_id,request_id,body,recorded_at) VALUES (?,?,?,?,?)",
                         (user_id, plan_id, request_id, _json(body), now))
        return {**body, "recorded_at": now, "changed": True}

    def review(self, *, user_id: str, plan_id: str, at: str, known_at: str | None = None) -> dict[str, Any]:
        """Descriptive execution review only, not a return attribution or prediction hit rate."""

        plan = self.show(user_id=user_id, plan_id=plan_id, known_at=known_at)
        calendar = self.calendar(user_id=user_id, at=at, known_at=known_at, include_closed=True)
        groups = {key: [row for row in calendar[key] if row["plan_id"] == plan_id]
                  for key in ("overdue", "due", "upcoming", "blocked", "closed")}
        latest = {(record["task_id"], record["occurrence"]): record for record in plan["records"]}
        counts = {outcome: sum(record["outcome"] == outcome for record in latest.values()) for outcome in sorted(OUTCOMES)}
        return {**plan, "at": at, "calendar": groups, "handling_counts": counts, "gaps": calendar["gaps"],
            "execution_summary": {
                "deferrals": sum(record["outcome"] == "deferred" for record in plan["records"]),
                "blocked_checks": sum(record["outcome"] == "blocked" for record in plan["records"]),
                "late_closures": sum(record["outcome"] in TERMINAL
                and record["recorded_at"] > record["original_deadline_at"]
                for record in latest.values()),
            },
                "limitations": ["任务完成不等于成交；executed 依赖所附成交或用户确认引用，日历不验证经纪商成交。",
                                "收益与判断对错仍由原观点/动作账本结算；不由任务完成率推断。"]}