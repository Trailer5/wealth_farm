"""Recipes for the ten representative questions in 长期愿景."""

from __future__ import annotations

from src.research_contracts import (
    DecisionLane,
    Personalization,
    ResearchDepth,
    ResearchIntent,
    SubjectType,
)

from .models import (
    AvoidCheck,
    OutputElement,
    RecipeDefinition,
    RecipeMatch,
    RecipeStep,
    StepStatus,
    UserContextNeed,
)

A = StepStatus.AVAILABLE
P = StepStatus.PARTIAL
X = StepStatus.PLANNED
ANY_PERSONALIZATION = tuple(Personalization)
NOT_REQUIRED = (Personalization.NONE, Personalization.OPTIONAL)
PERSONAL = (Personalization.REQUIRED,)
ALL_DEPTHS = tuple(ResearchDepth)

ENTRY_PLAN = "cli plan-single / container.request_planner.plan"
ENTRY_BASELINE = "cli run-single / container.pipeline.run"
ENTRY_EXPLORATION = "container.exploration / container.queries"
ENTRY_LEDGER = "container.ledger"
ENTRY_PORTFOLIO = "container.portfolio_risk"
ENTRY_DIAGNOSIS = "run-recipe / cli portfolio-diagnose"
ENTRY_ADJUSTMENT = "run-recipe / cli adjustment-proposal"
ENTRY_DECISION_REVIEW = "run-recipe / cli decision-review"
ENTRY_AGENT_SOURCES = (
    "Agent 外部官方资料核查（.agent/docs/research/exploration_playbook.md）"
)


class RecipeRegistry:
    def __init__(self, recipes: tuple[RecipeDefinition, ...]) -> None:
        by_id = {item.recipe_id: item for item in recipes}
        if len(by_id) != len(recipes):
            raise ValueError("recipe_id must be unique")
        for recipe in recipes:
            unknown = set(recipe.sub_recipes) - set(by_id)
            if unknown:
                raise ValueError(
                    f"{recipe.recipe_id}: unknown sub recipes {sorted(unknown)}"
                )
        self._recipes = by_id

    def list_recipes(self) -> tuple[RecipeDefinition, ...]:
        return tuple(self._recipes.values())

    def get(self, recipe_id: str) -> RecipeDefinition:
        return self._recipes[recipe_id]


def _elements(*items: tuple[str, str]) -> tuple[OutputElement, ...]:
    return tuple(OutputElement(key, text) for key, text in items)


def _avoid(*items: tuple[str, str, str | None]) -> tuple[AvoidCheck, ...]:
    return tuple(AvoidCheck(key, text, enforced) for key, text, enforced in items)


def default_recipes() -> tuple[RecipeDefinition, ...]:
    return (
        RecipeDefinition(
            recipe_id="market_brief",
            question_ids=("Q1",),
            title="市场与宏观简报",
            handles=(
                RecipeMatch(
                    SubjectType.MARKET,
                    (ResearchIntent.UNDERSTAND,),
                    ANY_PERSONALIZATION,
                ),
                RecipeMatch(
                    SubjectType.MACRO,
                    (ResearchIntent.UNDERSTAND,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "market_state",
                    "市场估值、股债性价比、利率、趋势、波动、宽度和两融的状态评估与快照；"
                    "资金流与情绪待建",
                    "R4",
                    P,
                    produces=("state", "recent_change", "confidence"),
                    roadmap_stage=5,
                    entry="cli market-archive / container.market_state",
                ),
                RecipeStep(
                    "macro_state",
                    "宏观增长、通胀、货币与流动性、信用和政策利率状态；政策文本与外部环境待建",
                    "W4",
                    P,
                    produces=("state", "implications"),
                    roadmap_stage=5,
                    entry="cli macro-archive / container.macro_state",
                ),
                RecipeStep(
                    "historical_position",
                    "相对历史的位置与同类历史分布：指数层面的指标分位→前瞻收益分布可用，多条件与行业层面待建",
                    "T3",
                    P,
                    produces=("state",),
                    roadmap_stage=5,
                    entry="cli analog / container.analogs",
                ),
                RecipeStep(
                    "asset_class_implications",
                    "对不同资产类别和时间尺度的含义",
                    "R4",
                    X,
                    produces=("implications", "confidence"),
                    roadmap_stage=5,
                ),
                RecipeStep(
                    "portfolio_overlay",
                    "叠加用户持仓：可读取组合估值与权重，市场状态到组合暴露的映射待建",
                    "T7",
                    P,
                    produces=("portfolio_impact",),
                    roadmap_stage=8,
                    entry=ENTRY_PORTFOLIO,
                    personal_only=True,
                ),
            ),
            output_elements=_elements(
                ("state", "估值、趋势、宽度、资金、情绪、波动和宏观环境的当前状态，以及相对历史的位置。"),
                ("recent_change", "近期变化及其主要来源。"),
                ("implications", "对不同资产类别和时间尺度的含义。"),
                ("confidence", "当前判断的把握度，以及哪些信号出现会改变判断。"),
                ("portfolio_impact", "用户提供持仓时，对其组合的影响。"),
            ),
            avoid_checks=_avoid(
                ("data_dump", "只罗列数据", None),
                ("short_as_long", "把短期波动解释为长期结论", None),
                ("no_cutoff", "不说明数据截止日", "ResearchRequest.as_of_date 与 StateAssessment.as_of_date 必填"),
            ),
            supported_depths=ALL_DEPTHS,
            optional_user_context=(UserContextNeed.POSITIONS,),
        ),
        RecipeDefinition(
            recipe_id="sector_brief",
            question_ids=("Q2",),
            title="行业与主题简报",
            handles=(
                RecipeMatch(
                    SubjectType.SECTOR,
                    (ResearchIntent.UNDERSTAND,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "sector_definition",
                    "领域范围、分类与成分定义：申万行业可用，主题由 Agent 映射为行业组合",
                    "W5",
                    P,
                    produces=("scope",),
                    roadmap_stage=6,
                    entry="run-recipe / cli industry",
                ),
                RecipeStep(
                    "sector_fundamentals",
                    "行业档案：成分股财报汇总的盈利、拥挤度与宽度可用，景气、供需与竞争格局待建",
                    "R4",
                    P,
                    produces=("fundamentals",),
                    roadmap_stage=6,
                    entry="run-recipe / cli sector-archive",
                ),
                RecipeStep(
                    "sector_valuation_position",
                    "行业估值的历史分位与相对大盘强弱（行业档案）",
                    "T3",
                    P,
                    produces=("valuation",),
                    roadmap_stage=6,
                    entry="run-recipe / cli sector-archive",
                ),
                RecipeStep(
                    "sector_flows_policy",
                    "行业资金、政策与周期位置：拥挤度与相对强弱见行业档案，资金流、政策与周期定位待建",
                    "W2",
                    P,
                    produces=("flows_policy_cycle",),
                    roadmap_stage=6,
                    entry="run-recipe / cli sector-archive",
                ),
                RecipeStep(
                    "representative_assets",
                    "代表性标的与差异：Agent 从成分中按市值选取并比较估值位置（不由 run-recipe 产出）",
                    "T3",
                    P,
                    produces=("representatives",),
                    roadmap_stage=6,
                    entry="cli industry --members / cross-section --snapshot --symbols / valuation-position",
                ),
            ),
            output_elements=_elements(
                ("scope", "领域的范围和成分定义。"),
                ("fundamentals", "景气与供需、竞争格局和利润分布。"),
                ("valuation", "估值相对自身历史和市场的位置。"),
                ("flows_policy_cycle", "资金、政策和所处周期位置。"),
                ("representatives", "代表性标的及其差异。"),
            ),
            avoid_checks=_avoid(
                ("index_as_fundamentals", "用行业指数涨跌代替基本面", None),
                ("vague_scope", "领域定义含糊", None),
            ),
            supported_depths=ALL_DEPTHS,
        ),
        RecipeDefinition(
            recipe_id="asset_decision",
            question_ids=("Q3",),
            title="标的评价与决策",
            handles=(
                RecipeMatch(
                    SubjectType.ASSET,
                    (ResearchIntent.EVALUATE, ResearchIntent.DECIDE),
                    ANY_PERSONALIZATION,
                ),
                RecipeMatch(
                    SubjectType.ASSET,
                    (ResearchIntent.EVALUATE,),
                    ANY_PERSONALIZATION,
                ),
                RecipeMatch(
                    SubjectType.ASSET,
                    (ResearchIntent.DECIDE,),
                    NOT_REQUIRED,
                ),
            ),
            steps=_single_asset_steps(holding=False),
            output_elements=_elements(
                ("decision_lanes", "是否值得买入或继续持有、何时行动、买多少；不能回答的轨道给出正式原因。"),
                ("scenarios", "乐观、基准和悲观三情景。"),
                ("priced_expectations", "当前价格隐含的预期。"),
                ("counter_and_review", "最强反方、失效条件和复查节点。"),
            ),
            avoid_checks=_avoid(
                ("no_conclusion", "两面话不给结论", "最终报告要求三条决策轨道各有结论或正式原因"),
                ("fake_position", "缺少用户约束时给伪精确仓位", "缺少投资任务或约束快照时仓位为 user_context_required"),
                ("baseline_as_conclusion", "把基础数据包当作结论", "最终报告复查拒绝以基础研究包冒充最终报告"),
            ),
            supported_depths=ALL_DEPTHS,
            optional_user_context=(
                UserContextNeed.INVESTMENT_MANDATE,
                UserContextNeed.CONSTRAINT_SNAPSHOT,
            ),
        ),
        RecipeDefinition(
            recipe_id="strategy_evaluation",
            question_ids=("Q4",),
            title="策略评估",
            handles=(
                RecipeMatch(
                    SubjectType.STRATEGY,
                    (ResearchIntent.EVALUATE,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "strategy_spec",
                    "策略档案：机制、公开来源、适用范围、参数、失效条件与生命周期状态；用户口述的常见策略对照模板登记",
                    "T5",
                    A,
                    produces=("mechanism", "conditions"),
                    entry="strategy-archive [--strategy-id <id> | --templates | --from-template <模板>]",
                ),
                RecipeStep(
                    "backtest_validation",
                    "完整验证：成本、整手、不可成交、容量、简单基准、扩展窗口滚动样本外与参数邻域",
                    "T6",
                    A,
                    produces=("samples", "costs", "benchmark"),
                    entry="strategy-evaluate --strategy-id <id>（单次回测 strategy-backtest）",
                ),
                RecipeStep(
                    "selection_samples",
                    "选股策略的时点样本：只有当前行业成分，历史成分与退市股行情待补",
                    "D7",
                    P,
                    produces=("samples",),
                    roadmap_stage=10,
                    entry="strategy-evaluate（输出 limitations 写明幸存者偏差）",
                ),
                RecipeStep(
                    "regime_analysis",
                    "沪深300 趋势、波动、估值状态下的表现与样本量",
                    "T6",
                    A,
                    produces=("samples",),
                    entry="strategy-evaluate（regimes）",
                ),
                RecipeStep(
                    "attempt_discount",
                    "尝试次数登记与折扣夏普、最短回测长度",
                    "T6",
                    A,
                    produces=("significance",),
                    entry="strategy-evaluate / strategy-backtest（significance）",
                ),
                RecipeStep(
                    "forward_tracking",
                    "前向跟踪信号、结算记录与启用门槛",
                    "R4",
                    A,
                    produces=("conditions",),
                    entry="strategy-track --strategy-id <id>",
                ),
            ),
            output_elements=_elements(
                ("mechanism", "机制和公开来源。"),
                ("samples", "样本内外表现，以及不同市场状态下的表现。"),
                ("costs", "成本、容量和可执行性。"),
                ("significance", "按尝试次数折扣后的显著性。"),
                ("benchmark", "与简单基准的比较。"),
                ("conditions", "适用条件、失效条件和前向跟踪记录。"),
            ),
            avoid_checks=_avoid(
                ("curve_only", "只给回测曲线", None),
                ("single_sample", "用单段样本宣称有效", None),
                ("hidden_attempts", "隐瞒参数尝试次数", None),
            ),
            supported_depths=ALL_DEPTHS,
        ),
        RecipeDefinition(
            recipe_id="portfolio_rebalance",
            question_ids=("Q5",),
            title="组合改造",
            handles=(
                RecipeMatch(
                    SubjectType.PORTFOLIO,
                    (ResearchIntent.EVALUATE, ResearchIntent.PLAN),
                    PERSONAL,
                ),
            ),
            steps=(
                RecipeStep(
                    "confirm_preference",
                    "确认“激进”的含义并记录为新版本偏好：只把确认的维度升一档，列出变化项",
                    "P2",
                    A,
                    produces=("meaning",),
                    entry="cli record-preference / container.portfolio_risk",
                ),
                RecipeStep(
                    "portfolio_valuation",
                    "组合估值、权重与集中度",
                    "P2",
                    A,
                    produces=("diagnosis",),
                    entry=ENTRY_PORTFOLIO,
                ),
                RecipeStep(
                    "portfolio_diagnosis",
                    "大类与行业暴露（指数基金与 ETF 按跟踪指数成分、主动基金按季报前十大穿透）、重叠、相关性、风险贡献与当前组合的历史压力情景",
                    "T7",
                    A,
                    produces=("diagnosis",),
                    entry=ENTRY_DIAGNOSIS,
                ),
                RecipeStep(
                    "risk_budget",
                    "目标权益区间（偏好、目标波动、历史最差情景与现金底线取最严）、大类风险预算与权益内部的风险均衡参照",
                    "T7",
                    A,
                    produces=("risk_budget",),
                    entry="run-recipe / cli allocation-proposal",
                ),
                RecipeStep(
                    "adjustment_plan",
                    "换入换出与比例区间：个股与行业超限先减，权益拉回目标区间，增加部分只给点名候选、已持有基金或默认宽基 ETF",
                    "R6",
                    A,
                    produces=("adjustment",),
                    entry=ENTRY_ADJUSTMENT,
                ),
                RecipeStep(
                    "migration_path",
                    "分步迁移路径、时点条件与交易成本（场外基金避开短期赎回费）",
                    "R6",
                    A,
                    produces=("migration",),
                    entry=ENTRY_ADJUSTMENT,
                ),
                RecipeStep(
                    "stress_test",
                    "调整前后组合在五段历史情景中的损失对比",
                    "T7",
                    A,
                    produces=("stress",),
                    entry=ENTRY_ADJUSTMENT,
                ),
            ),
            output_elements=_elements(
                ("meaning", "先确认“激进”的具体含义：更高权益比例、更集中、更高波动或更长期限。"),
                ("diagnosis", "当前组合诊断：暴露、重叠、相关性和风险来源。"),
                ("risk_budget", "调整后的风险预算。"),
                ("adjustment", "调整方案：换入换出和比例区间。"),
                ("migration", "分步迁移路径和时点条件。"),
                ("stress", "调整后的压力情景损失。"),
            ),
            avoid_checks=_avoid(
                ("unconfirmed_meaning", "不确认含义就加仓高波动标的", "请求未引用该用户已记录且带激进含义的偏好版本时路由状态为 needs_user_context"),
                ("ignored_overlap", "忽略已有持仓的重叠暴露", None),
            ),
            supported_depths=(ResearchDepth.STANDARD, ResearchDepth.DEEP),
            required_user_context=(
                UserContextNeed.POSITIONS,
                UserContextNeed.AGGRESSIVENESS_MEANING,
            ),
        ),
        RecipeDefinition(
            recipe_id="portfolio_review",
            question_ids=(),
            title="组合诊断",
            handles=(
                RecipeMatch(
                    SubjectType.PORTFOLIO,
                    (ResearchIntent.EVALUATE,),
                    PERSONAL,
                ),
            ),
            steps=(
                RecipeStep(
                    "portfolio_valuation",
                    "组合估值、权重与集中度",
                    "P2",
                    A,
                    produces=("exposure",),
                    entry=ENTRY_PORTFOLIO,
                ),
                RecipeStep(
                    "portfolio_diagnosis",
                    "大类与行业暴露、重叠、相关性、风险贡献、历史压力情景与偏好对照",
                    "T7",
                    A,
                    produces=("exposure", "risk_sources", "stress"),
                    entry=ENTRY_DIAGNOSIS,
                ),
            ),
            output_elements=_elements(
                ("exposure", "大类、行业与重叠暴露。"),
                ("risk_sources", "风险主要来自哪些持仓：波动、相关性与风险贡献。"),
                ("stress", "历史压力情景下的组合损失。"),
            ),
            avoid_checks=_avoid(
                ("weight_as_risk", "把持仓权重当成风险占比", "风险贡献按协方差分解，与权重分列"),
                ("ignored_overlap", "忽略基金与直接持股的重叠", None),
            ),
            supported_depths=ALL_DEPTHS,
            required_user_context=(UserContextNeed.POSITIONS,),
            optional_user_context=(UserContextNeed.INVESTMENT_PREFERENCE,),
        ),
        RecipeDefinition(
            recipe_id="decision_review",
            question_ids=(),
            title="决策复盘与偏好校准",
            handles=(
                RecipeMatch(
                    SubjectType.PORTFOLIO,
                    (ResearchIntent.REVIEW,),
                    PERSONAL,
                ),
            ),
            steps=(
                RecipeStep(
                    "track_record",
                    "账本中已结算的观点与动作：命中率与 90% 区间、择时贡献，按来源、期限、方向和标的分组",
                    "R5",
                    A,
                    produces=("track_record",),
                    entry=ENTRY_DECISION_REVIEW,
                ),
                RecipeStep(
                    "plan_review",
                    "已入账计划的到期复盘节点；有交易流水时核对计划动作是否执行、哪些交易在计划之外",
                    "R6",
                    A,
                    produces=("plan_review",),
                    entry=ENTRY_DECISION_REVIEW,
                ),
                RecipeStep(
                    "preference_calibration",
                    "自述可接受回撤与目标波动对照回放、压力情景与按流水重建的实际回撤和波动，以及流水中的行为提示；不自动修改偏好",
                    "P2",
                    A,
                    produces=("calibration",),
                    entry=ENTRY_DECISION_REVIEW,
                ),
            ),
            output_elements=_elements(
                ("track_record", "判断的命中率、择时贡献与分组表现。"),
                ("plan_review", "计划复盘节点与执行情况。"),
                ("calibration", "偏好与实际表现、行为是否一致，以及可选的处理。"),
            ),
            avoid_checks=_avoid(
                ("small_sample", "用几条结算下结论", "命中率附 90% 区间，少于 5 条的分组标记样本少"),
                ("outcome_as_process", "只看结果好坏评判决策过程", None),
                ("auto_preference_change", "替用户修改偏好", "校准只给提示，偏好变更须经 record-preference 记录用户原话"),
            ),
            supported_depths=ALL_DEPTHS,
            optional_user_context=(
                UserContextNeed.POSITIONS,
                UserContextNeed.INVESTMENT_PREFERENCE,
            ),
        ),
        RecipeDefinition(
            recipe_id="sector_discovery",
            question_ids=("Q6",),
            title="领域内标的发现",
            handles=(
                RecipeMatch(
                    SubjectType.SECTOR,
                    (ResearchIntent.DISCOVER,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "research_scope",
                    "研究范围：申万行业、指数当前成分、全市场或 Agent 指定的标的清单；主题由 Agent 映射",
                    "W1",
                    P,
                    produces=("scope",),
                    roadmap_stage=6,
                    entry="run-recipe / cli discover --scope",
                ),
                RecipeStep(
                    "strategy_funnel",
                    "多策略截面漏斗：四条首批策略的入选与淘汰理由、反证、数据缺口和规则历史表现"
                    "（范围 ≤300 只才有依赖历史的策略与规则历史）",
                    "R3",
                    P,
                    produces=("rules", "candidates"),
                    roadmap_stage=6,
                    entry="run-recipe / cli discover",
                ),
                RecipeStep(
                    "event_name_discovery",
                    "事件名称注意力发现（专项，需要事件与关系证据输入）",
                    "R3",
                    A,
                    produces=("candidates",),
                    entry="cli run-event-name-discovery",
                ),
                RecipeStep(
                    "multi_strategy_priority",
                    "候选优先级（命中策略数与策略内排名）与前 N 名自动基础包；跨策略的组合与权重待建",
                    "R3",
                    P,
                    produces=("priority",),
                    roadmap_stage=6,
                    entry="run-recipe / cli discover --base-packs",
                ),
                RecipeStep(
                    "deep_dive_handoff",
                    "入选候选转入单标的研究",
                    "O1",
                    A,
                    produces=("priority",),
                    entry=ENTRY_PLAN,
                ),
            ),
            output_elements=_elements(
                ("scope", "研究范围。"),
                ("rules", "筛选规则及其依据。"),
                ("candidates", "候选名单、入选理由、主要淘汰理由和数据缺口。"),
                ("priority", "候选优先级，以及建议进入深度研究的对象。"),
            ),
            avoid_checks=_avoid(
                ("list_only", "只给名单", "发现结果保存入选理由、淘汰理由和数据缺口"),
                ("momentum_ranking", "用近期涨幅排序代替研究", None),
                ("candidate_as_buy", "把候选说成买入结论", "候选只生成研究任务草稿，买入结论须经单标的最终报告"),
            ),
            supported_depths=ALL_DEPTHS,
        ),
        RecipeDefinition(
            recipe_id="view_test",
            question_ids=("Q7",),
            title="宏观与市场观点检验",
            handles=(
                RecipeMatch(
                    SubjectType.MACRO,
                    (ResearchIntent.TEST_VIEW,),
                    ANY_PERSONALIZATION,
                ),
                RecipeMatch(
                    SubjectType.MARKET,
                    (ResearchIntent.TEST_VIEW,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "record_user_view",
                    "用户观点以用户来源、个人可见入账；改写成可证伪命题后用 record-view 入账按指标或价格结算的版本",
                    "R5",
                    A,
                    produces=("proposition",),
                    entry=ENTRY_LEDGER,
                ),
                RecipeStep(
                    "counter_evidence",
                    "正反证据收集：档案维度可作结构化证据，政策与新闻由 Agent 标注来源与发布日期",
                    "O3",
                    P,
                    produces=("evidence",),
                    roadmap_stage=5,
                    entry=ENTRY_AGENT_SOURCES,
                    counter_evidence=True,
                ),
                RecipeStep(
                    "leading_indicators",
                    "领先指标与宏观数据：run-recipe 生成宏观（市场观点为市场）档案，观点专用指标组合待建",
                    "W4",
                    P,
                    produces=("indicators",),
                    roadmap_stage=5,
                    entry="run-recipe / cli macro / macro-archive",
                ),
                RecipeStep(
                    "base_rate",
                    "历史类比与同类历史的发生比例：市场指标分位→沪深300 前瞻收益分布可用，观点专用事件定义待建",
                    "T3",
                    P,
                    produces=("indicators", "system_view"),
                    roadmap_stage=5,
                    entry="cli analog",
                ),
                RecipeStep(
                    "system_view",
                    "系统自己的概率区间、时间窗口和会改变判断的信号：Agent 基于档案与同类历史给出，"
                    "用 record-view 以系统观点入账并按指标结算；概率模型待建",
                    "R4",
                    P,
                    produces=("system_view", "signals"),
                    roadmap_stage=5,
                    entry="cli record-view",
                ),
                RecipeStep(
                    "portfolio_impact",
                    "命题成立与否对用户组合的影响（可读取持仓，情景映射待建）",
                    "T7",
                    P,
                    produces=("portfolio_impact",),
                    roadmap_stage=8,
                    entry=ENTRY_PORTFOLIO,
                    personal_only=True,
                ),
            ),
            output_elements=_elements(
                ("proposition", "把用户观点改写为可证伪命题：什么程度、什么时间窗口。"),
                ("indicators", "领先指标、历史类比和同类历史的发生比例。"),
                ("evidence", "支持和反对的证据。"),
                ("system_view", "系统自己的概率区间和时间窗口。"),
                ("portfolio_impact", "命题成立与不成立时对用户组合的影响，以及可选应对。"),
                ("signals", "会改变判断的信号。"),
            ),
            avoid_checks=_avoid(
                ("confirmation_bias", "顺着用户观点找证据", "配方含反方证据步骤；用户观点只以待检验的用户来源观点入账"),
                ("exact_timing", "给出精确时点", "契约只接受概率区间与跨多日的时间窗口"),
                ("story_only", "只讲叙事不给证据", None),
            ),
            supported_depths=ALL_DEPTHS,
            optional_user_context=(UserContextNeed.POSITIONS,),
        ),
        RecipeDefinition(
            recipe_id="holding_review",
            question_ids=("Q8",),
            title="持仓复核",
            handles=(
                RecipeMatch(
                    SubjectType.ASSET,
                    (ResearchIntent.DECIDE,),
                    PERSONAL,
                ),
            ),
            steps=_single_asset_steps(holding=True),
            output_elements=_elements(
                ("original_thesis", "原买入理由是否仍然成立。"),
                ("rebuy_test", "以“今天手持现金是否会买”重新评估。"),
                ("opportunity_cost", "机会成本和同风险替代。"),
                ("risk_share", "该持仓在组合中的风险占比。"),
                ("behavioral_bias", "沉没成本、处置效应等行为偏差提示。"),
                ("action", "持有、加仓、减仓、退出或换仓的建议及触发条件。"),
            ),
            avoid_checks=_avoid(
                ("cost_anchor", "以成本价为锚", None),
                ("comforting", "安慰式回答", None),
                ("averaging_down", "为摊低成本而加仓", None),
            ),
            supported_depths=(ResearchDepth.STANDARD, ResearchDepth.DEEP),
            required_user_context=(UserContextNeed.POSITIONS,),
            optional_user_context=(
                UserContextNeed.ORIGINAL_THESIS,
                UserContextNeed.INVESTMENT_MANDATE,
                UserContextNeed.CONSTRAINT_SNAPSHOT,
            ),
        ),
        RecipeDefinition(
            recipe_id="sector_view_recommendation",
            question_ids=("Q9",),
            title="行业观点检验与推荐",
            handles=(
                RecipeMatch(
                    SubjectType.SECTOR,
                    (ResearchIntent.TEST_VIEW, ResearchIntent.DISCOVER),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "record_user_view",
                    "用户的行业观点以用户来源、个人可见入账；改写为行业指数相对沪深300 的命题后用 record-view 入账可结算版本",
                    "R5",
                    A,
                    produces=("view_test",),
                    entry=ENTRY_LEDGER,
                ),
                RecipeStep(
                    "counter_evidence",
                    "行业观点的正反证据：行业档案维度可作结构化证据，政策与新闻由 Agent 标注来源与发布日期",
                    "O3",
                    P,
                    produces=("view_test",),
                    roadmap_stage=6,
                    entry=ENTRY_AGENT_SOURCES,
                    counter_evidence=True,
                ),
                RecipeStep(
                    "sector_brief",
                    "行业现状：主题映射到的每个申万行业生成行业档案并入账，附可结算观点模板；景气、资金与政策待建",
                    "R4",
                    P,
                    produces=("sector",),
                    roadmap_stage=6,
                    entry="run-recipe / cli sector-archive",
                ),
                RecipeStep(
                    "sector_discovery",
                    "行业内候选发现：在映射行业的并集上运行多策略漏斗",
                    "R3",
                    P,
                    produces=("candidates",),
                    roadmap_stage=6,
                    entry="run-recipe / cli discover",
                ),
                RecipeStep(
                    "candidate_decisions",
                    "前 N 名候选自动生成基础包；对用户点名或已有候选的标的做单标的研究与决策",
                    "O1",
                    A,
                    produces=("candidates", "fit"),
                    entry=ENTRY_PLAN,
                ),
                RecipeStep(
                    "portfolio_fit",
                    "组合层适配与仓位区间：把已研究的候选传给 adjustment-proposal --candidate，得到加入后的目标区间、行业与重叠变化；执行器尚未自动串接候选",
                    "R6",
                    P,
                    produces=("fit",),
                    roadmap_stage=8,
                    entry="cli adjustment-proposal --candidate",
                ),
            ),
            output_elements=_elements(
                ("view_test", "对用户观点的正反检验。"),
                ("sector", "行业现状分析。"),
                ("candidates", "行业内候选及理由。"),
                ("fit", "与用户组合的适配和仓位区间。"),
            ),
            avoid_checks=_avoid(
                ("support_only", "只找支持证据", "配方含反方证据步骤"),
                ("constraint_conflict", "推荐与用户约束冲突的标的", None),
            ),
            supported_depths=(ResearchDepth.STANDARD, ResearchDepth.DEEP),
            optional_user_context=(
                UserContextNeed.POSITIONS,
                UserContextNeed.INVESTMENT_PREFERENCE,
            ),
            sub_recipes=(
                "sector_brief",
                "sector_discovery",
                "asset_decision",
                "portfolio_rebalance",
            ),
        ),
        RecipeDefinition(
            recipe_id="plan_schedule",
            question_ids=("Q10",),
            title="计划与时间表",
            handles=(
                RecipeMatch(
                    SubjectType.PORTFOLIO,
                    (ResearchIntent.PLAN,),
                    PERSONAL,
                ),
            ),
            steps=(
                RecipeStep(
                    "ledger_actions",
                    "汇总该用户账本中有效的可结算动作（定期与条件触发、数量规则）",
                    "R5",
                    A,
                    produces=("nodes", "node_actions"),
                    entry=ENTRY_LEDGER,
                ),
                RecipeStep(
                    "review_nodes",
                    "已发布报告的复查日期与已确认的监测规则",
                    "P3",
                    A,
                    produces=("review",),
                    entry="container.monitoring / container.alerts",
                ),
                RecipeStep(
                    "plan_object",
                    "计划对象：迁移步骤与账本动作的定期/条件节点、数量规则、目标区间与概率、复盘节点与改进方法、冲突处理；用户确认后入账（需要偏好）",
                    "R6",
                    A,
                    produces=("nodes", "node_actions", "targets", "review", "conflicts"),
                    entry="run-recipe / cli portfolio-plan",
                ),
                RecipeStep(
                    "html_timeline",
                    "与结构化计划同源的离线 HTML 时间表与看板；生成后逐项核对，不一致不发布",
                    "T8",
                    A,
                    produces=("html",),
                    entry="run-recipe / cli portfolio-plan",
                ),
            ),
            output_elements=_elements(
                ("nodes", "每个标的的定期节点和条件节点。"),
                ("node_actions", "每个节点的动作和数量规则。"),
                ("targets", "目标用区间和概率表达，并注明依据。"),
                ("review", "复盘节点和改进方法。"),
                ("conflicts", "组合层冲突的处理，例如现金不足和动作优先级。"),
                ("html", "可离线打开的 HTML 文件，内容与结构化计划同源。"),
            ),
            avoid_checks=_avoid(
                ("exact_targets", "承诺精确日期和点位", "TargetRange 只接受数值区间与跨多日窗口"),
                ("constraint_conflict", "与用户约束冲突的动作", None),
                ("html_mismatch", "HTML 与分析结论不一致", None),
            ),
            supported_depths=(ResearchDepth.STANDARD, ResearchDepth.DEEP),
            required_user_context=(UserContextNeed.POSITIONS,),
            optional_user_context=(UserContextNeed.INVESTMENT_PREFERENCE,),
        ),
        RecipeDefinition(
            recipe_id="asset_understanding",
            question_ids=(),
            title="标的理解",
            handles=(
                RecipeMatch(
                    SubjectType.ASSET,
                    (ResearchIntent.UNDERSTAND,),
                    ANY_PERSONALIZATION,
                ),
            ),
            steps=(
                RecipeStep(
                    "identity_and_plan",
                    "身份解析、profile 与基础任务草稿",
                    "O1",
                    A,
                    produces=("facts",),
                    entry=ENTRY_PLAN,
                ),
                RecipeStep(
                    "baseline",
                    "基础研究包：数据、指标、图表、比较和证据",
                    "R1",
                    A,
                    produces=("facts", "questions"),
                    entry=ENTRY_BASELINE,
                ),
                RecipeStep(
                    "exploration",
                    "围绕候选问题的探索与查询",
                    "O1",
                    A,
                    produces=("questions",),
                    entry=ENTRY_EXPLORATION,
                ),
            ),
            output_elements=_elements(
                ("facts", "标的身份与带截止日的基础事实。"),
                ("questions", "值得继续研究的关键问题与假设。"),
            ),
            avoid_checks=_avoid(
                ("baseline_as_conclusion", "把基础数据包当作结论", "基础研究包不含买卖结论"),
            ),
            supported_depths=ALL_DEPTHS,
        ),
    )


def _single_asset_steps(*, holding: bool) -> tuple[RecipeStep, ...]:
    lanes = (
        (
            "original_thesis",
            "rebuy_test",
            "opportunity_cost",
        )
        if holding
        else ("decision_lanes",)
    )
    timing = ("action",) if holding else ("decision_lanes",)
    position = ("risk_share", "action") if holding else ("decision_lanes",)
    report = ("action",) if holding else ("scenarios", "counter_and_review")
    steps = [
        RecipeStep(
            "identity_and_plan",
            "身份解析、profile 与基础任务草稿"
            + ("（复核持仓意图）" if holding else ""),
            "O1",
            A,
            entry=ENTRY_PLAN,
        ),
        RecipeStep(
            "baseline",
            "基础研究包",
            "R1",
            A,
            entry=ENTRY_BASELINE,
        ),
        RecipeStep(
            "exploration",
            "Agent 多轮探索：支持证据与反方证据",
            "O1",
            A,
            produces=report if not holding else ("original_thesis",),
            entry=ENTRY_EXPLORATION,
            counter_evidence=True,
        ),
        RecipeStep(
            "suitability",
            "适配评估" + ("（hold_review）" if holding else ""),
            "R2",
            A,
            produces=lanes,
            entry="container.suitability.assemble",
            decision_lane=DecisionLane.SUITABILITY,
        ),
        RecipeStep(
            "timing",
            "时点计划",
            "R2",
            A,
            produces=timing,
            entry="container.entry.assemble",
            decision_lane=DecisionLane.TIMING,
        ),
        RecipeStep(
            "position",
            "仓位计划（需要投资任务与约束快照）",
            "R2",
            A,
            produces=position,
            entry="container.position.assemble",
            decision_lane=DecisionLane.POSITION_SIZING,
        ),
        RecipeStep(
            "final_report",
            "最终决策报告：三情景、最强反方、失效条件与复查节点",
            "R2",
            A,
            produces=report,
            entry="container.final_decision.assemble_and_publish",
        ),
        RecipeStep(
            "ledger_record",
            "发布即入账，到期前向结算",
            "R5",
            A,
            entry=ENTRY_LEDGER,
        ),
    ]
    if holding:
        steps += [
            RecipeStep(
                "thesis_check",
                "原论点对照：账本中的论点指标按新披露财报判为符合、偏离、失效或待数据；没有论点时按用户口述重建",
                "R5",
                A,
                produces=("original_thesis",),
                entry="run-recipe / cli thesis-check / record-thesis",
            ),
            RecipeStep(
                "exit_plan",
                "退出计划：论点、估值透支、风险预算、机会成本、时间止损五个维度的卖出条件并列给出，已触发与已设定的条件入账；继续持有的最终报告必须带退出条件",
                "R2",
                A,
                produces=("action", "opportunity_cost", "risk_share"),
                entry="run-recipe / cli exit-plan / final_decision.assemble_and_publish(exit_plan=)",
            ),
            RecipeStep(
                "behavioral_review",
                "行为偏差的规则型提示（成本锚定、摊低成本加仓、论点失效仍持有、盈利过早卖出）；偏好模型与行为记录待建",
                "P1",
                P,
                produces=("behavioral_bias",),
                roadmap_stage=8,
                entry="run-recipe / cli exit-plan --intended-action",
            ),
        ]
    else:
        steps.append(
            RecipeStep(
                "thesis_record",
                "买入或继续持有的结论发布后，把论点写成 3–5 个可观察的财务指标与预期区间并入账，按后续财报结算",
                "R5",
                A,
                produces=("counter_and_review",),
                entry="cli record-thesis",
            )
        )
        steps.append(
            RecipeStep(
                "implied_expectations",
                "当前价格隐含的预期：反向 DCF（银行用 PB-ROE、周期用正常化利润、高成长未盈利用营收口径）、三情景与敏感性，不是目标价；"
                "基金不估内在价值，场外基金看基础包“底层估值位置”，ETF 看标的指数估值",
                "T2",
                A,
                produces=("priced_expectations",),
                entry="cli valuation-model / 基础研究包“隐含预期与内在价值”一节",
            )
        )
    return tuple(steps)


def default_recipe_registry() -> RecipeRegistry:
    return RecipeRegistry(default_recipes())
