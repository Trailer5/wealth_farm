# Agent Docs

`.agent/AGENT_README.md` 是每轮唯一固定必读，`.agent/SKILL.md` 负责场景路由。本目录按职责分三层：

## common

- `common/working_principles.md`：跨开发和研究场景的协作、事实、时间与工具边界。

## develop

- `develop/development_rule.md`：项目组织、代码、测试、数据和版本规则。
- `develop/development_report_rule.md`：短期计划、tracking 和开发汇报。
- `develop/architecture.md`：分层、依赖方向、模块归属和新能力放置。

## research

- `research/question_routing.md`：研究问题的标准流程、任务流程表、按路由状态行动和回答纪律。
- `research/playbooks/`：市场与宏观（`research/playbooks/market_macro.md`）、行业与主题（`research/playbooks/sector.md`）、持仓复核（`research/playbooks/holding.md`）、组合与计划（`research/playbooks/portfolio.md`）、策略（`research/playbooks/strategy.md`）的步骤清单。
- `research/mainline.md`：标的类问题的统一研究编排入口和停止条件。
- `research/cold_start_intake.md`：新对话代码/名称输入和最小澄清。
- `research/agent_charter.md`：Agent 的分析、推演和用户决策职责。
- `research/user_decision_model.md`：是否、时点和仓位三个决策轨道。
- `research/user_context_questions.md`：按决策影响选择最小用户问题。
- `research/profile_routing.md`：资产、profile、问题和时间尺度路由。
- `research/project_capability_map.md`：怎样查当前能力与缺口。
- `research/research_strategy_playbook.md`：按事实、比较、投资、择时、事件、发现、市场和复核任务选择不同研究策略。
- `research/exploration_playbook.md`：假设、证据、反证、轮次和出报告门禁。
- `research/exploration_api.md`：探索、三轨装配和最终发布的调用步骤。
- `research/final_report_contract.md`：最终用户报告章节、证据和行动边界。
- `research/agent_acceptance.md`：代表性新对话行为和文档准入。
- `research/discovery_handoff.md`：发现候选交给 Agent 后的判断边界。
- `research/event_name_discovery.md`：重大事件名称、谐音和错配专项。

## 读取原则

1. 先读最小相关目录。
2. 开发任务不加载 research 全套。
3. 研究任务先读 `research/question_routing.md` 路由；标的类问题从 `research/mainline.md` 开始。
4. 确定流程由代码编排，Agent 不手工串联服务。
5. 只按当前场景读取专题文件，不一次加载 research 全套。
6. 指引只给流程和动作；字段、口径和读法看命令的 `--help`、输出和文档字符串。
