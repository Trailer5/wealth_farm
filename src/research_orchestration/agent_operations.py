"""Preview and recover multi-product Agent operations using an explicit step journal.

Design notes: docs/modules/6-编排/问题路由模块.md. This is not a distributed
transaction: ambiguous non-idempotent writes stop for reconciliation, never blind replay.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .agent_protocol import failure


class AgentOperationService:
    """One business request contains typed operations and stable per-request step IDs.

    Supported kinds: plan_revision, task_resolution, debate_amendment,
    position_acceptance. Preview never writes. Reusing request_id with different
    content is rejected. Successful steps are retained when a later step fails.
    Only idempotent plan/task/acceptance steps may automatically resume.
    """

    KINDS = {"plan_revision", "plan_patch", "task_resolution", "debate_amendment", "position_acceptance"}

    def __init__(self, *, plans, debates):
        self.plans, self.debates = plans, debates
        self.repository = plans.registry.repository
        with self.repository.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_operation_requests (
                user_id TEXT NOT NULL, request_id TEXT NOT NULL, digest TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL, results TEXT NOT NULL,
                error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, request_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_operation_reconciliations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
                step_id TEXT NOT NULL, confirmed_applied INTEGER NOT NULL, evidence_ref TEXT NOT NULL,
                result TEXT, recorded_at TEXT NOT NULL)""")

    def inspect(self, *, user_id: str, request_id: str) -> dict[str, Any]:
        self.plans.users.context_for_user(user_id)
        with self.repository.connect() as conn:
            row = conn.execute("SELECT * FROM agent_operation_requests WHERE user_id=? AND request_id=?",
                               (user_id, request_id)).fetchone()
        if row is None:
            raise KeyError("operation request not found")
        with self.repository.connect() as conn:
            reconciliations = [dict(item) for item in conn.execute(
                "SELECT step_id,confirmed_applied,evidence_ref,recorded_at FROM agent_operation_reconciliations "
                "WHERE user_id=? AND request_id=? ORDER BY sequence", (user_id, request_id))]
        return {"request_id": request_id, "state": row["state"], "request": json.loads(row["payload"]),
                "results": json.loads(row["results"]), "error": json.loads(row["error"]) if row["error"] else None,
                "reconciliations": reconciliations,
                "updated_at": row["updated_at"], "gaps": [],
                "next_actions": [{"operation": "operations", "arguments": {"request_id": request_id, "user_id": user_id},
                                  "reason": "检查已完成步骤；仅可安全重试的请求能继续提交。"}]}

    def reconcile(self, *, user_id: str, request_id: str, confirmed_applied: bool,
                  evidence_ref: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
        """After verifying the worker stopped and checking the owning product, acknowledge an ambiguous step.

        confirmed_applied needs its actual result. False explicitly certifies that
        retry will not duplicate a write. Reconciliation evidence is append-only.
        """

        self.plans.users.context_for_user(user_id)
        if type(confirmed_applied) is not bool or not evidence_ref.strip():
            raise ValueError("reconciliation needs confirmed_applied and evidence_ref")
        if confirmed_applied and not isinstance(result, dict):
            raise ValueError("confirmed applied needs the observed result")
        with self.repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM agent_operation_requests WHERE user_id=? AND request_id=?",
                               (user_id, request_id)).fetchone()
            if row is None or row["state"] not in {"running", "needs_reconciliation"}:
                raise ValueError("only an ambiguous or interrupted request can be reconciled")
            request, results = json.loads(row["payload"]), json.loads(row["results"])
            step = request["steps"][len(results)]
            conn.execute("INSERT INTO agent_operation_reconciliations "
                         "(user_id,request_id,step_id,confirmed_applied,evidence_ref,result,recorded_at) VALUES (?,?,?,?,?,?,?)",
                         (user_id, request_id, step["step_id"], int(confirmed_applied), evidence_ref,
                          json.dumps(result, ensure_ascii=False) if result is not None else None, self._now()))
            if confirmed_applied:
                results.append({"step_id": step["step_id"], "kind": step["kind"], "result": result})
            state = "completed" if len(results) == len(request["steps"]) else "retryable_failure"
            conn.execute("UPDATE agent_operation_requests SET state=?,results=?,error=NULL,updated_at=? WHERE user_id=? AND request_id=?",
                         (state, json.dumps(results, ensure_ascii=False), self._now(), user_id, request_id))
        return self.inspect(user_id=user_id, request_id=request_id)

    def _validate(self, user_id: str, request: dict[str, Any]) -> list[dict[str, Any]]:
        self.plans.users.context_for_user(user_id)
        if set(request) != {"request_id", "steps"} or not isinstance(request["request_id"], str) or not request["request_id"].strip():
            raise ValueError("request needs request_id and steps")
        if not isinstance(request["steps"], list) or not request["steps"] or len(request["steps"]) > 100:
            raise ValueError("steps must contain 1..100 operations")
        seen, preview = set(), []
        for step in request["steps"]:
            if not isinstance(step, dict) or set(step) != {"step_id", "kind", "arguments"}:
                raise ValueError("each step needs step_id, kind and arguments")
            if not isinstance(step["step_id"], str) or not step["step_id"].strip() or step["step_id"] in seen:
                raise ValueError("step_id must be nonempty and unique")
            seen.add(step["step_id"])
            if step["kind"] not in self.KINDS or not isinstance(step["arguments"], dict):
                raise ValueError("unsupported operation kind or arguments")
            arguments = step["arguments"]
            if "user_id" in arguments:
                raise ValueError("step user_id cannot override the request owner")
            if step["kind"] in {"plan_revision", "plan_patch"}:
                from src.user_space.plan_registry import normalize_plan

                if step["kind"] == "plan_patch":
                    arguments = self._plan_patch(user_id, arguments)
                normalized = normalize_plan(arguments["plan"])
                current = next((row for row in self.plans.registry.list_plans(user_id=user_id)
                                if row["registration_key"] == arguments["registration_key"]), None)
                if current and current["plan"] != normalized and current["revision"] != arguments.get("expected_revision"):
                    raise ValueError("plan revision conflict; reload before previewing")
                preview.append({"step_id": step["step_id"], "kind": step["kind"],
                                "before": current["plan"] if current else None, "after": normalized})
            else:
                debate_id = arguments.get("debate_id")
                if step["kind"] in {"debate_amendment", "position_acceptance"}:
                    record = self.debates.show(debate_id)
                    if record["debate"]["user_id"] != user_id:
                        raise PermissionError("debate does not belong to this user")
                if step["kind"] == "task_resolution":
                    self.plans.show(user_id=user_id, plan_id=arguments["plan_id"])
                preview.append({"step_id": step["step_id"], "kind": step["kind"], "arguments": arguments})
        return preview

    def run(self, *, user_id: str, request: dict[str, Any], apply: bool = False) -> dict[str, Any]:
        """Preview or resume. An ambiguous debate amendment requires human/Agent reconciliation."""

        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if not apply:
            return {"status": "preview", "request_id": request.get("request_id"),
                    "steps": self._validate(user_id, request), "writes": False, "gaps": [], "next_actions": []}
        self.plans.users.context_for_user(user_id)
        request_id = request.get("request_id")
        with self.repository.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM agent_operation_requests WHERE user_id=? AND request_id=?",
                                    (user_id, request_id)).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise ValueError("request_id reused with different content")
                if previous["state"] in {"completed", "running", "needs_reconciliation"}:
                    return self.inspect(user_id=user_id, request_id=request_id)
                results = json.loads(previous["results"])
            else:
                self._validate(user_id, request)
                results = []
            if previous:
                remaining = {"request_id": request_id, "steps": request["steps"][len(results):]}
                if remaining["steps"]:
                    self._validate(user_id, remaining)
            conn.execute("INSERT OR REPLACE INTO agent_operation_requests VALUES (?,?,?,?,?,?,?,?)",
                         (user_id, request_id, digest, encoded, "running", json.dumps(results), None, self._now()))
        for step in request["steps"][len(results):]:
            retry_safe = step["kind"] != "debate_amendment"
            try:
                if not retry_safe:
                    self._save(user_id, request_id, "needs_reconciliation", results, None)
                result = self._execute(user_id, step)
            except Exception as exc:
                self._save(user_id, request_id, "retryable_failure" if retry_safe else "needs_reconciliation",
                           results, failure(exc, operation=step["kind"], retryable=retry_safe))
                return self.inspect(user_id=user_id, request_id=request_id)
            results.append({"step_id": step["step_id"], "kind": step["kind"], "result": result})
            self._save(user_id, request_id, "running", results, None)
        self._save(user_id, request_id, "completed", results, None)
        return self.inspect(user_id=user_id, request_id=request_id)

    def _execute(self, user_id: str, step: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(step["arguments"])
        if step["kind"] == "plan_patch":
            return self.plans.register(user_id=user_id, **self._plan_patch(user_id, arguments))
        if step["kind"] == "plan_revision":
            return self.plans.register(user_id=user_id, **arguments)
        if step["kind"] == "task_resolution":
            return self.plans.resolve(user_id=user_id, **arguments)
        if step["kind"] == "position_acceptance":
            return self.debates.debates.accept_position(**arguments).to_dict()
        return self.debates.debates.amend(**arguments)

    def _plan_patch(self, user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Compile an explicit field patch against an immutable base revision; untouched tasks survive."""

        allowed = {"plan_id", "expected_revision", "changes", "task_changes", "add_tasks", "remove_task_ids"}
        if set(arguments) - allowed:
            raise ValueError("unsupported plan_patch arguments")
        current = self.plans.registry.show(user_id=user_id, plan_id=arguments["plan_id"])
        revision = arguments["expected_revision"]
        base = next((row["plan"] for row in current["revisions"] if row["revision"] == revision), None)
        if base is None:
            raise ValueError("base revision not found")
        plan = deepcopy(base)
        changes = arguments.get("changes", {})
        if not isinstance(changes, dict) or set(changes) - {"title", "objective", "reason", "status", "constraints", "uncertainties", "source_refs", "rule_exclusions"}:
            raise ValueError("plan_patch changes must explicitly name editable plan fields")
        plan.update(changes)
        indexed = {task["task_id"]: task for task in plan["tasks"]}
        for task_id, patch in arguments.get("task_changes", {}).items():
            if task_id not in indexed or not isinstance(patch, dict) or "task_id" in patch:
                raise ValueError("task patch needs an existing task_id and cannot rename it")
            indexed[task_id].update(patch)
        removed = arguments.get("remove_task_ids", [])
        if any(task_id not in indexed for task_id in removed):
            raise ValueError("cannot remove an unknown task")
        plan["tasks"] = [task for task in plan["tasks"] if task["task_id"] not in removed] + arguments.get("add_tasks", [])
        return {"registration_key": current["registration_key"], "expected_revision": revision, "plan": plan}

    def _save(self, user_id, request_id, state, results, error):
        with self.repository.connect() as conn:
            conn.execute("UPDATE agent_operation_requests SET state=?,results=?,error=?,updated_at=? WHERE user_id=? AND request_id=?",
                         (state, json.dumps(results, ensure_ascii=False), json.dumps(error) if error else None,
                          self._now(), user_id, request_id))

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()