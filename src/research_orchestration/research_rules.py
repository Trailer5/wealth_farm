"""Canonical research-rule references and execution-plan coverage checks.

Design notes: docs/modules/5-研究产物/观点辩论模块.md.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def rule_record(rule_id: str, source_id: str, kind: str, content: dict[str, Any]) -> dict[str, Any]:
    """Stable identity plus a fingerprint of all decision-relevant content."""

    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"rule_id": rule_id, "source_id": source_id, "kind": kind,
            "content_hash": digest, "content": content}


def audit_rules(plan: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    """Check explicit links, not prose equivalence. Missing coverage remains visible."""

    indexed = {rule["rule_id"]: rule for rule in rules}
    covered, tasks, gaps = set(), [], []
    for task in plan["tasks"]:
        links, issues = [], []
        for ref in task.get("rule_refs", []):
            covered.add(ref["rule_id"])
            rule = indexed.get(ref["rule_id"])
            if rule is None:
                issues.append({"code": "rule_missing_or_replaced", "rule_id": ref["rule_id"]})
            else:
                links.append(rule)
                if rule["content_hash"] != ref["content_hash"]:
                    issues.append({"code": "rule_changed", "rule_id": ref["rule_id"],
                                   "expected_hash": ref["content_hash"], "current_hash": rule["content_hash"]})
        state = "stale" if issues else "linked" if links else "unbound"
        tasks.append({"task_id": task["task_id"], "state": state, "rules": links, "issues": issues})
        gaps.extend(f"{task['task_id']}: {issue['code']} ({issue['rule_id']})" for issue in issues)
    exclusions = plan.get("rule_exclusions", {})
    relevant = [rule for rule in rules if rule["source_id"] in plan["source_refs"]]
    uncovered = [rule for rule in relevant if rule["rule_id"] not in covered and rule["rule_id"] not in exclusions]
    invalid_exclusions = sorted(rule_id for rule_id in exclusions if rule_id not in indexed)
    return {"tasks": tasks, "uncovered_rules": uncovered, "exclusions": exclusions,
            "invalid_exclusions": invalid_exclusions, "gaps": gaps,
            "notes": ["指纹匹配只证明引用未变，不证明任务说明与研究规则语义等价。",
                      "uncovered_rules 必须补登记或写明不适用理由；不得仅凭报告摘录宣称覆盖完整。"]}