"""Assemble and record exit plans for held stocks.

Design notes: docs/modules/6-编排/研究编排模块.md, “退出计划与行为偏差提示”.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from src.fundamental_analysis import INDICATOR_PREFIX
from src.master_data import MasterDataService
from src.portfolio_risk import PortfolioRiskService
from src.research_contracts import (
    ActionTrigger,
    ActionType,
    ClaimComparison,
    InvestmentThesis,
    QuantityRule,
    QuantityRuleKind,
    ResearchHorizon,
    SettleableAction,
    SettlementMethod,
    SettlementRule,
    TriggerKind,
    to_payload,
)
from src.research_ledger import LedgerScope, PriceDataUnavailable, ResearchLedgerService, fin_metric_id
from src.research_state import ResearchStateService
from src.user_space import UserContext

from .behavioral_hints import behavioral_hints
from .exit_plan_models import (
    SELL_ACTIONS,
    ExitAction,
    ExitCondition,
    ExitDimension,
    ExitPlan,
    ExitState,
    HoldingFacts,
    exit_plan_lines,
)
from .thesis import DEVIATING, INVALIDATED, ThesisService
from .valuation_runner import ValuationModelRunner

# Model error between two companies' implied returns is large; smaller gaps are not a reason to switch.
SWITCH_MARGIN = Decimal("0.03")
# Unprofitable companies: implied revenue growth (optimistic terminal margin) this far above the
# historical revenue CAGR means the price already discounts more than the track record supports.
REVENUE_OVERREACH = Decimal("0.10")
CONDITION_WINDOW_DAYS = 365
_QUANTITY = {
    ExitAction.REDUCE: "分批减持：首批约三分之一，其余按仓位计划与复查结果",
    ExitAction.EXIT: "全部卖出",
    ExitAction.SWITCH: "卖出后换入替代标的，数量按替代标的的仓位计划",
}


class ExitPlanService:
    def __init__(
        self,
        *,
        research_state: ResearchStateService,
        portfolio_risk: PortfolioRiskService,
        thesis: ThesisService,
        valuation: ValuationModelRunner,
        prices: Any,
        master_data: MasterDataService,
        ledger: ResearchLedgerService,
    ) -> None:
        self.research_state = research_state
        self.portfolio_risk = portfolio_risk
        self.thesis = thesis
        self.valuation = valuation
        self.prices = prices
        self.master_data = master_data
        self.ledger = ledger

    def assemble(
        self,
        *,
        user_id: str,
        asset_id: str,
        as_of_date: str,
        alternatives: tuple[str, ...] = (),
        intended_action: str | None = None,
        allow_remote: bool = True,
    ) -> ExitPlan:
        context = UserContext(user_id)
        holding = self.research_state.get_holding(context, asset_id=asset_id)
        if holding is None or holding.quantity <= 0:
            raise ValueError("用户空间没有该持仓；先用 record-holding 记录数量、成本与首次买入日")
        asset = self.master_data.get_asset(asset_id)
        gaps: list[str] = []
        model_payload = None
        try:
            model_payload = self.valuation.run(None, as_of_date, asset_id=asset_id, record=False, allow_remote=allow_remote)
        except Exception as exc:  # noqa: BLE001 - reported as a gap
            gaps.append(f"估值模型不可用：{type(exc).__name__}: {exc}")
        if model_payload is not None and not model_payload.get("applicable", True):
            gaps.append(model_payload["note"])
            model_payload = None
        price, price_date = self._price(asset_id, as_of_date, model_payload)
        average_cost = None if holding.average_cost is None else Decimal(str(holding.average_cost))
        weight, weight_as_of, weight_note = self._weight(context, asset_id, as_of_date)
        if weight_note:
            gaps.append(weight_note)
        facts = HoldingFacts(
            quantity=Decimal(str(holding.quantity)),
            price=price,
            price_date=price_date,
            average_cost=average_cost,
            unrealized_return=(
                None if not average_cost else (price / average_cost - 1).quantize(Decimal("0.0001"))
            ),
            weight=weight,
            weight_as_of=weight_as_of,
            first_bought_on=holding.first_bought_on,
        )
        thesis_result, thesis_artifact, thesis_entry_id = self._thesis(asset_id, as_of_date, user_id)
        model = None if model_payload is None else model_payload["model"]
        conditions = [
            *_thesis_conditions(thesis_result, thesis_artifact),
            _valuation_condition(model, price),
            self._risk_condition(context, asset_id, as_of_date, weight),
            self._opportunity_condition(model, price, alternatives, as_of_date, allow_remote),
            _time_stop(thesis_artifact, thesis_entry_id, thesis_result),
        ]
        hints = behavioral_hints(
            facts,
            thesis_status=None if thesis_result is None else thesis_result["status"],
            reconstructed=bool(thesis_result and thesis_result["reconstructed"]),
            conditions=tuple(conditions),
            intended_action=intended_action,
            as_of_date=as_of_date,
        )
        payload = {
            "plan_id": "",
            "user_id": user_id,
            "asset_id": asset_id,
            "asset_name": asset.display_name or asset.standard_symbol,
            "as_of_date": as_of_date,
            "holding": facts,
            "conditions": tuple(conditions),
            "behavioral_hints": hints,
            "gaps": tuple(gaps),
            "intended_action": intended_action,
        }
        digest = hashlib.sha256(
            json.dumps(to_payload(ExitPlan(**payload)), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return ExitPlan(**{**payload, "plan_id": f"exit-plan:{digest}"})

    def run(
        self,
        *,
        user_id: str,
        asset_id: str,
        as_of_date: str,
        alternatives: tuple[str, ...] = (),
        intended_action: str | None = None,
        record: bool = True,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        plan = self.assemble(
            user_id=user_id,
            asset_id=asset_id,
            as_of_date=as_of_date,
            alternatives=alternatives,
            intended_action=intended_action,
            allow_remote=allow_remote,
        )
        return {
            "plan": plan.to_dict(),
            "lines": exit_plan_lines(plan),
            "ledger_entries": self.record(plan) if record else [],
        }

    def record(self, plan: ExitPlan, *, horizon: ResearchHorizon = ResearchHorizon.MEDIUM_SHORT) -> list[dict[str, Any]]:
        """Every set or met sell condition becomes a settleable action in the user's ledger."""

        actions = tuple(_action(plan, item, horizon) for item in plan.conditions if _recordable(item))
        if not actions:
            return []
        entries = self.ledger.record(
            actions,
            scope=LedgerScope.PERSONAL,
            user_id=plan.user_id,
            source_ref=f"exit_plan:{plan.plan_id}",
        )
        return [
            {"entry_id": entry.entry_id, "condition_id": action.action_id.rsplit(":", 1)[-1]}
            for entry, action in zip(entries, actions)
        ]

    def _price(self, asset_id: str, as_of_date: str, model_payload: dict[str, Any] | None) -> tuple[Decimal, str]:
        if model_payload is not None and model_payload.get("price"):
            return Decimal(model_payload["price"]), as_of_date
        start = (date.fromisoformat(as_of_date) - timedelta(days=30)).isoformat()
        series = self.prices.series(asset_id, start, as_of_date)
        if not series:
            raise PriceDataUnavailable(f"{as_of_date} 前 30 天没有价格")
        return series[-1].value, series[-1].date

    def _weight(self, context: UserContext, asset_id: str, as_of_date: str) -> tuple[Decimal | None, str | None, str | None]:
        for snapshot in sorted(
            (s for s in self.portfolio_risk.list_snapshots(context) if s.as_of_date <= as_of_date),
            key=lambda s: (s.as_of_date, s.created_at),
            reverse=True,
        ):
            valuation = self.portfolio_risk.get_snapshot(context, snapshot.snapshot_id)
            position = next((p for p in valuation.positions if p.asset_id == asset_id), None)
            if position is not None:
                return Decimal(str(position.weight)).quantize(Decimal("0.0001")), snapshot.as_of_date, None
        holdings = [h for h in self.research_state.list_holdings(context) if h.quantity > 0]
        start = (date.fromisoformat(as_of_date) - timedelta(days=30)).isoformat()
        values = {}
        try:
            for item in holdings:
                series = self.prices.series(item.asset_id, start, as_of_date)
                if not series:
                    raise PriceDataUnavailable(item.asset_id)
                values[item.asset_id] = Decimal(str(item.quantity)) * series[-1].value
        except PriceDataUnavailable as exc:
            return None, None, f"没有组合估值快照，且现算持仓权重时缺价格（{exc}）"
        total = sum(values.values())
        if not total or asset_id not in values:
            return None, None, "没有组合估值快照，无法计算持仓权重"
        return (
            (values[asset_id] / total).quantize(Decimal("0.0001")),
            as_of_date,
            "持仓权重按已记录持仓与最新价格现算（不含现金；场外基金用复权净值，可能偏离实际净值），未保存为组合快照",
        )

    def _thesis(self, asset_id: str, as_of_date: str, user_id: str):
        result = self.thesis.check(asset_id, as_of_date=as_of_date, user_id=user_id)
        if not result["theses"]:
            return None, None, None
        latest = max(result["theses"], key=lambda item: item["as_of_date"])
        artifact = self.thesis.ledger.get_entry(latest["thesis_entry_id"], user_id=user_id).artifact
        return latest, artifact, latest["thesis_entry_id"]

    def _risk_condition(
        self, context: UserContext, asset_id: str, as_of_date: str, weight: Decimal | None
    ) -> ExitCondition:
        snapshots = sorted(
            (s for s in self.portfolio_risk.list_constraint_snapshots(context) if s.as_of_date <= as_of_date),
            key=lambda s: (s.subject_asset_id == asset_id, s.as_of_date, s.created_at),
        )
        if not snapshots:
            return _condition(
                ExitDimension.RISK_BUDGET,
                ExitState.INSUFFICIENT,
                ExitAction.INSUFFICIENT,
                "没有组合约束快照（单一标的权重上限），无法判断风险预算；需要投资任务与约束快照",
            )
        limit = snapshots[-1].max_single_asset_weight
        if weight is None:
            return _condition(
                ExitDimension.RISK_BUDGET,
                ExitState.INSUFFICIENT,
                ExitAction.INSUFFICIENT,
                f"单一标的上限为 {limit:.1%}，但缺少持仓权重",
            )
        refs = (f"constraints:{snapshots[-1].snapshot_id}",)
        if weight > limit:
            return _condition(
                ExitDimension.RISK_BUDGET,
                ExitState.TRIGGERED,
                ExitAction.REDUCE,
                f"持仓权重 {weight:.1%} 超过单一标的上限 {limit:.1%}：减至上限以内",
                evidence_refs=refs,
            )
        return _condition(
            ExitDimension.RISK_BUDGET,
            ExitState.ARMED,
            ExitAction.REDUCE,
            f"持仓权重 {weight:.1%}，上限 {limit:.1%}",
            trigger_text=f"持仓权重超过 {limit:.1%} 时减至上限以内",
            observability_key=f"max_weight_above:{limit}",
            evidence_refs=refs,
        )

    def _opportunity_condition(
        self,
        model: dict[str, Any] | None,
        price: Decimal,
        alternatives: tuple[str, ...],
        as_of_date: str,
        allow_remote: bool,
    ) -> ExitCondition:
        own = _implied_return(model)
        if own is None:
            return _condition(
                ExitDimension.OPPORTUNITY_COST,
                ExitState.INSUFFICIENT,
                ExitAction.INSUFFICIENT,
                "没有隐含回报（估值模型不可用或没有为正的利润/现金流口径），无法比较机会成本",
            )
        rf = Decimal(str(model["assumptions"]["risk_free_10y"]))
        compared = [f"10 年国债 {rf:.2%}"]
        notes = [
            "隐含回报 = 按历史增速（周期股用长期营收增速）兑现时、现价买入的年化回报；不同公司的增速假设不同，差距小于 3 个百分点不作为换仓理由",
            "这是模型回报，不是预期收益",
        ]
        if model["method"] == "pb_roe":
            notes.append("银行按近年年报 ROE 中位数计算，ROE 正在下行时偏高，低市净率银行几乎不会触发；需用最新 ROE 复核")
        best: tuple[Decimal, str, str] | None = None
        for symbol in alternatives:
            try:
                payload = self.valuation.run(symbol, as_of_date, record=False, allow_remote=allow_remote)
            except Exception as exc:  # noqa: BLE001 - one failed alternative does not block the plan
                notes.append(f"替代 {symbol} 估值失败：{type(exc).__name__}: {exc}")
                continue
            value = _implied_return(payload.get("model")) if payload.get("applicable", True) else None
            if value is None:
                notes.append(f"替代 {symbol} 没有可比的隐含回报")
                continue
            compared.append(f"{payload.get('name') or symbol} {value:.2%}")
            if best is None or value > best[0]:
                best = (value, payload["asset_id"], payload.get("name") or symbol)
        text = f"按现价隐含年化回报 {own:.2%}；对照：" + "、".join(compared)
        if own < rf:
            return _condition(
                ExitDimension.OPPORTUNITY_COST,
                ExitState.TRIGGERED,
                ExitAction.SWITCH,
                text + "。隐含回报低于无风险利率",
                notes=tuple(notes),
            )
        if best is not None and best[0] - own >= SWITCH_MARGIN:
            return _condition(
                ExitDimension.OPPORTUNITY_COST,
                ExitState.TRIGGERED,
                ExitAction.SWITCH,
                text + f"。{best[2]} 高出 {best[0] - own:.2%}，需确认两者风险相近",
                alternative_asset_id=best[1],
                notes=tuple(notes),
            )
        return _condition(
            ExitDimension.OPPORTUNITY_COST,
            ExitState.CLEAR,
            ExitAction.HOLD,
            text + ("" if alternatives else "。未提供同风险替代，只与国债比较"),
            notes=tuple(notes),
        )


def _condition(
    dimension: ExitDimension,
    state: ExitState,
    action: ExitAction,
    summary: str,
    *,
    index: int = 0,
    **fields: Any,
) -> ExitCondition:
    return ExitCondition(f"{dimension.value}-{index}", dimension, state, action, summary, **fields)


def _thesis_conditions(result: dict[str, Any] | None, thesis: InvestmentThesis | None) -> list[ExitCondition]:
    if result is None or thesis is None:
        return [
            _condition(
                ExitDimension.THESIS,
                ExitState.INSUFFICIENT,
                ExitAction.INSUFFICIENT,
                "账本中没有该持仓的论点：请用户说出当初的买入理由，改写为指标后用 record-thesis 记录（事后重建）",
            )
        ]
    conditions = []
    by_id = {item["indicator_id"]: item for item in result["indicators"]}
    refs = (result["thesis_entry_id"],)
    if result["status"] == INVALIDATED:
        broken = [f"{item['label']}（{item['note']}）" for item in by_id.values() if item["status"] == INVALIDATED]
        conditions.append(
            _condition(
                ExitDimension.THESIS,
                ExitState.TRIGGERED,
                ExitAction.EXIT,
                "论点失效：" + "；".join(broken),
                evidence_refs=refs,
            )
        )
    elif result["status"] == DEVIATING:
        off = [f"{item['label']}（{item['note']}）" for item in by_id.values() if item["status"] == DEVIATING]
        conditions.append(
            _condition(
                ExitDimension.THESIS,
                ExitState.TRIGGERED,
                ExitAction.REDUCE,
                "论点偏离：" + "；".join(off) + "。分批减持并复核论点是否需要改写",
                evidence_refs=refs,
            )
        )
    for index, item in enumerate(thesis.indicators, start=1):
        if item.invalidation_comparison is None or by_id.get(item.indicator_id, {}).get("status") == INVALIDATED:
            continue
        word = "below" if item.invalidation_comparison is ClaimComparison.BELOW else "above"
        # Disclosed indicators are year-to-date, so only thesis-check (same-quarter rule) can judge them.
        key = (
            f"thesis_indicator:{result['thesis_entry_id']}:{item.indicator_id}"
            if item.metric.startswith(INDICATOR_PREFIX)
            else f"metric_{word}:{item.invalidation_threshold}:{fin_metric_id(thesis.asset_id, item.metric)}"
        )
        conditions.append(
            _condition(
                ExitDimension.THESIS,
                ExitState.ARMED,
                ExitAction.EXIT,
                f"论点指标 {item.label} 设有失效线",
                index=index,
                trigger_text=by_id.get(item.indicator_id, {}).get("invalidation"),
                observability_key=key,
                valid_until=thesis.expires_on,
                evidence_refs=refs,
            )
        )
    return conditions


def _valuation_condition(model: dict[str, Any] | None, price: Decimal) -> ExitCondition:
    if model is not None and model["method"] == "revenue_reverse_dcf":
        return _revenue_valuation_condition(model)
    optimistic, basis = _optimistic_price(model, price)
    if optimistic is None:
        return _condition(ExitDimension.VALUATION, ExitState.INSUFFICIENT, ExitAction.INSUFFICIENT, basis)
    notes = (
        "这个价格是估值透支的触发线，不是目标价；乐观情景用默认折现率（10 年国债 + 5%），国债收益率低时会远高于现价，触发概率低",
        basis,
    )
    refs = (f"valuation_model:{model['method']}:{model['report_period']}",)
    if price >= optimistic:
        return _condition(
            ExitDimension.VALUATION,
            ExitState.TRIGGERED,
            ExitAction.REDUCE,
            f"现价 {price:.2f} 元不低于乐观情景每股价值 {optimistic:.2f} 元：价格隐含的预期已超过乐观假设",
            evidence_refs=refs,
            notes=notes,
        )
    return _condition(
        ExitDimension.VALUATION,
        ExitState.ARMED,
        ExitAction.REDUCE,
        f"乐观情景每股价值 {optimistic:.2f} 元，较现价 {optimistic / price - 1:+.0%}",
        trigger_text=f"收盘价高于 {optimistic:.2f} 元（价格隐含预期超过乐观情景）时分批减持",
        observability_key=f"price_above:{optimistic:.2f}",
        evidence_refs=refs,
        notes=notes,
    )


def _revenue_valuation_condition(model: dict[str, Any]) -> ExitCondition:
    """未盈利公司没有每股价值情景，用“乐观终局净利率下的隐含营收增速 vs 历史营收复合增速”判断透支。

    设计说明见 docs/modules/6-编排/研究编排模块.md「退出计划与行为偏差提示」。
    """

    implied_by_margin = model["implied_revenue_growth_by_terminal_margin"]
    margin = max(implied_by_margin, key=lambda key: float(key.rstrip("%")))
    implied = implied_by_margin[margin]
    history = (model.get("historical_growth") or {}).get("revenue_cagr")
    refs = (f"valuation_model:revenue_reverse_dcf:{model['report_period']}",)
    if implied is None:
        return _condition(
            ExitDimension.VALUATION,
            ExitState.INSUFFICIENT,
            ExitAction.INSUFFICIENT,
            f"终局净利率 {margin} 时隐含营收增速超出求解范围（−50%～100%），无法判断估值透支",
            evidence_refs=refs,
        )
    if history is None:
        return _condition(
            ExitDimension.VALUATION,
            ExitState.INSUFFICIENT,
            ExitAction.INSUFFICIENT,
            f"隐含营收年增 {implied:.1%}（终局净利率 {margin}），但缺年报营收复合增速，无法对照",
            evidence_refs=refs,
        )
    gap = Decimal(str(implied)) - Decimal(str(history))
    text = (
        f"终局净利率取乐观的 {margin} 时，市值隐含未来 {model['assumptions']['explicit_years']} 年营收年增 {implied:.1%}，"
        f"历史营收复合增速 {history:.1%}（差 {gap:+.1%}）"
    )
    notes = (
        f"阈值：隐含增速超过历史营收复合增速 {REVENUE_OVERREACH:.0%} 以上视为价格透支乐观假设；是研究假设，未经回测",
        "未盈利公司没有可靠的每股价值情景，因此不生成价格触发条件；下次报告期重算后再判断",
    )
    if gap > REVENUE_OVERREACH:
        return _condition(
            ExitDimension.VALUATION,
            ExitState.TRIGGERED,
            ExitAction.REDUCE,
            text + "：价格隐含的增长已远超历史轨迹",
            evidence_refs=refs,
            notes=notes,
        )
    return _condition(
        ExitDimension.VALUATION,
        ExitState.CLEAR,
        ExitAction.HOLD,
        text,
        evidence_refs=refs,
        notes=notes,
    )


def _optimistic_price(model: dict[str, Any] | None, price: Decimal) -> tuple[Decimal | None, str]:
    if model is None:
        return None, "估值模型不可用，无法判断估值透支"
    if model["method"] in ("reverse_dcf", "normalized_reverse_dcf"):
        scenarios = model.get("scenarios")
        if not scenarios:
            return None, "没有情景区间（缺历史增速或没有为正的现金流/利润口径），无法判断估值透支"
        return Decimal(str(scenarios["optimistic"]["per_share"])).quantize(Decimal("0.01")), (
            f"乐观情景：年增 {scenarios['optimistic']['growth']:.1%}，"
            f"{scenarios.get('anchor', {}).get('basis') or model['primary_basis']} 口径"
        )
    if model["method"] == "pb_roe":
        roes = [item["roe"] for item in model["roe_history"][-5:]]
        a = model["assumptions"]
        r, g = Decimal(str(a["discount_rate"])), Decimal(str(a["terminal_growth"]))
        if not roes or Decimal(str(max(roes))) <= g:
            return None, "缺年报 ROE 历史，无法给出乐观情景的市净率"
        roe = Decimal(str(max(roes)))
        justified = (roe - g) / (r - g)
        book = price / Decimal(str(model["pb"]))
        return (book * justified).quantize(Decimal("0.01")), (
            f"乐观情景：可持续 ROE 取近年年报最高 {roe:.1%}，对应市净率 {justified:.2f} 倍"
        )
    return None, f"估值方法 {model['method']} 没有定义估值透支条件"


def _implied_return(model: dict[str, Any] | None) -> Decimal | None:
    if model is None:
        return None
    if model["method"] == "pb_roe":
        roe = model.get("roe_recent_median")
        if roe is None:
            return None
        g = Decimal(str(model["assumptions"]["terminal_growth"]))
        return (g + (Decimal(str(roe)) - g) / Decimal(str(model["pb"]))).quantize(Decimal("0.0001"))
    scenarios = model.get("scenarios") or {}
    value = (scenarios.get("anchor") or {}).get("implied_return")
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.0001"))


def _time_stop(thesis: InvestmentThesis | None, entry_id: str | None, result: dict[str, Any] | None) -> ExitCondition:
    if thesis is None:
        return _condition(
            ExitDimension.TIME_STOP,
            ExitState.INSUFFICIENT,
            ExitAction.INSUFFICIENT,
            "没有论点，就没有催化窗口与时间止损的基准",
        )
    return _condition(
        ExitDimension.TIME_STOP,
        ExitState.ARMED,
        ExitAction.EXIT,
        f"论点到期日 {thesis.expires_on}",
        trigger_text=f"到 {thesis.expires_on} 论点仍未达到“符合”（目标期偏离或仍待数据）则退出",
        observability_key=f"thesis_status:{entry_id}",
        valid_until=thesis.expires_on,
        evidence_refs=(entry_id,),
        notes=(
            *((f"当前论点状态：{result['status_label']}",) if result else ()),
            "与论点维度的关系：偏离时先分批减持并复核论点，到期仍未回到“符合”再全部退出",
        ),
    )


def _recordable(item: ExitCondition) -> bool:
    return item.action in SELL_ACTIONS and item.state in (ExitState.TRIGGERED, ExitState.ARMED)


def _action(plan: ExitPlan, item: ExitCondition, horizon: ResearchHorizon) -> SettleableAction:
    if item.state is ExitState.TRIGGERED:
        trigger = ActionTrigger(
            TriggerKind.SCHEDULED, plan.as_of_date, plan.as_of_date, scheduled_on=plan.as_of_date
        )
    else:
        until = item.valid_until or (
            date.fromisoformat(plan.as_of_date) + timedelta(days=CONDITION_WINDOW_DAYS)
        ).isoformat()
        trigger = ActionTrigger(
            TriggerKind.CONDITIONAL,
            plan.as_of_date,
            max(until, plan.as_of_date),
            observability_key=item.observability_key or f"manual:{item.dimension.value}",
            condition=item.trigger_text or item.summary,
        )
    return SettleableAction(
        action_id=f"{plan.plan_id}:{item.condition_id}",
        asset_id=plan.asset_id,
        action_type=ActionType.REDUCE if item.action is ExitAction.REDUCE else ActionType.EXIT,
        horizon=horizon,
        trigger=trigger,
        quantity_rule=QuantityRule(QuantityRuleKind.USER_DEFINED, description=_QUANTITY[item.action]),
        settlement_rule=SettlementRule(
            SettlementMethod.PRICE_RELATIVE,
            plan.asset_id,
            alternative_id=item.alternative_asset_id,
        ),
        invalidation_refs=item.evidence_refs,
    )
