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

## Quick Routing

| User intent | Read first | Escalate when |
| --- | --- | --- |
| Project layout or file placement | `.agent/docs/develop/development_rule.md` | Ask if a new directory responsibility is ambiguous. |
| Development progress or plan tracking | `.agent/docs/develop/development_report_rule.md` | Also read the plan being updated. |
| Data source or database work | `docs/modules/README.md` and the owning module doc | Inspect `src/data_src/`, `src/data_store/`, and the owning module under `src/tests/`. |
| Unit tests or CI | `.agent/docs/develop/development_rule.md` | Inspect `src/tests/run_all_unit_tests.py`, `src/tests/<module>/`, and `.github/workflows/`. |
| Agent rule changes | `.agent/SKILL.md` | Inspect `.agent/AGENT_README.md` and the owning `.agent/docs/` file. |
| 单标的完整研究 | `.agent/docs/research/mainline.md` | 再读 routing、execution 和 report 文档。 |
| 数据、公告或研报 | `.agent/docs/research/research_data_manual.md` | 数据不足时停止结论并报告来源失败。 |
| 指标或图表 | `.agent/docs/research/research_metrics_charts.md` | 同时检查 profile、单位、版本和截止日期。 |
| 策略或时间尺度 | `.agent/docs/research/strategy_horizon_selection.md` | 必须同时匹配 horizon 和 profile。 |
| 标的发现 | `.agent/docs/research/asset_discovery.md` | 候选最终回到 research mainline。 |
| 研究报告复核 | `.agent/docs/research/research_report_review.md` | 事实必须有证据引用。 |

## Working Rules

- Prefer project docs, unit tests, and existing provider/store/service patterns before inventing new interfaces.
- Read source when implementing behavior, docs are stale, or tests define the real contract.
- If a choice changes future project behavior, ask before changing rules, schema, provider semantics, dependencies, CI, or persistent data layout.
- Prefer the smallest useful change. Do not add speculative providers, compatibility layers, placeholder credentials, or unrelated schema fields.

## Common Development Request

1. Read `docs/modules/README.md`, then the current relevant development plan.
2. Pick the next incomplete checklist item or ask the user if multiple next steps have comparable priority.
3. Implement the smallest useful slice.
4. Add or update unit tests under `src/tests/<module>/unit_tests/`.
5. Run `python src/tests/run_all_unit_tests.py` unless the change is docs-only.
6. Use `.agent/docs/develop/development_report_rule.md` for the final development progress checklist.

## Default Boundary

Do not scan or edit `references/` unless the user asks for reference analysis or the task needs external project context. Do not write runtime data, logs, databases, or temporary files outside their assigned directories.
