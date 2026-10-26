# 多轮探索手册

## 何时开始

只有统一编排产生 complete 或 partial workflow run 及 `BaselineResearchHandoff` 后才开始探索。blocked 基础执行没有 handoff，必须先修复底层能力或确认无法继续，不得由 Agent 手工补成基础包。

探索的输入包括 handoff 中的基础报告路径、事实章节、partial/capability gaps、候选问题、候选假设、用户决策轨道、截止日期和已确认用户上下文。候选问题与假设是可选择起点，不是固定清单或最终论文。

## 初始化

1. 为是否、时点、仓位和最强反方分别列出问题；不适用的轨道显式标记。
2. 建立少量可证伪假设，每项写明：
   - 对哪个决策有影响；
   - 成立时应观察到什么；
   - 哪些事实会削弱或否定；
   - 当前支持、反证和冲突。
3. 将身份、关键事实、用户约束或能力缺口设为 blocking；一般补充项保持非阻塞。
4. 根据“决策影响 × 当前不确定性 ÷ 获取成本”选择下一问题，不机械执行固定清单。

## 每轮记录

每个探索轮次至少记录：

- 本轮要验证或证伪的一个明确目标；
- 实际执行的项目公共查询、官方资料或外部检索；
- 新发现及其截止日期和证据引用；
- 发现属于事实、推断、假设还是未知；
- 它支持、削弱、否定了哪个假设，或暴露了什么冲突；
- 下一轮最有价值的问题；
- 必要时与用户确认的决策约束。

无结果、远程失败和业务无数据也是不同发现，不能合并为“没找到”。

## 查询执行

各步的确切调用、字段和校验规则见 `exploration_api.md`。

1. 每次项目查询先创建 `ResearchQueryPlan`，绑定当前 session 中一个开放问题、注册表声明的能力、预期证据、完成条件和不晚于任务的截止日。
2. 调用 `container.queries.execute(context, session_id=..., plan=...)`。默认公共路由包括数据访问、官方文档证据、基准、事件、同行、用户研究状态和组合快照；不得绕到 repository 或 Provider。
3. 外部官方网页使用 `external_official_web` 路由登记 Agent 已阅读的 HTTPS 来源、标题、官方机构和发布日期；登记不等于系统抓取或已解释内容。
4. `success`、`partial`、`business_empty`、`remote_failure`、`user_context_required`、`unsupported` 和 `failed` 必须按原状态保留。远程失败和业务无数据不关闭问题；能力不支持形成 capability gap，不能用 prompt 补值。
5. 每次执行会更新问题的查询 ID、最近结果和证据，并追加不可覆盖的 `query_executed` 账本项。查询 success 不自动回答问题；Agent 解释证据、记录结构化轮次后再显式调用 `answer_question`。
6. 查询产生的自动 finding 是待解释输入，不会自动改变假设；Agent 必须在探索轮次中记录其支持、削弱、否定或无影响。
7. 查询结果的值在 `result.metadata`：数据访问看 `latest_records` 与覆盖区间，文档证据看 `items`（标题、日期、来源、层级、链接）与 `events`，基准看 `metrics`，事件看 `events`，同行看 `comparisons`。需要全文或超出列表上限的内容时，按链接阅读官方原文。行业查询只拿到 profile 分配的行业名称时返回 `partial`，同行比较仍需先建立同行行业归属。

## 反证纪律

1. 主动寻找能推翻核心论文的最强解释，不只收集支持材料。
2. 价格上涨、排名领先或策略通过不能作为论文的循环证明。
3. 对冲突来源比较定义、期间、发布日期和原文，不按来源数量投票。
4. 合理替代方案必须使用相同风险暴露和时间尺度；不可比时说明原因。
5. 短期与长期证据分别保留，冲突时展示冲突，不求平均分。

## 是否轨道结构化发现

准备 `SuitabilityAssessment` 时，不得让装配器从 finding 文本猜测用途。Agent 必须显式记录：

- `suitability_dimension`：收益来源、定价隐含预期、下行、替代方案或机会成本；
- `suitability_input_kind`：与 profile 对应的公司基本面、金融机构质量、周期经济性、ETF 暴露效率或场外基金经理/风格，以及基准、风险和可比替代证据；
- 替代方案和机会成本的 `subject_asset_id` 与 `comparable_exposure`；
- 证据引用、来源等级、限制和截止日。

只有探索轮次 `evidence_refs` 已入账的事实或推断能满足装配门禁。替代方案与机会成本必须存在共同的标的和可比暴露；最强反方未解决时返回 insufficient。调用入口为 `container.suitability.assemble(session=..., decision_brief=...)`，结果不包含时点和仓位。

## 时点轨道结构化发现

准备 `EntryPlan` 时，Agent 必须为 timing track 发现显式记录：

- `timing_dimension`：当前状态、进入、等待、分批、回避、催化窗口、失效、下一可交易时间、执行约束或跨周期冲突；
- `timing_input_kind`：估值状态、趋势、周期、催化、流动性、ETF 折溢价、市场风险、交易日历或场外基金交易规则；
- `timing_state`、`signal_horizon`、`timing_valid_from`、`timing_valid_until` 和 `observability_key`；
- 催化窗口的起止日期，或下一可交易时间的带时区时间戳；
- 证据引用、来源等级和限制。

策略 pass/fail、技术信号或事件日期只能先成为已解释的结构化发现，不能直接变成 act。act/stage 要求执行周期条件齐备且全部有效窗口有交集；下一可交易候选必须与 profile、执行周期和交集窗口一致。短期/长期冲突必须记录为 `horizon_conflict` 并返回 wait。调用入口为 `container.entry.assemble(session=..., suitability=..., decision_brief=...)`，结果不包含目标价、底部预测、仓位或订单。

## 仓位轨道正式输入

准备 `PositionPlan` 时，Agent 不从自然语言发现猜测比例，而要先完成以下可复现 handoff：

- `ResearchDecisionBrief` 已确认组合角色、持仓状态，并引用用户所属 `InvestmentMandate` 和 `PortfolioConstraintSnapshot`；
- 组合约束快照绑定同日组合估值和目标标的，冻结币种、当前现金、计划资金、最小目标、单标的上限、现金底线、压力损失与冲击、相关性阈值与高相关上限、成交额、参与率和执行天数；
- `PositionCalculationInput` 绑定目标 `asset_id` 和截止日，记录最新价格、实现波动率、当前回撤、相对任务所需的基准波动率，以及证据引用和限制；
- EntryPlan 为 stage 时，快照中的用户确认分批比例必须与活动 `tranche_rule` 数量一致；不得由 Agent 等分或套用固定百分比。

调用入口为 `container.position.assemble(context=..., session=..., suitability=..., entry_plan=..., decision_brief=..., calculation_inputs=...)`。available 结果只展示正式约束计算出的区间、压力损失、绑定约束和规划金额；user_context_required 列明需要补充的用户/组合输入；unsupported 说明 profile 或上游能力缺口。三类状态都不能变成自动订单。

## 与用户交互

例行数据查询无需逐项请示。以下情况才应暂停并询问：

- 标的、期限或动作目标存在实质歧义；
- 新发现会改变研究范围或成本；
- 仓位结论缺少用户风险约束；
- 两个可行方向的取舍只能由用户决定；
- 用户必须确认是否启用后续监测。

具体最小问题见 `user_context_questions.md`。

## Ready 门禁

`mark_ready` 已程序化检查轮次、blocking 问题、请求的决策轨道、替代方案、主论文证伪条件、未解决冲突和最强反方。Agent 在调用前还必须确认内容质量：

1. 至少完成一轮支持证据验证和一轮反证验证；同一轮同时完成也必须有两类独立证据。
2. 所有 blocking 问题已回答，或以正式不可回答状态关闭。
3. 是否、时点、仓位三个轨道均有结论、不适用或不可用原因。
4. 主论文、最强反方、主要下行和失效条件已建立。
5. 用户要求仓位时，组合约束和计算输入完整；否则仓位状态为需要上下文/不支持。
6. 事实未超过截止日期，推断可回溯，冲突没有静默覆盖。
7. 下一复查节点或会改变结论的可观察条件明确。

未满足时继续探索或正式停止，不为了生成报告强制得出行动结论。

## 停止规则

- **可出报告**：关键问题已闭环，剩余未知不会改变当前行动。
- **等待证据**：缺失项未来可观察，给出触发条件和复查时间。
- **能力阻塞**：项目没有公共能力且无法可靠外部补足，记录 capability gap。
- **用户上下文不足**：标的研究可继续，但个性化适配或仓位暂停。
- **论文被否定**：保留否定过程，结论可以是回避或不行动。

探索快照与追加式账本已按用户、workflow run 和 handoff 持久化。换进程或换新对话时通过 `PersistentResearchExplorationService.resume_for_workflow(...)` 恢复，不依赖聊天摘要；恢复后仍须从未决问题继续，不能把已保存状态当作已完成结论。
