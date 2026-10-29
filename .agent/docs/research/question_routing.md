# 研究问题：标准流程

所有研究问题都从这里开始。本文只给流程和动作；字段、示例和结果读法在各命令的 `--help` 与输出里（`next_actions`、`gaps`、`notes`、`limitations`），用到哪个命令先看它的 `--help`。

## 一、环境

1. PowerShell 先执行 `[Console]::OutputEncoding=[Text.Encoding]::UTF8; $env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="."`。
2. 命令写作 `python -m src.research_orchestration.cli <命令>`，输出 JSON，默认数据库 `data/database/wealth_farm.sqlite3`。
3. stderr 另存文件（`2> temp/<任务>/x.err`），不要丢弃；脚本先写进 `temp/` 再运行，带 `if __name__ == "__main__":`。

## 二、标准流程

1. **判断问题**：研究对象、意图、是否需要用户的持仓、偏好或约束。这一步由你判断，代码不做意图分类。
2. **确认用户**：单用户本地使用时用 `user:default`；`user-context` 查看已有持仓、偏好和约束。问题不涉及个人情况（`personalization=none`，如策略、行业现状）时跳过。
3. **写请求**：看 `route --help`（字段、问题形态对照表、示例），把请求 JSON 写进 `temp/`。
4. **路由**：`route --request-json <path>`，按第四节的状态表行动。
5. **执行**：`run-recipe --request-json <path>`（组合问题加 `--cash <元>`）；逐步看每步的 `status`、`summary` 和 `next_actions`。
6. **进入任务流程**：按第三节的表继续。
7. **作答**：按第五节的回答纪律；判断类产出按流程入账。

## 三、任务流程

| 问题 | 流程 |
| --- | --- |
| 这个标的怎么样、要不要买、何时买、买多少 | `mainline.md` |
| 已持有的标的怎么办 | `playbooks/holding.md` |
| 市场或宏观现状；检验用户的市场或宏观观点 | `playbooks/market_macro.md` |
| 行业现状；领域内找标的；检验行业观点并推荐 | `playbooks/sector.md` |
| 组合风险、组合改造、时间表与计划、复盘、偏好 | `playbooks/portfolio.md` |
| 策略好不好 | `playbooks/strategy.md` |
| 例行维护（结算、刷新档案、检查论点、到期复盘） | `routine --user-id <id>`，按 `next_actions` 继续 |
| 重大事件名称、谐音或错配 | `event_name_discovery.md` |
| 新对话只给代码、名称或模糊请求 | `cold_start_intake.md` |

## 四、按路由状态行动

| 状态 | 行动 |
| --- | --- |
| executable / partial | 运行 `run-recipe`；先给能支持的判断，再说明缺什么、影响哪部分、在哪个阶段补齐 |
| gap_only | 不运行；说明正式缺口，可做标注来源的外部资料分析（回答纪律 3） |
| needs_user_context | 只问 `user_questions`；得到回答后记录持仓或补 `personal_refs`，再重新路由。等待期间标的类问题可另建非个性化请求（`intents=[evaluate]`、`personalization=none`）先跑基础研究，回答中标明“不含你的个人情况” |
| unresolved_subject | 看 `subjects[].issues`：`asset_identity_ambiguous` 只问是哪一个候选（用户原话的名称恰好对应一个候选时直接填 `resolved_id` 并说明）；`asset_identity_not_found` 请用户核对代码；`asset_identity_lookup_failed` 稍后重试，不猜测 |
| unresolved | 按 `suggestions` 改写请求；仍无配方时作为新问题类型报告，不硬套已有配方 |

`run-recipe` 某步返回 `needs_input` 或 `blocked` 时照实转述，按该步 `next_actions` 处理；不得绕过公共入口手工补齐。

## 五、回答纪律

1. **不硬塞进单标的流程**：市场、行业或组合问题不改写成某个标的的研究；用代表性标的（如宽基 ETF）作代理时说明它只回答该标的的问题，并取得用户同意。策略规则用跟踪指数的 ETF 执行不属于这种情形，说明即可。
2. **smoke 不是研究入口**：不运行或复制 smoke 脚本来生成用户结论。
3. **外部资料须标注**：可以用研究截止日之前的官方公开资料分析，列明来源和发布日期，标注“未经项目确定性流程”，不入账；可以给较宽的概率区间和时间窗口，标明是 Agent 判断并写出依据；不给单点概率、目标价或具体时点。
4. **先给判断，再说缺口**：缺口只说明缺什么、影响哪部分、在哪个阶段补齐，不用缺口抵消正文。
5. **遵守“必须避免”**：路由结果的 `avoid_checks` 逐条遵守。
6. **用户观点先检验**：先找反方证据，不顺着用户观点找支持。
7. **只记用户说出的数字**：持仓、偏好、交易都按用户原话记录，不推算；不替用户做选择（偏好档位、冲突选项）。
8. **开发样例不是真实判断**：账本里 `dev-seed:` 开头的条目不得当作用户观点或历史记录引用。
