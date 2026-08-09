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
- `docs/modules/README.md`: 当前模块顺序和当前短期开发计划入口。
- `docs/当前阶段-IDE投研核心.md`: 当前阶段的短期计划顺序、依赖和唯一 active 计划。
- `.agent/docs/research/mainline.md`: 所有真实研究的唯一主线。
- `.agent/docs/research/agent_charter.md`: Research Agent 的分析职责与禁止事项。
- `.agent/docs/research/research_strategy_playbook.md`: 不同研究任务的假设、证据、探索和停止策略。
- `.agent/docs/research/discovery_handoff.md`: 发现结果交给 Agent 后的判断边界。

## Quick Routing

| User intent | Read first | Escalate when |
| --- | --- | --- |
| Project layout or file placement | `.agent/docs/develop/development_rule.md` | Ask if a new directory responsibility is ambiguous. |
| Development progress or plan tracking | `.agent/docs/develop/development_report_rule.md` | Also read the plan being updated. |
| Data source or database work | `docs/modules/README.md` and the owning module doc | Inspect `src/data_src/`, `src/data_store/`, and the owning module under `src/tests/`. |
| Unit tests or CI | `.agent/docs/develop/development_rule.md` | Inspect `src/tests/run_all_unit_tests.py`, `src/tests/<module>/`, and `.github/workflows/`. |
| Agent rule changes | `.agent/SKILL.md` | Inspect `.agent/AGENT_README.md` and the owning `.agent/docs/` file. |
| 单标的完整研究 | `.agent/docs/research/mainline.md` | 调用统一编排入口；禁止手工串服务。 |
| 研究数据、指标、图表或策略 | `.agent/docs/research/mainline.md` | 由编排器执行；Agent只解释结果与缺口。 |
| 新策略或策略扩展 | `docs/evidence/strategy_sources/README.md` | 先审阅机制、实现和反证；复用代码时再核验许可证。 |
| 标的发现 | `.agent/docs/research/discovery_handoff.md` | 只分析编排器返回的候选和理由。 |
| 研究推演与用户结论 | `.agent/docs/research/agent_charter.md` | 区分事实、推断、情景、反方和行动。 |
| 研究任务类型选择 | `.agent/docs/research/research_strategy_playbook.md` | 任务在理解、比较、投资、择时、事件、发现、市场或复核间切换时重新路由。 |

## Working Rules

- Prefer project docs, unit tests, and existing provider/store/service patterns before inventing new interfaces.
- Read source when implementing behavior, docs are stale, or tests define the real contract.
- If a choice changes future project behavior, ask before changing rules, schema, provider semantics, dependencies, CI, or persistent data layout.
- Prefer the smallest useful change. Do not add speculative providers, compatibility layers, placeholder credentials, or unrelated schema fields.
- Research 的确定流程必须通过公共编排服务或 CLI；缺入口时修复入口，不允许 Agent 临时手工拼装。

## Common Development Request

1. Read `docs/modules/README.md`, then the current relevant development plan.
2. Pick the next incomplete checklist item or ask the user if multiple next steps have comparable priority.
3. Implement the smallest useful slice.
4. Add or update unit tests under `src/tests/<module>/unit_tests/`.
5. Run `python src/tests/run_all_unit_tests.py` unless the change is docs-only.
6. Use `.agent/docs/develop/development_report_rule.md` for the final development progress checklist.

## Default Boundary

Do not scan or edit `references/` unless the user asks for reference analysis or the task needs external project context. Do not write runtime data, logs, databases, or temporary files outside their assigned directories.
