"""Common strategy templates (stage 9 slice 4).

A user strategy is registered as "template + parameters": the template fixes the mechanism, sources,
failure conditions and limitations; the user supplies the universe and parameter values. A strategy
family is the template on one universe, so every parameter variant counts as an attempt of the
same family. Descriptions that no template expresses are a formal gap (NOT_EXPRESSIBLE).

Design notes: docs/modules/3-分析工具/策略规则模块.md「策略模板」.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.research_contracts import ResearchHorizon

from .archive import StrategyArchive
from .template_evidence import assess_template

_SOURCES = "docs/evidence/strategy_sources/"
FREQUENCIES = ("month", "quarter", "year")
RANK_FACTORS = ("earnings_yield", "book_yield", "roe", "profit_growth")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class TemplateParameter:
    name: str
    default: str
    description: str
    # int, float, choice, weights (symbol=weight,...), scope (sw:<code> or symbols:<codes>), text
    kind: str = "float"
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyTemplate:
    template_id: str
    name: str
    kind: str
    horizons: tuple[ResearchHorizon, ...]
    mechanism: str
    # single: one asset; set: two or more assets (optional :defensive role); scope: stock pool in parameter scope
    universe_rule: str
    universe_hint: str
    parameters: tuple[TemplateParameter, ...]
    source_ref: str
    failure_conditions: tuple[str, ...]
    limitations: tuple[str, ...]
    examples: tuple[str, ...]

    def defaults(self) -> dict[str, str]:
        return {item.name: item.default for item in self.parameters}

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "kind": self.kind,
            "horizons": [item.value for item in self.horizons],
            "mechanism": self.mechanism.format(**self.defaults()),
            "universe": self.universe_hint,
            "parameters": [
                {"name": p.name, "default": p.default, "description": p.description, **({"choices": list(p.choices)} if p.choices else {})}
                for p in self.parameters
            ],
            "examples": list(self.examples),
            "source_ref": self.source_ref,
        }


STRATEGY_TEMPLATES: dict[str, StrategyTemplate] = {
    item.template_id: item
    for item in (
        StrategyTemplate(
            template_id="ma_timing",
            name="均线择时",
            kind="timing",
            horizons=(ResearchHorizon.MEDIUM_SHORT, ResearchHorizon.MEDIUM_LONG),
            mechanism="收盘价在 {window} 日均线之上持有，跌破则清仓转为现金；信号日收盘判断，下一交易日开盘成交",
            universe_rule="single",
            universe_hint="一个标的：指数（只作研究）、场内基金或股票，如 510300:exchange_fund",
            parameters=(TemplateParameter("window", "200", "均线天数（交易日）", "int"),),
            source_ref=_SOURCES + "ma_timing.md",
            failure_conditions=(
                "滚动样本外年化收益低于买入持有且最大回撤没有改善",
                "前向跟踪满一年信号后的超额收益为负",
            ),
            limitations=("震荡市来回止损，交易成本与滑点会显著侵蚀收益", "窗口长度是用户假设，需参数邻域检验"),
            examples=("沪深300 跌破 200 日线清仓、站上再买回", "黄金 ETF 在 120 日均线上方才持有"),
        ),
        StrategyTemplate(
            template_id="dual_momentum_rotation",
            name="双动量轮动",
            kind="rotation",
            horizons=(ResearchHorizon.MEDIUM_SHORT,),
            mechanism=(
                "月末按 {short_window} 日与 {long_window} 日收益加权排名（相对动量），只保留 {long_window} 日收益为正且"
                "收盘价在 {trend_window} 日均线之上的（绝对动量），取前 {top_n} 名按 {volatility_window} 日波动倒数分配；"
                "都不合格时转入防御资产，没有防御资产则空仓；下一交易日开盘换仓"
            ),
            universe_rule="set",
            universe_hint="两个以上风险资产，可加一个防御资产，如 510300:exchange_fund 518880:exchange_fund 511010:exchange_fund:defensive",
            parameters=(
                TemplateParameter("short_window", "126", "短动量窗口（交易日）", "int"),
                TemplateParameter("long_window", "252", "长动量窗口与绝对动量窗口（交易日）", "int"),
                TemplateParameter("trend_window", "200", "趋势均线窗口（交易日）", "int"),
                TemplateParameter("volatility_window", "60", "波动率窗口（交易日）", "int"),
                TemplateParameter("short_weight", "0.5", "短动量在排名中的权重"),
                TemplateParameter("long_weight", "0.5", "长动量在排名中的权重"),
                TemplateParameter("top_n", "2", "持有的风险资产数", "int"),
            ),
            source_ref=_SOURCES + "etf_dual_momentum.md",
            failure_conditions=(
                "滚动样本外连续两段跑输同集合等权买入持有",
                "前向跟踪满一年超额收益为负",
            ),
            limitations=("资产集合由用户选定，存在事后选择偏差", "趋势反转时月度调仓滞后"),
            examples=("在沪深300、中证500、黄金之间每月轮动强势的", "股债轮动：股票趋势转弱就切到国债 ETF"),
        ),
        StrategyTemplate(
            template_id="valuation_percentile_timing",
            name="估值分位择时",
            kind="timing",
            horizons=(ResearchHorizon.MEDIUM_LONG, ResearchHorizon.LONG),
            mechanism=(
                "估值序列 {valuation_series} 每有新值可见时，计算它在最近 {lookback_years} 年历史中的分位；分位不高于 "
                "{buy_below} 时持有标的，不低于 {sell_above} 时清仓，介于两者之间保持原状态（首次判断时低于两阈值中点则持有）；"
                "估值点按发布日可见，下一交易日开盘成交"
            ),
            universe_rule="single",
            universe_hint="一个与估值序列对应的标的，如 csi300_pe_ttm 对应 510300:exchange_fund 或 000300:index",
            parameters=(
                TemplateParameter("valuation_series", "csi300_pe_ttm", "宏观目录里的指数估值序列（macro 命令可查）", "text"),
                TemplateParameter("lookback_years", "10", "分位的回看年数", "int"),
                TemplateParameter("min_history_years", "5", "至少有多少年估值历史才开始判断", "int"),
                TemplateParameter("buy_below", "0.3", "买入分位阈值（0–1）"),
                TemplateParameter("sell_above", "0.7", "卖出分位阈值（0–1）"),
            ),
            source_ref=_SOURCES + "valuation_timing.md",
            failure_conditions=(
                "滚动样本外年化收益与回撤都劣于买入持有",
                "估值序列口径变化（成分、会计准则）使历史分位不可比",
            ),
            limitations=(
                "估值低位可以持续多年，长期空仓会错过上涨",
                "估值序列为月末值，信号频率低，样本内切换次数少、统计意义有限",
            ),
            examples=("沪深300 市盈率低于历史 30% 分位买入、高于 70% 卖出", "创业板估值便宜时才持有"),
        ),
        StrategyTemplate(
            template_id="factor_selection",
            name="因子选股",
            kind="selection",
            horizons=(ResearchHorizon.MEDIUM_LONG,),
            mechanism=(
                "股票池 {scope} 中，每月末只用当时已上市的股票和已公告的最新年报：ROE 不低于 {min_roe}%、资产负债率不高于 "
                "{max_debt_ratio}%、净利润同比不低于 {min_profit_growth}%，再按 {rank_by} 从高到低取前 {top_n} 只等权持有；"
                "没有合格股票时持有现金；下一交易日开盘调仓"
            ),
            universe_rule="scope",
            universe_hint="参数 scope：sw:<申万行业代码>（当前成分）或 symbols:<代码,...>（可含已退市代码）",
            parameters=(
                TemplateParameter("scope", "", "股票池：sw:<申万代码> 或 symbols:<代码,...>", "scope"),
                TemplateParameter("min_roe", "10", "最低年报 ROE（%）"),
                TemplateParameter("max_debt_ratio", "60", "最高资产负债率（%）"),
                TemplateParameter("min_profit_growth", "0", "最低净利润同比（%）"),
                TemplateParameter("top_n", "10", "持有只数", "int"),
                TemplateParameter(
                    "rank_by", "earnings_yield", "排序因子：盈利收益率（市盈率倒数）、账面收益率（市净率倒数）、ROE、利润增长",
                    "choice", RANK_FACTORS,
                ),
            ),
            source_ref=_SOURCES + "factor_selection.md",
            failure_conditions=(
                "滚动样本外连续两段跑输股票池等权",
                "前向跟踪满一年相对股票池等权的超额为负",
            ),
            limitations=(
                "只用年报，季报信息滞后最长约一年",
                "行业范围用当前成分，已退市公司缺行情，存在幸存者偏差",
                "没有剔除壳价值很高的微盘股",
            ),
            examples=("食品饮料里 ROE 高、负债低、最便宜的 10 只", "银行股按市净率最低的 5 只"),
        ),
        StrategyTemplate(
            template_id="fixed_weight_rebalance",
            name="定期再平衡",
            kind="allocation",
            horizons=(ResearchHorizon.MEDIUM_LONG, ResearchHorizon.LONG),
            mechanism="按目标权重 {weights} 持有，每个 {frequency} 末再平衡回目标权重；尚无行情的标的权重按比例分给其余标的",
            universe_rule="set",
            universe_hint="两个以上标的，如 510300:exchange_fund 511010:exchange_fund",
            parameters=(
                TemplateParameter("weights", "", "目标权重，如 510300=0.6,511010=0.4；留空为等权", "weights"),
                TemplateParameter("frequency", "quarter", "再平衡频率", "choice", FREQUENCIES),
            ),
            source_ref=_SOURCES + "rebalance_staged_entry.md",
            failure_conditions=("滚动样本外风险调整后收益劣于同标的买入持有",),
            limitations=("单边趋势市中再平衡会持续卖出强势资产，可能跑输买入持有",),
            examples=("股六债四每季度再平衡", "沪深300、黄金、国债各三分之一每年再平衡"),
        ),
        StrategyTemplate(
            template_id="staged_entry",
            name="分批建仓（定投）",
            kind="allocation",
            horizons=(ResearchHorizon.MEDIUM_LONG, ResearchHorizon.LONG),
            mechanism=(
                "期初资金分 {periods} 期投入：第 k 期把总仓位调到 k/{periods}，每个 {frequency} 末一期，未投入部分按现金计息；"
                "按目标权重 {weights} 在标的间分配；投完后持有不再调整"
            ),
            universe_rule="any",
            universe_hint="一个或多个标的，如 510300:exchange_fund",
            parameters=(
                TemplateParameter("periods", "12", "分几期投完", "int"),
                TemplateParameter("frequency", "month", "每期间隔", "choice", FREQUENCIES),
                TemplateParameter("weights", "", "标的间权重，如 510300=0.7,510500=0.3；留空为等权", "weights"),
            ),
            source_ref=_SOURCES + "rebalance_staged_entry.md",
            failure_conditions=("与同期一次性买入相比，最大回撤没有改善而收益更低",),
            limitations=(
                "表达的是“已有一笔钱分批买入”；工资式持续追加资金没有建模",
                "一次性买入的对照是 buy_and_hold 基准（期初等权买入）",
            ),
            examples=("每月定投沪深300ETF，一年投完", "把 50 万分 6 个月买入中证500"),
        ),
    )
}

NOT_EXPRESSIBLE = (
    "日内交易、盘中止损止盈与分钟级信号（只有日线）",
    "做空、融资融券杠杆、期货与期权",
    "按新闻、舆情或公告文本触发的规则（没有可回测的历史文本信号）",
    "单只股票的买入价与止损价规则（单标的研究用 exit-plan，不进回测）",
    "季报或分析师预期驱动的因子（选股只用年报）",
    "工资式持续追加资金的定投收益率（分批建仓模板只表达期初资金分批投入）",
)


# One step down and up per parameter for the neighbourhood check (stage 9 slice 6); weights, choices and
# data-availability settings are not perturbed.
NEIGHBOURHOOD_STEPS: dict[str, dict[str, float]] = {
    "ma_timing": {"window": 20},
    "dual_momentum_rotation": {"short_window": 21, "long_window": 42, "trend_window": 20, "volatility_window": 20, "top_n": 1},
    "valuation_percentile_timing": {"lookback_years": 2, "buy_below": 0.05, "sell_above": 0.05},
    "factor_selection": {"min_roe": 2, "max_debt_ratio": 10, "min_profit_growth": 5, "top_n": 3},
    "fixed_weight_rebalance": {},
    "staged_entry": {"periods": 3},
}
_STEP_ALIASES = {"etf_dual_momentum_trend": "dual_momentum_rotation"}


def neighbourhood_steps(strategy_id: str, template_id: str | None) -> dict[str, float]:
    key = template_id or _STEP_ALIASES.get(strategy_id, strategy_id)
    return dict(NEIGHBOURHOOD_STEPS.get(key, {}))


def archive_from_template(
    template_id: str,
    *,
    strategy_id: str,
    universe_assets: Sequence[str] = (),
    parameters: Mapping[str, str] | None = None,
    name: str | None = None,
    version: str = "v1",
) -> StrategyArchive:
    """Build an archive for "template + parameters"; raises TemplateError when the template, universe or values don't fit."""

    template = STRATEGY_TEMPLATES.get(template_id)
    if template is None:
        raise TemplateError(f"没有模板 {template_id}；可用模板：{', '.join(STRATEGY_TEMPLATES)}")
    gate = assess_template(template_id)
    if not gate.implementation_ready:
        raise TemplateError(f"模板 {template_id} 来源门禁未通过：{', '.join(gate.blocking_reasons)}")
    unknown = sorted(set(parameters or {}) - set(template.defaults()))
    if unknown:
        raise TemplateError(f"模板 {template_id} 没有这些参数：{', '.join(unknown)}")
    values = {**template.defaults(), **{key: str(value).strip() for key, value in (parameters or {}).items()}}
    assets = tuple(universe_assets)
    symbols = [item.split(":")[0] for item in assets]
    for spec in template.parameters:
        _check_value(spec, values[spec.name], symbols)
    _check_universe(template, assets, values)
    if template_id == "valuation_percentile_timing" and not 0 <= float(values["buy_below"]) < float(values["sell_above"]) <= 1:
        raise TemplateError("需要 0 ≤ buy_below < sell_above ≤ 1")
    key = values["scope"] if template.universe_rule == "scope" else "+".join(sorted(symbols))
    return StrategyArchive(
        strategy_id=strategy_id,
        version=version,
        name=name or f"{template.name}：{key}",
        kind=template.kind,
        mechanism=template.mechanism.format(**{k: v or "等权" for k, v in values.items()}),
        universe=f"股票池 {values['scope']}" if template.universe_rule == "scope" else "标的：" + "、".join(assets),
        horizons=template.horizons,
        parameters=values,
        source_refs=(template.source_ref,),
        failure_conditions=template.failure_conditions,
        limitations=template.limitations,
        family=f"{template_id}:{key}",
        template_id=template_id,
        universe_assets=assets,
    )


def parse_weights(text: str) -> dict[str, float]:
    """"510300=0.6,511010=0.4" -> {symbol: weight}; empty text -> {} (equal weights)."""

    weights = {}
    for part in filter(None, (item.strip() for item in text.split(","))):
        symbol, _, value = part.partition("=")
        weights[symbol.strip()] = float(value)
    return weights


def _check_value(spec: TemplateParameter, value: str, symbols: Sequence[str]) -> None:
    try:
        if spec.kind == "int" and int(value) <= 0:
            raise TemplateError(f"{spec.name} 必须是正整数")
        if spec.kind == "float":
            float(value)
        if spec.kind == "weights" and value:
            weights = parse_weights(value)
            if set(weights) - set(symbols) or any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
                raise TemplateError(f"{spec.name} 只能给 universe 里的标的非负权重：{value}")
    except ValueError as exc:
        if isinstance(exc, TemplateError):
            raise
        raise TemplateError(f"{spec.name} 的值无法解析：{value}") from exc
    if spec.kind == "choice" and value not in spec.choices:
        raise TemplateError(f"{spec.name} 只能是 {', '.join(spec.choices)}")
    if spec.kind == "scope" and not value.startswith(("sw:", "symbols:")):
        raise TemplateError("scope 需要 sw:<申万代码> 或 symbols:<代码,...>")


def _check_universe(template: StrategyTemplate, assets: Sequence[str], values: Mapping[str, str]) -> None:
    risk = [item for item in assets if not item.endswith(":defensive")]
    if template.universe_rule == "scope":
        if assets:
            raise TemplateError(f"{template.name} 的标的由参数 scope 决定，不接受 --asset")
        return
    if template.universe_rule == "single" and (len(assets) != 1 or len(risk) != 1):
        raise TemplateError(f"{template.name} 需要且只能有一个标的：{template.universe_hint}")
    if template.universe_rule == "set" and len(risk) < 2:
        raise TemplateError(f"{template.name} 至少需要两个风险资产：{template.universe_hint}")
    if template.universe_rule == "any" and not assets:
        raise TemplateError(f"{template.name} 至少需要一个标的：{template.universe_hint}")
    if len(assets) != len(risk) and template.template_id != "dual_momentum_rotation":
        raise TemplateError("只有双动量轮动支持 :defensive 防御资产")
