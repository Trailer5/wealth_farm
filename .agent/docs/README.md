# Agent Docs

`.agent/AGENT_README.md` 是每轮唯一固定必读，`.agent/SKILL.md` 负责场景路由。本目录按职责分三层：

## common

- `common/working_principles.md`：跨开发和研究场景的协作、事实、时间与工具边界。

## develop

- `develop/development_rule.md`：项目组织、代码、测试、数据和版本规则。
- `develop/development_report_rule.md`：短期计划、tracking 和开发汇报。

## research

- `research/mainline.md`：统一研究编排入口和停止条件。
- `research/cold_start_intake.md`：新对话代码/名称输入和最小澄清。
- `research/agent_charter.md`：Agent 的分析、推演和用户决策职责。
- `research/user_decision_model.md`：是否、时点和仓位三个决策轨道。
- `research/user_context_questions.md`：按决策影响选择最小用户问题。
- `research/profile_routing.md`：资产、profile、问题和时间尺度路由。
- `research/project_capability_map.md`：当前稳定公共能力与明确缺口。
- `research/research_strategy_playbook.md`：按事实、比较、投资、择时、事件、发现、市场和复核任务选择不同研究策略。
- `research/exploration_playbook.md`：假设、证据、反证、轮次和出报告门禁。
- `research/final_report_contract.md`：最终用户报告章节、证据和行动边界。
- `research/agent_acceptance.md`：代表性新对话行为和文档准入。
- `research/discovery_handoff.md`：发现候选交给 Agent 后的判断边界。
- `research/event_name_discovery.md`：重大事件名称、谐音和错配专项。

## 读取原则

1. 先读最小相关目录。
2. 开发任务不加载 research 全套。
3. 研究任务从 `research/mainline.md` 开始。
4. 确定流程由代码编排，Agent 不手工串联服务。
5. 只按当前场景读取专题文件，不一次加载 research 全套。
