# 当前阶段：Agent 决策研究闭环

## 一、阶段定位

本阶段把 Wealth Farm 从“能够生成单标的基础研究包”推进为“任何新对话中的 Agent 都能稳定完成面向用户决策的完整研究”。

用户只提供一个标的代码时，Agent 应能通过 `.agent` 指引和项目公共入口：

1. 正确解析标的身份、资产类型和适用研究模板。
2. 理解用户最终关心的是是否购买、何时购买、购买多少，以及什么条件会改变判断。
3. 使用统一编排入口生成基础研究包，不手工串数据、指标、图表和报告服务。
4. 从决策问题出发建立假设、关键问题、反方和证据门槛。
5. 进行多轮针对性查询、项目数据调用、联网研究和反证验证。
6. 只在会改变目标、范围、风险预算或结论时询问用户。
7. 在证据充分时给出明确结论；证据不足时明确留白和补充条件。
8. 产出普通 Markdown 最终研究报告，回答是否买、何时买、买多少、为什么、可能错在哪里以及后续观察什么。

本阶段不把 Agent 包装成自动荐股或自动交易系统。研究结论可以是“考虑配置”“等待条件”“不适合”“继续持有/减持”或“证据不足”，不得强制给出买入结论。

## 二、用户真正需要的决策输出

### 1. 是否购买

必须回答：

1. 标的的收益来源是什么，是否可持续。
2. 当前价格或净值对应了什么市场预期。
3. 相对现金、基准和合理替代标的，风险收益是否有吸引力。
4. 乐观、基准和悲观情景分别依赖什么假设。
5. 永久性损失、深度回撤或论文失效可能来自哪里。
6. 最强反方是否已被正面回答。
7. 当前证据支持配置、观察、回避还是无法判断。

### 2. 何时购买

必须回答：

1. 当前处于什么估值、趋势、周期、事件或市场风险状态。
2. 研究逻辑是否已经被价格充分反映。
3. 哪个价格、估值、基本面事实或催化剂会改善赔率。
4. 应一次买入、分批建立还是等待验证。
5. 哪些条件意味着不应追涨、应暂停买入或应放弃等待。
6. 短期执行判断与中长期价值判断是否冲突。

Agent 不预测精确最低点。时点结论必须表达为可观察条件和执行窗口。

### 3. 购买多少

必须回答：

1. 标的在用户组合中承担什么角色。
2. 用户是否已经持有，当前权重、成本和重合暴露是什么。
3. 用户可承受的组合损失、流动性需求和持有期限是什么。
4. 压力情景下标的可能损失多少。
5. 仓位上限受目标波动、最大回撤、集中度、相关性、流动性和现金底线中的哪项约束。
6. 建议是观察仓、目标仓位区间、分批增仓、维持、减仓还是暂不配置。

没有用户组合和风险约束时，Agent 可以分析标的风险，但不得给出伪精确仓位比例。

## 三、当前基线与核心缺口

### 已有能力

1. `ResearchTaskSpec` 已包含标的、profile、问题类型、时间尺度、截止日期和输出深度。
2. `src/research_orchestration/` 已统一完成身份后的数据获取、质量检查、指标、图表、策略、证据、基础包复查和发布。
3. 普通公司、金融机构、周期资源、ETF 和场外基金已有正式 execution service。
4. 基准、同行、估值、财务质量、风险、策略和低频回测已有公共模块。
5. `ResearchExplorationSession` 已能记录问题、假设、轮次和证据引用。
6. `research_state` 已能保存用户持仓和 Markdown 报告。
7. `portfolio_risk` 已有投资任务和基于目标波动、回撤、仓位上限及现金底线的仓位预算基础。
8. 基础研究包与静态图表已有验收和发布流程。

### Agent 指引缺口

以下是计划启动时的基线缺口；完成情况以各切片实施结果和末尾 Tracking 为准。

1. 现有指引说明了“如何研究”，但没有把“用户最终要决定什么”作为顶层契约。
2. 新对话只收到代码时，没有明确的默认路由、最小澄清问题和继续工作的规则。
3. 没有统一说明什么信息会改变是否买、何时买和买多少。
4. 没有最终决策报告的章节、证据和行动质量契约。
5. 项目能力分散在模块文档中，Agent 缺少只包含公共入口和能力状态的研究能力地图。
6. 没有明确区分标的层结论和用户适配结论；容易在缺少组合约束时给固定仓位。
7. 没有可重复执行的新对话验收场景来验证 Agent 是否遵守指引。

### 流程与代码缺口

以下是计划启动时的基线缺口；完成情况以各切片实施结果和末尾 Tracking 为准。

1. CLI 仍要求调用者预先提供资产类型、profile、问题和时间尺度；缺少面向“代码 + 决策目标”的高层研究入口。
2. 基础收集任务与用户决策目标混在同一个问题类型中；某些 profile 只能运行理解型基础包，但用户仍可能需要购买决策。
3. `ResearchExplorationSession` 只要求至少一轮且无阻塞问题，没有强制覆盖适配、时点、仓位、替代方案、最强反方和失效条件。
4. 探索会话目前不持久化，换对话或进程重启后不能可靠恢复。
5. 假设只记录支持和反证引用，没有预期观察、证伪条件、证据等级、时间和冲突状态。
6. 没有项目能力注册表告诉 Agent 哪些问题可由自动化完成、哪些需要 Agent 联网研究、哪些当前不支持。
7. 基础包和最终报告之间没有正式 handoff 对象、状态机和发布门槛。
8. 仓位计算没有接入单标的研究流程，也没有缺少用户约束时的正式不可用状态。
9. 最终报告仍由 Agent 自由撰写，缺少“是否、何时、多少”结构化契约与程序化复查。
10. 同行、行业、市场、宏观和部分 profile 专用能力虽有独立服务，但尚未按决策问题稳定进入基础执行或探索查询。

## 四、设计原则

1. **先决策问题，后信息收集**：每项查询必须能说明会影响哪个结论。
2. **基础收集与最终判断分层**：确定性流程自动化；Agent 负责解释、验证、反证和综合。
3. **标的研究与用户适配分层**：同一标的事实可共享，持仓、风险预算和行动只属于当前用户。
4. **公共入口优先**：已有 service 或编排器时，Agent 不直接查 SQLite 或拼外部 API。
5. **能力不足显式化**：指引不能用 prompt 掩盖数据、比较、回测或仓位能力缺失。
6. **行动必须可观察**：买入、等待、增减仓和失效条件使用价格、估值、事实或风险阈值表达。
7. **不制造精度**：概率、目标价和仓位只有在输入与方法足够时才给出。
8. **反方先于结论完成**：未建立最强反方和论文失效条件，不得发布最终报告。
9. **时间尺度不求平均**：长期价值、短期时点和组合仓位分别形成结论，再解释冲突。
10. **普通 Markdown 交付**：最终报告和静态图片写入工作区，不依赖 Canvas 或交互式界面。

## 五、目标状态机

```text
用户输入代码
    ↓
IDENTITY_RESOLUTION
    ├─ 唯一身份 → 继续
    └─ 多义/冲突 → 只询问身份所需的最小问题
    ↓
DECISION_BRIEF
    ├─ 建立临时默认研究目标
    ├─ 记录准备买/已持有/复核/只理解
    └─ 标记仓位结论所缺用户约束
    ↓
BASELINE_COLLECTION
    ├─ 统一编排生成基础研究包
    ├─ blocked → 修复底层能力或明确停止
    └─ complete/partial → 进入 handoff
    ↓
QUESTION_DECOMPOSITION
    ├─ 是否值得买
    ├─ 何时行动
    ├─ 买多少
    ├─ 最强反方
    └─ 关键未知与替代标的
    ↓
MULTI_ROUND_EXPLORATION
    ├─ 本轮目标
    ├─ 项目公共查询或针对性联网研究
    ├─ 发现、证据和冲突
    ├─ 更新假设状态
    └─ 生成下一轮最高价值问题
    ↓
DECISION_GATES
    ├─ SUITABILITY_READY
    ├─ TIMING_READY / NOT_APPLICABLE
    ├─ SIZING_READY / USER_CONTEXT_REQUIRED
    └─ COUNTERARGUMENT_RESOLVED
    ↓
FINAL_REVIEW
    ├─ 事实、推断、假设和未知分离
    ├─ 证据、日期、反方和失效条件完整
    ├─ 是否/何时/多少均有答案或明确不可回答原因
    └─ 行动与用户约束一致
    ↓
普通 Markdown 最终研究报告
```

## 六、新对话默认行为

当用户只输入一个代码并要求“研究”时：

1. Agent 先读取 `.agent/AGENT_README.md`、`.agent/SKILL.md` 和研究主线。
2. 通过高层身份入口解析代码，不自行猜测股票、ETF、指数或场外基金。
3. 身份唯一时立即开始标准深度基础收集；身份冲突时只询问资产类型或交易所。
4. 暂时将用户目标记录为“决策支持待确认”，不把基础理解任务等同于最终投资结论。
5. 基础收集期间或完成后，只集中询问会改变研究路线的问题：
   - 准备买入、已经持有、复核旧结论，还是只想理解；
   - 大致持有期限；
   - 如果需要仓位建议，当前持仓/计划资金、可承受损失和流动性约束。
6. 用户暂未回复时继续完成不依赖个人约束的标的研究，但仓位结论保持不可用。
7. 基础包不是最终答复；Agent 必须进入探索和反证阶段。

## 七、Agent 指引目标结构

### 保持精简的入口

- `.agent/AGENT_README.md`
  - 只保留每轮必读、研究唯一主线、禁止手工串服务和保护用户改动。
- `.agent/SKILL.md`
  - 只负责按用户意图路由到最小相关文档。

### 修改现有研究指引

- `.agent/docs/research/mainline.md`
  - 改为目标状态机。
  - 增加代码单独输入时的默认行为。
  - 明确基础包到最终报告的正式 handoff。
- `.agent/docs/research/agent_charter.md`
  - 增加用户决策价值、标的判断与用户适配边界。
  - 明确 Agent 最终必须回答是否、何时、多少或说明不可回答原因。
- `.agent/docs/research/research_strategy_playbook.md`
  - 为每类任务补充主要决策、用户输入、证据门槛、典型反方和停止条件。
  - 明确投资、择时和仓位是三个可组合但不可混淆的研究轨道。

### 新增专题指引

- `.agent/docs/research/cold_start_intake.md`
  - 新对话只输入代码时的身份、默认目标、最小澄清和继续工作规则。
- `.agent/docs/research/profile_routing.md`
  - 资产类型、profile、基础问题和时间尺度的公共路由，不复制 execution 实现。
- `.agent/docs/research/user_decision_model.md`
  - 用户真正关心的决策、最小用户上下文、默认值和禁止伪精确规则。
- `.agent/docs/research/user_context_questions.md`
  - 按是否、时点和仓位轨道选择最少的用户问题。
- `.agent/docs/research/project_capability_map.md`
  - 只登记稳定公共入口、支持的 profile/问题和明确缺口。
  - 详细能力继续链接 `docs/modules/`，不复制模块正文。
- `.agent/docs/research/exploration_playbook.md`
  - 假设、轮次、反证、问题优先级、停止和 `ready_for_report` 规则。
- `.agent/docs/research/final_report_contract.md`
  - 最终报告章节、证据、行动、反方、失效条件和复查规则。
- `.agent/docs/research/agent_acceptance.md`
  - 冻结新对话场景、预期行为和指引准入标准。
- `.agent/docs/research/event_name_discovery.md`
  - 从通用任务策略手册移出事件名称注意力专项，避免普通单标的研究加载无关长规则。

### 防止指引膨胀

1. 长期原则只保留一份所有者文档，其他文件只链接。
2. 项目能力状态优先由代码注册表输出；Markdown 只解释如何使用。
3. 资产专用数据和指标继续归模块文档，不写入 Agent 主线。
4. 已经由代码门禁保证的细节不在多个指引中重复。
5. 单个专题文件以一个明确路由触发器为边界；过长时按触发器拆分，不建立第二套总入口。

## 八、目标代码契约

### 1. 用户决策简报

新增独立于 `ResearchTaskSpec` 的用户层对象，例如：

```text
ResearchDecisionBrief
  user_id
  asset_id
  decision_intent
  position_state
  horizon
  as_of_date
  portfolio_role
  mandate_id
  portfolio_constraint_snapshot_id
  constraints
  assumptions
  missing_user_context
```

`ResearchDecisionBrief` 只拥有对话目标和用户确认状态。当前持仓、计划资金、最大可接受损失、目标波动、流动性、现金底线和单标的上限由 `research_state`、`InvestmentMandate` 与 `PortfolioConstraintSnapshot` 统一拥有，简报只引用稳定 ID，避免复制两套风险约束。

基础研究任务只描述标的层确定性收集；用户持仓和风险约束不得进入可共享的基础事实摘要或结构 digest。

### 2. 研究能力注册表

新增可查询的 `ResearchCapabilityRegistry`：

```text
profile + question + decision_lane
  automated_capabilities
  agent_query_capabilities
  required_user_context
  unsupported_capabilities
  public_entrypoints
  evidence_requirements
```

Agent 必须能够在研究开始时知道：

1. 哪些数据、指标和图表由基础编排自动提供。
2. 哪些问题应通过项目公共 service 做针对性补充。
3. 哪些问题需要外部网页或官方文档研究。
4. 哪些问题当前没有可靠能力，必须形成缺口。

### 3. 基础包 handoff

新增 `BaselineResearchHandoff`：

```text
workflow_run_id
task
run_status
report_path
asset_identity
available_fact_sections
capability_gaps
blocking_gaps
candidate_questions
candidate_hypotheses
```

候选问题和假设只是探索起点，不得直接进入用户结论。

`ResearchWorkflowRun` 负责连接决策简报、基础包、handoff、探索会话、最终报告和可选监测状态，使换对话后能够从一个稳定运行记录恢复，不把这些关联只留在 Markdown 路径中。

### 4. 探索与证据

扩展探索对象：

```text
ResearchHypothesis
  statement
  decision_impact
  expected_observations
  falsification_conditions
  status
  supporting_evidence
  counterevidence
  unresolved_conflicts

ResearchFinding
  claim
  claim_kind
  as_of_date
  published_at
  evidence_refs
  source_tier
  supports_or_weakens
  limitations

ResearchQuestion
  decision_lane
  priority
  blocking
  answerability
  required_capability
```

探索轮次优先级由“对决策的影响 × 当前不确定性 × 获取成本”决定，不按固定清单机械执行。

### 5. 三类决策结果

```text
SuitabilityAssessment
  action: consider / watch / avoid / hold_review / insufficient
  asset_role
  thesis
  expected_return_sources
  priced_expectations
  alternatives
  downside
  confidence
  evidence_refs

EntryPlan
  status: act / stage / wait / not_applicable / insufficient
  current_regime
  entry_conditions
  tranche_rules
  avoid_conditions
  catalyst_window
  invalidation_conditions

PositionPlan
  status: available / user_context_required / unsupported
  portfolio_role
  current_weight
  target_range
  risk_budget
  stress_loss
  binding_constraints
  calculation_inputs

MonitoringPlan
  status: proposed / armed / user_confirmation_required / unsupported
  review_date
  observable_conditions
  alert_rule_refs
  limitations
```

### 6. 最终报告对象

新增 `FinalDecisionResearchReport`，引用基础包和探索会话，不复制底层全部数据。报告必须通过专用 reviewer 后才能发布。

## 九、最终报告质量契约

最终普通 Markdown 至少包含：

1. **决策摘要**
   - 当前结论、置信度、截止日期。
   - 是否买、何时买、买多少的直接回答。
2. **用户目标与适用边界**
   - 持有期限、持仓状态、风险预算和缺失约束。
3. **标的与核心论文**
   - 收益来源、竞争优势或风险暴露。
4. **是否值得买**
   - 当前价格隐含预期、替代方案、机会成本和主要下行。
5. **何时行动**
   - 当前状态、可观察触发条件、分批规则和不追涨条件。
6. **购买多少**
   - 组合角色、仓位区间、压力损失和约束；不可计算时明确缺什么。
7. **三情景**
   - 每个情景的关键假设、观察指标和影响，不使用无来源伪概率。
8. **最强反方**
   - 最能推翻主论文的解释及当前回答。
9. **失效、退出与复查**
   - 论文失效、风险触发、减仓/退出条件、下次复查节点和可选监测规则。
10. **关键证据与未知**
    - 官方来源、发布日期、页码或链接；仍可能改变结论的未知项。

程序化复查至少保证：

1. 基础包存在且身份一致。
2. 每个核心事实有来源和截止日期。
3. 每项推断可回溯到事实或明确假设。
4. 最强反方和失效条件非空。
5. 三个决策问题都有答案或正式不可用状态。
6. 仓位建议存在时，用户约束和计算输入完整。
7. 最终结论没有把远程失败、业务无数据或派生值写成官方事实。
8. 报告不包含内部 ID、版本、digest、源码路径或测试成熟度标签。
9. 监测规则只来自可观察失效条件；未经用户确认不自动启用。

## 十、能力扩充重点

### 跨资产共通

1. 高层代码身份与 profile 路由入口。
2. 历史估值、同行分位、行业与市场状态。
3. 基准、替代标的和机会成本比较。
4. 可复核的情景输入与价格隐含预期。
5. 压力测试、组合相关性和仓位约束。
6. 新闻、事件和催化剂的时点与来源。
7. 最终报告证据链接、发布日期和页码可见。

### 股票与公司

1. 商业模式、竞争格局、管理层和资本配置的证据结构。
2. 财务趋势、盈利质量、估值历史和同行统一进入决策分析。
3. 基准/悲观情景估值与隐含预期。
4. 行业景气、市场 Beta 和公司自身贡献分解。

### ETF 与基金

1. 总收益、分红、基准历史定义和完整现金利率历史。
2. 同类产品、费用、流动性、跟踪和底层暴露比较。
3. 多期持仓、规模、份额流、风格漂移和经理变化。
4. 底层指数或组合当前估值、行业集中度和市场状态。

### 金融与周期资产

1. profile 专用压力情景和关键领先指标。
2. 利率、信用、商品、汇率和政策敏感性。
3. 周期位置、正常化盈利和资产负债表风险。

上述能力不能作为一个巨型切片一次实现。每类代表性资产在工作流基础设施完成后建立独立短期计划。

## 十一、开发切片

### 切片一：冻结用户决策模型与 Agent 报告契约

状态：completed

#### 输入和依赖

- 现有 `.agent` 研究指引。
- `ResearchTaskSpec`、基础研究包和长期路线图。
- 本计划第二、六、七、九节。

#### 新增或修改

- 新增 `cold_start_intake.md`、`profile_routing.md`、`user_decision_model.md`、`user_context_questions.md`、`project_capability_map.md`、`exploration_playbook.md`、`final_report_contract.md` 和 `agent_acceptance.md`。
- 将事件名称注意力专项从通用策略手册拆到 `event_name_discovery.md`。
- 更新 `agent_charter.md`、`mainline.md`、`research_strategy_playbook.md` 和 `.agent/SKILL.md`。
- 明确代码单独输入时的默认行为、最小澄清问题和不可给仓位的条件。

#### 实施步骤

1. 将用户是否买、何时买和买多少冻结为 Agent 顶层决策模型。
2. 定义代码单独输入、目标不明确、已持有和只理解四类入口行为。
3. 定义最终报告章节、证据门槛、不可回答状态和禁止伪精确规则。
4. 按文件所有权更新指引并删除重复规则，只保留公共入口引用。
5. 为专题文件、链接和稳定代码入口增加静态一致性测试。
6. 使用代表性新对话问题进行文档走查。

#### 验收

- 新对话只给代码时，指引能唯一说明先读什么、调用什么和何时询问。
- 报告契约明确回答是否、何时、多少或不可回答原因。
- 不把基础研究包写成最终报告。
- 不引用尚不存在的公共入口作为已实现能力。
- 通用策略手册不再承载事件名称专项长流程，专题链接无断链。

#### 实施结果（2026-08-23）

- 新增冷启动、用户决策、最小上下文、profile 路由、当前能力地图、探索、最终报告和 Agent 验收八份专题指引。
- `mainline.md` 已按冷启动、任务路由、基础执行、探索、报告门禁和发布形成唯一状态机。
- 是否、何时和多少已冻结为三个独立决策轨道；缺少用户约束时禁止伪仓位。
- 当前 symbol-only、探索持久化和最终决策 reviewer 等未完成能力在指引中显式标注，不作为已实现入口。
- 事件名称注意力专项已从通用策略手册和发现交接中拆出，避免普通研究加载无关长规则。
- 新增 Agent 指引静态测试，检查主线路由、本地 Markdown 引用、专项所有权、当前能力边界和最终报告关键门槛。

#### 本切片不处理

- 新代码契约、数据库和指标。

### 切片二：用户决策简报与研究上下文

状态：completed

#### 输入和依赖

- `UserContext`、`ResearchTaskSpec`、当前持仓和用户偏好。
- 切片一冻结的最小用户上下文和不可给仓位规则。

#### 新增或修改

- `src/research_workflow/decision_models.py`
- `src/research_state/` 的决策简报持久化。
- `ResearchDecisionBrief`、`InvestmentMandate`/组合约束快照引用、持仓状态和缺失上下文状态。

#### 实施步骤

1. 冻结决策意图、持仓状态、组合角色和风险约束引用字段。
2. 明确标的层任务与用户层决策上下文的序列化边界。
3. 增加按 `user_id` 和 `asset_id` 保存、读取和更新的 service。
4. 输出缺失上下文清单，不在模型默认值中伪造用户风险偏好。
5. 复用 `InvestmentMandate` 保存数值风险约束，不在简报中复制另一套仓位和风险字段。

#### 验收

- 标的层任务与用户私有约束分离。
- 简报与投资约束只有一个数值真源。
- 缺少风险预算时 `PositionPlan` 只能是 `user_context_required`。
- 重启后可恢复同一用户的决策简报。
- 单元测试覆盖准备买入、已有持仓、只理解和约束缺失。

#### 实施结果（2026-08-23）

- 新增 `ResearchDecisionBrief`、`DecisionIntent`、`DecisionLane`、`PositionState`、缺失上下文类型和仓位上下文状态。
- 理解、准备买入和持仓复核与是否、时点、仓位轨道分离；时点和仓位轨道必须保留是否适配判断。
- 简报只保存 `mandate_id` 与组合约束快照 ID，不复制计划资金、目标波动、最大回撤和仓位上限。
- `ResearchStateService` 已支持按 `user_id + asset_id` 保存、读取和更新简报，并从当前持仓及引用状态计算缺失用户上下文。
- SQLite 新增 `research_decision_briefs`，稳定 brief ID 在更新和进程重启后保持一致。
- 单元测试覆盖理解型、准备买入、已有持仓、仓位约束缺失/补齐、用户隔离、序列化和标的任务私有字段隔离。

#### 本切片不处理

- 仓位计算和最终报告。

### 切片三：代码到正式研究任务的高层路由

状态：completed

#### 输入和依赖

- 切片二的决策简报。
- 主数据、profile assignment、`ResearchTaskFactory` 和默认生产容器。

#### 新增或修改

- `ResearchRequestPlanner` 或等价公共服务。
- CLI 新增面向代码和标准化决策目标的入口。
- 复用主数据、profile assignment、任务工厂和默认生产容器。

#### 实施步骤

1. 定义代码、市场、资产类型和截止日期的高层请求。
2. 解析唯一身份或返回可供用户选择的冲突身份。
3. 将用户决策目标映射为 profile 支持的基础收集任务。
4. 通过 CLI 和 Python API 暴露同一规划结果。

#### 验收

- 唯一代码可生成身份、profile 和基础任务草稿。
- 同代码存在股票、ETF 或基金歧义时返回明确选择，不猜测。
- 场外基金等只支持理解型执行的 profile，仍能与用户购买决策目标分离。
- 不要求 Agent 手工选择 execution service。

#### 实施结果（2026-08-23）

- 新增 `ResearchRequestPlanner`、规划请求/结果、身份候选和 ready/歧义/未解析/不支持状态。
- 支持代码或 asset ID、资产类型、市场、交易所、截止日期、标准化决策意图和决策轨道输入。
- 代码唯一时自动读取有效 profile assignment，否则使用资产类型默认 profile；只生成默认 dispatcher 已支持的 profile 任务。
- 同代码存在股票、ETF 或场外基金歧义时返回完整候选，不按代码外形或默认资产类型猜测。
- 场外基金保留用户购买目标，但基础任务自动使用当前唯一支持的 `understanding`。
- 默认生产容器已装配规划器；新增 `plan-single` CLI，与 Python API 使用同一服务和结构化结果。
- 单元测试覆盖唯一身份、歧义选择、profile assignment、场外基金目标分离、基金身份 bootstrap、决策简报接入和 CLI。

#### 本切片不处理

- 自然语言意图分类模型。

### 切片四：能力注册表与基础包 handoff

状态：**completed**

#### 输入和依赖

- 切片三生成的正式任务。
- `ResearchRunResult`、profile registry、模块公共 service 和 acceptance gaps。

#### 新增或修改

- `ResearchCapabilityRegistry`
- `BaselineResearchHandoff`
- `ResearchWorkflowRun`
- profile × question × decision lane 能力声明。
- 编排器从基础执行结果生成 handoff。

#### 实施步骤

1. 冻结能力、证据要求、用户上下文和不支持项的声明结构。
2. 为当前五类 execution profile 建立最小能力登记。
3. 从基础包事实章节、阶段状态和 gaps 生成 handoff。
4. 创建顶层 workflow run，连接决策简报、基础结果与 handoff。
5. 增加能力登记与真实公共入口的一致性测试。

#### 验收

- Agent 可查询自动化能力、Agent 查询能力、用户上下文需求和明确不支持项。
- handoff 能区分 blocking、partial 和能力缺口。
- 基础执行为 complete/partial 时可从 workflow run 找到 handoff；blocked 时保留阻塞状态但不伪造探索会话。
- 模块公共入口变化时由测试发现能力注册表失配。
- `.agent` 能力地图只引用注册表和模块文档，不复制实现清单。

#### 实施结果（2026-08-23）

- 新增 `ResearchCapabilityRegistry`，为五类默认 execution profile 声明支持问题、自动化能力、Agent 查询能力、用户上下文、证据要求、不支持项和真实 Python 公共入口。
- 注册表按 profile × question × decision lane 返回查询视图；场外基金对非 `understanding` 基础问题返回明确不支持，不把购买目标伪装成已自动执行。
- 新增 `ResearchWorkflowRun`、`BaselineResearchHandoff`、资产身份快照和三类 handoff gap；partial、blocking 与 capability gap 分开保存。
- `ResearchWorkflowCoordinator` 已接入默认基础流水线。complete/partial 保存 workflow run 和 handoff；blocked 保存阻塞代码但不创建 handoff 或探索会话。
- workflow run 与 handoff 通过共享 SQLite 持久化，可在进程重启后按 workflow run 找回。
- 一致性测试覆盖五类 profile、决策上下文、不支持问题、注册入口导入、complete/partial handoff、blocked 门禁、重启恢复和流水线挂载。
- `.agent` 能力地图已改为解释机器注册表与模块文档，不再维护 profile 实现副本。

#### 本切片不处理

- 补齐各 profile 的全部能力。

### 切片五：探索会话、证据账本与持久化

状态：**completed**

#### 输入和依赖

- `ResearchDecisionBrief` 和 `BaselineResearchHandoff`。
- 现有 `ResearchExplorationSession`、`ResearchWorkflowRun`、`research_state` 和证据引用契约。

#### 新增或修改

- 扩展 `research_orchestration/exploration.py` 或拆分同模块模型。
- 增加 `ResearchFinding`、决策轨道、证伪条件、冲突和问题优先级。
- 通过 `research_state` 保存和恢复会话。

#### 实施步骤

1. 扩展问题、假设、发现和轮次模型。
2. 为是否、时点、仓位、反方和替代方案建立决策轨道。
3. 实现追加式证据与假设状态历史。
4. 增加会话 repository、恢复和状态迁移门禁。
5. 将会话稳定关联到 workflow run、基础包和决策简报。

#### 验收

- 探索轮次必须记录目标、查询、发现、证据、假设影响和下一问题。
- 支持/削弱/否定均保留历史，不覆盖旧证据。
- 阻塞问题、最强反方和失效条件未闭环时不能 `ready_for_report`。
- 进程重启后可继续同一会话。

#### 实施结果（2026-08-23）

- 将探索模型拆为结构化状态与状态机，新增 `ResearchFinding`、证据等级、是否/时点/仓位/反方/替代轨道、问题优先级、预期观察、证伪条件和冲突。
- 每轮探索必须记录目标、查询、结构化发现、证据引用、对已知假设的影响和明确下一步；事实发现同时要求来源等级和截止日。
- 假设创建及后续 supported/weakened/rejected 变化形成 `HypothesisStatusEvent` 历史，旧状态与旧证据不会被当前快照覆盖。
- 新增 `PersistentResearchExplorationService`，从 complete/partial handoff 显式启动会话，按用户隔离读写，并将唯一 session ID 关联回 `ResearchWorkflowRun`；blocked workflow 不能启动探索。
- research_state 新增探索快照与追加式账本表，账本具有数据库级禁止更新/删除门禁；快照、workflow run、handoff 和决策简报保持稳定引用。
- `mark_ready` 已程序化检查轮次、blocking 问题、请求决策轨道、替代方案、主论文证伪条件、未解决冲突和最强反方。
- 默认生产容器已装配用户空间与持久化探索服务；单元测试覆盖用户隔离、blocked 门禁、支持/削弱/否定历史、账本防改写和进程重启恢复。

#### 本切片不处理

- 自动替 Agent 生成自然语言研究结论。

### 切片六：面向缺口的查询计划与项目能力调用

状态：**completed**

#### 输入和依赖

- 切片四的能力注册表和切片五的高优先级研究问题。
- 行业、同行、基准、事件、文档、数据访问和组合公共 service。

#### 新增或修改

- 研究问题到公共项目能力的查询计划契约。
- 统一记录项目查询、外部官方网页研究、失败和证据引用。
- 行业、同行、基准、事件、文档和组合查询的公共路由。

#### 实施步骤

1. 定义问题、预期证据、公共能力和输出引用的查询计划。
2. 为已有公共 service 建立显式路由，不暴露内部 repository。
3. 登记外部官方网页研究和项目不支持状态。
4. 将查询结果追加到探索证据账本并更新问题状态。

#### 验收

- Agent 不直接查询 SQLite 或复制 Provider 调用。
- 每项查询对应一个决策问题和预期证据。
- 外部来源失败与业务无数据分开。
- 项目没有能力时形成显式 capability gap，不用 prompt 补值。

#### 本切片不处理

- 通用新闻爬虫和全市场自动跟踪。
- 从 handoff 缺口静默触发固定查询清单；查询仍由 Agent 按问题价值显式规划。

#### 实施结果

- 新增 `ResearchQueryPlan`、`ResearchQueryResult` 与 `ResearchQueryOutcome`，每项查询必须绑定 session、开放问题、能力、预期证据、完成条件、截止日和 JSON 参数。
- `ResearchExplorationQueryService` 只通过公共 service 显式路由数据访问、官方文档证据、基准、事件、同行、用户研究状态和组合快照；默认容器注册全部路由，不向 Agent 暴露 repository 或 Provider。
- 外部官方网页采用登记而非通用抓取：要求 HTTPS URL、标题、官方来源和发布日期，生成稳定证据引用并保留 `registration_only`。
- success、partial、business_empty、remote_failure、user_context_required、unsupported 和 failed 使用不同结果状态；项目未声明或未注册的能力形成 `ExplorationQueryGap`，不由 prompt 补值。
- 数据、文档、事件和用户/组合状态均受查询截止日约束；没有历史快照时返回用户上下文不足，不用当前状态回填过去。
- 每次执行原子更新问题的 query IDs、最近结果、证据和状态，并追加 `query_executed` 账本事件；查询缺口同时保存在探索快照，重启后可恢复。
- 查询 success 只更新证据和最近结果，不自动回答问题；Agent 解释证据并记录结构化轮次后再显式关闭。远程失败和业务无数据保持问题开放，unsupported 以正式 capability gap 延后问题，后续探索可以继续。

### 切片七：是否购买与替代方案评估

状态：**completed**

#### 输入和依赖

- 已完成关键问题的探索会话。
- 基准、同行、估值、财务、风险和用户决策简报。

#### 新增或修改

- `SuitabilityAssessment`
- 当前价格隐含预期、收益来源、下行、替代标的和机会成本契约。
- 至少一个主论文和一个最强反方。

#### 实施步骤

1. 冻结适配结论状态和证据门槛。
2. 从探索发现装配主论文、下行、替代方案和机会成本。
3. 建立最强反方未解决时的 `insufficient` 门禁。
4. 为不同 profile 定义适用输入，不使用统一评分。

#### 验收

- 输出 consider/watch/avoid/hold_review/insufficient 之一。
- 每个结论有证据、限制和时间尺度。
- 未比较合理替代标的时不得声称“值得买”。
- 代表性股票、ETF 和场外基金各完成一例。

#### 本切片不处理

- 时点和仓位。
- 把结构化适配结论直接发布成最终 Markdown；最终报告与 reviewer 仍属于切片十。

#### 实施结果

- 新增 `SuitabilityAssessment`、五类行动状态和定性置信边界，分别保存主论文、收益来源、定价隐含预期、下行、替代方案、机会成本和最强反方，不包含统一数值评分。
- `ResearchFinding` 可显式标注 suitability dimension、profile 输入类型、替代标的和可比风险暴露；只有带证据引用、来源等级、限制和截止日的事实/推断才能满足装配门禁。
- `SuitabilityAssessmentService` 只消费探索轮次账本中已登记的证据引用；未入账引用、跨决策轨道论文、假设/未知发现均不能支持正向结论。
- 普通公司、金融机构、周期资源、ETF 和场外基金使用不同 profile policy；公司基本面不能替代 ETF 暴露效率，ETF 指标也不能替代基金经理/风格证据。
- `consider` 要求已回答替代方案问题，并且替代方案与机会成本针对同一标的和同一可比风险暴露；缺失时降为 watch 或 insufficient，不伪造“值得买”。
- 最强反方未解决、核心论文/下行/收益来源证据不足、探索未 ready 或决策简报缺失时返回 insufficient；论文被否定或反方成立时返回 avoid，持仓复核意图单独返回 hold_review。
- 评估 ID 由完整结构化内容确定，反序列化会复核行动、置信边界、缺口与 ID 一致性。代表性股票、ETF 和场外基金均有单元测试样例。

### 切片八：时点与分批执行计划

状态：**completed**

#### 输入和依赖

- `SuitabilityAssessment`。
- 与时间尺度一致的估值、趋势、周期、催化剂、流动性和市场状态。

#### 新增或修改

- `EntryPlan`
- 估值、趋势、周期、催化剂和市场风险状态的可观察触发条件。
- 分批规则、等待条件和不追涨条件。

#### 实施步骤

1. 冻结 act/stage/wait/not_applicable/insufficient 状态。
2. 将时点证据转换为可观察的进入、等待和放弃条件。
3. 记录信号有效期、下一可交易时间和执行限制。
4. 分别保留短期执行与长期论文，不合并为总分。

#### 验收

- 不输出精确底部日期。
- 每项行动条件可由项目数据或明确外部事实观察。
- 短期与长期信号冲突时分别展示，不合并评分。
- 对不适合择时的任务允许 `not_applicable`。

#### 本切片不处理

- 自动下单和盘中高频执行。
- 目标仓位、分批金额和组合风险预算；这些属于切片九。

#### 实施结果

- 新增 `EntryPlan`、act/stage/wait/not_applicable/insufficient 五类状态和定性置信边界；计划分别保存当前状态、进入、等待、分批、不追涨/回避、催化、失效、执行约束和跨周期冲突。
- `ResearchFinding` 可显式记录 timing dimension、profile 输入类型、条件状态、信号时间尺度、有效起止、可观察键、催化窗口和带时区的下一可交易时间；装配器不从自然语言或策略 pass/fail 猜测行动。
- 每个输出条件都保留事实/推断类型、已入账证据、限制、截止日、时间尺度和有效期。未入账引用、unknown 状态、错误 profile 输入和非执行周期信号不能产生可行动计划。
- `EntryPlanService` 以 `SuitabilityAssessment` 为前置：适配结论为 avoid 时返回 not_applicable，为 insufficient 时返回 insufficient，为 watch 时最多返回 wait。
- act 要求全部进入条件满足、无活动等待/回避/失效/冲突，分批规则明确为不适用；stage 要求活动分批规则。任何已确认跨周期冲突均返回 wait，不合并评分。
- 下一可交易候选必须与 profile 和执行周期一致、带时区，并落在所有已输出条件的交集有效窗口内；多个候选选择最早有效项，窗口不相交时返回 insufficient。
- 普通公司、金融机构、周期资源和 ETF 使用不同 timing policy；ETF 额外要求折溢价证据。场外基金当前 `open_fund_timing_execution` 仍不支持，因此显式返回 insufficient，不伪装为盘中可执行计划。
- 结构化对象拒绝目标价、仓位等未知字段，按完整内容生成稳定 ID，并在反序列化时复核状态、section、周期、profile、有效窗口和置信边界。自动下单与仓位计算未接入。

### 切片九：仓位预算与组合适配

状态：**completed**

#### 输入和依赖

- `ResearchDecisionBrief`、`SuitabilityAssessment` 和 `EntryPlan`。
- `research_state` 当前持仓、`portfolio_risk`、最新价格和风险指标。

#### 新增或修改

- `PositionPlan`
- `PortfolioConstraintSnapshot`
- 将 `ResearchDecisionBrief`、`InvestmentMandate`、当前持仓和 `portfolio_risk` 连接。
- 增加压力损失、集中度、相关性、流动性和现金底线约束。

#### 实施步骤

1. 冻结仓位可用、上下文不足和能力不支持状态。
2. 冻结本次决策使用的 mandate、持仓和现金约束快照。
3. 加载当前组合并计算现有权重和重合暴露。
4. 依次应用风险预算、压力损失、集中度、相关性、流动性和现金约束。
5. 输出绑定约束、目标区间和分批调整，不输出自动订单。

#### 验收

- 仓位范围来自可追溯输入和约束，不来自 Agent 固定比例。
- 缺少用户约束、价格或组合数据时正式不可用。
- 已持有和未持有使用不同动作语义。
- 测试覆盖单一约束、多个约束取最小值和压力损失超限。

#### 本切片不处理

- 自动交易、杠杆、做空和复杂均值—方差优化。

#### 实施结果

- 新增 `PositionPlan`，冻结 available/user_context_required/unsupported 三类状态；可用结果包含组合角色、持仓状态、当前权重、目标权重和金额区间、风险预算、压力损失、绑定约束及分批调整，不生成订单。
- `InvestmentMandate` 与 `PortfolioConstraintSnapshot` 已由 `portfolio_risk` 按用户隔离持久化。约束快照绑定同日组合估值、标的、币种、现金、计划资金、压力场景、相关性和流动性输入；被引用的组合估值不可再改写。
- `PositionConstraintCalculator` 依次计算风险预算、单标的集中度、压力损失、组合相关性、流动性、现金底线和计划可用资金上限，最终区间取全部正式上限的最小值；多个相同最小值同时保留为绑定约束。
- 压力约束由最大组合压力损失除以冻结的标的压力跌幅得到，流动性约束由成交额、参与率和执行天数得到；高正相关才触发相关性仓位上限，不把负相关误判为高相关。
- `PositionPlanService` 验证用户、workflow、简报、适配、时点、profile、周期、截止日、mandate、估值和约束快照身份。缺少用户约束、价格/风险输入或持仓状态时返回 user_context_required；场外基金当前返回 unsupported。
- 已持有标的在区间内为 maintain，超过任一上限为 reduce；未持有标的在可行动时为 new_allocation。EntryPlan 为 wait/insufficient 时保留约束区间但不生成执行分批；stage 的金额比例必须来自用户确认的约束快照。
- 价格、波动率、回撤和基准波动率通过 `PositionCalculationInput` 保留标的、截止日、证据与限制。相对收益 mandate 的跟踪误差冲突会正式阻止可用仓位。
- 结构化对象按完整内容生成稳定 ID，反序列化时复核字段类型、状态、绑定约束、持仓动作、分批方向、比例和金额合计；测试覆盖股票、ETF、场外基金、单一约束、多约束取最小值、压力超限、相关性、持仓语义和用户隔离。

### 切片十：最终决策报告、复查与发布

状态：**completed**

#### 输入和依赖

- 基础研究包、完整探索会话及三个决策结果。
- `ReportArtifactService`、证据引用和用户 Markdown 边界。

#### 新增或修改

- `FinalDecisionResearchReport`
- `MonitoringPlan`
- 最终 Markdown renderer、reviewer 和发布服务。
- 展示官方链接、发布日期、页码、关键图表和用户适配边界。

#### 实施步骤

1. 冻结最终报告结构化对象和文件命名规则。
2. 将决策结果、关键证据、反方、失效和未知渲染为用户 Markdown。
3. 增加身份、证据、时点、用户约束和仓位输入 reviewer。
4. 从可观察失效条件装配待确认的监测计划。
5. 复用基础包图表并发布最终报告，不复制内部审计字段。

#### 验收

- 报告完整覆盖本计划第九节。
- 三个决策问题均有答案或正式不可用状态。
- 事实、推断、假设和未知可区分。
- 结论、反方、失效和仓位输入可追溯。
- 最终报告与基础研究包使用不同文件名和角色说明。

#### 本切片不处理

- Web 展示和交互式报告。

#### 实施结果

- 新增 `FinalDecisionResearchReport`、`FinalDecisionSection`、`FinalStatement`、`FinalScenarioBlock` 和 `FinalEvidenceCitation` 冻结契约；最终对象按完整内容生成稳定 ID，反序列化会复核字段、三轨道、三情景、证据覆盖和相对静态路径。
- `FinalDecisionReportAssemblyService` 只消费已完成的基础 handoff、ready 探索会话、用户决策简报以及已验证的 `SuitabilityAssessment`、`EntryPlan`、`PositionPlan`。用户、workflow、标的、profile、截止日、期限和上游 ID 任一不一致都会拒绝装配。
- `FinalDecisionReportRenderer` 固定渲染十个用户章节，分别回答是否、何时和多少；available 仓位只显示区间、压力损失、绑定约束和规划分批，不把区间上限改写为点位或订单。
- `FinalDecisionReportReviewer` 在结构化对象和 Markdown 两阶段检查身份链、基础包、三轨道、反方、失效条件、用户约束、仓位输入、证据日期/链接/派生方法、图表相对路径和内部字段泄漏。
- 基础 handoff 保存基础 Markdown digest 以及每张已发布图表、同源数据、metadata 的相对路径与 digest。`FinalDecisionReportService` 只有在这些摘要全部一致、图表 metadata 绑定同一标的和截止日、两阶段复查都通过后，才使用受限发布入口写入 `*-完整决策报告-YYYY-MM-DD-<运行指纹>.md`；通用发布入口拒绝最终报告文件名。
- 探索结论及价格/风险计算引用必须先存在于探索账本，并与 primary/secondary/derived/user_context 来源层级一致；仓位计算后新增的 mandate、组合和约束引用只允许标为用户上下文，最终装配器按简报用户重新加载这些持久化对象并复核标的、截止日、币种和估值 digest。正文事实、推断、情景影响和监测条件逐项标明证据序号，不只在文末堆放来源。
- 运行指纹按用户与 workflow 内容派生，避免同标的同日跨用户覆盖；会话用户必须与决策简报用户一致，基础包继续使用独立文件名。
- `MonitoringPlan` 已从时点失效条件和论文证伪条件装配为待用户确认的机器观察候选与人工复查项；本切片不注册提醒规则，`alert_rule_refs` 必须为空。

### 切片十一：失效条件与监测交接

状态：**completed**

#### 输入和依赖

- 已通过 reviewer 的最终报告和 `MonitoringPlan`。
- `src/alerts/` 公共规则服务、用户确认状态和本地研究状态。

#### 新增或修改

- 失效条件到价格、事件、日期或权重规则的显式适配器。
- workflow run 保存监测启用状态和规则引用。
- 无法机器观察的条件保留人工复查语义。

#### 实施步骤

1. 冻结可机器观察、需人工复查和当前不支持三类条件。
2. 仅将可观察条件转换为待确认规则，不让 Agent 自创内部 Provider 调用。
3. 用户确认后通过 alerts 公共 service 注册，并保存规则引用。
4. 在复查日期或条件触发后关联回原 workflow run 和论文。

#### 验收

- 未经用户确认不启用监测。
- 每条规则都可回溯到最终报告的论文失效或风险条件。
- 不能转换的条件明确标记为人工复查，不静默丢失。
- 本地规则注册失败不改变已发布报告结论，并形成明确 gap。

#### 实施结果

- 新增 `MonitoringHandoff`、条件 disposition、规则候选、用户确认、规则注册、失败 gap 和 armed/partial/manual/unsupported 状态契约；handoff 使用最终报告与监测计划生成稳定 ID，并按用户、workflow 和报告绑定。
- 最终报告服务在发布后持久化 `FinalDecisionPublicationAttestation`，绑定用户、workflow、探索会话、简报、基础 handoff、规范化监测计划、标的、截止日、文件路径和 Markdown digest。`MonitoringHandoffService.prepare(...)` 重新加载并复核该证明，不接受仅由调用方声称“已发布/已通过”的对象。
- 机器条件只有在显式 `MonitoringRuleBinding` 与受支持观察键一致时才生成价格、事件或组合权重候选；候选标的必须与最终报告一致，报告复查日期生成日期候选，不从自然语言猜阈值、事件类型或内部 Provider。
- 准备阶段不创建提醒规则。`confirm_and_activate(...)` 只注册用户明确选择的候选；支持部分确认、幂等重试和失败 gap。人工复查及无法适配条件继续保存在 handoff，不静默丢失。
- `AlertService` 新增显式日期规则和研究规则来源对象。监测规则及触发提醒携带 workflow run、最终报告、监测计划、原条件来源和证据引用，可回到原论文或风险条件复查。
- `ResearchWorkflowRun` 的用户归属只从已持久化且身份链一致的探索会话建立，并保存最终报告、监测 handoff、启用状态、实际规则引用和 gap 代码；SQLite 可在重启后恢复。确认先写入 `activation_pending`，研究规则只有在 handoff 与 workflow 同时记录授权后才能触发；分步写入中断时规则保持惰性，重试会对账。规则注册失败不修改不可变的已发布报告或其中的 `MonitoringPlan`。
- 默认容器公开 `container.alerts` 与 `container.monitoring`；能力注册表登记经用户确认的本地监测交接。当前仍无后台调度、实时监听、外部通知和自动交易。

#### 本切片不处理

- 外部通知渠道、后台调度平台和自动交易。

### 切片十二：代表性资产与新对话验收

状态：implementation_complete（独立新对话人工复核随总体 review 延后）

#### 输入和依赖

- 前十一个切片的公共入口、状态机、决策结果和 Agent 指引。
- 五类 profile 的真实数据库、Provider 和主 smoke。

#### 代表性场景

1. 只输入 `600519`，无用户约束。
2. 输入 `002236` 并询问是否适合长期配置。
3. 输入 `518880` 并询问未来1至6个月的机会。
4. 已持有金融机构，要求继续持有、增持或减持判断。
5. 周期资源标的，要求判断周期位置和仓位。
6. 同代码存在资产类型歧义。
7. Provider 远程失败、业务无数据和基础执行 blocked。

#### 实施步骤

1. 冻结场景输入、用户上下文、预期行为和允许缺口。
2. 运行公共高层入口、基础包、探索和最终报告。
3. 执行新对话人工复核与可自动化的结构断言。
4. 将发现的 profile 能力阻塞拆为独立短期计划，修复后重跑。

#### 验收

- 统一入口完成身份、基础包和 handoff。
- Agent 只询问影响结论的最小问题。
- 每个场景至少完成一轮支持证据和一轮反证验证。
- 缺少用户约束时不给伪仓位。
- 最终 Markdown 可独立阅读并通过程序化 reviewer。
- 新对话不依赖历史聊天即可按 `.agent` 完成流程。
- 全量单元测试及各 profile 主 smoke 通过。

#### 本切片不处理

- 自动评价模型文字风格或承诺收益准确率。

#### 当前实施结果

- 新增 `slice12_v1` 冻结场景契约，覆盖五类 profile、只给代码、长期基金配置、一至六个月 ETF、已持有金融机构、周期资源仓位、同代码歧义，以及远程失败、业务无数据和基础 blocked。
- `container.new_conversation_acceptance` 通过公共 `ResearchRequestPlanner` 重跑提示输入，并把实际基础执行、workflow/handoff、探索、查询、适配、时点、仓位和最终发布对象归一为观察记录。
- 程序化断言要求身份链一致、最小用户问题、至少一轮支持证据和一轮反证、缺约束时无伪仓位、三个最终决策章节、独立 Markdown 及三类失败语义隔离。
- 场外基金主 smoke 支持先经公共规划器 bootstrap 身份，也支持用 SQLite backup 克隆已有事实库到临时数据库，避免验证过程修改用户数据库。
- 修复了含圆括号基金名称的 Markdown 图表路径在基础 handoff manifest 中被截断的问题；ETF 主 smoke 改用当天截止日，避免把实时市场快照错误送入历史截止日任务。
- 自动验证已通过全量 668 项单元测试，以及普通公司、金融机构、周期资源、ETF 和场外基金五类正式主 smoke。独立新对话的人工文字与行为复核按“总体 review 放到后面”的决定保留，不把构造的观察记录冒充人工验收。

## 十二、代表性验收问题

Agent 指引和流程必须稳定处理：

1. “研究一下 600519。”
2. “002236 现在值得买吗？”
3. “我已经持有 163417，还该继续拿吗？”
4. “黄金 ETF 最近能不能买，什么时候买？”
5. “这个标的适合占我组合多少？”
6. “数据不全，你先给个大概仓位。”
7. “历史收益很好，是不是可以买？”
8. “策略显示 pass，是否应该立即买入？”

正确行为包括：

- 不把历史收益、策略 pass 或单一指标直接转成买入建议。
- 不因用户催促而用固定比例代替仓位约束。
- 不在底层 blocked 后手工拼数据绕过门禁。
- 不把理解型基础包当作最终研究。
- 能明确给出继续研究、等待、回避或无法判断。

## 十三、主要风险与控制

1. **Agent 指引过长**
   - 入口保持短；专题文档按任务选择性读取。
2. **指引与代码能力不一致**
   - 使用能力注册表和存在性测试，不在 Markdown 复制实现列表。
3. **为了行动而强制结论**
   - 支持 `insufficient`、`watch` 和 `user_context_required`。
4. **用户上下文污染共享研究事实**
   - 决策简报与基础任务分离，用户对象统一带 `user_id`。
5. **LLM 承担关键金融计算**
   - 估值、情景、风险和仓位计算使用项目正式服务。
6. **探索流程变成固定清单**
   - 以决策影响和不确定性确定下一问题，只设置必要门禁。
7. **报告看似完整但不可执行**
   - 触发条件、仓位约束和失效条件必须可观察。
8. **过早扩展产品层**
   - 本阶段不推进 Web、认证、通知、自动交易或通用 AI harness。

## 十四、依赖顺序

```text
切片一：用户决策与报告契约
    ↓
切片二：用户决策简报
    ↓
切片三：高层任务路由
    ↓
切片四：能力注册表与 handoff
    ↓
切片五：探索与证据持久化
    ↓
切片六：缺口驱动查询
    ↓
切片七：是否购买
    ↓
切片八：何时购买
    ↓
切片九：购买多少
    ↓
切片十：最终报告
    ↓
切片十一：失效条件与监测交接
    ↓
切片十二：新对话端到端验收
```

各 profile 的数据和分析缺口在切片四形成能力矩阵后，按阻塞代表性验收的优先级另建独立短期计划，不与工作流状态机一次混做。

## 十五、进度 Tracking

任务：Agent 决策研究闭环

- [x] 切片一——冻结用户决策模型与 Agent 报告契约
- [x] 切片二——用户决策简报与研究上下文
- [x] 切片三——代码到正式研究任务的高层路由
- [x] 切片四——能力注册表与基础包 handoff
- [x] 切片五——探索会话、证据账本与持久化
- [x] 切片六——面向缺口的查询计划与项目能力调用
- [x] 切片七——是否购买与替代方案评估
- [x] 切片八——时点与分批执行计划
- [x] 切片九——仓位预算与组合适配
- [x] 切片十——最终决策报告、复查与发布
- [x] 切片十一——失效条件与监测交接
- [ ] 切片十二——代表性资产与新对话验收（开发与自动验证完成；独立新对话人工复核随总体 review 延后）
