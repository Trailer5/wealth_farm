---
name: wealth-farm-agent-reference
description: "Use for project-level Wealth Farm work: data sources, SQLite storage, official disclosures, PDF processing, unit tests, CI, project rules, development plans, and progress tracking."
---

# Wealth Farm Agent Index

Use this file to choose the smallest relevant project guidance. Do not read every `.agent/docs/` file by default.

## Required First Reads

1. Read `.agent/AGENT_README.md` every turn.
2. Read this file when starting a new task, recovering context, or routing is unclear.
3. Read `.agent/docs/README.md` only when `.agent/docs/` ownership is unclear.

## Selective Reads

- `.agent/docs/development_rule.md`: code, tests, directories, docs, data, logs, references, temp files, or dependencies.
- `.agent/docs/development_report_rule.md`: development plans, progress tracking, or development summaries.
- `docs/短期开发计划-数据源与数据库.md`: A 股行情、基金数据、官方披露、研报、PDF、SQLite、provider、落库、相关测试。

## Quick Routing

| User intent | Read first | Escalate when |
| --- | --- | --- |
| Project layout or file placement | `.agent/docs/development_rule.md` | Ask if a new directory responsibility is ambiguous. |
| Development progress or plan tracking | `.agent/docs/development_report_rule.md` | Also read the plan being updated. |
| Data source or database work | `docs/短期开发计划-数据源与数据库.md` | Inspect `src/data_src/`, `src/data_store/`, `src/unit_tests/`. |
| Unit tests or CI | `.agent/docs/development_rule.md` | Inspect `src/unit_tests/run_all_unit_tests.py` and `.github/workflows/`. |
| Agent rule changes | `.agent/SKILL.md` | Inspect `.agent/AGENT_README.md` and the owning `.agent/docs/` file. |

## Working Rules

- Prefer project docs, unit tests, and existing provider/store/service patterns before inventing new interfaces.
- Read source when implementing behavior, docs are stale, or tests define the real contract.
- If a choice changes future project behavior, ask before changing rules, schema, provider semantics, dependencies, CI, or persistent data layout.
- Prefer the smallest useful change. Do not add speculative providers, compatibility layers, placeholder credentials, or unrelated schema fields.

## Common Development Request

1. Read the relevant development plan, usually `docs/短期开发计划-数据源与数据库.md`.
2. Pick the next incomplete checklist item or ask the user if multiple next steps have comparable priority.
3. Implement the smallest useful slice.
4. Add or update unit tests under `src/unit_tests/`.
5. Run `python src/unit_tests/run_all_unit_tests.py` unless the change is docs-only.
6. Use `.agent/docs/development_report_rule.md` for the final development progress checklist.

## Default Boundary

Do not scan or edit `references/` unless the user asks for reference analysis or the task needs external project context. Do not write runtime data, logs, databases, or temporary files outside their assigned directories.
