---
name: wealth-farm-agent-reference
description: "Use for Wealth Farm development and IDE investment research: task routing, data, evidence, metrics, charts, comparisons, strategies, discovery, reports, tests, and project rules."
---

# Wealth Farm Agent Index

Use this file to choose the smallest relevant project guidance. Do not read every `.agent/docs/` file by default.

## Required First Reads

1. Read `.agent/AGENT_README.md` every turn.
2. Read this file when starting a new task, recovering context, or routing is unclear.
3. Read `.agent/docs/README.md` only when common/develop/research ownership is unclear.

## Selective Reads

- `.agent/docs/common/working_principles.md`: cross-scenario facts, time, collaboration, and tool boundaries.
- `.agent/docs/develop/development_rule.md`: code, tests, directories, docs, data, logs, references, temp files, or dependencies.
- `.agent/docs/develop/development_report_rule.md`: development plans, progress tracking, or development summaries.
- `.agent/docs/develop/architecture.md`: layer map, dependency direction, module ownership, and where new capabilities go.
- `docs/long_term/长期愿景.md`: 项目定位、代表性问题验收集、组织模型和研究纪律。
- `docs/long_term/发展路线图.md`: 阶段顺序、各层能力需求和长期 tracking。
- `docs/long_term/系统模块总图.md`: 系统模块拆解、关联拓扑、代码模块归属和已知依赖偏离。
- `docs/modules/README.md`: 当前模块顺序和当前短期开发计划入口。
- `docs/当前阶段-Agent决策研究闭环.md`: 单标的 Agent 决策研究闭环的阶段计划；切片十二人工复核并入路线图阶段 4 验收。
- `docs/当前阶段-智囊骨架.md`: 阶段 4 计划：共享契约、观点账本与结算、问题路由与配方、代表性问题验收。
- `.agent/docs/research/question_routing.md`: 研究问题的标准流程：写请求 → `route` → `run-recipe` → 按状态行动 → 进入任务流程，以及回答纪律。
- `.agent/docs/research/playbooks/`: 市场与宏观、行业与主题、持仓复核、组合与计划的步骤清单。
- `.agent/docs/research/mainline.md`: 标的类问题的研究主线。
- `.agent/docs/research/cold_start_intake.md`: 新对话只给代码、名称或模糊研究请求时的最小动作。
- `.agent/docs/research/agent_charter.md`: Research Agent 的分析职责与禁止事项。
- `.agent/docs/research/user_decision_model.md`: 是否、时点和仓位三个顶层用户决策。
- `.agent/docs/research/profile_routing.md`: 资产、profile、问题和时间尺度路由。
- `.agent/docs/research/project_capability_map.md`: 怎样查当前能力与缺口（不维护清单）。
- `.agent/docs/research/research_strategy_playbook.md`: 不同研究任务的假设、证据、探索和停止策略。
- `.agent/docs/research/exploration_playbook.md`: 多轮假设、反证和 ready 门禁。
- `.agent/docs/research/exploration_api.md`: 探索到最终发布的调用步骤（字段规则在文档字符串里）。
- `.agent/docs/research/user_context_questions.md`: 只询问影响结论的最小用户约束。
- `.agent/docs/research/final_report_contract.md`: 最终用户决策报告的章节和证据门槛。
- `.agent/docs/research/agent_acceptance.md`: 新对话行为与文档准入场景。
- `.agent/docs/research/discovery_handoff.md`: 发现结果交给 Agent 后的判断边界。
- `.agent/docs/research/event_name_discovery.md`: 事件名称/谐音注意力专项。

## Quick Routing

| User intent | Read first | Escalate when |
| --- | --- | --- |
| Project layout or file placement | `.agent/docs/develop/development_rule.md` and `.agent/docs/develop/architecture.md` | Then `docs/long_term/系统模块总图.md` for module ownership; ask if a new directory or layer responsibility is ambiguous. |
| Project direction, vision, or stage planning | `docs/long_term/长期愿景.md` | Then `docs/long_term/发展路线图.md`; ask before changing core contracts or boundaries. |
| Development progress or plan tracking | `.agent/docs/develop/development_report_rule.md` | Also read the plan being updated. |
| Data source or database work | `docs/modules/README.md` and the owning module doc | Inspect `src/data_src/`, `src/data_store/`, and the owning module under `src/tests/`. |
| Unit tests or CI | `.agent/docs/develop/development_rule.md` | Inspect `src/tests/run_all_unit_tests.py`, `src/tests/<module>/`, and `.github/workflows/`. |
| Agent rule changes | `.agent/SKILL.md` | Inspect `.agent/AGENT_README.md` and the owning `.agent/docs/` file. |
| 任意研究问题 | `.agent/docs/research/question_routing.md` | 按其标准流程和任务流程表走；用到的命令先看 `--help`，按输出的 `next_actions` 继续。 |
| 例行维护 | `routine --user-id <id>` | 按输出的 `next_actions` 继续。 |
| 新对话只给代码或名称 | `.agent/docs/research/cold_start_intake.md` | 身份、目标或当前公共入口存在实质歧义时只问最小问题。 |
| 单标的完整研究 | `.agent/docs/research/mainline.md` | 按是否、时点、仓位三个轨道推进；调用统一编排入口。 |
| 标的研究中的数据、指标、图表或策略 | `.agent/docs/research/mainline.md` | 由编排器执行；Agent只解释结果与缺口。 |
| 新策略或策略扩展 | `docs/evidence/strategy_sources/README.md` | 先审阅机制、实现和反证；复用代码时再核验许可证。 |
| 标的发现 | `.agent/docs/research/discovery_handoff.md` | 只分析编排器返回的候选和理由。 |
| 研究推演与用户结论 | `.agent/docs/research/agent_charter.md` | 区分事实、推断、情景、反方和行动。 |
| 研究任务类型选择 | `.agent/docs/research/research_strategy_playbook.md` | 任务在理解、比较、投资、择时、事件、发现、市场或复核间切换时重新路由。 |
| 事件名称、谐音或错配 | `.agent/docs/research/event_name_discovery.md` | 必须先通过事件注意力门禁并使用正式发现编排。 |
| 最终决策报告 | `.agent/docs/research/final_report_contract.md` | 基础包和探索未闭环时不得发布。 |

## Working Rules

- Prefer project docs, unit tests, and existing provider/store/service patterns before inventing new interfaces.
- Read source when implementing behavior, docs are stale, or tests define the real contract.
- If a choice changes future project behavior, ask before changing rules, schema, provider semantics, dependencies, CI, or persistent data layout.
- Prefer the smallest useful change. Do not add speculative providers, compatibility layers, placeholder credentials, or unrelated schema fields.
- Research 的确定流程必须通过公共编排服务或 CLI；缺入口时修复入口，不允许 Agent 临时手工拼装。
- Agent 指引只给流程和动作；字段、口径和读法在 CLI `--help`、命令输出和文档字符串里（规则见 `.agent/docs/develop/development_rule.md`“Agent 指引”）。

## Common Development Request

1. Read `docs/modules/README.md`, then the current relevant development plan.
2. Confirm which representative questions in `docs/long_term/长期愿景.md` the change serves, which system module in `docs/long_term/系统模块总图.md` it touches, and which layer in `.agent/docs/develop/architecture.md` it belongs to.
3. Pick the next incomplete checklist item or ask the user if multiple next steps have comparable priority.
4. Implement the smallest useful slice.
5. Add or update unit tests under `src/tests/<module>/unit_tests/`.
6. Run `python src/tests/run_all_unit_tests.py` unless the change is docs-only.
7. Use `.agent/docs/develop/development_report_rule.md` for the final development progress checklist.

## Default Boundary

Do not scan or edit `references/` unless the user asks for reference analysis or the task needs external project context. Do not write runtime data, logs, databases, or temporary files outside their assigned directories.
