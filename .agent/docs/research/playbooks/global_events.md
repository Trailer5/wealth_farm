# 全球事件、政策与美股

适用：问题涉及海外公司、美国或其他国家的政策与事件、全球新闻，或需要美股价格；也用于把这类判断入账跟踪。情报由 Agent 用项目入口自行获取，不交给用户。

## A. 取得事实

1. 新闻：`intel --source news --query <词> --since <日期> [--lang zh]`；英文、中文各查一次。
2. 官方来源优先：公司申报 `intel --source sec-filings --ticker <代码> [--forms 8-K,10-Q]`，全文 `intel --source sec-search --query '"<短语>"'`，美国法规 `intel --source federal-register [--agency industry-and-security-bureau]`；正文 `intel --fetch-url <链接>`。
3. 在别处找到的官方页面：`intel --add-evidence-url <链接> --title <标题> --published-on <日期>` 登记后才能作为证据引用。
4. 美股价格：`prices --symbols <代码,...> --market us --start-date <日期>`。
5. 作答：事实写来源、媒体与发布日期；只有媒体报道、没有官方来源的写明“报道称”；读法见各命令 `--help`。

## B. 事件或政策观点入账

1. 把观点拆成可核实的事件或数值：每个写清判定标准（发布机构、文件类型、门槛）和截止日。
2. 每个事件或数值：先用 A 的检索确认当前状态，再 `define-observable --json <path>`（字段见 `--help`，基线引用刚才的检索）。
3. 观点入账：`record-view --view-json <path> [--supersedes-entry-id <原话条目>]`，`observed_id` 写 `obs:<id>`；美股价格观点用 `asset --symbol <代码> --market us` 的 asset_id 写 `price_relative`。
4. 告诉用户改写后的命题、判定标准、检查日和截止日。

## C. 跟踪

1. `routine --mode maintain --user-id <id>`；看 `steps.events` 与 `next_actions`；只查执行待办时用默认只读模式。
2. 有新闻或到期：按 A 核实；满足判定标准才 `record-observation --json <path>`，到期核实写到期日状态。
3. 记录后 `settle-ledger`，结果用 `decision-review` 汇总。
4. 事件结论产生持续观察、复评或应对动作时，按 `playbooks/execution_plans.md` 登记，引用可观测量及观点；事件日期不确定也要安排下一检查节点。

## D. 只按比例管理的持仓（雇主股票、海外股票）

1. 用户不报数量、只要推荐比例时：先按 A 更新事实，`prices` 取现价与价格退出线的距离（没有退出线时取历史最大回撤并写明）。
2. `position-guidance --user-id <id> --symbol <代码> --market us --history` 看上一期；依据写法见 `position-guidance --help`。
3. 先 `--dry-run` 看结论，再去掉 `--dry-run` 记录；把推荐比例、结论（加持、减持、维持）、执行规则和复评日告诉用户。
