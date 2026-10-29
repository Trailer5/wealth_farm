# 持仓复核（“我拿着 X，怎么办”）

1. 记录持仓：`record-holding --user-id <id> --symbol <代码> --asset-type <类型> --quantity <数> [--average-cost] [--first-bought-on]`，用输出核对是不是用户说的那只。
2. 用户说了当初的买入理由：改写为指标（`record-thesis --help`），以事后重建的论点 `record-thesis` 入账。必须在下一步之前做，退出计划才有论点维度。
3. 请求：`subject_type=asset`，`intents=["decide"]`，`personalization=required`，带 `user_id`。`route` → `run-recipe`：基础包 → `thesis_check` → `exit_plan` → `behavioral_review`。
4. 用户说了打算（补仓、继续拿、减仓、清仓）或有替代要比较：`exit-plan --symbol <代码> --user-id <id> --intended-action <...> --alternatives <代码,...>`，发布时用这份退出计划。
5. 回看买入日到今天已披露的各期：读定期报告和分红公告，作为探索发现记录（论点检查只从记录日起）。
6. 按 `mainline.md` 第 5 步起继续探索、装配三轨，发布时把退出计划传给 `assemble_and_publish`。
7. 作答：读法见 `exit-plan --help`、`thesis-check --help`。
