"""Rule-based behavioral-bias hints for a holding review (decision 8 of stage 7).

Design notes: docs/modules/6-编排/研究编排模块.md, “退出计划与行为偏差提示”.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .exit_plan_models import BehavioralHint, ExitAction, ExitCondition, ExitDimension, ExitState, HoldingFacts

LOSS_ANCHOR = Decimal("-0.10")
PROFIT_TAKING = Decimal("0.20")
INTENDED_ACTIONS = ("hold", "add", "reduce", "exit")
_BROKEN = ("invalidated", "deviating")


def behavioral_hints(
    facts: HoldingFacts,
    *,
    thesis_status: str | None,
    reconstructed: bool,
    conditions: tuple[ExitCondition, ...],
    intended_action: str | None,
    as_of_date: str,
) -> tuple[BehavioralHint, ...]:
    if intended_action is not None and intended_action not in INTENDED_ACTIONS:
        raise ValueError(f"intended_action 只接受 {'、'.join(INTENDED_ACTIONS)}")
    hints = []
    pnl = facts.unrealized_return
    triggered = {item.dimension for item in conditions if item.state is ExitState.TRIGGERED}
    sell_triggered = any(
        item.state is ExitState.TRIGGERED and item.action is not ExitAction.HOLD for item in conditions
    )
    if pnl is None:
        hints.append(
            BehavioralHint(
                "cost_unknown",
                "info",
                "缺少持仓成本",
                "用户空间没有记录平均成本",
                "无法检查成本锚定与处置效应；用户说出成本后用 record-holding --average-cost 记录",
            )
        )
    elif pnl <= LOSS_ANCHOR:
        hints.append(
            BehavioralHint(
                "cost_anchor",
                "warning" if intended_action in (None, "hold") and sell_triggered else "caution",
                "以成本价为锚",
                f"浮亏 {-pnl:.1%}" + ("，且已有卖出条件被触发" if sell_triggered else ""),
                "成本价只影响已发生的盈亏，不影响未来收益；按“今天手持现金是否会以现价买入”复核，"
                "不要以“回本”作为卖出或持有的条件",
            )
        )
    if intended_action == "add" and pnl is not None and pnl < 0:
        weak = thesis_status in _BROKEN or ExitDimension.VALUATION in triggered
        hints.append(
            BehavioralHint(
                "averaging_down",
                "warning" if weak or thesis_status is None else "caution",
                "为摊低成本加仓",
                f"浮亏 {-pnl:.1%} 时计划加仓"
                + ("；论点已偏离或失效" if thesis_status in _BROKEN else "")
                + ("；没有可检查的论点" if thesis_status is None else ""),
                "加仓理由必须来自论点仍成立且估值更有吸引力，不能来自“摊低成本”；加仓后的权重仍受单一标的上限约束",
            )
        )
    if thesis_status in _BROKEN and intended_action in (None, "hold", "add"):
        hints.append(
            BehavioralHint(
                "thesis_broken_hold",
                "warning",
                "论点失效或偏离仍持有",
                "论点检查为" + ("失效" if thesis_status == "invalidated" else "偏离"),
                "继续持有需要一个新的、可检验的论点并重新入账；否则按退出计划中的论点维度执行",
            )
        )
    if (
        intended_action in ("reduce", "exit")
        and pnl is not None
        and pnl >= PROFIT_TAKING
        and thesis_status == "on_track"
        and not sell_triggered
    ):
        hints.append(
            BehavioralHint(
                "premature_profit_taking",
                "caution",
                "盈利标的过早卖出",
                f"浮盈 {pnl:.1%}，论点符合，没有估值、风险预算或机会成本条件被触发",
                "处置效应：倾向于卖出盈利、留下亏损。卖出理由应来自论点、估值或风险预算，而不是锁定盈利",
            )
        )
    if reconstructed:
        hints.append(
            BehavioralHint(
                "reconstructed_thesis",
                "info",
                "论点为事后重建",
                "论点按用户回忆记录",
                "回忆容易向现在的结果靠拢（后见之明），复核时以入账后的前向检查为准",
            )
        )
    if facts.first_bought_on is not None:
        days = (date.fromisoformat(as_of_date) - date.fromisoformat(facts.first_bought_on)).days
        hints.append(
            BehavioralHint(
                "holding_period",
                "info",
                "持有时长",
                f"自 {facts.first_bought_on} 首次买入已 {days} 天",
                "持有时长本身不是买卖理由；与论点的观察期对照，判断是否已过催化窗口",
            )
        )
    return tuple(hints)
