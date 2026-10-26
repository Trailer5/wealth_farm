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
                    "领域范围、分类与成分定义",
                    "W5",
                    X,
                    produces=("scope",),
                    roadmap_stage=6,
                ),
                RecipeStep(
                    "sector_fundamentals",
                    "景气、供需、竞争格局与利润分布的行业档案",
                    "R4",
                    X,
                    produces=("fundamentals",),
                    roadmap_stage=6,
                ),
                RecipeStep(
                    "sector_valuation_position",
                    "行业估值的历史分位与相对市场位置",
                    "T3",
                    X,
                    produces=("valuation",),
                    roadmap_stage=6,
                ),
                RecipeStep(
                    "sector_flows_policy",
                    "行业资金、政策与周期位置（市场状态服务尚未接入正式入口）",
                    "W2",
                    X,
                    produces=("flows_policy_cycle",),
                    roadmap_stage=5,
                ),
                RecipeStep(
                    "representative_assets",
                    "从具体标的出发的行业归属与同行比较",
                    "T3",
                    P,
                    produces=("representatives",),
                    roadmap_stage=6,
                    entry=ENTRY_EXPLORATION,
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
                    "已登记策略的规格、机制和公开来源",
                    "T5",
                    A,
                    produces=("mechanism", "conditions"),
                    entry="src.strategy.default_strategy_registry；docs/evidence/strategy_sources/",
                ),
                RecipeStep(
                    "backtest_validation",
                    "样本内外回测、成本与不可成交（回测引擎未接入正式入口）",
                    "T6",
                    X,
                    produces=("samples", "costs", "benchmark"),
                    roadmap_stage=9,
                ),
                RecipeStep(
                    "regime_analysis",
                    "不同市场状态下的表现",
                    "R4",
                    X,
                    produces=("samples",),
                    roadmap_stage=9,
                ),
                RecipeStep(
                    "attempt_discount",
                    "尝试次数登记与显著性折扣",
                    "T6",
                    X,
                    produces=("significance",),
                    roadmap_stage=9,
                ),
                RecipeStep(
                    "forward_tracking",
                    "策略生命周期与前向跟踪记录",
                    "R4",
                    X,
                    produces=("conditions",),
                    roadmap_stage=9,
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
                    "确认“激进”的含义并落到投资任务（任务已有，偏好确认流程待建）",
                    "P2",
                    P,
                    produces=("meaning",),
                    roadmap_stage=8,
                    entry=ENTRY_PORTFOLIO,
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
                    "暴露、重叠、相关性与风险来源诊断",
                    "T7",
                    X,
                    produces=("diagnosis",),
                    roadmap_stage=8,
                ),
                RecipeStep(
                    "risk_budget",
                    "调整后的风险预算分配",
                    "T7",
                    X,
                    produces=("risk_budget",),
                    roadmap_stage=8,
                ),
                RecipeStep(
                    "adjustment_plan",
                    "换入换出方案与比例区间",
                    "R6",
                    X,
                    produces=("adjustment",),
                    roadmap_stage=8,
                ),
                RecipeStep(
                    "migration_path",
                    "分步迁移路径与时点条件",
                    "R6",
                    X,
                    produces=("migration",),
                    roadmap_stage=8,
                ),
                RecipeStep(
                    "stress_test",
                    "调整后组合的压力情景损失（目前只有单标的压力损失）",
                    "T7",
                    X,
                    produces=("stress",),
                    roadmap_stage=8,
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
                ("unconfirmed_meaning", "不确认含义就加仓高波动标的", "未提供激进含义时路由状态为 needs_user_context"),
                ("ignored_overlap", "忽略已有持仓的重叠暴露", None),
            ),
            supported_depths=(ResearchDepth.STANDARD, ResearchDepth.DEEP),
            required_user_context=(
                UserContextNeed.POSITIONS,
                UserContextNeed.AGGRESSIVENESS_MEANING,
                UserContextNeed.INVESTMENT_MANDATE,
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
                    "研究范围：指数成分研究宇宙可用，行业/主题范围待建",
                    "W1",
                    P,
                    produces=("scope",),
                    roadmap_stage=6,
                    entry="src.research_universe",
                ),
                RecipeStep(
                    "quality_valuation_funnel",
                    "质量—估值—相对表现分级漏斗（没有默认容器或 CLI 入口）",
                    "R3",
                    X,
                    produces=("rules", "candidates"),
                    roadmap_stage=6,
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
                    "多策略分级与候选优先级",
                    "R3",
                    X,
                    produces=("priority",),
                    roadmap_stage=6,
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
                    "用户的行业观点以用户来源、个人可见入账",
                    "R5",
                    A,
                    produces=("view_test",),
                    entry=ENTRY_LEDGER,
                ),
                RecipeStep(
                    "counter_evidence",
                    "行业观点的正反证据：可用标注来源的官方资料，行业档案待建",
                    "O3",
                    P,
                    produces=("view_test",),
                    roadmap_stage=6,
                    entry=ENTRY_AGENT_SOURCES,
                    counter_evidence=True,
                ),
                RecipeStep(
                    "sector_brief",
                    "行业现状（见行业简报配方）",
                    "R4",
                    X,
                    produces=("sector",),
                    roadmap_stage=6,
                ),
                RecipeStep(
                    "sector_discovery",
                    "行业内候选发现（见领域发现配方）",
                    "R3",
                    X,
                    produces=("candidates",),
                    roadmap_stage=6,
                ),
                RecipeStep(
                    "candidate_decisions",
                    "对用户点名或已有候选的标的做单标的研究与决策",
                    "O1",
                    A,
                    produces=("candidates", "fit"),
                    entry=ENTRY_PLAN,
                ),
                RecipeStep(
                    "portfolio_fit",
                    "组合层适配与仓位区间（单标的仓位轨道可用，组合层待建）",
                    "R6",
                    X,
                    produces=("fit",),
                    roadmap_stage=8,
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
                UserContextNeed.INVESTMENT_MANDATE,
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
                    "计划对象：目标区间、组合层冲突处理与改进方法",
                    "R6",
                    X,
                    produces=("nodes", "node_actions", "targets", "review", "conflicts"),
                    roadmap_stage=8,
                ),
                RecipeStep(
                    "html_timeline",
                    "与结构化计划同源的离线 HTML 时间表",
                    "T8",
                    X,
                    produces=("html",),
                    roadmap_stage=8,
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
            optional_user_context=(UserContextNeed.INVESTMENT_MANDATE,),
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
                "exit_plan",
                "退出计划与卖出侧触发条件",
                "R2",
                X,
                produces=("action",),
                roadmap_stage=7,
            ),
            RecipeStep(
                "behavioral_review",
                "沉没成本、处置效应等行为偏差的结构化提示",
                "P1",
                X,
                produces=("behavioral_bias",),
                roadmap_stage=8,
            ),
        ]
    else:
        steps.append(
            RecipeStep(
                "implied_expectations",
                "当前价格隐含的预期：适配评估中的定性判断可用，反向 DCF 等定量方法待建",
                "T2",
                P,
                produces=("priced_expectations",),
                roadmap_stage=7,
                entry="container.suitability.assemble",
            )
        )
    return tuple(steps)


def default_recipe_registry() -> RecipeRegistry:
    return RecipeRegistry(default_recipes())
