# 组合与计划

## A. 准备（所有组合问题先做）

1. 每笔持仓 `record-holding --user-id <id> --symbol <代码> --asset-type stock|exchange_fund|open_fund --quantity <数>`；`user-context` 核对代码、名称和类型。
2. 问未投资的现金（不记为持仓，运行时用 `--cash <元>`）。
3. 没有偏好：问期限和能接受的最大亏损 → `record-preference ... --dry-run` → 念给用户确认 → 去掉 `--dry-run` 记录（参数见 `record-preference --help`）。
4. 用户说“更激进”“稳一点”：先问清含义 → `record-preference --meaning <...> --statement "<原话>" --dry-run` → 确认 → 记录。
5. 请求：`subject_type=portfolio`，`subject_refs=[]`，`personal_refs=["preference:<id>"]`，带 `user_id`；`intents` 见下文各节。

## B. 组合风险在哪里（`intents=["evaluate"]`）

1. `route` → `run-recipe --cash <元>`（或 `portfolio-diagnose --user-id <id> --cash <元>`）。
2. 作答：读法与必须写明的口径见 `portfolio-diagnose --help`。诊断不给调整建议。

## C. 组合改造（`intents=["evaluate", "plan"]`，Q5）

1. `route` → `run-recipe --cash <元>`：诊断 → 配置与风险预算 → 调整方案与迁移路径 → 压力对比。
2. 配置步骤 `conflict=true`（后续步骤为 `needs_input`）：把两个选项和代价讲给用户 → 用户选定后 `record-preference` 记新版本 → 回到第 1 步。
3. 用户点名的标的或已研究过的候选：`adjustment-proposal --user-id <id> --cash <元> --candidate <代码>[:<类型>] ...`。
4. 作答：顺序与读法见 `allocation-proposal --help`、`adjustment-proposal --help`。

## D. 时间表与计划（`intents=["plan"]`，Q10）

1. `route` → `run-recipe --cash <元>`（预览），或 `portfolio-plan --user-id <id> --cash <元> [--candidate ...]`。
2. 把节点表、目标区间与概率、复盘节点和冲突处理讲给用户，给出 HTML 文件路径。
3. 用户确认后 `portfolio-plan ... --record` 入账。
4. 作答：读法见 `portfolio-plan --help`。

## E. 复盘与偏好校准（`intents=["review"]`）

1. 用户说了实际买卖：每笔 `record-trade`；持仓变了再 `record-holding`。
2. `routine --user-id <id>`（含结算）或 `settle-ledger`。
3. `route` → `run-recipe --cash <元>`（或 `decision-review --user-id <id> --cash <元>`）。
4. 作答：读法与两种处理选项见 `decision-review --help`；用户选定后按 A.3 或 C 继续。

## F. 候选放进组合的效果（Q9 组合适配）

1. 从漏斗候选里挑与偏好相符的 1–3 只（基础包足以作为候选依据，回答说明未做完整决策）。
2. `adjustment-proposal --user-id <id> --cash <元> --candidate <代码>[:<类型>] ...`；先看输出的数据缺口。
3. 作答：加入后的目标区间、金额与股数，行业、重叠和波动变化。
