"""Debate workflow across products: settlement status of linked ledger entries, party scorecards, signpost
watch, and the layer-1 agenda.

The debate store (research products layer) keeps positions, rebuttals and signposts. This module
joins them with the ledger (did the settleable rewrites of a position come true?) and with tracked
observables (has a signpost's observable moved since the signpost was written?). The agenda gathers
what changed in the world — archive change signals, fresh news on standing world topics, triggered
signposts — so layer-1 views get proposed even when the user brings none. Design notes:
docs/modules/5-研究产物/观点辩论模块.md.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.event_intel import OBS_PREFIX, EventIntelService, watch_query
from src.research_debate import DEBATE_OPEN, LAYER_WORLD, PERSONAS, WORLD_TOPICS, DebateService
from src.research_ledger import LedgerEntryType, ResearchLedgerService, SettlementStatus

_CLI = "python -m src.research_orchestration.cli debate"
_DUE_WINDOW_DAYS = 14
_AGENDA_INTERVAL_DAYS = 7
_ARCHIVE_PREFIXES = {"market_archive": "市场档案", "macro_archive": "宏观档案", "sector_archive": "行业档案"}


class DebateWorkflow:
    def __init__(self, *, debates: DebateService, ledger: ResearchLedgerService, intel: EventIntelService) -> None:
        self.debates = debates
        self.ledger = ledger
        self.intel = intel

    def show(self, debate_id: str) -> dict[str, Any]:
        """The debate record, each linked ledger entry with its latest settlement per checkpoint."""

        payload = self.debates.show(debate_id)
        user_id = payload["debate"]["user_id"]
        for position in payload["positions"]:
            position["ledger"] = [self._entry_status(entry_id, user_id) for entry_id in position["ledger_refs"]]
        return payload

    def scorecard(self, *, user_id: str, debate_id: str | None = None) -> dict[str, Any]:
        """Per party: how positions were resolved in debate, and how their linked ledger entries settled."""

        parties: dict[str, dict[str, Any]] = {}
        for record in self.debates.records(user_id=user_id):
            if debate_id is not None and record.debate.debate_id != debate_id:
                continue
            for position in record.positions:
                row = parties.setdefault(
                    position.party,
                    {"positions": {}, "checkpoints": {"met": 0, "missed": 0, "other": 0}, "awaiting_entries": 0},
                )
                row["positions"][position.status] = row["positions"].get(position.status, 0) + 1
                for entry_id in position.ledger_refs:
                    status = self._entry_status(entry_id, user_id)
                    settled = [item for item in status["checkpoints"] if item["status"] == SettlementStatus.SETTLED.value]
                    if not settled:
                        row["awaiting_entries"] += 1
                    for item in settled:
                        key = item["claim_result"] if item["claim_result"] in ("met", "missed") else "other"
                        row["checkpoints"][key] += 1
        for row in parties.values():
            decided = row["checkpoints"]["met"] + row["checkpoints"]["missed"]
            row["hit_rate"] = round(row["checkpoints"]["met"] / decided, 4) if decided else None
        return {
            "user_id": user_id,
            "debate_id": debate_id,
            "parties": parties,
            "notes": [
                "positions 是辩论中的结论（立住、被修正、被驳倒、撤回）；checkpoints 是关联账本条目的结算结果，只有入账的命题才计入命中率",
                "样本少时命中率不作为依据；读法与口径见 docs/modules/5-研究产物/观点辩论模块.md",
            ],
        }

    def watch(self, *, user_id: str, as_of_date: str) -> dict[str, Any]:
        """Signposts due within 14 days, or whose observable or ledger entry changed since the signpost was written."""

        horizon = (date.fromisoformat(as_of_date) + timedelta(days=_DUE_WINDOW_DAYS)).isoformat()
        rows = []
        for record in self.debates.records(user_id=user_id):
            for signpost in record.signposts:
                if signpost.replaced_by is not None:
                    continue
                reasons = []
                if signpost.due_on is not None and signpost.due_on <= horizon:
                    reasons.append(f"{'已到期' if signpost.due_on <= as_of_date else '14 天内到期'}（{signpost.due_on}）")
                for ref in signpost.refs:
                    reasons.extend(self._changes(ref, signpost.created_at, user_id))
                if reasons:
                    rows.append(
                        {
                            "debate_id": record.debate.debate_id,
                            "topic": record.debate.topic,
                            "signpost": signpost.to_dict(),
                            "reasons": reasons,
                        }
                    )
        open_debates = [
            {"debate_id": item["debate_id"], "topic": item["topic"], "counts": item["counts"]}
            for item in self.debates.list_debates(user_id=user_id, status=DEBATE_OPEN)
        ]
        world_dates = [
            item["as_of_date"]
            for item in self.debates.list_debates(user_id=user_id)
            if LAYER_WORLD in item.get("layers", [LAYER_WORLD])
        ]
        return {
            "as_of_date": as_of_date,
            "signposts": rows,
            "open_debates": open_debates,
            "last_world_debate": max(world_dates) if world_dates else None,
        }

    def agenda(self, *, user_id: str, as_of_date: str, days: int = 7, allow_remote: bool = True) -> dict[str, Any]:
        """Candidate layer-1 topics from the last ``days``: archive change signals, world-topic news and
        triggered signposts. It proposes topics; the Agent picks one to three and opens world debates."""

        since = (date.fromisoformat(as_of_date) - timedelta(days=days)).isoformat()
        archives: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in self.ledger.list_entries(user_id=None, entry_type=LedgerEntryType.ASSESSMENT):
            prefix = entry.source_ref.split(":", 1)[0]
            if prefix not in _ARCHIVE_PREFIXES or not since <= entry.as_of_date <= as_of_date:
                continue
            key = (prefix, entry.subject_id)
            if key not in archives or entry.as_of_date > archives[key]["as_of_date"]:
                payload = entry.payload
                archives[key] = {
                    "kind": _ARCHIVE_PREFIXES[prefix],
                    "subject_id": entry.subject_id,
                    "as_of_date": entry.as_of_date,
                    "label": payload.get("label"),
                    "change_signals": list(payload.get("change_signals") or ()),
                    "ledger_entry_id": entry.entry_id,
                }
        topics = []
        for topic in WORLD_TOPICS:
            hits, searches, errors = [], [], []
            for text in topic.queries:
                language, query = watch_query(text)
                result = self.intel.search(
                    "news", query=query, language=language, since=since, until=as_of_date, limit=5, allow_remote=allow_remote
                )
                if result.get("search_id") is not None:
                    searches.append(f"search:{result['search_id']}")
                if result.get("error"):
                    errors.append(result["error"])
                hits.extend(
                    {"intel_id": item["item_id"], "published_on": item["published_on"], "title": item["title"]}
                    for item in result["items"][:3]
                )
            topics.append(
                {
                    "topic_id": topic.topic_id,
                    "title": topic.title,
                    "lenses": [f"{PERSONAS[item].name}（{item}）" for item in topic.lenses],
                    "headlines": sorted(hits, key=lambda item: item["published_on"], reverse=True),
                    "searches": searches,
                    "errors": errors,
                }
            )
        recent = [
            {"debate_id": item["debate_id"], "topic": item["topic"], "status": item["status"]}
            for item in self.debates.list_debates(user_id=user_id)
            if LAYER_WORLD in item.get("layers", [LAYER_WORLD]) and item["as_of_date"] >= since
        ]
        return {
            "user_id": user_id,
            "as_of_date": as_of_date,
            "window": {"since": since, "until": as_of_date},
            "archive_signals": [row for row in archives.values() if row["change_signals"]],
            "topics": topics,
            "signposts": self.watch(user_id=user_id, as_of_date=as_of_date)["signposts"],
            "recent_world_debates": recent,
            "next_actions": [
                {
                    "command": f"{_CLI} --action start --json <path>",
                    "reason": "从议程里选 1–3 个变化最大或最可能形成非共识判断的议题，各开一场第一层辩论（layers 写 [\"world\"]）；"
                    "已有近期辩论的议题接着原辩论或写 parent_debate_id",
                },
                {
                    "command": f"{_CLI} --action brief --debate-id <id> --persona <角色>",
                    "reason": "第一层视角（达利欧、索罗斯、马克斯、产业链研究员、政策与地缘研究员）用子代理并行提出观点；"
                    "Agent 写自己的观点；用户有观点时一并写入",
                },
            ],
            "notes": [
                "议程只列线索：档案的变化信号来自已入账的市场、宏观与行业档案；新闻是标题与链接，不是判断",
                "没有人带观点时，第一层观点由各视角子代理与 Agent 从议程提出，再按辩论纪律互相驳斥",
            ],
        }

    def watched_observables(self, *, user_id: str) -> frozenset[str]:
        """Observables referenced by the user's active signposts, so the routine searches news for them."""

        return frozenset(
            ref
            for record in self.debates.records(user_id=user_id)
            for signpost in record.signposts
            if signpost.replaced_by is None
            for ref in signpost.refs
            if ref.startswith(OBS_PREFIX)
        )

    def _changes(self, ref: str, since: str, user_id: str) -> list[str]:
        if ref.startswith(OBS_PREFIX):
            newer = [item for item in self.intel.observations(ref) if item.recorded_at > since]
            if not newer:
                return []
            latest = newer[-1]
            return [f"{ref} 有新观测：{latest.value}（期末 {latest.period_end}，可得 {latest.available_at}）"]
        if ref.startswith("ledger-"):
            status = self._entry_status(ref, user_id)
            return [
                f"{ref} 检查点 {item['checkpoint_date']} 已结算：{item['claim_result']}"
                for item in status["checkpoints"]
                if item["status"] == SettlementStatus.SETTLED.value and item["computed_at"] > since
            ]
        return []

    def _entry_status(self, entry_id: str, user_id: str) -> dict[str, Any]:
        try:
            settlements = self.ledger.list_settlements(entry_id, user_id=user_id)
        except (KeyError, PermissionError, ValueError) as exc:
            return {"entry_id": entry_id, "error": f"{type(exc).__name__}: {exc}", "checkpoints": []}
        latest: dict[str, Any] = {}
        for item in settlements:
            current = latest.get(item.checkpoint_date)
            if current is None or item.computed_at > current.computed_at:
                latest[item.checkpoint_date] = item
        return {
            "entry_id": entry_id,
            "checkpoints": [
                {
                    "checkpoint_date": key,
                    "status": latest[key].status.value,
                    "claim_result": latest[key].claim_result.value if latest[key].claim_result else None,
                    "computed_at": latest[key].computed_at,
                }
                for key in sorted(latest)
            ],
        }


def debate_next_actions(watch: dict[str, Any] | None) -> list[dict[str, str]]:
    """Routine follow-ups: act on triggered signposts; finish open debates."""

    if not watch:
        return []
    actions = [
        {
            "command": f"{_CLI} --action show --debate-id {row['debate_id']}",
            "reason": f"观察点“{row['signpost']['observation']}”：{'；'.join(row['reasons'])}。"
            f"按触发条件（{row['signpost']['trigger']}）核对，成立则执行：{row['signpost']['action']}",
        }
        for row in watch["signposts"]
    ]
    for item in watch["open_debates"]:
        actions.append(
            {
                "command": f"{_CLI} --action show --debate-id {item['debate_id']}",
                "reason": f"辩论“{item['topic']}”尚未结论：按输出的 next_actions 继续",
            }
        )
    last = watch.get("last_world_debate")
    stale = (date.fromisoformat(watch["as_of_date"]) - timedelta(days=_AGENDA_INTERVAL_DAYS)).isoformat()
    if last is None or last < stale:
        actions.append(
            {
                "command": f"{_CLI} --action agenda",
                "reason": f"超过 {_AGENDA_INTERVAL_DAYS} 天没有第一层（行业、市场与世界观）辩论：扫议程，由各视角与 Agent 主动提出观点",
            }
        )
    return actions
