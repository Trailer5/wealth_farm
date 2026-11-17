"""Debate service: propose positions, rebut them across parties, resolve, and leave signposts.

Discipline enforced here (the Agent cannot skip it):
- a rebuttal must come from a party other than the position's own;
- a position stands only after at least one rebuttal on it (or on the same party's version it refines)
  is closed and none is pending — an unchallenged view is not a consensus, and another party's rewrite
  of a view must face its own rebuttal;
- a position is refuted only by an accepted rebuttal;
- a refinement is a new position that supersedes the old one, which is then marked refined;
- a debate concludes only when no position is open and no rebuttal is pending;
- a major debate (the default) concludes with decisions; when none can be made, the method or
  infrastructure gaps that blocked them are recorded as blockers and shown as gaps.
Coverage (not a gate): for each layer in play, which persona lenses spoke, declared no view, or are
absent; absent lenses are blind spots listed in next_actions and gaps.
Design notes: docs/modules/5-研究产物/观点辩论模块.md.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

from .models import (
    DEBATE_CONCLUDED,
    DEBATE_OPEN,
    DECISION_ACTIONS,
    HORIZONS,
    LAYER_ASSET,
    LAYER_LABELS,
    LAYER_WORLD,
    LAYERS,
    PARTY_AGENT,
    PARTY_USER,
    PERSONA_PREFIX,
    POSITION_OPEN,
    POSITION_REFINED,
    POSITION_REFUTED,
    POSITION_STANDS,
    POSITION_WITHDRAWN,
    REBUTTAL_ACCEPTED,
    REBUTTAL_ANSWERED,
    REBUTTAL_KINDS,
    REBUTTAL_PENDING,
    SIGNIFICANCE_MAJOR,
    SIGNIFICANCES,
    SIZED_ACTIONS,
    Debate,
    DebateRecord,
    Participation,
    Position,
    Rebuttal,
    Signpost,
)
from .personas import persona, persona_brief, personas_for
from .sqlite_repository import SQLiteDebateRepository

_CLI = "python -m src.research_orchestration.cli debate"


class DebateService:
    def __init__(
        self,
        repository: SQLiteDebateRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.repository.initialize()

    def start(
        self,
        *,
        user_id: str,
        topic: str,
        trigger: str,
        as_of_date: str,
        evidence_refs: tuple[str, ...] = (),
        parent_debate_id: str | None = None,
        layers: tuple[str, ...] = (LAYER_WORLD,),
        subjects: tuple[str, ...] = (),
        significance: str = SIGNIFICANCE_MAJOR,
    ) -> Debate:
        if not topic.strip() or not trigger.strip():
            raise ValueError("辩题 topic 与起因 trigger 都要写")
        if significance not in SIGNIFICANCES:
            raise ValueError("significance 取 major（重大事件，必须形成决策）或 routine（常规新闻与日常观察）")
        if not layers or any(layer not in LAYERS for layer in layers):
            raise ValueError(f"layers 取 {', '.join(LAYERS)}（world 第一层，asset 第二层）")
        if LAYER_ASSET in layers and not subjects:
            raise ValueError("第二层辩论要写 subjects（标的代码与名称）")
        if parent_debate_id is not None:
            self._record(parent_debate_id)
        digest = hashlib.sha1(f"{user_id}|{topic}|{as_of_date}".encode("utf-8")).hexdigest()[:8]
        debate = Debate(
            debate_id=f"debate:{as_of_date}:{digest}",
            user_id=user_id,
            topic=topic.strip(),
            trigger=trigger.strip(),
            as_of_date=as_of_date,
            evidence_refs=tuple(evidence_refs),
            layers=tuple(dict.fromkeys(layers)),
            subjects=tuple(subjects),
            parent_debate_id=parent_debate_id,
            significance=significance,
            created_at=self._now(),
        )
        if self.repository.get_debate(debate.debate_id) is not None:
            raise ValueError(f"同一用户、辩题和截止日的辩论已存在：{debate.debate_id}")
        self.repository.save_debate(debate)
        return debate

    def add_position(
        self,
        debate_id: str,
        *,
        party: str,
        statement: str,
        mechanism: str,
        horizon: str,
        falsifiers: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        supersedes: str | None = None,
        layer: str = LAYER_WORLD,
        subject: str | None = None,
    ) -> Position:
        record = self._open(debate_id)
        _check_party(party)
        if not statement.strip() or not mechanism.strip():
            raise ValueError("观点要写命题 statement 和机制 mechanism（因果链）")
        if horizon not in HORIZONS:
            raise ValueError(f"horizon 取 {', '.join(HORIZONS)}")
        if layer not in LAYERS:
            raise ValueError(f"layer 取 {', '.join(LAYERS)}")
        if layer == LAYER_ASSET and not (subject or "").strip():
            raise ValueError("第二层观点要写 subject（标的代码与名称）")
        if party != PARTY_USER and not falsifiers:
            raise ValueError("Agent 与角色的观点必须写证伪条件 falsifiers")
        previous = None
        if supersedes is not None:
            previous = _find(record.positions, supersedes, "观点")
            if previous.status != POSITION_OPEN:
                raise ValueError(f"{supersedes} 已是“{previous.status}”，只能修正待驳斥的观点")
        seq = self.repository.next_seq("debate_positions", debate_id)
        position = Position(
            position_id=f"{debate_id}:p{seq}",
            debate_id=debate_id,
            party=party,
            statement=statement.strip(),
            mechanism=mechanism.strip(),
            horizon=horizon,
            falsifiers=tuple(falsifiers),
            evidence_refs=tuple(evidence_refs),
            layer=layer,
            subject=(subject or "").strip() or None,
            supersedes=supersedes,
            created_at=self._now(),
        )
        self.repository.save("debate_positions", position, seq)
        if previous is not None:
            self.repository.save(
                "debate_positions",
                replace(
                    previous,
                    status=POSITION_REFINED,
                    resolution_note=f"由 {position.position_id} 修正",
                    resolved_at=self._now(),
                ),
            )
        return position

    def rebut(
        self,
        debate_id: str,
        *,
        position_id: str,
        party: str,
        argument: str,
        kind: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> Rebuttal:
        record = self._open(debate_id)
        _check_party(party)
        target = _find(record.positions, position_id, "观点")
        if target.status != POSITION_OPEN:
            raise ValueError(f"{position_id} 已是“{target.status}”，不再接受驳斥")
        if party == target.party:
            raise ValueError("驳斥要来自其他参与方；同一方修改自己的观点请用 supersedes 写修正版")
        if kind not in REBUTTAL_KINDS:
            raise ValueError(f"kind 取 {', '.join(REBUTTAL_KINDS)}（事实、推断、领域知识）")
        if not argument.strip():
            raise ValueError("argument 不能为空")
        seq = self.repository.next_seq("debate_rebuttals", debate_id)
        rebuttal = Rebuttal(
            rebuttal_id=f"{debate_id}:r{seq}",
            debate_id=debate_id,
            position_id=position_id,
            party=party,
            argument=argument.strip(),
            kind=kind,
            evidence_refs=tuple(evidence_refs),
            created_at=self._now(),
        )
        self.repository.save("debate_rebuttals", rebuttal, seq)
        return rebuttal

    def answer(self, debate_id: str, *, rebuttal_id: str, accepted: bool, response: str) -> Rebuttal:
        """Close a rebuttal: accepted means it holds against the position; otherwise the response answers it."""

        record = self._open(debate_id)
        rebuttal = _find(record.rebuttals, rebuttal_id, "驳斥")
        if rebuttal.status != REBUTTAL_PENDING:
            raise ValueError(f"{rebuttal_id} 已经回应过")
        if not response.strip():
            raise ValueError("response 写明接受的理由或反驳的依据")
        closed = replace(
            rebuttal,
            status=REBUTTAL_ACCEPTED if accepted else REBUTTAL_ANSWERED,
            response=response.strip(),
            closed_at=self._now(),
        )
        self.repository.save("debate_rebuttals", closed)
        return closed

    def resolve(
        self,
        debate_id: str,
        *,
        position_id: str,
        status: str,
        note: str,
        ledger_refs: tuple[str, ...] = (),
    ) -> Position:
        record = self._open(debate_id)
        position = _find(record.positions, position_id, "观点")
        if position.status != POSITION_OPEN:
            raise ValueError(f"{position_id} 已是“{position.status}”")
        if status == POSITION_REFINED:
            raise ValueError("修正请写一条 supersedes 指向它的新观点，旧观点会自动标为被修正")
        if status not in (POSITION_STANDS, POSITION_REFUTED, POSITION_WITHDRAWN):
            raise ValueError("status 取 stands、refuted 或 withdrawn")
        if not note.strip():
            raise ValueError("note 写明结论理由")
        own = [item for item in record.rebuttals if item.position_id == position_id]
        if any(item.status == REBUTTAL_PENDING for item in own):
            raise ValueError("还有未回应的驳斥，先 answer")
        if status == POSITION_STANDS:
            lineage = _lineage(record.positions, position)
            closed = [item for item in record.rebuttals if item.position_id in lineage and item.status != REBUTTAL_PENDING]
            if not closed:
                raise ValueError("没有经过驳斥的观点不能立住：先由其他参与方给出最强反驳")
        if status == POSITION_REFUTED and not any(item.status == REBUTTAL_ACCEPTED for item in own):
            raise ValueError("被驳倒需要至少一条已接受的驳斥")
        resolved = replace(
            position,
            status=status,
            resolution_note=note.strip(),
            ledger_refs=tuple(dict.fromkeys((*position.ledger_refs, *ledger_refs))),
            resolved_at=self._now(),
        )
        self.repository.save("debate_positions", resolved)
        return resolved

    def link(self, debate_id: str, *, position_id: str, ledger_refs: tuple[str, ...]) -> Position:
        """Attach ledger entries (settleable rewrites of the position) at any time, also after conclusion."""

        record = self._record(debate_id)
        position = _find(record.positions, position_id, "观点")
        if not ledger_refs:
            raise ValueError("ledger_refs 不能为空")
        linked = replace(position, ledger_refs=tuple(dict.fromkeys((*position.ledger_refs, *ledger_refs))))
        self.repository.save("debate_positions", linked)
        return linked

    def add_signpost(
        self,
        debate_id: str,
        *,
        observation: str,
        trigger: str,
        action: str,
        refs: tuple[str, ...] = (),
        position_ids: tuple[str, ...] = (),
        due_on: str | None = None,
        replaces: str | None = None,
    ) -> Signpost:
        record = self._record(debate_id)
        if not observation.strip() or not trigger.strip() or not action.strip():
            raise ValueError("观察点要写 observation（看什么）、trigger（什么算触发）和 action（对应动作）")
        if not refs and due_on is None:
            raise ValueError("观察点要能被检查：写 refs（obs:<id>、账本条目）或 due_on")
        for position_id in position_ids:
            _find(record.positions, position_id, "观点")
        old = None
        if replaces is not None:
            old = self._replaceable_signpost(record, replaces)
        seq = self.repository.next_seq("debate_signposts", debate_id)
        signpost = Signpost(
            signpost_id=f"{debate_id}:s{seq}",
            debate_id=debate_id,
            observation=observation.strip(),
            trigger=trigger.strip(),
            action=action.strip(),
            refs=tuple(refs),
            position_ids=tuple(position_ids),
            due_on=due_on,
            created_at=self._now(),
        )
        self.repository.save("debate_signposts", signpost, seq)
        if old is not None:
            self.repository.save("debate_signposts", replace(old, replaced_by=signpost.signpost_id))
        return signpost

    def conclude(
        self,
        debate_id: str,
        *,
        conclusion: str,
        decisions: tuple[dict[str, Any], ...] = (),
        blockers: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        record = self._open(debate_id)
        open_positions = [item.position_id for item in record.positions if item.status == POSITION_OPEN]
        pending = [item.rebuttal_id for item in record.rebuttals if item.status == REBUTTAL_PENDING]
        if open_positions or pending:
            raise ValueError(f"还有待结论的观点 {open_positions} 或未回应的驳斥 {pending}")
        if not conclusion.strip():
            raise ValueError("conclusion 写明阶段结论：立住的推论、机会与剪枝、决策")
        checked = tuple(_decision(item) for item in decisions)
        blockers = tuple(item.strip() for item in blockers if item.strip())
        if record.debate.significance == SIGNIFICANCE_MAJOR and not checked and not blockers:
            raise ValueError(
                "重大事件必须给出 decisions（标的、动作、仓位、时点或条件、失效条件）；"
                "确实做不出时在 blockers 写明缺的是哪种研究方法或基础设施，作为待改进项"
            )
        debate = replace(
            record.debate,
            status=DEBATE_CONCLUDED,
            conclusion=conclusion.strip(),
            decisions=checked,
            blockers=blockers,
            concluded_at=self._now(),
        )
        self.repository.save_debate(debate)
        return self.show(debate_id)

    def amend(
        self,
        debate_id: str,
        *,
        reason: str,
        as_of_date: str,
        decisions: tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        """Revise a concluded debate's decisions after new user facts or constraints, keeping the trail.

        A decision replaces the one with the same subject; a new subject is added. Arguments that change
        the judgement itself belong in a new round (parent_debate_id), not here.
        """

        record = self._record(debate_id)
        if record.debate.status != DEBATE_CONCLUDED:
            raise ValueError("只有已结论的辩论能修订决策；未结论时直接在 conclude 里写")
        if not reason.strip():
            raise ValueError("reason 写明修订依据（如用户新的约束、持仓变化）")
        date.fromisoformat(as_of_date)
        checked = tuple(_decision(item) for item in decisions)
        if not checked:
            raise ValueError("decisions 至少一条")
        subjects = {item["subject"] for item in checked}
        replaced = tuple(item for item in record.debate.decisions if item["subject"] in subjects)
        kept = tuple(item for item in record.debate.decisions if item["subject"] not in subjects)
        amendment = {
            "amended_at": self._now(),
            "as_of_date": as_of_date,
            "reason": reason.strip(),
            "decisions": list(checked),
            "replaced": list(replaced),
        }
        debate = replace(
            record.debate,
            decisions=kept + checked,
            amendments=record.debate.amendments + (amendment,),
        )
        self.repository.save_debate(debate)
        return self.show(debate_id)

    def show(self, debate_id: str) -> dict[str, Any]:
        record = self._record(debate_id)
        coverage = _coverage(record)
        return {
            "debate": record.debate.to_dict(),
            "positions": [item.to_dict() for item in record.positions],
            "rebuttals": [item.to_dict() for item in record.rebuttals],
            "signposts": [item.to_dict() for item in record.signposts],
            "participations": [item.to_dict() for item in record.participations],
            "coverage": coverage,
            "counts": _counts(record),
            "next_actions": _next_actions(record, coverage),
            "gaps": _gaps(record, coverage),
        }

    def list_debates(self, *, user_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for debate in self.repository.list_debates(user_id=user_id, status=status):
            record = self._record(debate.debate_id)
            rows.append({**debate.to_dict(), "counts": _counts(record)})
        return rows

    def records(self, *, user_id: str | None = None) -> list[DebateRecord]:
        return [self._record(item.debate_id) for item in self.repository.list_debates(user_id=user_id)]

    def brief(self, debate_id: str, persona_id: str) -> dict[str, Any]:
        item = persona(persona_id)
        return {"persona": item.to_dict(), "debate_id": debate_id, "brief": persona_brief(item, self.show(debate_id))}

    def import_persona(self, debate_id: str, persona_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Record a sub-agent's JSON answer (the format asked for in ``brief``) under its persona party.

        Positions default to the persona's layer when the debate covers it, otherwise to the debate's
        first layer; the round is kept as a participation even when the persona had no view.
        """

        item_persona = persona(persona_id)
        party = item_persona.party
        debate = self._open(debate_id).debate
        default_layer = item_persona.layer if item_persona.layer in debate.layers else debate.layers[0]
        positions = [
            self.add_position(
                debate_id,
                party=party,
                statement=str(item.get("statement", "")),
                mechanism=str(item.get("mechanism", "")),
                horizon=str(item.get("horizon", "")),
                falsifiers=tuple(item.get("falsifiers") or ()),
                evidence_refs=tuple(item.get("evidence_refs") or ()),
                layer=str(item.get("layer") or default_layer),
                subject=item.get("subject"),
            ).position_id
            for item in payload.get("positions") or ()
        ]
        rebuttals = [
            self.rebut(
                debate_id,
                position_id=str(item.get("position_id", "")),
                party=party,
                argument=str(item.get("argument", "")),
                kind=str(item.get("kind", "")),
                evidence_refs=tuple(item.get("evidence_refs") or ()),
            ).rebuttal_id
            for item in payload.get("rebuttals") or ()
        ]
        no_view = (payload.get("no_view") or "").strip() or None
        if not positions and not rebuttals and no_view is None:
            raise ValueError("角色没有观点也没有驳斥时，要在 no_view 写明原因")
        seq = self.repository.next_seq("debate_participations", debate_id)
        participation = Participation(
            participation_id=f"{debate_id}:j{seq}",
            debate_id=debate_id,
            party=party,
            positions=tuple(positions),
            rebuttals=tuple(rebuttals),
            cannot_rebut=tuple(payload.get("cannot_rebut") or ()),
            outside_circle=tuple(payload.get("outside_circle") or ()),
            no_view=no_view,
            created_at=self._now(),
        )
        self.repository.save("debate_participations", participation, seq)
        return participation.to_dict()

    def _replaceable_signpost(self, record: DebateRecord, signpost_id: str) -> Signpost:
        """A live signpost of this debate or of an earlier round (parent chain): a new round takes over the old plan."""

        current: DebateRecord | None = record
        while current is not None:
            found = next((item for item in current.signposts if item.signpost_id == signpost_id), None)
            if found is not None:
                if found.replaced_by is not None:
                    break
                return found
            parent = current.debate.parent_debate_id
            current = self.repository.record(parent) if parent else None
        raise ValueError(f"{signpost_id} 不存在于本辩论及其上一轮，或已被取代")

    def _record(self, debate_id: str) -> DebateRecord:
        record = self.repository.record(debate_id)
        if record is None:
            raise ValueError(f"未知辩论 {debate_id}；用 debate --action list 查看")
        return record

    def _open(self, debate_id: str) -> DebateRecord:
        record = self._record(debate_id)
        if record.debate.status != DEBATE_OPEN:
            raise ValueError(f"{debate_id} 已结论；新的分歧请开新一轮（parent_debate_id 指向它）")
        return record

    def _now(self) -> str:
        return self.clock().isoformat()


def _check_party(party: str) -> None:
    if party in (PARTY_USER, PARTY_AGENT):
        return
    if not party.startswith(PERSONA_PREFIX):
        raise ValueError(f"参与方取 {PARTY_USER}、{PARTY_AGENT} 或 {PERSONA_PREFIX}<角色>")
    persona(party)


def _find(items, item_id: str, label: str):
    key = "rebuttal_id" if label == "驳斥" else "position_id"
    for item in items:
        if getattr(item, key) == item_id:
            return item
    raise ValueError(f"本辩论没有{label} {item_id}")


def _decision(item: dict[str, Any]) -> dict[str, Any]:
    decision = {key: str(item.get(key) or "").strip() for key in ("subject", "action", "size", "timing", "exit")}
    decision["basis"] = list(item.get("basis") or ())
    if not decision["subject"] or decision["action"] not in DECISION_ACTIONS:
        raise ValueError(f"决策要写 subject（标的）与 action（取 {', '.join(DECISION_ACTIONS)}）")
    if not decision["timing"]:
        raise ValueError(f"{decision['subject']}：timing 写“现在”或可检查的触发条件")
    if decision["action"] in SIZED_ACTIONS and not decision["size"]:
        raise ValueError(f"{decision['subject']}：{decision['action']} 要写 size（目标占比或分批规则）")
    if decision["action"] in ("buy", "add", "hold") and not decision["exit"]:
        raise ValueError(f"{decision['subject']}：{decision['action']} 要写 exit（失效或离场条件）")
    return decision


def _lineage(positions: tuple[Position, ...], position: Position) -> set[str]:
    """The position and the earlier versions it refines from the same party; another party's rewrite starts fresh."""

    by_id = {item.position_id: item for item in positions}
    ids, current = set(), position
    while current is not None:
        ids.add(current.position_id)
        previous = by_id.get(current.supersedes) if current.supersedes else None
        current = previous if previous is not None and previous.party == position.party else None
    return ids


def _counts(record: DebateRecord) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for item in record.positions:
        by_status[item.status] = by_status.get(item.status, 0) + 1
    return {
        "positions": by_status,
        "pending_rebuttals": sum(1 for item in record.rebuttals if item.status == REBUTTAL_PENDING),
        "signposts": len(record.signposts),
    }


def _coverage(record: DebateRecord) -> dict[str, list[dict[str, str]]]:
    """Per layer in play, which lenses spoke, declared no view, or have not taken part yet."""

    spoke = {item.party for item in record.positions} | {item.party for item in record.rebuttals}
    declined = {item.party for item in record.participations if item.no_view}
    layers = tuple(dict.fromkeys((*record.debate.layers, *(item.layer for item in record.positions))))
    coverage = {}
    for layer in layers:
        rows = [
            {"party": party, "name": name, "status": "spoke" if party in spoke else "absent"}
            for party, name in ((PARTY_USER, "用户"), (PARTY_AGENT, "Agent"))
        ]
        for item in personas_for(layer):
            status = "spoke" if item.party in spoke else "no_view" if item.party in declined else "absent"
            rows.append({"party": item.party, "name": item.name, "status": status})
        coverage[layer] = rows
    return coverage


def _absent_lenses(record: DebateRecord, coverage: dict[str, list[dict[str, str]]]) -> list[tuple[str, dict[str, str]]]:
    return [
        (layer, row)
        for layer in record.debate.layers
        for row in coverage.get(layer, ())
        if row["party"].startswith(PERSONA_PREFIX) and row["status"] == "absent"
    ]


def _next_actions(record: DebateRecord, coverage: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    debate_id = record.debate.debate_id
    actions = []
    if record.debate.status == DEBATE_CONCLUDED:
        return actions
    absent = _absent_lenses(record, coverage)
    if absent:
        actions.append(
            {
                "command": f"{_CLI} --action brief --debate-id {debate_id} --persona <角色>",
                "reason": "这些视角还没参与（盲点）："
                + "、".join(f"{row['name']}（{row['party'].removeprefix(PERSONA_PREFIX)}，{LAYER_LABELS[layer]}）" for layer, row in absent)
                + "；用子代理并行执行后 --action import",
            }
        )
    if PARTY_AGENT not in {row["party"] for rows in coverage.values() for row in rows if row["status"] == "spoke"}:
        actions.append(
            {
                "command": f"{_CLI} --action position --debate-id {debate_id} --json <path>",
                "reason": "Agent 还没有提出自己的观点或驳斥",
            }
        )
    for rebuttal in record.rebuttals:
        if rebuttal.status == REBUTTAL_PENDING:
            actions.append(
                {
                    "command": f"{_CLI} --action answer --debate-id {debate_id} --json <path>",
                    "reason": f"{rebuttal.rebuttal_id}（{rebuttal.party} 驳 {rebuttal.position_id}）待回应：接受或给出反驳依据",
                }
            )
    rebutted = {item.position_id for item in record.rebuttals}
    for position in record.positions:
        if position.status != POSITION_OPEN:
            continue
        lineage = _lineage(record.positions, position)
        if not lineage & rebutted:
            actions.append(
                {
                    "command": f"{_CLI} --action rebut --debate-id {debate_id} --json <path>",
                    "reason": f"{position.position_id}（{position.party}）还没有被驳斥：由其他参与方给出最强反方",
                }
            )
        elif not any(item.status == REBUTTAL_PENDING for item in record.rebuttals if item.position_id == position.position_id):
            actions.append(
                {
                    "command": f"{_CLI} --action resolve --debate-id {debate_id} --json <path>",
                    "reason": f"{position.position_id} 的驳斥都已回应：判定立住、被驳倒或撤回（修正写新观点 supersedes）",
                }
            )
    standing = [item for item in record.positions if item.status == POSITION_STANDS]
    if standing and not record.signposts:
        actions.append(
            {
                "command": f"{_CLI} --action signpost --debate-id {debate_id} --json <path>",
                "reason": "有立住的推论但没有观察点：为关键推论写观察点、触发条件和对应动作",
            }
        )
    settled = all(item.status != POSITION_OPEN for item in record.positions) and not any(
        item.status == REBUTTAL_PENDING for item in record.rebuttals
    )
    if record.positions and settled and (record.signposts or not standing):
        actions.append(
            {
                "command": f"{_CLI} --action conclude --debate-id {debate_id} --json <path>",
                "reason": "观点都已结论：写阶段结论（立住的推论、机会与剪枝、决策）"
                + ("；重大事件必须写 decisions" if record.debate.significance == SIGNIFICANCE_MAJOR else "")
                + ("；未参与的视角写进结论的局限" if absent else ""),
            }
        )
    return actions


def _gaps(record: DebateRecord, coverage: dict[str, list[dict[str, str]]]) -> list[str]:
    gaps = [
        f"{LAYER_LABELS[layer]}的{row['name']}视角未参与"
        for layer, row in _absent_lenses(record, coverage)
    ]
    debate = record.debate
    if debate.status == DEBATE_CONCLUDED and debate.significance == SIGNIFICANCE_MAJOR and not debate.decisions:
        gaps.append("重大事件没有形成决策，待改进的研究方法或基础设施：" + "；".join(debate.blockers))
    for position in record.positions:
        if position.status == POSITION_STANDS and not position.ledger_refs:
            gaps.append(f"{position.position_id} 立住但没有关联账本条目：改写为可结算命题入账后用 --action link 关联")
    covered = {position_id for signpost in record.signposts for position_id in signpost.position_ids}
    for position in record.positions:
        if position.status == POSITION_STANDS and record.signposts and position.position_id not in covered:
            gaps.append(f"{position.position_id} 立住但没有观察点覆盖")
    return gaps
