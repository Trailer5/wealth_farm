# 组合与计划

## A. 准备（所有组合问题先做）

1. 每笔持仓 `record-holding --user-id <id> --symbol <代码> --asset-type stock|exchange_fund|open_fund --quantity <数>`；`user-context` 核对代码、名称和类型。用户只给比例、不给数量和金额时，改用 `record-weights`（写法见其 `--help`），报告里的金额是名义金额，说给用户时换成比例。
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
5. 按 `playbooks/execution_plans.md` 将已确认的节点、观察与应对动作登记到执行日历，引用组合计划账本编号及报告；未确认方案只登记草稿。

## E. 复盘与偏好校准（`intents=["review"]`）

1. 用户说了实际买卖：每笔 `record-trade`；持仓变了再 `record-holding`。
2. `routine --mode maintain --user-id <id>`（含结算）或 `settle-ledger`；默认 routine 只读，不触发结算。
3. `route` → `run-recipe --cash <元>`（或 `decision-review --user-id <id> --cash <元>`）。
4. 作答：读法与两种处理选项见 `decision-review --help`；用户选定后按 A.3 或 C 继续。

## F. 候选放进组合的效果（Q9 组合适配）

1. 从漏斗候选里挑与偏好相符的 1–3 只（基础包足以作为候选依据，回答说明未做完整决策）。
2. `adjustment-proposal --user-id <id> --cash <元> --candidate <代码>[:<类型>] ...`；先看输出的数据缺口。
3. 作答：加入后的目标区间、金额与股数，行业、重叠和波动变化。

## G. 执行窗口内的节奏（每一笔已定的买入或卖出）

1. 研究给出标的、数量和执行窗口后，下单前运行 `entry-timing --symbol <代码> --side buy|sell --window-days <窗口交易日数>`（读法与纪律见 `entry-timing --help`），不凭“最近在跌”“最近在涨”的印象择时。
2. 按 `recommendation` 告诉用户每批比例与大致日期，并说明依据的相似状态、段数和与全部交易日的差距；样本不足或差距很小时照常分两批。
3. 延后的批次在窗口末必须执行（研究的退出或停止条件触发除外）；节奏改变已登记的执行计划时，按 `playbooks/execution_plans.md` 修订节点。

## H. 现在值得把钱部署到哪里（没有指定标的或行业）

1. 请求：`subject_type=portfolio`，`intents=["discover"]`（要顺带时间表时加 `plan`），`personal_refs` 写最新偏好；`subject_refs[0]` 写发现范围（用户没有主题时用 `index:000300`，有主题时映射到申万行业并写明依据）。用户没说“更激进/更稳”时不走 C。
2. `route` → `run-recipe`（范围 ≤300 只时漏斗约 15–20 分钟，一次跑完）。按步骤顺序读输出：
   - `plan_constraints`：生效计划的约束和回避清单，后面的筛选和额度都受它约束。
   - `plan_nodes`：对到期节点用 `plan-show --task-id` 读条件原文，价格条件用 `prices --symbols <代码> --state` 核对，结果用 `task-resolve` 记录（写法见其 `--help`）。
   - `market_state`、`risk_budget`：市场状态和各大类还能部署多少。
   - `strategy_funnel`、`multi_strategy_priority`：候选与前 5 名基础包，读法见 `discover --help`。
3. 挑候选：先按回避清单和偏好剔除，再按 `discover --help` 的读法剔除周期高点和非经常性利润；交易权限只标注。前 5 名之外的候选按 `discover --help` 生成基础包。
4. 组合适配：`adjustment-proposal --user-id <id> --candidate <代码>[:<类型>] ...`，看额度、重叠和压力情景；给用户的仓位区间以它和计划约束为准，不自己推算。
5. 作答顺序：条件已成立的既定动作 → 各大类还能部署多少 → 未触发的计划条件及当前距离 → 新候选（研究邀请，优先级和理由）→ 写入与未登记事项。
6. 用户要买某个候选：转 `mainline.md` 做单标的研究，得出是否、何时、多少和退出线后，按 `playbooks/execution_plans.md` 登记。
