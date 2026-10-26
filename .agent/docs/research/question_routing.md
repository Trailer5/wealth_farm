# 问题分流

## 用途

长期愿景要求按“研究对象 × 意图”回答各类问题（见 `docs/long_term/长期愿景.md` 第三、四节）。本文规定 Agent 收到研究问题时如何构造结构化请求、调用路由、按路由结果行动。配方、路由规则和执行边界的唯一所有者是 `docs/modules/6-编排/问题路由模块.md`，本文不复制配方清单。

## 步骤

1. **理解问题**：由 Agent 判断研究对象、意图和是否需要用户的持仓、偏好或约束；代码不做自然语言意图分类。
2. **构造请求**：写一份 `ResearchRequest` JSON（字段见 `docs/modules/0-共享契约/研究契约模块.md`）。
3. **路由**：`python -m src.research_orchestration.cli route --request-json <path>`，得到配方、每步状态、元素覆盖、缺口（系统模块与路线图阶段）、用户问题和“必须避免”检查项。缺口只列元素与模块编号，缺什么看 `steps` 中状态不是 available 的步骤的 `description`。
4. **执行**：路由为 executable 或 partial 时，`python -m src.research_orchestration.cli run-recipe --request-json <path>` 执行当前可执行的步骤；其余部分只是缺口。执行结果的 `status=executed` 只表示至少一个步骤完成，覆盖是否完整以 `routing.status` 和元素覆盖为准。所有 CLI 默认使用 `data/database/wealth_farm.sqlite3`，`--db-path` 只在指定其他数据库时使用。
5. **继续研究**：标的配方执行完基础研究后，按 `mainline.md` 继续探索、三轨决策和最终报告；其他配方按路由结果组织回答。

## 构造请求的约定

请求字段：`request_id`（自定，唯一）、`subject_type`、`subject_refs`（`[{"text": "用户原话或代码", "resolved_id": "asset_id 或省略"}]`）、`intents`、`personalization`（`none`/`optional`/`required`）、`as_of_date`，可选 `user_id`、`user_view`、`depth`（`quick`/`standard`/`deep`）、`horizon`（`short`/`medium_short`/`medium_long`/`long`）、`personal_refs`。示例（整体市场）：`{"request_id": "q-market-2026-10-26", "subject_type": "market", "subject_refs": [{"text": "A股"}], "intents": ["understand"], "personalization": "optional", "as_of_date": "2026-10-26", "user_id": "user:default", "depth": "quick"}`。

| 问题形态 | subject_type | intents | personalization |
| --- | --- | --- | --- |
| 市场或宏观现状 | market / macro | understand | optional（带 `user_id` 时读取持仓，没有持仓只返回缺口） |
| 行业或主题现状 | sector | understand | none |
| 这个标的怎么样、要不要买 | asset | evaluate（可加 decide） | optional |
| 已持有标的怎么办 | asset | decide | required |
| 策略好不好 | strategy | evaluate | none |
| 组合改造 | portfolio | evaluate + plan | required |
| 时间表或计划 | portfolio | plan | required |
| 领域内找标的 | sector | discover | optional |
| 检验用户的宏观、市场观点 | macro / market | test_view | optional |
| 检验用户的行业观点并推荐 | sector | test_view + discover | required |

补充规则：

1. 用户以“我觉得……”表达观点时，`user_view` 写原文，意图用 `test_view`，并必须带 `user_id`；观点入账还需要 `horizon`（观点的时间窗口），缺少时路由会在用户问题里先问。
2. 标的引用写代码（`text` 会被当作代码解析），已确认身份时在 `resolved_id` 填 `asset_id`。本地没有的代码会自动从交易所证券和基金名录补登，同一代码常对应股票、指数和基金。
3. 用户身份：单用户本地使用时用默认用户 `user:default`；用 `python -m src.research_orchestration.cli user-context` 查看已有用户、持仓、组合估值、投资任务和约束快照。要引用或复盘过去的判断（含用户自己的观点），用 `ledger --user-id <id> [--subject-id <asset_id>] [--active-on <日期>]` 读取账本条目及每个检查点的最新结算。账本里 `artifact_id` 以 `dev-seed:` 开头的是开发样例，不是真实判断，不得当作用户观点或历史记录引用。
4. 用户已提供的个人上下文用 `personal_refs` 声明：投资任务 `mandate:<id>`、组合约束 `constraints:<id>`（ID 取自 `user-context`）、已确认的激进含义 `preference:aggressiveness:<含义>`、原买入理由 `thesis:<说明>`。持仓以用户空间的记录为准：用户在对话里给出持仓后，用 `record-holding --user-id <id> --asset-id <id> --quantity <数量> [--average-cost <成本>]` 记录，只记录用户明确说出的数字，不推算、不假设。
5. 一个问题包含多个对象时，按主对象构造一份请求；路由结果的 `recipe_ids` 会列出相关配方。
6. 市场与宏观数据：“整体市场怎么样”类问题路由到 `market_brief`，`run-recipe` 会生成市场档案和宏观档案并入账；也可以直接运行 `market-archive` 或 `macro-archive --as-of-date <研究截止日>`。市场档案给出估值、股债性价比、利率与流动性、趋势、波动、宽度和两融，宏观档案给出增长、通胀、货币与流动性、信用和政策利率，各维度都有数值、历史分位（含窗口与样本数）、近期变化和 `gaps`；回答时引用具体数值与分位，写明缺失维度（资金流、情绪、政策文本、外部环境），标签只是分位分档不是结论。档案的把握度（`confidence`）只反映已建维度是否取到、数据是否新鲜，不覆盖尚未建立的维度，也不是判断可信度。估值类历史为月末值、当前值为最近交易日；各维度历史长度不同（宽度约两年），以输出里的窗口与样本数为准。同一截止日、同一内容的档案重复运行只入账一次，数据更新后内容不同则新增一条。同类历史用 `analog` 列出指标，用 `analog --metric <id> --as-of-date <研究截止日> [--band low,high] [--horizons 20,60,250]` 查询“指标处于相似分位时沪深300 之后 N 个交易日的收益分布”，只引用不重叠样本的统计并与 baseline 比较；`insufficient_samples` 的周期不得下结论。宏观与利率序列用 `macro` 列出目录，用 `macro --series <id> [--start-date] [--as-of-date <研究截止日>] [--limit 0]` 读取 CPI、PPI、PMI、GDP、M1/M2、信贷、社融、LPR、Shibor、国债收益率和市场序列（默认只打印最近 24 期，`point_count` 是全部期数；`--limit 0` 打印全部）。引用时写明期数、来源和 `available_at`；`available_at_basis=inferred_conservative` 的发布日是推定的，不写成官方发布日；`conflicts` 非空时说明来源不一致；最新期明显滞后（如社融）时如实说明。这些是数据，不是系统的宏观判断；判断仍按下文“外部资料分析”的纪律标注为 Agent 判断。
7. 观点检验（`view_test`）：`run-recipe` 把用户原话入账为个人条目，并生成宏观档案（市场观点生成市场档案；宏观观点涉及市场时另运行 `market-archive`）和同类历史分布。路由状态不是 `needs_user_context` 时，`user_questions` 里的持仓问题只影响组合影响一步，先回答其余部分、在末尾提问。随后把观点改写为可证伪命题（指标、程度、时间窗口），写成 View JSON，用 `record-view --view-json <path> --supersedes-entry-id <原话条目 ID>` 入账可结算版本，并告诉用户改写后的命题。“经济危机”这类没有定义的词，拆成一个或多个可观察指标，每个指标一条观点；多条改写可以都指向同一条原话，各自结算，系统不汇总，复盘时由 Agent 逐条说明。
   - View 字段：`view_id`（自定，唯一）、`subject_type`、`subject_id`（宏观 `macro:cn`，市场 `market:cn_a`，标的或指数写其 ID）、`origin`（`user`/`system`）、`user_id`（仅用户观点）、`proposition`、`horizon`、`as_of_date`、`expires_on`、`confidence`（`low`/`medium`/`high`）、`claim`、`settlement_rule`，可选 `support_refs`、`counter_refs`（文本引用，建议 `macro:<series_id>:<期>`、账本条目 ID 或资料来源与日期）、`base_rate_ref`（文本）、`probability`（`{"low": "0.1", "high": "0.25"}`）、`falsifiers`、`implications`。小数一律写成字符串。
   - 指标类：`"settlement_rule": {"method": "metric_observation", "observed_id": "macro:<series_id>"}`（`series_id` 取自 `macro` 目录），`claim.metric_key` 取 `level`（检查点前最新一期）、`change`（最新一期减观点日已发布的值）、`min_level` 或 `max_level`（观点日之后发布各期的极值）、`level_at:<期末日>`（指定一期，如全年 GDP 累计同比写 `level_at:2026-12-31`；累计口径序列每年重新起算，不要对它用极值）。`comparison` 为 `above`（严格大于）或 `below`（严格小于），`threshold` 用序列自身单位（CPI 的 `"0.5"` 表示 0.5%）。只使用检查点前已发布的数据；没有新一期或目标期尚未发布时记 `data_insufficient`。检查点按时间窗口固定（中短期 +90、+180 天），低频序列把 `expires_on` 设在目标期预计发布日（`macro` 目录的 `release_rule`）之后，会追加为最后一个检查点；`record-view` 返回实际检查点。
   - 价格类：`"method": "price_relative"`，`observed_id` 写标的或指数的 `asset_id`（形如 `asset:<uuid>`，用 `asset --symbol <代码> [--asset-type index]` 查询，本地没有时会自动登记），`claim.metric_key` 为 `total_return`，阈值是小数收益（`"-0.2"` 表示 -20%）。只比较检查点时的区间收益，不是期间最大跌幅。
   - 系统自己的观点：`origin` 为 `system`，不带 `user_id`，必须写 `falsifiers`，入客观账本；概率区间写入 `probability` 并按第 3 条回答纪律标注为 Agent 判断。
   - 宏观事件的历史发生比例（如“PMI 处于当前区间时半年内跌破 48 的比例”）没有公共入口：可以用 `macro --limit 0` 的输出自行计算，写明样本区间、样本数和独立事件数，标注“Agent 计算，未经确定性流程”，写入 `base_rate_ref`。
   - 示例：`{"view_id": "view:user:<request_id>:pmi", "subject_type": "macro", "subject_id": "macro:cn", "origin": "user", "user_id": "<user_id>", "proposition": "未来半年制造业 PMI 至少一个月跌破 48", "horizon": "medium_short", "as_of_date": "<研究截止日>", "expires_on": "<截止日 + 180 天>", "confidence": "medium", "claim": {"metric_key": "min_level", "comparison": "below", "threshold": "48"}, "settlement_rule": {"method": "metric_observation", "observed_id": "macro:pmi_manufacturing"}}`。`record-view` 会拒绝不可结算的写法（未知序列、不支持的 `metric_key`、本地没有的标的）。
8. 行业与同行：行业一律用申万 2021 分类。`industry` 列出一级行业（`--level 2|3`、`--parent <代码>` 看下级）；`industry --symbol <股票代码>` 给出一、二、三级行业、各级行业指数的 `asset_id` 和默认同行（同一三级行业，不足 5 个时用二级）；`industry --code <行业代码> [--members] [--valuation]` 给出成分和估值采样（月末加最新，一、二级行业才有）。成分是当前成分，回溯使用时说明这一点。“快消”“新能源”这类主题没有现成成分，映射成申万行业组合或标的清单并写明依据。行业观点按行业指数结算：`price_relative` 的 `observed_id` 写行业指数的 `asset_id`，相对判断的基准写沪深300 的 `asset_id`。全市场或一组股票的数据用 `cross-section`：`--snapshot` 当前价格与估值（只有最新一天）、`--financials [--as-of-date] [--period]` 按报告期的财务（只含截止日前已公告的行）、`--valuation --symbols <代码,...>` 个股估值历史、`--delisted` 退市标的；用 `--symbols` 或 `--output <path>` 控制输出量。

## 按路由状态行动

| 状态 | 行动 |
| --- | --- |
| executable / partial | 运行 `run-recipe`；先给出能支持的判断，再按缺口说明缺什么、影响哪部分、在哪个阶段补齐 |
| gap_only | 不运行；说明正式缺口，可做标注来源的外部资料分析 |
| needs_user_context | 只问路由给出的 `user_questions`，得到回答后记录持仓或补充 `personal_refs`，再重新路由。等待回答期间，标的类问题可以对同一标的另建非个性化请求（`intents=[evaluate]`、`personalization=none`）先跑基础研究，回答中标明“不含你的个人情况”，个人适配和仓位等用户回答后再做 |
| unresolved_subject | 看 `subjects[].issues`：`asset_identity_ambiguous` 只问是哪一个候选（`candidate_labels` 与 `candidates` 一一对应；用户原话已给出名称且恰好对应一个候选时，把该候选填入 `resolved_id` 并向用户说明）；`asset_identity_not_found` 请用户核对代码；`asset_identity_lookup_failed` 是身份来源远程失败，稍后重试，不猜测 |
| unresolved | 参考 `suggestions` 改写请求；仍无配方时作为新问题类型报告，不硬套已有配方 |

`run-recipe` 返回 needs_input 或 blocked 时照实转述：例如观点缺少时间窗口、周期资源公司需要商品权重、缺少比较基准（`benchmark_unavailable`，ETF 需要先登记标的指数关系）、基础研究被阻断。这些是工程或数据阻塞，不得绕过公共入口手工补齐。

## 回答纪律

1. **不硬塞进单标的流程**：不得把市场、行业或组合问题改写成某个标的的研究。可以建议用代表性标的（如宽基 ETF）作为代理，但必须说明它只回答该标的的问题，并取得用户同意。
2. **smoke 不是研究入口**：不得运行或复制 smoke 脚本来生成用户结论；smoke 只用于开发验证。
3. **外部资料分析须标注**：可以基于研究截止日之前的官方公开资料进行分析，必须列明来源和发布日期，并标注“未经项目确定性流程”。这类分析不作为系统观点，也不入账。可以给出较宽的概率区间和大致时间窗口，但必须标明是 Agent 判断、不是系统模型输出，并写出依据（同类历史比例、当前信号）；不给单点概率、目标价或具体时点。
4. **先给判断，再说缺口**：缺口只说明缺什么、影响哪部分结论、在哪个阶段补齐，不用缺口说明抵消正文。
5. **遵守“必须避免”**：路由结果中的 `avoid_checks` 逐条遵守；结构化检查项由代码保证，其余由 Agent 自查。
6. **用户观点先检验**：检验观点时先找反方证据，不顺着用户观点找支持。
