# 当前研究能力地图

## 用途

本文件解释如何读取能力，不复制实现清单。机器可查询事实以默认容器中的 `ResearchCapabilityRegistry` 为准；模块职责、公共入口和工程边界以 `docs/modules/README.md` 及所属模块文档为准。

本文覆盖标的类研究能力。所有问题先经 `QuestionRouter` 路由；市场、行业、宏观、策略、组合和计划类问题的当前可执行部分与缺口以路由结果为准，用法见 `question_routing.md`。

## 权威来源

1. 调用 `container.capability_registry.resolve(profile_id=..., question_type=..., decision_lane=...)` 查询当前组合。
2. `automated_capabilities` 是确定性流水线已声明的能力，不表示本次运行一定取得数据。
3. `agent_query_capabilities` 是基础包后可安排的探索动作，不表示已经完成。
4. `required_user_context` 只表示该决策轨道需要哪些用户上下文。
5. `unsupported_capabilities` 和 `question_supported=false` 必须作为能力缺口保留，不能由 prompt 假装完成；`lane_unsupported_capabilities` 非空表示当前 profile 不支持所查询的决策轨道。
6. `public_entrypoints` 由一致性测试校验；入口职责见 `docs/modules/6-编排/研究编排模块.md`，profile 细节见对应模块文档。

注册表声明的是当前五类正式 execution profile 的最小能力边界，不是完整未来清单。不得用其他 profile 的指标补齐当前 profile 的必需缺口。

## 基础研究与 handoff

统一入口和 CLI 用法见 `docs/modules/6-编排/研究编排模块.md`。运行状态为 `complete`、`partial` 或 `blocked`；基础包不包含最终买入建议。

默认流水线为 complete/partial 创建 `ResearchWorkflowRun` 和 `BaselineResearchHandoff`。handoff 分开保存：

- 已有事实章节；
- 每个确定性执行阶段的状态与摘要；
- 非阻塞执行/验收缺口；
- 阻塞缺口；
- 注册表声明的能力缺口；
- 可供 Agent 选择而非机械执行的候选问题和假设。

blocked 运行只保存 workflow 阻塞状态，不创建 handoff 或探索会话。Agent 不得绕过这一门禁。

### 探索

`PersistentResearchExplorationService` 已能从 handoff 建立用户隔离的探索会话，记录结构化问题、假设、查询、发现、轮次和追加式账本，并在重启后通过 workflow run 恢复。探索必须先判断 partial 与 capability gaps 对各决策轨道的影响。

当前限制：

- 基础运行会生成 handoff，但探索会话仍需通过 `container.exploration.start_from_handoff(...)` 显式创建；
- 公共查询通过 `container.queries.execute(context, session_id=..., plan=ResearchQueryPlan(...))` 显式执行；计划必须绑定开放问题、声明能力、预期证据、完成条件和截止日；
- 数据、文档、基准、事件、同行、用户状态和组合查询只走已注册公共 service；外部官方网页只登记 Agent 已阅读来源，当前没有通用网页抓取；
- 不会从 handoff 缺口静默执行固定查询清单；Agent 仍按决策影响和获取成本创建计划；
- `mark_ready` 已检查阻塞问题、决策轨道、替代方案、证伪条件、冲突和最强反方；仓位输入是否足够再由决策简报、持久化约束和 `PositionPlanService` 正式门禁；
- 适配轨道可通过 `container.suitability.assemble(session=..., decision_brief=...)` 装配 `SuitabilityAssessment`；Agent 必须先在结构化发现中标注收益来源、定价预期、下行、同风险替代方案、机会成本和 profile 输入类型，装配器不从自然语言猜测分类；
- consider 只接受探索轮次已入账的事实/推断证据，并要求替代方案与机会成本对应同一标的和同一可比暴露；时点、仓位与最终报告不在该对象中；
- 时点轨道可通过 `container.entry.assemble(session=..., suitability=..., decision_brief=...)` 装配 `EntryPlan`；Agent 必须先记录 timing dimension、输入类型、条件状态、执行周期、有效窗口、可观察键、交易日历和执行限制；
- act/stage 只接受执行周期且位于全部条件交集有效窗口内的证据。短期与长期冲突显式保留并返回 wait；场外基金 timing execution 仍是 profile capability gap；
- `container.final_decision.assemble_and_publish(...)` 已提供最终决策报告专用装配、十章节白名单渲染、结构/Markdown 双重复查和静态发布门禁；输入必须是 ready 会话、已发布基础包、决策简报、三个决策对象及显式 `FinalReportNarrativeInput`。
- `container.monitoring.prepare(...)` 重新加载最终报告服务持久化的发布证明，复核 workflow 用户、规范化监测计划、报告路径和 Markdown digest 后才生成待确认 handoff；`container.monitoring.confirm_and_activate(...)` 只注册用户明确选择的价格、事件、日期或组合权重规则。规则、触发提醒和 workflow run 均保留最终报告、监测计划及原条件引用。
- `container.new_conversation_acceptance` 先调用同一 `ResearchRequestPlanner`，再把实际公共基础执行、handoff、探索、查询、三个决策对象和最终发布结果归一为 `slice12_v1` 观察记录；它检查五类 profile、身份歧义、支持/反证、伪仓位和失败语义，不生成研究结论。

### 组合与仓位

`portfolio_risk` 提供单币种组合估值、集中度、用户隔离的 `InvestmentMandate`/`PortfolioConstraintSnapshot` 持久化、`PositionBudgetCalculator` 和 `PositionConstraintCalculator`。

当前限制：

- `container.position.assemble(...)` 已连接决策简报、适配、时点、mandate、同日组合估值、约束快照和带证据的价格/风险输入；
- available 区间取风险预算、压力损失、集中度、正相关、流动性、现金底线和计划资金上限的最小值；已持有、未持有和超限持仓使用不同动作语义；
- 场外基金仓位执行、跨币种换算、自动压力情景、完整相关矩阵、订单与复杂优化仍不支持。

### 用户决策上下文

`ResearchDecisionBrief` 已将理解、准备买入和持仓复核意图与是否、时点、仓位轨道分开，并只引用 `InvestmentMandate` 和组合约束快照，不复制数值风险字段。

`ResearchStateService` 可以按 `user_id + asset_id` 保存、读取和更新简报，结合当前持仓输出缺失的组合角色、持仓详情、mandate 或约束快照状态；进程重启后可以恢复。`PortfolioRiskService` 负责保存、读取并校验简报所引用的 mandate、组合估值和约束快照。

当前限制：

- `run-recipe` 在请求带 `user_id` 时按请求保存决策简报并绑定到 workflow run；`run-single` 不创建简报，需要时由 Agent 在启动探索前通过 `container.research_state.save_decision_brief(...)` 保存；
- mandate、组合估值、约束快照和最新价格/风险输入仍需由 Agent 在用户确认后显式准备，不由基础运行擅自创建；
- `PositionPlan` 已由最终报告服务按独立“购买多少”章节渲染；available 只显示区间、压力损失、绑定约束和规划分批，缺少上下文或 profile 不支持时显示正式不可用原因。

### 最终报告与监测建议

`FinalDecisionReportAssemblyService` 会验证 handoff、会话、简报、适配、时点和仓位对象的 workflow、标的、profile、截止日、期限与上游 ID。`FinalDecisionReportReviewer` 要求三轨道均有答案或正式不可用状态，反方和失效条件非空，核心引用由带来源、日期、链接/页码或派生方法的 `FinalEvidenceCitation` 覆盖。

`FinalDecisionReportService` 只在基础包与引用图表已存在、结构复查和 Markdown 复查均通过后发布 `*-完整决策报告-*`。报告中的 `MonitoringPlan` 是不可变的发布时建议，仍不允许已有 `alert_rule_refs`；发布后由 `MonitoringHandoffService` 保存候选、用户确认、实际规则引用和注册 gap，不能机器转换的条件保留人工复查或 unsupported 语义。

最终发布还要求会话用户与决策简报用户一致、基础 Markdown digest 与 handoff 一致、图表 metadata 的标的和截止日一致。旧 handoff 没有 digest 时必须重新运行基础包，不能只凭同名文件通过门禁。

### 标的发现

通用发现结果必须经 discovery 编排后交给 Agent；事件名称注意力使用 `EventNameDiscoveryOrchestrationPipeline` 或 `run-event-name-discovery`。具体边界见 `discovery_handoff.md` 和 `event_name_discovery.md`。

### 问题路由与观点账本

`container.router`（`QuestionRouter`）把 Agent 构造的 `ResearchRequest` 匹配到研究配方，返回步骤状态、元素覆盖、带系统模块与阶段的缺口、用户问题和“必须避免”检查项；`container.recipe_executor`（`RecipeExecutor`）执行其中当前可执行的步骤。配方与执行规则见 `docs/modules/6-编排/问题路由模块.md`。

`ResearchLedgerService` 保存已发布的状态评估、观点、可结算动作和计划，只追加且按用户隔离；最终报告发布后自动入账，观点检验配方把用户观点记为个人条目，Agent 改写的可证伪观点用 `record-view`（`container.view_recorder`）入账。`LedgerSettlementService` 按检查点做前向结算：价格类按复权总回报，指标类（`metric_observation`，`observed_id` 为 `macro:<series_id>`）按检查点前已发布的宏观与市场序列。规则见 `docs/modules/5-研究产物/观点账本模块.md`。

当前限制：

- 路由不做自然语言理解，研究对象、意图和个人化程度由 Agent 写入请求；
- 持仓以用户空间记录为准：`user-context` 查看用户上下文，`record-holding` 记录用户明确提供的持仓；投资任务和约束快照仍由已有服务创建，尚无 CLI；
- 行业景气、策略回测、组合优化和完整计划仍是路由缺口，执行器不调用 smoke 或未接入正式入口的服务；申万行业分类、成分、默认同行、行业指数与估值采样用 `industry` 命令（见 `question_routing.md` 补充规则 8），标的或指数的 `asset_id` 用 `asset --symbol` 查询；市场状态由市场档案（`market-archive`，资金流与情绪待建）给出，宏观状态由宏观档案（`macro-archive`，政策文本与外部环境待建）给出，宏观与利率序列可用 `macro` 命令按截止日读取（见 `question_routing.md` 补充规则 6）；
- 结算只能手动运行 `settle-ledger`，条件触发动作暂不可结算；指标类观点只支持宏观数据目录中的序列（写法见 `question_routing.md` 补充规则 7）；`ledger` 只读展示条目、每个检查点的最新结算和判定计数；复盘只有服务写入入口；股票、场内基金和场外基金按复权总回报结算，指数只有价格指数，结算备注写明各腿口径；`reference_value`、`observed_value` 是复权水平，不是成交价。
- 研究流水线的区间收益、波动、回撤和相对基准指标按复权总回报计算；复权日线不可用时回退未复权并记非阻塞缺口 `total_return_bars_unavailable`，引用这些指标前先看事实限制里的口径。

## 当前路由限制

`ResearchRequestPlanner` 和 `plan-single` 已支持：

- 代码或 `asset_id` 到唯一身份；本地没有时从交易所证券和基金名录补登全部同代码身份，身份来源远程失败与代码不存在分开报告；
- 资产类型、市场和交易所过滤；
- 歧义时返回全部候选，不猜测；
- 读取已持久化 profile assignment；股票没有分配时按行业自动分配，行业查询失败才用资产类型默认 profile 并在规划说明里注明；
- 需要比较基准而拿不到时返回 blocked，不报 ready；
- 将理解、准备买入、持仓复核及决策轨道映射为 profile 可执行的基础任务；
- 场外基金保留用户购买目标，但基础 question 使用当前唯一支持的 `understanding`。

`plan-single` 默认当前日期与理解型目标，输出结构化任务草稿。规划 ready 不等于已经执行。

`run-single` 仍要求显式提供：

- `asset-id`，或 `symbol` 加 `asset-type`；
- `profile`；
- `question`；
- `horizon`；
- `as-of-date`。

交易所可选；存在身份冲突时必须补充。本地没有身份时按 `--asset-type` 通过正式数据获取流程补登。

当前冷启动限制：北交所代码不支持补登；ETF 需要已登记的标的指数关系才有比较基准，尚无自动来源；金融、周期、成长公司的行业或风格基准尚未建立，暂用沪深 300。

当前不存在自然语言意图分类；研究对象与意图由 Agent 写入 `ResearchRequest`。标的配方的 `run-recipe` 把规划与基础运行合成一次调用，`plan-single` 仍只规划。基础流水线自身会生成 workflow run 和 handoff；Agent 通过持久化探索服务显式启动或恢复会话。必须按 `cold_start_intake.md` 使用规划结果，不得编造 `run-bootstrap` 等尚不存在的入口。

## Profile 能力边界

不要在本文维护 profile 实现列表。先查询 `ResearchCapabilityRegistry`，再用实际 `ResearchRunResult` 和 `BaselineResearchHandoff` 判断本次取得的事实和缺口。注册表不替代运行结果，运行结果也不能扩张注册表明确标注的不支持项。

## Agent 可做的补充

workflow run 为 complete/partial 且存在 handoff 后，Agent可以：

1. 从 handoff 选择高价值问题，建立假设与反方。
2. 只为注册表声明的 Agent 查询能力创建 `ResearchQueryPlan`，由 `ResearchExplorationQueryService` 调用公共入口。
3. 研究截止日期内可访问的官方网页、公告和公开材料；外部官方网页必须登记 URL、标题、官方来源和发布日期。
4. 将每项发现绑定到问题、来源、发布日期和对假设的影响。
5. 在 `required_user_context` 不足时提出最小问题。
6. 适配问题闭环后调用 `SuitabilityAssessmentService`，不得用其他决策轨道论文、未入账引用、假设或统一评分补齐门禁。
7. 时点问题闭环后调用 `EntryPlanService`；策略结果只作为证据输入，不直接映射行动，不得加入目标价、底部日期、分批金额或仓位。
8. 仓位问题闭环后调用 `PositionPlanService`；先复用或保存用户所属 mandate、同日组合估值和约束快照，价格与风险输入必须带标的、截止日、证据和限制。不得自行填入 5%、10% 等固定仓位。
9. 三轨道均有对象后，按最终报告契约建立 `FinalReportNarrativeInput`，再调用 `FinalDecisionReportService`。不得直接把 Agent 自由正文交给基础发布服务绕过 reviewer。
10. 用户要求监测时，先为可机器条件提供显式 `MonitoringRuleBinding` 并调用 `MonitoringHandoffService.prepare`；展示候选后取得用户确认，再调用 `confirm_and_activate`。不得从条件文字猜测价格、事件类型或权重阈值。
11. 修改研究主线后，用 `representative_new_conversation_scenarios()` 和 `container.new_conversation_acceptance` 重放公开输出；验收记录不是研究输出，不能用构造的通过状态替代五类 profile 主 smoke 或独立新对话人工复核。

Agent 不得直接查询内部 SQLite、复制 Provider 调用、复刻 smoke 注入或在 blocked 后手工拼接替代基础包。

## 明确未完成

- handoff 缺口到查询计划的静默自动生成与循环执行；
- 基础运行到用户 mandate、组合约束和仓位输入的自动创建；
- 路由结果中标为规划的市场、行业、宏观、策略、组合与计划能力；
- 观点账本的定时结算、条件动作触发记录和校准摘要；
- 后台定时评估、实时行情监听和外部通知渠道。

遇到上述缺口时按工程阻塞或正式不可用状态处理，不用 prompt 假装已有能力。
