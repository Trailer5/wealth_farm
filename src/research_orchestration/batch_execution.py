"""Per-item, checkpointed execution of read/fetch batches for Agent callers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from .agent_protocol import failure


class BatchExecutionService:
    """Checkpoint after each item. Fetches may repeat after a crash, successful responses do not.

    This runner is only for idempotent reads/fetches, never investment-state writes.
    Reuse request_id with identical items/parameters; retry_failed retries errors only.
    """

    def __init__(self, repository):
        self.repository = repository
        with repository.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_read_batches (
                request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, results TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def run(self, *, items: list[str], parameters: dict[str, Any], execute: Callable[[str], Any],
            request_id: str | None = None, retry_failed: bool = False) -> dict[str, Any]:
        if not items or len(items) > 1000 or any(not isinstance(item, str) or not item.strip() for item in items):
            raise ValueError("batch needs 1..1000 nonempty item keys")
        items = list(dict.fromkeys(items))
        encoded = json.dumps({"items": items, "parameters": parameters}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        completed: dict[str, dict[str, Any]] = {}
        if request_id:
            with self.repository.connect() as conn:
                row = conn.execute("SELECT * FROM agent_read_batches WHERE request_id=?", (request_id,)).fetchone()
            if row:
                if row["digest"] != digest:
                    raise ValueError("batch request_id reused with different items or parameters")
                completed = {item["key"]: item for item in json.loads(row["results"])}
        for key in items:
            previous = completed.get(key)
            if previous and (previous["status"] == "completed" or not retry_failed):
                continue
            try:
                result = execute(key)
                if isinstance(result, dict) and result.get("error"):
                    raise RuntimeError(str(result["error"]))
                completed[key] = {"key": key, "status": "completed", "result": result}
            except Exception as exc:
                completed[key] = {"key": key, "status": "failed", "failure": failure(exc, operation="batch_item", retryable=True)}
            if request_id:
                with self.repository.connect() as conn:
                    conn.execute("INSERT OR REPLACE INTO agent_read_batches VALUES (?,?,?,?)",
                        (request_id, digest, json.dumps(list(completed.values()), ensure_ascii=False, default=str),
                         datetime.now(timezone.utc).isoformat()))
        rows = [completed[key] for key in items]
        failed = [row["key"] for row in rows if row["status"] == "failed"]
        return {"status": "partial" if failed and len(failed) < len(rows) else "error" if failed else "completed",
                "request_id": request_id, "items": rows, "completed": len(rows) - len(failed), "failed": len(failed),
                "retry_items": failed, "parameters": parameters,
                "gaps": [row["failure"]["error"]["message"] for row in rows if row["status"] == "failed"],
                "next_actions": [{"operation": "prices", "arguments": {**parameters, "request_id": request_id,
                    "retry_failed": True}, "reason": "保持原标的和参数，复用成功项，仅重试失败项。"}] if failed and request_id else []}