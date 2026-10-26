# 单标的研究主线

## 适用范围

本文是标的类问题（评价一个标的、决定买卖或复核持仓）的研究主线。所有研究问题先按 `question_routing.md` 构造结构化请求并路由；路由到标的配方（`asset_decision`、`holding_review`、`asset_understanding`）时进入本主线。

## 用户目标

单标的完整研究最终服务于三个独立问题：是否值得买或持有、何时行动、购买或持有多少。先读 `agent_charter.md` 和 `user_decision_model.md`；不把三者合成总分，也不强制每次都给行动建议。

## 场景读取

1. 新对话只给代码、名称或“研究一下”：读 `cold_start_intake.md`。
2. 构造基础任务：读 `profile_routing.md` 和 `project_capability_map.md`。
3. 选择理解、比较、投资、择时、事件、发现、市场或复核策略：读 `research_strategy_playbook.md`。
4. 基础包完成后：读 `exploration_playbook.md`；写调用脚本前读 `exploration_api.md`（探索到最终发布的公共调用与字段规则，不必读源码）。
5. 需要用户约束：读 `user_context_questions.md`。
6. 准备最终报告：读 `final_report_contract.md`。
7. 验收新对话行为：读 `agent_acceptance.md`。

## 确定性基础流程

身份解析、任务创建、profile 分发、数据获取、质量检查、基础指标、静态图表、规则计算、基础复查和基础包落盘必须由 `src/research_orchestration/` 公共入口完成。

Agent 不得手工拼装这些服务、复制 smoke 依赖注入、直接查询内部 SQLite、逐项重建 Provider 调用或自行安排产物路径。

首选入口是配方执行：

```text
python -m src.research_orchestration.cli run-recipe --request-json <请求文件>
```

标的配方先用 `ResearchRequestPlanner` 解析身份和 profile（本地没有时自动补登身份、按行业分配 profile），请求带 `user_id` 时按请求保存决策简报（意图、轨道、期限；缺少的用户上下文列为缺项），规划 ready 后自动用规划出的任务运行基础流水线，返回 workflow run、handoff、决策简报、基础包路径和下一步；规划未就绪、缺少比较基准或基础研究阻断时停止并返回原因。周期资源公司需要商品权重，配方会要求改用下方的 `run-single`。金融、周期类公司的行业基准尚未建立，暂以沪深 300 为比较基准，最终报告须说明。

高层任务规划 CLI（只规划，不执行）：

```text
python -m src.research_orchestration.cli plan-single
  --symbol <代码>
  --decision-intent <标准化目标，可选>
  --decision-lane <决策轨道，可选、可重复>
```

代码唯一时直接返回身份、profile 和基础任务草稿；歧义时返回候选供用户选择。该命令不执行研究。

基础执行 CLI：

```text
python -m src.research_orchestration.cli run-single
  --symbol <代码>
  --asset-type <资产类型>
  --exchange <交易所，可选>
  --profile <profile_id>
  --question <question>
  --horizon <horizon>
  --as-of-date <YYYY-MM-DD>
```

也可使用 `--asset-id`。默认容器已装配规划器和五类正式执行服务。`run-single` 生成基础研究包、workflow run 和可继续探索的 handoff，但不生成决策简报、最终决策报告或探索会话；用它的基础包做决策研究时，先按 `exploration_api.md` 保存决策简报再启动探索。`plan-single` 本身不执行研究；一步完成规划到基础研究的是 `run-recipe`，两者都不是完整研究。

## 单标的状态机

1. **冷启动**：识别标的和入口意图；只询问影响身份或任务的最小问题。
2. **任务路由**：按 `question_routing.md` 路由到标的配方；配方调用 `ResearchRequestPlanner`，分开用户决策目标和 profile 支持的基础收集任务。
3. **基础执行**：`run-recipe` 在规划 ready 后调用统一编排入口；能力解释以 `ResearchCapabilityRegistry` 和路由结果中的步骤状态为准。
4. **状态处理**：
   - complete：从 `BaselineResearchHandoff` 进入探索；
   - partial：从 handoff 分开读取非阻塞缺口与能力缺口，判断它们阻塞哪个决策轨道；
   - blocked：workflow run 保留阻塞代码且没有 handoff；停止 Agent 补算，修复底层能力或正式结束。
5. **探索初始化**：调用 `container.exploration.start_from_handoff(...)` 并传入该 workflow run 的决策简报，从 handoff 的事实章节、候选问题和候选假设建立持久化会话；候选项不是固定清单。会话启动后不能换绑简报，没有简报的会话无法发布最终报告。
6. **多轮验证**：先为开放问题建立 `ResearchQueryPlan`，通过 `container.queries.execute(...)` 调用已声明的项目公共能力或登记外部官方来源；再通过持久化探索服务追加目标、结构化发现、证据、假设影响和下一问题，不得覆盖旧状态历史。
7. **是否购买装配**：适配轨道关键问题闭环后，通过 `container.suitability.assemble(...)` 把 Agent 已记录的 profile 输入、定价预期、下行、同风险替代方案、机会成本、主论文和最强反方投影为结构化结论；不得用假设、未入账引用或其他轨道论文补门禁。
8. **时点装配**：时点轨道关键问题闭环后，通过 `container.entry.assemble(...)` 把 Agent 已记录的状态、进入、等待、分批、回避、催化、失效、交易日历、执行约束和跨周期冲突投影为结构化计划；每项必须有可观察键和有效窗口，不得输出底部预测、目标价或仓位。
9. **仓位装配**：仓位轨道关键问题和用户约束闭环后，先由 `container.portfolio_risk` 保存或读取用户所属 mandate、同日组合估值和约束快照，再通过 `container.position.assemble(...)` 连接适配、时点、价格与风险输入；只输出正式约束计算所得区间、绑定约束和分批金额，不给固定比例或订单。
10. **用户交互**：只在范围、目标、约束或用户选择会改变结论时询问。
11. **报告叙事输入**：Agent 按 `final_report_contract.md` 提供三情景、证据标题/来源/发布日期/可选页码、额外未知、复查日期、需要复用的基础图表相对路径，以及可选的发布前概率区间；不得在此重算三个决策。
12. **报告门禁**：通过 `container.final_decision.assemble_and_publish(...)` 连接 handoff、ready 会话、决策简报和三个决策对象。结构、基础静态产物和用户 Markdown 任一复查失败都不发布。
13. **最终发布**：通过复查后生成“标的名-完整决策报告-YYYY-MM-DD-运行指纹.md”；运行指纹用于避免同标的同日跨用户覆盖，不包含原始用户 ID。基础包仍保持独立文件和角色，不复制内部 ID、digest 或图表 metadata。发布后三轨判断自动记入观点账本；发布结果的入账状态为 `pending_replay` 时运行 `replay-ledger` 补录，到期后由 `settle-ledger` 前向结算。
14. **监测交接**：如用户需要后续监测，先通过 `container.monitoring.prepare(...)` 把最终报告条件显式适配为价格、事件、日期或组合权重候选；该步骤不创建规则。仅在用户确认具体候选后调用 `confirm_and_activate(...)`。无法适配的条件和人工复查项必须保留，不得从自然语言猜阈值或绕过 alerts 公共服务。
15. **代表性验收**：开发或修改研究主线后，按 `agent_acceptance.md` 使用 `slice12_v1` 冻结场景和 `container.new_conversation_acceptance` 检查公共规划、上下文身份链、支持/反证轮次、三个决策对象、最终 Markdown 及失败语义。其类型为 `NewConversationAcceptanceRunner`；runner 只检查真实传入的公开输出，不替 Agent 生成研究叙事。问题层另用 `vision_q_v1`（`vision-acceptance` CLI）检查路由与覆盖。

workflow run、基础 handoff、探索快照、追加式账本和监测 handoff 均已持久化，可按用户和 workflow run 恢复。显式问题查询已支持项目公共 service 路由、外部官方来源登记和结果分类；适配、时点、仓位、最终决策报告、经用户确认的本地监测和代表性新对话结构验收均有公共入口。当前没有后台调度或外部通知，提醒只在显式传入价格、事件、日期或组合快照观察时评估；系统也不会从 handoff 缺口静默执行固定查询清单。

## 标的发现

发现流程由 discovery 编排入口运行。Agent只接收带入选、淘汰、数据缺口和理由的候选结果，按 `discovery_handoff.md` 做优先级判断。名称/谐音事件专项另读 `event_name_discovery.md`。

## 禁止回退

统一入口缺失或失败时，将其记录为工程阻塞并修复公共入口；不得退回手工 service-by-service 流程，不得用 prompt 或网页资料伪造确定性基础结果。
