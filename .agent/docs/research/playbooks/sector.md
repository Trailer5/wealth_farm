# 行业与主题

行业一律用申万 2021 分类。主题（“快消”“新能源”）先映射成申万行业或标的清单，映射依据写进回答。

## A. 行业现状

1. 找行业代码：`industry`（一级）→ `industry --level 2|3 --parent <代码>`；或 `industry --symbol <股票代码>`。
2. 请求：`subject_type=sector`，`intents=["understand"]`，`subject_refs[0].resolved_id = "sector:sw:<代码>"`（只写名称时按名称精确匹配）。
3. `route` → `run-recipe`：生成行业档案并入账。返回 `needs_input` 时回到第 1 步找代码。主题跨多个行业时每个行业一份请求（或直接 `sector-archive --code <代码>`）。
4. 要代表性标的：按 `sector-archive --help` 末尾的步骤挑选。
5. 作答：逐维度引用数值，读法见 `sector-archive --help`。

## B. 领域内找标的

1. 请求：`intents=["discover"]`，`resolved_id` 写范围（见 `discover --help`），`text` 写主题原话。
2. `route` → `run-recipe`（或 `discover --scope <范围>`）：多策略漏斗加前 5 名基础包。
3. 看全量或某只为何未入选：`discover --run-id <id> --limit 0` / `--symbols <代码>`。
4. 作答：候选是研究邀请，不是买入结论；读法见 `discover --help`。
5. 用户要看候选放进自己组合的效果：转 `portfolio.md` 的 F。

## C. 检验行业观点并推荐（“我觉得消费要起来了，推荐几只”）

1. 请求：`intents=["test_view", "discover"]`，带 `user_id`、`horizon`、`user_view`；`subject_refs[0].text` 写用户的主题词，`resolved_id` 写 `sector:sw:<代码>[,<代码>...]`。没有持仓不阻断。
2. `route` → `run-recipe`：原话入账 → 各行业档案 → 并集上的漏斗与基础包。
3. 入账可结算观点：填 `sector_brief` 输出的 `view_templates`（每个行业一份，填法见 `record-view --help`）→ `record-view --supersedes-entry-id <原话条目>`。
4. 作答顺序：反方证据 → 支持证据 → 候选（研究邀请）→ 与用户组合的适配仍需持仓与单标的研究。行业未来相对表现的概率没有系统入口，给出时标注为 Agent 判断。
