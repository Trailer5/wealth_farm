# 市场与宏观

## A. 现状（“现在市场怎么样”“宏观环境如何”）

1. 请求：`subject_type=market` 或 `macro`，`intents=["understand"]`，`personalization=optional`（带 `user_id` 时会读持仓）。
2. `route` → `run-recipe`：生成市场档案和宏观档案并入账。只要档案时直接 `market-archive` / `macro-archive --as-of-date <截止日>`。
3. 要历史参照：`analog` 列指标 → `analog --metric <id> --as-of-date <截止日>`。
4. 要具体序列：`macro` 列目录 → `macro --series <id> --as-of-date <截止日>`。
5. 作答：逐维度引用数值与分位，写明缺失维度；读法见 `market-archive --help`、`macro --help`、`analog --help`。你自己的判断按回答纪律 3 标注。

## B. 检验用户观点（“我觉得经济要衰退”）

1. 请求：`intents=["test_view"]`，`user_view` 写原话，带 `user_id` 和 `horizon`；缺 `horizon` 时路由会在 `user_questions` 里先问。
2. `route` → `run-recipe`：原话入账为个人条目，并生成档案和同类历史。宏观观点涉及市场时另运行 `market-archive`。路由不是 `needs_user_context` 时，持仓问题只影响组合影响一步：先答其余，末尾再问。
3. 把观点改写为可证伪命题（指标、程度、时间窗口）：看 `record-view --help` 写 View JSON → `record-view --view-json <path> --supersedes-entry-id <原话条目 ID>`。
4. 作答：先反方证据，再支持证据；告诉用户改写后的命题和检查日。
