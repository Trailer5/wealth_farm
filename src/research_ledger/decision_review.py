"""Decision review over settled ledger judgements: hit rates, timing contribution, dimensions.

Design notes: docs/modules/5-研究产物/观点账本模块.md「决策复盘」.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

# Below this many settled judgements a group's hit rate is shown but not interpreted.
MIN_SAMPLE = 5
_Z90 = 1.645
_BULLISH = frozenset({"buy", "add"})
_BEARISH = frozenset({"reduce", "exit"})
SOURCE_LABELS = {
    "final-report": "单标的最终报告",
    "exit_plan": "退出计划",
    "portfolio_plan": "组合计划",
    "request": "对话中记录",
    "thesis": "投资论点",
    "dev-seed": "开发样例",
    "strategy": "策略前向信号",
}
HORIZON_LABELS = {
    "short": "短期",
    "medium_short": "中短期",
    "medium_long": "中长期",
    "long": "长期",
}
ACTION_LABELS = {"buy": "买入", "add": "加仓", "reduce": "减仓", "exit": "退出", "hold": "持有"}


def source_group(source_ref: str) -> str:
    return source_ref.split(":", 1)[0]


def timing_contribution(action_type: str, settlement: dict[str, Any]) -> float | None:
    """Return of acting minus not acting: a buy against keeping cash (or the alternative), a sale against holding on."""

    asset = settlement.get("asset_return")
    if asset is None:
        return None
    alternative = float(settlement["alternative_return"]) if settlement.get("alternative_return") is not None else 0.0
    if action_type in _BULLISH:
        return float(asset) - alternative
    if action_type in _BEARISH:
        return alternative - float(asset)
    return None


def summarize_decisions(
    entries: list[dict[str, Any]],
    *,
    as_of_date: str,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Group ``ResearchLedgerService.track_record`` entries by their latest settled checkpoint.

    Views and actions are reviewed; assessments, theses and plans are not settled themselves
    (thesis expectations are settled as views). Revised entries are skipped so a judgement counts once.
    """

    labels = labels or {}
    statuses: dict[str, int] = defaultdict(int)
    view_rows, action_rows = [], []
    for entry in entries:
        kind = entry["entry_type"]
        if kind not in ("view", "action"):
            continue
        if entry["superseded"]:
            statuses["superseded"] += 1
            continue
        settled = [item for item in entry["settlements"] if item["status"] == "settled"]
        if not settled:
            statuses[_unsettled_status(entry["settlements"])] += 1
            continue
        latest = max(settled, key=lambda item: item["checkpoint_date"])
        artifact = entry["artifact"]
        row = {
            "entry_id": entry["entry_id"],
            "subject": labels.get(entry["subject_id"], entry["subject_id"]),
            "source": source_group(entry["source_ref"]),
            "strategy": entry["source_ref"].split(":")[1] if entry["source_ref"].startswith("strategy:") else None,
            "horizon": artifact.get("horizon"),
            "as_of_date": entry["as_of_date"],
            "checkpoint_date": latest["checkpoint_date"],
            "checkpoints_settled": len(settled),
            "result": latest["claim_result"],
            "asset_return": _number(latest.get("asset_return")),
            "excess_return": _number(latest.get("excess_return")),
            "interim": entry["effective_until"] > as_of_date,
        }
        if kind == "view":
            statuses["settled_view"] += 1
            probability = artifact.get("probability")
            row.update(
                origin=artifact.get("origin"),
                proposition=artifact.get("proposition"),
                stated_probability=(
                    None
                    if not probability
                    else round((float(probability["low"]) + float(probability["high"])) / 2, 4)
                ),
            )
            view_rows.append(row)
        else:
            statuses["settled_action"] += 1
            row.update(
                action_type=artifact.get("action_type"),
                timing_contribution=_rounded(timing_contribution(artifact.get("action_type"), latest)),
                trigger=artifact.get("trigger", {}).get("kind"),
            )
            action_rows.append(row)
    return {
        "as_of_date": as_of_date,
        "status_counts": dict(statuses),
        "views": {
            "overall": _stats(view_rows),
            "by_origin": _grouped(view_rows, lambda row: {"system": "系统观点", "user": "用户观点"}.get(row["origin"], row["origin"])),
            "by_horizon": _grouped(view_rows, lambda row: HORIZON_LABELS.get(row["horizon"], row["horizon"])),
            "by_source": _grouped(view_rows, lambda row: SOURCE_LABELS.get(row["source"], row["source"])),
        },
        "actions": {
            "overall": _stats(action_rows),
            "by_direction": _grouped(action_rows, lambda row: ACTION_LABELS.get(row["action_type"], row["action_type"])),
            "by_source": _grouped(action_rows, lambda row: SOURCE_LABELS.get(row["source"], row["source"])),
            "by_horizon": _grouped(action_rows, lambda row: HORIZON_LABELS.get(row["horizon"], row["horizon"])),
            "by_subject": _grouped(action_rows, lambda row: row["subject"]),
            # Forward signals of archived strategies (source strategy:<id>:<version>:<date>), one group per strategy.
            "by_strategy": _grouped([row for row in action_rows if row["strategy"]], lambda row: row["strategy"]),
        },
        "view_items": sorted(view_rows, key=lambda row: row["checkpoint_date"]),
        "action_items": sorted(action_rows, key=lambda row: row["checkpoint_date"]),
    }


def due_review_nodes(entries: list[dict[str, Any]], *, as_of_date: str, upcoming: int = 3) -> dict[str, Any]:
    """Review nodes of the user's current (unrevised) plans: those already due and the next few."""

    due, later = [], []
    for entry in entries:
        if entry["entry_type"] != "plan" or entry["superseded"]:
            continue
        for node in entry["artifact"].get("review_nodes", ()):
            row = {"plan_entry_id": entry["entry_id"], "review_on": node["review_on"], "focus": node["focus"]}
            (due if node["review_on"] <= as_of_date else later).append(row)
    return {
        "due": sorted(due, key=lambda row: row["review_on"]),
        "upcoming": sorted(later, key=lambda row: row["review_on"])[:upcoming],
    }


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    directional = [row for row in rows if row["result"] in ("met", "missed")]
    met = sum(1 for row in directional if row["result"] == "met")
    count = len(directional)
    stats: dict[str, Any] = {
        "settled": len(rows),
        "directional": count,
        "met": met,
        "missed": count - met,
        "hit_rate": round(met / count, 4) if count else None,
        "hit_rate_interval": _wilson(met, count) if count else None,
        "small_sample": count < MIN_SAMPLE,
        "interim": sum(1 for row in rows if row["interim"]),
    }
    contributions = [row["timing_contribution"] for row in rows if row.get("timing_contribution") is not None]
    if contributions:
        stats["timing_contribution_mean"] = round(sum(contributions) / len(contributions), 4)
        stats["timing_contribution_positive"] = sum(1 for item in contributions if item > 0)
        stats["timing_contribution_count"] = len(contributions)
    stated = [row["stated_probability"] for row in directional if row.get("stated_probability") is not None]
    if stated:
        with_probability = [row for row in directional if row.get("stated_probability") is not None]
        stats["stated_probability_mean"] = round(sum(stated) / len(stated), 4)
        stats["hit_rate_with_probability"] = round(
            sum(1 for row in with_probability if row["result"] == "met") / len(with_probability), 4
        )
        stats["with_probability"] = len(with_probability)
    return stats


def _grouped(rows: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key(row))].append(row)
    return [{"group": name, **_stats(items)} for name, items in sorted(groups.items())]


def _unsettled_status(settlements: list[dict[str, Any]]) -> str:
    if not settlements:
        return "pending"
    latest = max(settlements, key=lambda item: item["checkpoint_date"])
    if latest["status"] == "not_settleable" and "未触发" in (latest.get("note") or ""):
        return "not_triggered"
    return latest["status"]


def _wilson(successes: int, count: int) -> list[float]:
    share = successes / count
    denominator = 1 + _Z90 * _Z90 / count
    centre = (share + _Z90 * _Z90 / (2 * count)) / denominator
    spread = _Z90 * math.sqrt(share * (1 - share) / count + _Z90 * _Z90 / (4 * count * count)) / denominator
    return [round(max(centre - spread, 0.0), 4), round(min(centre + spread, 1.0), 4)]


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
