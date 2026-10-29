"""Strategy archives and their lifecycle (stage 9 slice 1).

An archive describes one strategy version: mechanism, sources, universe, horizons, parameters,
failure conditions and limitations. Its lifecycle state only moves one step at a time and only
when the gate below is met; any state can be retired.

Design notes: docs/modules/3-分析工具/策略规则模块.md「策略档案与生命周期」.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from src.research_contracts import ResearchHorizon

from .catalog import default_strategy_specs
from .etf_rotation import ETF_DUAL_MOMENTUM_STRATEGY_ID, ETFRotationParameters
from .models import ConditionGroup, MetricCondition, StrategySpec
from .sse_market_timing import SSE_MARKET_TIMING_STRATEGY_ID, SSEMarketTimingParameters


class LifecycleState(str, Enum):
    RESEARCH = "research"
    VALIDATING = "validating"
    FORWARD_TRACKING = "forward_tracking"
    ENABLED = "enabled"
    RETIRED = "retired"


STATE_LABELS = {
    LifecycleState.RESEARCH: "研究",
    LifecycleState.VALIDATING: "验证中",
    LifecycleState.FORWARD_TRACKING: "前向跟踪",
    LifecycleState.ENABLED: "启用",
    LifecycleState.RETIRED: "退役",
}
NEXT_STATE = {
    LifecycleState.RESEARCH: LifecycleState.VALIDATING,
    LifecycleState.VALIDATING: LifecycleState.FORWARD_TRACKING,
    LifecycleState.FORWARD_TRACKING: LifecycleState.ENABLED,
}
STRATEGY_KINDS = {
    "rule_screen": "单标的条件规则",
    "timing": "择时",
    "rotation": "轮动",
    "selection": "选股",
    "allocation": "配置与再平衡",
}
# Enabling needs a forward record of this length and size.
FORWARD_MIN_MONTHS = 6
FORWARD_MIN_SETTLED = 12
_ASSET_TYPES = ("stock", "exchange_fund", "index")


@dataclass(frozen=True)
class StrategyArchive:
    strategy_id: str
    version: str
    name: str
    kind: str
    mechanism: str
    universe: str
    horizons: tuple[ResearchHorizon, ...]
    parameters: dict[str, str]
    source_refs: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    limitations: tuple[str, ...]
    family: str = ""
    template_id: str | None = None
    universe_assets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value, name in (
            (self.strategy_id, "strategy_id"),
            (self.version, "version"),
            (self.name, "name"),
            (self.mechanism, "mechanism"),
            (self.universe, "universe"),
        ):
            if not str(value).strip():
                raise ValueError(f"{name} must not be blank")
        if self.kind not in STRATEGY_KINDS:
            raise ValueError(f"kind must be one of {', '.join(STRATEGY_KINDS)}")
        if not self.horizons or not all(isinstance(item, ResearchHorizon) for item in self.horizons):
            raise ValueError("horizons must be non-empty ResearchHorizon values")
        if not self.failure_conditions:
            raise ValueError("failure_conditions must not be empty: every strategy needs a retirement trigger")
        for item in self.universe_assets:
            parts = item.split(":")
            if len(parts) not in (2, 3) or parts[1] not in _ASSET_TYPES:
                raise ValueError(f"universe asset must be <symbol>:<asset_type>[:<role>]: {item}")
        if not self.family:
            object.__setattr__(self, "family", self.strategy_id)

    @property
    def subject_id(self) -> str:
        return f"strategy:{self.strategy_id}"

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["horizons"] = [item.value for item in self.horizons]
        payload["source_refs"] = list(self.source_refs)
        payload["failure_conditions"] = list(self.failure_conditions)
        payload["limitations"] = list(self.limitations)
        payload["universe_assets"] = list(self.universe_assets)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StrategyArchive":
        """JSON fields: strategy_id, version, name, kind (rule_screen/timing/rotation/selection/allocation),
        mechanism, universe, horizons (short/medium_short/medium_long/long), parameters (name -> string),
        source_refs (e.g. docs/evidence/strategy_sources/<file>.md), failure_conditions, limitations,
        optional family, template_id and universe_assets ("<symbol>:<asset_type>[:<role>]", e.g.
        "511010:exchange_fund:defensive"; asset_type stock/exchange_fund/index)."""

        return cls(
            strategy_id=str(payload["strategy_id"]),
            version=str(payload["version"]),
            name=str(payload["name"]),
            kind=str(payload["kind"]),
            mechanism=str(payload["mechanism"]),
            universe=str(payload["universe"]),
            horizons=tuple(ResearchHorizon(item) for item in payload["horizons"]),
            parameters={str(key): str(value) for key, value in (payload.get("parameters") or {}).items()},
            source_refs=tuple(payload.get("source_refs") or ()),
            failure_conditions=tuple(payload.get("failure_conditions") or ()),
            limitations=tuple(payload.get("limitations") or ()),
            family=str(payload.get("family") or ""),
            template_id=payload.get("template_id"),
            universe_assets=tuple(payload.get("universe_assets") or ()),
        )


@dataclass(frozen=True)
class ValidationRecord:
    """Summary of one complete validation (strategy-evaluate); missing parts block forward tracking."""

    evaluation_ref: str
    as_of_date: str
    rolling_out_of_sample: bool
    parameter_neighborhood: bool
    deflated_sharpe: bool
    out_of_sample_not_worse_than_benchmark: bool


@dataclass(frozen=True)
class ForwardRecord:
    """Settled forward signals of a strategy since it entered forward tracking; None: nothing to judge yet."""

    tracking_since: str
    settled_signals: int
    hit_rate_not_worse_than_benchmark: bool | None
    excess_not_worse_than_benchmark: bool | None
    entry_refs: tuple[str, ...] = field(default_factory=tuple)


def transition_blockers(
    archive: StrategyArchive,
    current: LifecycleState,
    target: LifecycleState,
    *,
    as_of_date: str,
    reason: str = "",
    sources_found: tuple[str, ...] = (),
    validation: ValidationRecord | None = None,
    forward: ForwardRecord | None = None,
) -> list[str]:
    """What still stands in the way of moving archive from current to target (empty: allowed)."""

    if current is LifecycleState.RETIRED:
        return ["已退役；要继续研究请登记新版本，新版本从“研究”开始"]
    if target is current:
        return [f"已经处于“{STATE_LABELS[current]}”"]
    if target is LifecycleState.RETIRED:
        return [] if reason.strip() else ["退役必须写明触发了哪条失效条件"]
    if NEXT_STATE.get(current) is not target:
        return ["只能按 研究 → 验证中 → 前向跟踪 → 启用 逐级迁移"]
    if target is LifecycleState.VALIDATING:
        missing = [ref for ref in archive.source_refs if ref not in sources_found]
        blockers = [] if archive.source_refs else ["没有来源登记（docs/evidence/strategy_sources/）"]
        return blockers + [f"来源登记文件不存在：{ref}" for ref in missing]
    if target is LifecycleState.FORWARD_TRACKING:
        if validation is None:
            return ["没有完整验证记录（strategy-evaluate）"]
        return [
            text
            for ok, text in (
                (validation.rolling_out_of_sample, "缺滚动样本外结果"),
                (validation.parameter_neighborhood, "缺参数邻域结果"),
                (validation.deflated_sharpe, "缺按尝试次数折扣后的显著性结果"),
                (validation.out_of_sample_not_worse_than_benchmark, "样本外表现劣于简单基准"),
            )
            if not ok
        ]
    if forward is None:
        return ["没有前向跟踪记录"]
    blockers = []
    months = _months_between(forward.tracking_since, as_of_date)
    if months < FORWARD_MIN_MONTHS:
        blockers.append(f"前向跟踪 {months} 个月，不足 {FORWARD_MIN_MONTHS} 个月")
    if forward.settled_signals < FORWARD_MIN_SETTLED:
        blockers.append(f"已结算信号 {forward.settled_signals} 个，不足 {FORWARD_MIN_SETTLED} 个")
    if forward.hit_rate_not_worse_than_benchmark is None:
        blockers.append("暂无已结算信号，命中率尚无法判断")
    elif not forward.hit_rate_not_worse_than_benchmark:
        blockers.append("命中率区间劣于基准")
    if forward.excess_not_worse_than_benchmark is None:
        blockers.append("暂无前向收益，超额尚无法判断")
    elif not forward.excess_not_worse_than_benchmark:
        blockers.append("超额收益劣于基准")
    return blockers


def _months_between(start: str, end: str) -> int:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    months = (last.year - first.year) * 12 + last.month - first.month
    return months - (1 if last.day < first.day else 0)


def default_archives() -> tuple[StrategyArchive, ...]:
    """Archives of the existing research rules, derived from their specs and parameters."""

    rules = {spec.strategy_id: spec for spec in default_strategy_specs()}
    archives = [
        _rule_archive(
            rules[strategy_id],
            universe,
            ("单标的研究中规则结论与之后同周期的超额收益方向持续相反（前向跟踪中命中率区间上限低于 50%）",),
        )
        for strategy_id, universe in (
            ("trend_timing", "单只股票或场内基金"),
            ("catalyst_repricing", "单只普通公司或周期公司股票"),
            ("quality_value", "单只普通公司股票"),
            ("business_thesis_check", "单只普通、周期或高成长公司股票"),
        )
    ]
    archives.append(
        StrategyArchive(
            strategy_id=ETF_DUAL_MOMENTUM_STRATEGY_ID,
            version="v1",
            name="ETF 双动量趋势轮动",
            kind="rotation",
            mechanism=(
                "在固定 ETF 集合中按 126 日与 252 日收益各半加权排名（相对动量），只保留 252 日收益为正且收盘价"
                "在 200 日均线之上的（绝对动量），取前 2 名按 60 日波动倒数分配权重；都不合格时转入防御资产，"
                "没有防御资产则空仓；信号在月末用收盘价计算、下一交易日生效"
            ),
            universe="固定 ETF 集合：沪深300ETF 510300、中证500ETF 510500、中证1000ETF 512100、黄金ETF 518880，防御资产国债ETF 511010",
            horizons=(ResearchHorizon.MEDIUM_SHORT,),
            parameters=_parameters(ETFRotationParameters()),
            source_refs=("docs/evidence/strategy_sources/etf_dual_momentum.md",),
            failure_conditions=(
                "滚动样本外连续两段跑输同集合等权买入持有",
                "前向跟踪满一年超额收益为负且命中率区间上限低于 50%",
            ),
            limitations=(
                "窗口、集合与 top_n 是初始研究假设，未经本项目验证",
                "ETF 趋势不等于个股动量",
                "集合固定为当前选定的 ETF，存在选择偏差",
            ),
            universe_assets=(
                "510300:exchange_fund",
                "510500:exchange_fund",
                "512100:exchange_fund",
                "518880:exchange_fund",
                "511010:exchange_fund:defensive",
            ),
        )
    )
    archives.append(
        StrategyArchive(
            strategy_id=SSE_MARKET_TIMING_STRATEGY_ID,
            version="v1",
            name="上证指数多周期高低位择时",
            kind="timing",
            mechanism=(
                "短周期看 RSI 与 20 日通道反转，中短周期看 120 日价格位置与 60 日均线，"
                "中长周期看 200 日趋势、回撤与估值分位，并用 50ETF 波动率指数确认；各周期分别给出信号，不合成总分"
            ),
            universe="上证指数（000001）",
            horizons=(ResearchHorizon.SHORT, ResearchHorizon.MEDIUM_SHORT, ResearchHorizon.MEDIUM_LONG),
            parameters=_parameters(SSEMarketTimingParameters()),
            source_refs=("docs/evidence/strategy_sources/sse_market_timing.md",),
            failure_conditions=(
                "滚动样本外各周期信号的后续收益与无条件基线无差别或方向相反",
                "前向跟踪满一年命中率区间上限低于 50%",
            ),
            limitations=(
                "阈值与窗口是初始研究假设，未经本项目验证",
                "估值与波动率指数历史较短，长周期样本少",
            ),
            universe_assets=("000001:index",),
        )
    )
    return tuple(archives)


def _rule_archive(spec: StrategySpec, universe: str, failure_conditions: tuple[str, ...]) -> StrategyArchive:
    descriptions = [
        *(_describe(node) for node in spec.hard_filters),
        *(f"否决：{_describe(node)}" for node in spec.vetoes),
        *(f"加分（权重 {rule.weight}）：{_describe(rule.condition)}" for rule in spec.score_rules),
    ]
    return StrategyArchive(
        strategy_id=spec.strategy_id,
        version=spec.version,
        name=spec.name,
        kind="rule_screen",
        mechanism="；".join(descriptions) + f"；得分不低于 {spec.passing_score} 为通过",
        universe=universe,
        horizons=spec.horizons,
        parameters={
            "data_window_days": str(spec.data_window_days),
            "valid_for_days": str(spec.valid_for_days),
            "passing_score": str(spec.passing_score),
        },
        source_refs=(),
        failure_conditions=failure_conditions,
        limitations=spec.limitations,
    )


def _describe(node: MetricCondition | ConditionGroup) -> str:
    return node.description


def _parameters(parameters: Any) -> dict[str, str]:
    return {key: str(value) for key, value in dataclasses.asdict(parameters).items() if value is not None}
