# 单标的研究主线

标的类问题（评价一个标的、要不要买、何时、买多少）的标准流程，由 `question_routing.md` 分流到这里。三个决策（是否、何时、多少）分开回答，不合成总分；不能回答时说明阻塞，不伪造精确结论。方法读 `agent_charter.md` 和 `user_decision_model.md`。

## 一、基础研究

1. 新对话只给代码或名称：先按 `cold_start_intake.md` 做最小澄清。
2. 请求：`subject_type=asset`，`intents=["evaluate"]`（要决定时加 `"decide"`），带 `user_id`（会保存决策简报）。`route` → `run-recipe`。
3. 看基础步骤的结果：
   - `complete` / `partial`：从 artifacts 取 `workflow_run_id`、`decision_brief_id`，进入第二节；partial 先判断缺口阻塞哪个决策轨道。
   - `blocked` / `needs_input`：照实转述原因，按 `next_actions` 处理（如周期资源公司改用 `run-single`，见 `run-single --help`）；不手工补算。
4. 读基础包：profile 特有的读法见 `run-recipe --help`；profile 判错或要手工指定时读 `profile_routing.md`；能力边界按 `project_capability_map.md` 查；不同研究任务的取舍读 `research_strategy_playbook.md`。

## 二、探索

5. 读 `exploration_playbook.md`（假设、反证、轮次和门禁），写调用脚本按 `exploration_api.md`。
6. 建会话：`container.exploration.start_from_handoff(...)`，传入该 workflow run 的决策简报。
7. 每一轮：为开放问题建 `ResearchQueryPlan` → `container.queries.execute(...)` → 记一轮（结构化发现、证据、假设影响、下一问题）。
8. 只在范围、目标、约束或用户选择会改变结论时问用户（`user_context_questions.md`）。
9. 三轨问题闭环后先在内存里装配三轨看 `assembly_gaps`，无误再 `mark_ready`。

## 三、装配与发布

10. 是否：`container.suitability.assemble(...)`；何时：`container.entry.assemble(...)`；多少：先由 `container.portfolio_risk` 保存或读取 mandate、组合估值和约束快照，再 `container.position.assemble(...)`。
11. 写报告叙事输入（三情景、证据引用、复查日），按 `final_report_contract.md`。
12. `container.final_decision.assemble_and_publish(...)`；持仓复核还要传 `exit_plan`。复查不通过不发布，按复查结果修正。
13. 发布后三轨判断自动入账；入账状态为 `pending_replay` 时运行 `replay-ledger`。
14. 用户要后续监测：`container.monitoring.prepare(...)` → 用户确认具体候选 → `confirm_and_activate(...)`。
15. 买入或继续持有的结论：把论点写成 InvestmentThesis，`record-thesis` 入账。

## 四、标的发现

发现结果由 discovery 编排返回，按 `discovery_handoff.md` 判断；名称/谐音专项读 `event_name_discovery.md`。

## 禁止回退

统一入口缺失或失败时记录为工程阻塞并修复入口；不手工串 service、不复制 smoke、不直接查 SQLite 或 Provider，不用 prompt 或网页资料伪造确定性基础结果。修改研究主线后按 `agent_acceptance.md` 验收。
