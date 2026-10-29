"""Long help texts (epilogs) of the research CLI: fields, examples, and how to read and cite outputs.

The Agent guidance in .agent/docs/research/ only gives the flow ("run X, then Y"); the details an
Agent needs while running a command live here and are printed by ``<command> --help``. Calculation
bases and design choices live in the module docs' design notes, referenced from these texts.
"""

from __future__ import annotations

_REQUEST = """\
请求 JSON（ResearchRequest，契约见 docs/modules/0-共享契约/研究契约模块.md）：
  必填 request_id（自定，唯一）、subject_type、subject_refs、intents、personalization、as_of_date
  可选 user_id、user_view（用户原话，只用于 intents 含 test_view 的观点检验，其他问题不要写）、
       depth（quick/standard/deep）、
       horizon（short/medium_short/medium_long/long）、personal_refs
  subject_refs: [{"text": "用户原话或代码", "resolved_id": "已确认的 ID，可省略"}]
    - 标的写代码（text 按代码解析）；确认身份后在 resolved_id 填 asset_id。本地没有的代码会自动补登，
      同一代码常对应股票、指数和基金。
    - 行业 sector:sw:<代码>[,<代码>...]；范围 index:<代码>、market:cn_a、symbols:<代码,...>。
    - 策略写已登记的策略 id（或完整的登记名），取自 strategy-archive；route 不解析策略，subjects 为空是正常的，
      run-recipe 执行时才匹配；没登记的说法会返回 needs_input 与可用模板。
    - 组合问题 subject_refs 留空。
  personal_refs：投资任务 mandate:<id>、约束 constraints:<id>（ID 取自 user-context）、
    偏好 preference:<id>（record-preference 返回的 personal_ref；--dry-run 返回的未入账，不要写）、
    原买入理由 thesis:<说明>。
  多个对象时按主对象构造一份请求；路由结果的 recipe_ids 列出相关配方。

问题形态 → subject_type / intents / personalization：
  市场或宏观现状            market|macro  understand            optional
  行业或主题现状            sector        understand            none
  这个标的怎么样、要不要买  asset         evaluate(+decide)     optional
  已持有标的怎么办          asset         decide                required
  策略好不好                strategy      evaluate              none
  组合改造                  portfolio     evaluate+plan         required
  组合风险在哪里            portfolio     evaluate              required
  时间表或计划              portfolio     plan                  required
  复盘判断、偏好设得对不对  portfolio     review                required
  领域内找标的              sector        discover              optional
  检验宏观/市场观点         macro|market  test_view             optional
  检验行业观点并推荐        sector        test_view+discover    required

  “我觉得……”（对市场、宏观、行业、标的的看法）是观点：user_view 写原文、intents 用 test_view、
  必须带 user_id；观点入账需要 horizon，缺少时路由会在 user_questions 里先问。
  “我觉得太保守了”是偏好表达，不是观点：走 record-preference。
  示例：{"request_id": "q-market-2026-10-26", "subject_type": "market", "subject_refs": [{"text": "A股"}],
         "intents": ["understand"], "personalization": "optional", "as_of_date": "2026-10-26",
         "user_id": "user:default", "depth": "quick"}
"""

_ROUTE = _REQUEST + """
输出：recipe_ids、steps（每步 status 与 description）、元素覆盖、gaps（只列元素与系统模块编号，
缺什么看 status 不是 available 的步骤的 description）、user_questions、avoid_checks。
"""

_RUN_RECIPE = """\
执行路由为 executable 或 partial 的配方中当前可执行的步骤。status=executed 只表示至少一步完成；
覆盖是否完整以 routing.status 和元素覆盖为准。每步有 status、summary、artifacts 和 next_actions。
组合配方用 --cash <元> 计入用户说出的未投资现金；日期取请求的 as_of_date。

needs_input / blocked 照实转述（观点缺时间窗口、周期资源公司需要商品权重、benchmark_unavailable、
基础研究被阻断、偏好冲突等）。这些是工程或数据阻塞，不得绕过公共入口手工补齐。

标的配方：基础步骤 artifacts 里有 workflow_run_id 与 decision_brief_id，之后按 mainline.md 探索。

策略配方（Q4）：逐个策略输出 strategy_spec（档案与状态）、selection_samples（只对选股策略是缺口，其余写“不适用”）、
  backtest_validation（完整验证，字段同 strategy-evaluate --help，evaluation_id 是验证记录）、regime_analysis、
  attempt_discount、forward_tracking。每次运行都会重跑完整验证并新增一条验证记录（不进研究账本，故
  ledger_entry_ids 为空）；处于“前向跟踪/启用”的策略顺带记录当天信号。未进入前向跟踪时 forward_tracking 为 gap，
  门槛已满足则 next_actions 给出迁移命令（是否迁移见 playbooks/strategy.md）。

基础包读法（按 profile）：
  高成长未盈利（high_growth_unprofitable，TTM 亏损且营收同比≥20%，或科创板 U 后缀且亏损；误判时用
  run-single --profile 纠正）：先写现金跑道与筹资依赖，再写营收增速、毛利率、研发费用率的逐期趋势，
  最后写“当前市值在终局净利率 5%/10%/15% 下隐含未来 10 年营收年增 X/Y/Z”并与近 3 年复合增速对照；
  不引用市盈率。跑道不含理财类金融资产（偏保守），不扣有息负债到期（偏乐观）；结论卡在跑道上时读
  最新定期报告的资产负债表和融资公告。适配输入类型 growth_runway；论点指标可用 revenue_growth、
  gross_margin、rd_intensity、cash_runway_years。
  场外基金（时点与仓位）：基础包有“申赎规则与交易成本”“费率累计拖累”“现任经理任期表现”“底层估值
  位置”四节。时点轨道必须有四类输入：fund_dealing_rule、valuation_regime、market_risk_state
  （可用 market-archive）、trading_calendar。下一可成交时间：下一交易日 15:00 前提交按当日净值成交；
  执行约束写确认天数和赎回费阶梯决定的最短持有期。估值写底层指数市盈率分位并注明是跟踪指数还是
  业绩基准的权益部分；没有估值序列时写“底层估值不可得”，不拿沪深300 代替。不预测净值，分批用定投
  或按估值分位分档。仓位：价格用最新单位净值、数量是份额、波动回撤用基金自身净值；约束快照的成交额
  一栏填日累计申购限额（无限额填基金规模的 10%），参与率填 1。长期持有用“费率累计拖累”与低费率
  ETF 比较；区分“现任组合任期”与“某位经理的连续管理期”。申赎规则是抓取当天的值。
  口径：docs/modules/3-分析工具/基金与ETF研究数据模块.md、估值与财务分析模块.md 的设计说明。
"""

_RECORD_VIEW = """\
把一条可证伪的观点入账（View JSON，小数一律写成字符串）。改写自用户原话时加
--supersedes-entry-id <原话条目 ID>；一个原话拆成多个指标时每个指标一条，都指向同一条原话。

字段：view_id（自定，唯一）、subject_type、subject_id（宏观 macro:cn，市场 market:cn_a，标的或指数写
asset_id）、origin（user/system）、user_id（仅用户观点）、proposition、horizon、as_of_date、expires_on、
confidence（low/medium/high）、claim、settlement_rule；可选 support_refs、counter_refs（建议
macro:<series_id>:<期>、账本条目 ID 或资料来源与日期）、base_rate_ref、
probability（{"low": "0.1", "high": "0.25"}）、falsifiers、implications。

指标类：settlement_rule = {"method": "metric_observation", "observed_id": "macro:<series_id>"}
  （series_id 取自 macro 目录；公司财务指标 fin:<asset_id>:<指标>）。
  claim.metric_key：level（检查点前最新一期）、change（最新一期减观点日已发布值）、
  min_level / max_level（观点日之后发布各期的极值）、level_at:<期末日>（指定一期；累计口径序列如全年
  GDP 写 level_at:2026-12-31，不对它用极值）。comparison：above（严格大于）/ below（严格小于）；
  threshold 用序列自身单位（CPI 的 "0.5" 表示 0.5%）。只用检查点前已发布的数据，未发布记
  data_insufficient。检查点按时间窗口固定（中短期 +90、+180 天）；低频序列把 expires_on 设在目标期预计
  发布日（macro 目录的 release_rule）之后，会追加为最后一个检查点；以命令返回的检查点为准。
价格类：{"method": "price_relative", "observed_id": "<asset_id>"}（asset --symbol <代码> [--asset-type index]
  查询），claim.metric_key 为 total_return，阈值是小数收益（"-0.2" 表示 -20%）；只比较检查点时的区间收益，
  不是期间最大跌幅。行业观点用申万行业指数的 asset_id，相对判断的基准写沪深300 的 asset_id。
  sector_brief 的 view_templates 是每个行业一份的骨架（行业指数相对沪深300，claim 为 outperform）：
  填 proposition 与 expires_on；confidence 按用户语气（“肯定、一定”high，“可能、试试”low，未表达保留
  medium 并在回答里说明是默认值）；必要时改 comparison 或加 threshold（小数超额，如 "0.05"）。
系统自己的观点：origin=system，不带 user_id，必须写 falsifiers；概率区间写进 probability，回答中标注为
  Agent 判断。
没有定义的词（如“经济危机”）拆成一个或多个可观察指标。宏观事件的历史发生比例没有公共入口：可用
  macro --limit 0 自行计算，写明样本区间、样本数和独立事件数，标注“Agent 计算，未经确定性流程”，写入
  base_rate_ref。
示例：{"view_id": "view:user:<request_id>:pmi", "subject_type": "macro", "subject_id": "macro:cn",
  "origin": "user", "user_id": "<user_id>", "proposition": "未来半年制造业 PMI 至少一个月跌破 48",
  "horizon": "medium_short", "as_of_date": "<截止日>", "expires_on": "<截止日 + 180 天>",
  "confidence": "medium", "claim": {"metric_key": "min_level", "comparison": "below", "threshold": "48"},
  "settlement_rule": {"method": "metric_observation", "observed_id": "macro:pmi_manufacturing"}}
命令会拒绝不可结算的写法（未知序列、不支持的 metric_key、本地没有的标的）。入账后把改写后的命题告诉用户。
"""

_ARCHIVE = """\
生成档案并作为客观评估入账（同一截止日同一内容只入账一次，数据更新后内容不同则新增）。
市场档案：估值、股债性价比、利率与流动性、趋势、波动、宽度和两融；宏观档案：增长、通胀、货币与流动性、
信用和政策利率。各维度有数值、历史分位（窗口与样本数）、近期变化和 gaps。
读法：引用具体数值与分位，写明缺失维度（资金流、情绪、政策文本、外部环境）；标签只是分位分档，不是结论。
confidence 只反映已建维度是否取到、数据是否新鲜，不是判断可信度。估值类历史为月末值、当前值为最近交易日；
各维度历史长度不同（宽度约两年），以输出里的窗口与样本数为准。
"""

_MACRO = """\
不带 --series 列出目录（含 release_rule）；--series <id> 读取 CPI、PPI、PMI、GDP、M1/M2、信贷、社融、LPR、
Shibor、国债收益率和市场估值序列。默认只打印最近 24 期（point_count 是全部期数；--limit 0 打印全部）。
引用时写明期数、来源和 available_at；available_at_basis=inferred_conservative 的发布日是推定的，不写成官方
发布日；conflicts 非空时说明来源不一致；最新期明显滞后（如社融）时如实说明。这些是数据，不是系统判断。
"""

_ANALOG = """\
不带 --metric 列出可用指标。--metric <id> --as-of-date <截止日> [--band low,high] [--horizons 20,60,250]：
“指标处于相似分位时沪深300 之后 N 个交易日的收益分布”。只引用不重叠样本的统计并与 baseline 比较；
insufficient_samples 的周期不下结论。
"""

_INDUSTRY = """\
申万 2021 分类。不带参数列一级行业（--level 2|3、--parent <代码> 看下级）；--symbol <股票代码> 给出
一、二、三级行业、各级行业指数的 asset_id 和默认同行（同一三级行业，不足 5 个时用二级）；
--code <行业代码> [--members] [--valuation] 给出成分和估值采样（月末加最新，一、二级行业才有）。
成分是当前成分，回溯使用时说明这一点。“快消”“新能源”这类主题没有现成成分：映射成申万行业组合或
标的清单并写明依据。
"""

_CROSS_SECTION = """\
全市场或一组股票：--snapshot 当前价格与估值（只有最近一次抓取；fetched_at 在收盘前即为盘中价；新上市公司
可能不在其中）；--financials [--as-of-date] [--period] 按报告期的财务（只含截止日前已公告的行，累计口径）；
--valuation --symbols <代码,...> 个股估值历史（2018 年起）；--delisted 退市标的；--index-members 历史指数成分
（来源当前可能不可用）。用 --symbols 或 --output <path> 控制输出量。
同一只的 PE 在截面（腾讯，当前价）与 valuation-position（东方财富，最近收盘）可能不同，一个回答里只用一个
来源并写明。
"""

_VALUATION_POSITION = """\
个股 PE/PB/PS 在自身历史和同行中的位置（月末样本，亏损不排位）。股票基础包已含“估值位置”和“相对行业
表现”两节。
"""

_SECTOR_ARCHIVE = """\
行业档案（也由 sector_brief 配方生成）并入账。名称对应不上时先用 industry 找代码。主题跨多个行业时逐个
行业运行，回答里写映射依据并分别引用。一、二级行业首次运行要取约 10 年估值采样与全部成分估值，可能超过
10 分钟且没有进度输出；三级行业没有拥挤度，需要时对上一级再运行。
维度：估值（一、二级用申万指数估值，三级用成分 PE/PB 中位数）、相对强弱（相对沪深300）、盈利（成分最新
已公告财报合计同比、最新单季净利润合计同比、ROE(TTM) 与毛利率中位数）、拥挤度（成交额占比）、宽度
（站上年线的成分占比）。
读法：逐维度引用数值，不只引用标签；写明 gaps（景气、产业链、资金流、政策未接入；当前成分有幸存者偏差）。
分位窗口以输出里的起始日为准（估值约 2018 年起），不写成“近 10 年”；相对收益是 (1+行业)/(1+沪深300)−1。
季节性强的行业看单季与 TTM，不看累计值。“净利润增量的 x% 来自……”说明增长是否集中，引用合计增速时一并
说明；“未计入的成分”和“已有 n 家披露新一期”写进回答。“近 60 日强于大盘”要同时写行业与沪深300 的涨跌。
同一行业同一截止日可能有多条档案条目，引用最新一条。
代表性标的：industry --code <代码> --members 列成分，cross-section --snapshot --symbols 取市值和估值后按市值
挑选，再用 valuation-position 或单标的基础包比较。
"""

_DISCOVER = """\
多策略截面漏斗。--scope：industry:<申万代码>、index:<指数代码>、market、symbols:<代码,...>（配 --label <主题名>）。
在 run-recipe 里写进 subject_refs[0].resolved_id（sector:sw:<代码>[,<代码>]、index:<代码>、market:cn_a、
symbols:<代码,...>），text 作为范围名（写主题原话）。主题映射到二级行业时直接用二级代码。成分列表取不全时
命令报错而不是返回部分成分，稍后重试。
范围 ≤300 只（非全市场）时四条策略都运行并给出规则历史表现；更大范围只用当前截面（截止日不得早于截面日期），
“深度回撤”“估值自身低位”和规则历史不计算——需要时缩小范围。默认对前 5 名生成基础包（--base-packs 0 跳过）。
run-recipe 只列前 10 个候选，全量用 --run-id <id> --limit 0；问某只为何未入选用 --run-id <id> --symbols <代码>。
读法：
  - 候选是研究邀请，不是买入结论；逐个写入选理由（数值与门槛）、反证和数据缺口。
  - 写明被排除的金融与周期公司数量（申万一级银行、非银金融、煤炭、石油石化、钢铁、有色金属不参与排序；
    主题含能源金属等有色子行业时告诉用户这部分没有被排序）。
  - 规则历史表现写样本数、命中率、均值与中位数；insufficient_samples（少于 8 个不重叠月份）不作依据；均值与
    中位数相差大时说明极端样本；说明收益口径（总市值变化）和幸存者偏差。
  - 优先级只按命中策略数与策略内排名；多条策略同时命中可能反映同一现象。
  - “累计净利润同比”是报告期年初累计口径（基础包里同名指标可能是 TTM）；data_gaps 的跨期提示表示该公司
    未披露最新一期；gaps 出现“财报季进行中”时说明排序跨期，建议披露结束后重跑。
  - 截面没有扣非利润：生成基础包时前列候选逐只核对扣非 TTM（recurring 字段）；one_off_suspected 的候选排在
    最后、不生成基础包、不作推荐。引用利润和市盈率优先用基础包的扣非口径。
  - “低估值高股息”这类没有对应策略的风格：说明差异，用最接近的策略并标注。
"""

_VALUATION_MODEL = """\
价格隐含预期（普通公司、周期公司、银行、高成长未盈利；基础包已有“隐含预期与内在价值”一节），结果作为
客观评估入账。默认参数（10 年国债 + 5% 风险溢价、永续 2%、显式期 10 年）不为单只股票调整；用户要求时用
--erp / --terminal-growth 并在回答中写明。基金返回 applicable=false。
引用顺序：先写反向 DCF（“当前市值隐含未来 10 年 X 口径年增 Y%”）并与历史增速对照，说明市场假设更乐观还是
更保守；再写三情景每股价值与敏感性范围——这是假设变化下的区间，不是目标价，不说“低估 N%”；“隐含回报”
可与 10 年国债和同风险替代比较。银行写“市净率隐含可持续 ROE”与近年 ROE 的差距（中期年化 ROE 说明季节性）。
周期公司以正常化利润为主并说明当期利润处于周期哪一侧。自由现金流与扣非口径差异大时说明原因，不择一引用。
探索发现里引用 valuation_model:<方法>:<报告期> 或入账条目 ID。
口径与局限：docs/modules/3-分析工具/估值与财务分析模块.md 设计说明。
"""

_RECORD_THESIS = """\
把投资论点（InvestmentThesis JSON）入账为可结算的指标观点。发布买入或继续持有的最终报告后入账；
持仓复核时用户说了当初理由，在 run-recipe 之前入账（origin=user、reconstructed=true、
original_date=<用户说的买入日>），回答中说明这是事后重建、从记录日起跟踪。
字段：thesis_id（如 thesis:<代码>-<日期>）、asset_id（股票）、origin（system/user，user 须带 user_id）、
summary、as_of_date、expires_on（不早于各目标期法定披露截止日：年报次年 4-30，一季报 4-30，半年报 8-31，
三季报 10-31）、horizon、indicators（1–5 个，建议 3–5），可选 confidence、source_refs。
每个指标：indicator_id、metric、label、observe_period（尚未披露的季末报告期）、expected_low/expected_high
（至少一个，小数字符串，比率写小数如 "0.10"）、可选 invalidation_comparison（below/above）与
invalidation_threshold、rationale。
metric（与股价无关）：roe、roe_recurring、roic、gross_margin、net_margin、operating_margin、non_recurring_share、
revenue_growth、net_profit_growth、recurring_profit_growth、operating_cash_flow_growth、free_cash_flow_growth、
debt_to_assets、interest_coverage（TTM 或期末口径）、revenue、earnings、recurring_earnings、operating_cash_flow、
free_cash_flow（TTM 金额，元）；高成长另可用 rd_intensity、cash_runway_years。银行用 indicator.<披露字段>
（如 indicator.roe 年初累计、目标期写年报期；indicator.non_performing_loan_ratio、indicator.capital_adequacy
是期末值；库里已是小数，不良率 1.05% 写 "0.0105"）。估值和价格条件不写进论点，留给退出计划。
状态判定与口径：docs/modules/5-研究产物/观点账本模块.md「投资论点」。
"""

_THESIS_CHECK = """\
检查标的的有效论点（--user-id 才能看到用户论点）。没有论点时，请用户说出当初买入的理由与时间，改写为
指标后用 record-thesis 入账（reconstructed）。
读法：逐个指标写状态（符合、偏离、失效、待数据）、依据期（目标期或中期）和数值；中期状态只说明趋势，
不等于目标期结果；失效指标直接说明论点哪一部分已不成立。
"""

_EXIT_PLAN = """\
持仓的退出计划：论点、估值透支、风险预算、机会成本、时间止损五个维度的卖出条件，已触发与已设定的条件
自动入账；另有规则型行为偏差提示。用户说了打算（补仓、继续拿、卖一部分、清仓）时用 --intended-action，
有同风险替代要比较时用 --alternatives（只写股票；ETF 没有隐含回报，用估值分位定性比较）。
读法：
  - 各维度并列写出，冲突时说明如何取舍及理由，不取平均。“已触发”是现在成立的条件，“已设定”是今后出现时
    执行的条件，“证据不足”要说明缺什么（论点、约束快照）。
  - 估值透支的价格是触发线，不是目标价（低利率下常远高于现价）；机会成本的“隐含回报”是模型回报，不是
    预期收益；银行按近年 ROE 中位数计算，ROE 下行时偏高。
  - 只记录了一笔持仓时组合权重显示为 100%，不是真实占比，请用户补充组合规模。
  - 行为偏差提示写在建议之后，用平实语言，不说教。
  - 重建论点只从记录日起检查；买入日到今天已披露的各期是否兑现原理由，要读定期报告和分红公告写成探索发现。
  - 发布最终报告时把这份退出计划传给 assemble_and_publish，继续持有的结论没有退出条件会被复查拦下。
规则与阈值：docs/modules/6-编排/研究编排模块.md「退出计划与行为偏差提示」。
"""

_RECORD_HOLDING = """\
按用户原话记录持仓，只记用户明确说出的数字，不推算、不假设。本地没有的代码会自动补登；输出带代码、名称和
类型，用来核对是不是用户说的那只（同一代码可能是股票、指数或基金）。LOF 按用户的买法：场外申购记
open_fund。成本与买入日当天的成交区间对不上时，在回答里请用户核对。未投资的现金不记为持仓，运行组合命令时
用 --cash。场外基金买入日未知时赎回费可能被低估，请用户补充 --first-bought-on。
"""

_RECORD_PREFERENCE = """\
组合层投资偏好（期限、权益比例区间、目标波动、可接受最大回撤、单只个股上限、单一行业上限、现金底线、近期用钱），
按版本保存。先加 --dry-run 预览，把 readable / changes 念给用户确认后去掉 --dry-run 记录；--dry-run 返回的
personal_ref 未入账，不要写进请求。记录后把 personal_ref（preference:<id>）写进请求的 personal_refs。

第一次记录：问清期限和能接受的最大亏损，选最接近的档位（conservative 稳健 / balanced 平衡 / growth 成长 /
aggressive 进取）；用户说出的具体数值用 --set 字段=小数 覆盖（如 --set max_drawdown=0.25）；近期用钱用
--liquidity-need-amount <元> --liquidity-need-by <日期>。说明哪些是档位默认值（overrides 里的才是用户说的）。
用户说“最多亏 X”时讲清口径：系统按历史最差情景的跌幅控制权益比例。

“更激进”“稳一点”：先问清含义——higher_equity（更高权益比例）、more_concentrated（更集中）、
higher_volatility（更高波动）、longer_horizon（更长期限），可多选；用 --meaning（可重复）预览。含义只把对应
维度升一档（一档可能很大，如平衡→成长的权益下限 30%→60%，要说给用户）；前三种同时把目标波动和可接受回撤
升一档，但用户之前亲口说过的数值（overrides）保持不变，要改时用 --set 重新说明；更长期限不放宽回撤。
没有已记录的偏好时先按用户现状记录基线。不替用户选含义或档位，不把系统推算的数值写成用户说的。
PowerShell 里原话含中文引号时先放进单引号变量（$s='...'）再传入。
单只标的的仓位轨道需要 mandate 时，用 src.portfolio_risk.mandate_for_asset(preference, ...) 由偏好换算后经
container.portfolio_risk 保存。换算规则：docs/modules/4-个人数据/组合与风险管理模块.md「投资偏好」。
"""

_PORTFOLIO_DIAGNOSE = """\
组合诊断报告（report/组合诊断-<用户>-<日期>.md 与同名 JSON）。先确认持仓都已记录；用户提到未投资现金时用
--cash 计入。
读法：引用大类比例、穿透后的行业与个股暴露、重叠（直接持有加基金持有的合计）、组合波动（近 3 年与近 1 年）、
风险贡献占比（与权重不同，说明谁“贡献风险多于权重”）、高相关的持仓对、各历史情景的组合损失与同期沪深300。
口径必须写明：指数基金与 ETF 按跟踪指数月末成分权重全部穿透（写权重日期）；主动基金只穿透季报前十大，其余
“未穿透”，给出覆盖率与报告期（证监会行业分布只能大致说明去向，不与申万行业上限比较）；数据缺口出现“没有
可用的成分权重来源”时说明只穿透了前十大；用沪深300 代替或用 Beta 估算的持仓要点名；压力情景是历史重演，
不是预测。有偏好时逐项写“与偏好对照”中超出或低于的项目；诊断本身不给调整建议。
"""

_ALLOCATION = """\
配置建议报告（需要已记录的偏好）。先写目标权益区间和起作用的约束（偏好上限、目标波动、历史最差情景下的
可接受回撤、现金底线），再写当前比例与需要转入或转出的金额范围，最后写权益内部哪些持仓超过单只上限。
conflict=true：风险约束允许的权益低于偏好下限，后续调整方案和计划不生成。把报告里的两个选项和各自代价讲给
用户（守住风险约束、权益最多到多少；或保持权益下限、接受多大的历史最差损失与波动），用户选定后
record-preference 记录新版本再重跑，不替用户选。
偏好里两年内要用的钱会提高现金底线，摘要会写明。风险均衡参照（等风险贡献权重）只说明“按风险均衡谁多谁少”，
不是目标权重，不说成推荐。代表指数与用户持有的基金不同，引用波动和回撤估算时说明。
"""

_ADJUSTMENT = """\
调整方案与迁移路径（需要已记录的偏好）。增加的部分只给三类：--candidate 传入的标的（用户点名或已完成单标的
研究）、已持有的基金、默认宽基 ETF；不把默认 ETF 说成选股结论。
点名候选的额度是机械规则（不是对候选的看法，要如实说）：权益低于区间时先用候选补足；在区间内或需要降低时，
每个候选按“多一个等权席位”从现有权益持仓等比例腾出，受单只和行业上限约束。债券基金作为候选时减持释放的资金
由它们均分承接；不传时只进现有债券和现金类持仓，黄金与商品不加仓。
数量：股票与场内基金按每手 100 股（份）取整（变化不足一手不交易，只有清仓可以是零股），差额计入现金；场外
基金按金额；成本含每笔 5 元最低佣金。取整让目标明显偏离时（如只有 100 股的高价股只能全留或全卖）把取舍讲给用户。
回答顺序：目标区间和原因 → 分步路径（时间窗、卖买金额与数量、条件、成本）→ 调整前后对比（包括损失反而变大的
情景）→ 调整后仍不符合偏好的项目。给区间和窗口，不承诺精确日期和点位。
status=conflict 时没有迁移路径，按 allocation-proposal 的冲突处理。数据缺口出现资金缺口时告诉用户需要新增资金
或接受更低的目标；候选取不到价格会列在缺口里并被跳过。
"""

_PORTFOLIO_PLAN = """\
组合计划（需要已记录的偏好）。不加 --record 只生成预览：把节点表（定期与条件、金额与数量、时间窗）、目标区间与
概率、复盘节点和冲突处理讲给用户，用户确认后加 --record 入账（内容没变时不产生新版本）。
HTML 时间表与看板随报告生成（html_path）并已与计划逐项核对；html_problems 非空表示页面与计划不一致、未发布，
如实告诉用户，不手写 HTML 代替。
一年收益区间是目标权重的历史回放：说“历史上约 80% 的一年期收益落在 X 到 Y，按独立年份估计这一比例在 A–B 之间”，
不说成预测或承诺；日期都是时间窗。
单标的研究得出的买入或退出条件，要先发布最终报告或运行 exit-plan 入账才会进入计划；被排除的动作把原因告诉用户。
计划只纳入用户自己的动作，以及对计划里标的（或市场、宏观）的方向性观点与论点的检查日。
用户要“未来半年”之类的计划时：迁移步骤在六周内完成，目标区间窗口是执行后 90 天，季度与年度复盘延到一年；
回答只展开用户关心的时间段。偏好冲突时命令返回阻塞，按 allocation-proposal 的冲突处理。
入账后不必立即 decision-review；在复盘节点、或用户告知成交并 record-trade 后再运行。
"""

_RECORD_TRADE = """\
用户说出实际买卖时每笔记录一条，只记用户说出的数字。流水不改持仓，持仓变了还要 record-holding。流水与当前
持仓对不上时复盘报告会列缺口、不重建实际组合。有流水才有计划执行核对和实际回撤。
"""

_DECISION_REVIEW = """\
决策复盘与偏好校准报告。先运行 settle-ledger（或 routine）把到期检查点结算完。
战绩：命中率带 90% 区间，“样本少”的分组只列不解读；命中看各判断的结算规则（多为相对基准），择时贡献看
“照做”减“不动”的绝对收益，两者可能一正一负，分开说。来源为“开发样例”的不是真实判断，默认不计入
（--include-dev-seed 只用于开发检查）。把结果和当时的依据分开谈。
偏好校准：把“超出”的项目和“警示”类行为讲给用户，说明度量来源（实际组合、当前持仓回放、历史情景），给出两种
处理由用户选：确认能承受更大的回撤或波动（用原话 record-preference --set 记录新版本），或降低风险（走配置、
调整、计划）。不替用户改偏好；“未用上”只是信息，不据此劝用户加风险。
"""

_ROUTINE = """\
手动例行：结算到期判断 → 刷新市场与宏观档案 → 检查用户有效论点 → 列出到期的计划复盘节点。每一步单独失败，
失败写进 gaps，其余步骤照常运行。按输出的 next_actions 继续（论点偏离或失效 → exit-plan；有到期复盘节点或
新结算 → decision-review）。不建定时任务；结算对所有用户的到期条目生效。
source_cooldowns 列出当前因封禁或限流而暂停请求的数据来源及解除时间；取数失败是因为来源冷却时，如实告诉用户，不要反复重试。
"""

_SETTLE = """\
按检查点前向结算到期条目（价格类按复权总回报，指标类按检查点前已发布的数据）；--recompute 在数据修订后追加
新结果。股票与基金按复权总回报，指数只有价格指数；reference_value、observed_value 是复权水平，不是成交价。
"""

_LEDGER = """\
读取账本条目及每个检查点的最新结算和判定计数（只读）。条目 source_ref 以 dev-seed: 开头的是开发样例，不是真实
判断，不得当作用户观点或历史记录引用。
"""

_USER_CONTEXT = """\
查看用户、持仓、组合估值、投资任务、约束快照与偏好（最新版本与全部历史）。单用户本地使用时用 user:default。
"""

_RUN_SINGLE = """\
只生成基础研究包、workflow run 和 handoff，不生成决策简报、探索会话或最终报告；首选 run-recipe。用它的基础包做
决策研究时，先 container.research_state.save_decision_brief(...) 再启动探索（见 exploration_api.md）。
周期资源公司需要商品权重时用本命令。
"""

_PLAN_SINGLE = """\
只规划不执行：代码唯一时返回身份、profile 和基础任务草稿；歧义时返回候选供用户选择。
"""

_STRATEGY_ARCHIVE = """\
策略档案：机制、来源、适用范围、时间尺度、参数、失效条件、局限与生命周期状态。
  不带参数：列出所有策略（最新版本）的状态和迁移到下一状态还缺什么（next_blockers）。
  --strategy-id <id> [--version]：查看一个策略的档案、版本、事件记录。
  --register-json <path>：登记新策略或新版本，从“研究”开始；同一版本内容不同会被拒绝，改了就登记新版本号。
  --templates：列出策略模板（机制、参数与默认值、标的要求、用户说法示例）和 not_expressible（模板表达不了的规则）。
  --from-template <模板> --strategy-id <id> [--asset 代码:类型[:defensive] ...] [--set 名称=值 ...] [--name]：
    把用户描述的策略登记为“模板 + 参数”；来源已登记，直接进入“验证中”。策略族 = 模板 + 标的（选股为 scope），
    同族的所有参数变体共用尝试次数。模板、标的或参数不合规时退出码 2 并说明原因。
    用户描述对不上任何模板（或落在 not_expressible）时，按缺口回答：说明哪部分无法回测，不要硬套模板。
  --strategy-id <id> --transition <状态> --reason "<理由>" [--evidence-ref ...]：迁移状态；不满足门槛时
    返回 status=rejected 与 blockers（退出码 3），拒绝也会留痕。
生命周期：research 研究 → validating 验证中 → forward_tracking 前向跟踪 → enabled 启用，只能逐级前进；任何状态
都可以 retired 退役（写明触发了哪条失效条件）；退役后继续研究要登记新版本。
门槛：进入验证中需要 docs/evidence/strategy_sources/ 下的来源登记；进入前向跟踪需要一次 strategy-evaluate 完整验证（滚动样本外、
参数邻域、按尝试次数折扣后的显著性结果（只要求算出来，不要求显著；是否显著在作答时如实写），且样本外不劣于简单基准）；
启用需要前向跟踪满 6 个月、至少 12 个已结算信号、
命中率与超额不劣于基准。“启用”只表示可以在研究中作为“经前向检验”的证据引用，不代表自动交易。
登记 JSON 字段：strategy_id、version、name、kind（rule_screen 单标的条件规则 / timing 择时 / rotation 轮动 /
selection 选股 / allocation 配置与再平衡）、mechanism、universe、horizons（short/medium_short/medium_long/long）、
parameters（名称 → 字符串）、source_refs、failure_conditions（至少一条）、limitations，可选 family、template_id。
读法：回答“这个策略好不好”时先说状态——“研究”和“验证中”的策略没有经过本项目验证，只能说明机制与来源；
每次登记与状态变化都作为客观评估入账（对象 strategy:<id>）。
family_backtests：runs 是同族回测总次数（含重跑），trials 是不同「参数 + 样本区间」的尝试数；引用“尝试次数”用 trials。
用户描述的策略先在列表里按“模板 + 标的”找已有登记，有就直接用，不重复登记。
迁移的 --as-of-date 不要早于档案最近一次事件的日期。
"""

_STRATEGY_BACKTEST = """\
用正式入口回测一个已登记的策略（需要有信号实现：ETF 双动量，以及从模板登记的策略——均线择时、双动量轮动、
估值分位择时、因子选股、定期再平衡、分批建仓），自动记一次尝试。选股策略的参数 scope 写 sw:<申万代码> 或
symbols:<代码,...>，每月末只用当时已上市未退市的股票和已公告的年报；输出 selection 写候选与入选数量，limitations
写行业范围的幸存者偏差与估值口径，引用结果时一并说明。分批建仓与 buy_and_hold（一次性买入）对比着读。
  --set 名称=值 改参数（只能改档案里已有的参数；改参数也计入同一策略族的尝试次数）；--start-date 默认 2005-01-01，
  不足参数窗口时自动后移并写进 gaps；--lots 按每手 100 取整。
口径：总回报价格（分红再投资）；信号日收盘决定、下一交易日开盘成交，先卖后买；佣金万 2.5 最低 5 元、滑点万 5、
股票卖出印花税按当时税率；停牌与开盘涨跌停不可成交（skipped_orders）；闲置现金按 Shibor 3 个月计息。
输出：metrics（总收益、年化、波动、夏普〔超额于现金〕、最大回撤、换手、成本拖累、平均仓位）；benchmarks 四个简单
基准——同标的买入持有、标的池等权月末再平衡（单标的时省略）、沪深300 价格指数、现金；comparison 逐项对比；
capacity（95% 分位单笔占当日成交额 5% 时的资金规模）；family_attempts 同一策略族已回测的次数。
significance：trials 同族不同「参数 + 样本区间」的尝试数（同一组合重跑不增加）；psr 真实夏普大于 0 的概率；
dsr 按尝试数折扣后的概率（门槛 expected_max_sharpe_annual 是 N 次无技能尝试的期望最大夏普）；
min_track_record_years 在当前夏普与分布下 dsr 达到 95% 所需的年数；min_backtest_years 尝试数对应的最短回测
长度；verdict 一句话结论。引用显著性只用 dsr 与 verdict，并写明尝试数。
读法：先写与四个基准的对比和最大回撤，再写容量与成本；单次回测不是有效性证明，写明尝试次数；样本区间、
标的池选择偏差与 limitations 一并说明。不用回测曲线代替结论。
"""

_STRATEGY_EVALUATE = """\
完整验证一个已登记的策略，结果记为验证记录，供“验证中 → 前向跟踪”的门槛读取。
  rolling_oos：扩展窗口逐段样本外。训练段从起点到各段开始前（--train-years 首段长度，默认 3），检验段长 --test-years
    （默认 2），两者之间留 --gap-days 个交易日（默认 21）。参数不做搜索，只报告登记参数的逐段表现；每段并列
    简单基准（单标的为买入持有，标的集合与股票池为月末等权）。not_worse = 各段合并后的夏普不低于基准。
    各段 beat_benchmark 按夏普判（不是按收益）；两边都亏损的段里波动低的一方夏普反而更难看，
    这类段要同时写年化与回撤。
  neighbourhood：每个数值参数各取上下一档，在共同区间 common_start 起与登记参数配对比较；z_vs_base 是登记参数减邻近参数的
    Jobson–Korkie 统计量，≥ 1.645（单侧 5%）或邻近参数从未交易算“显著更低”；多数显著更低时 sensitive=true。
    每个邻近参数都作为一次尝试登记，计入折扣。
  regimes：按沪深300 三个维度划分市场状态（定义见 definitions，版本见 version），每种状态给出天数、独立段数、
    策略与简单基准的年化超额（超额于现金）与夏普、z_vs_benchmark（|z| ≥ 1.645 算显著）；readable=false（少于 126 天或
    3 段）只列不读。某日的收益归入前一交易日收盘时的状态。current 是样本最后一天所处的状态（用它说
    “现在属于哪一档”，口径与 market-archive 不同）；exposure_driven 列出策略仓位几乎由其决定的维度（各状态
    平均仓位相差 ≥ 0.7，如均线择时对趋势维度），这些维度只是复述规则，不解读；gaps 是状态划分本身的数据缺口。
  full_sample：全样本回测（与 strategy-backtest 同口径，psr、dsr、min_track_record_years 等字段见其 --help），
    significance 已计入邻域尝试。
  validation：四项是否具备（滚动样本外至少 2 段、邻域有结果、有折扣显著性结果、样本外不劣于基准）；
    deflated_sharpe=true 只表示算出了折扣显著性，显著与否看 significance.verdict。
  evaluation_id 是这次验证记录，迁移时作 --evidence-ref。
  next_blockers 是进入下一状态还缺什么。门槛满足后用 strategy-archive --transition forward_tracking 迁移。
读法：先写样本外合并结论与各段胜负，再写参数是否敏感，然后写折扣后的显著性，最后写哪些市场状态下显著强或弱
（只引用 readable=true 的状态）；各项都要带上样本区间与尝试数。
参数敏感或样本外劣于基准时，不要用全样本结果替策略辩护。
"""

_STRATEGY_TRACK = """\
前向跟踪：给处于“前向跟踪”或“启用”的策略记录截至最近交易日的信号（--all 跟踪全部；routine 也会自动跑一次）。
  信号按标的入账为客观可结算动作（来源 strategy:<id>:<版本>:<日期>）：持有的为买入，不持有的为退出（对照权重
  最大的持仓，没有持仓时对照现金）；结算周期用档案的第一个时间尺度，由 settle-ledger 或 routine 结算。
  同一交易日重复运行不重复记录（status=already_recorded）；两次运行之间错过的信号日写入档案事件
  forward_gap 和 missed_signal_dates，不补造。
输出：signal_date 是记录信号的交易日；rule_signal_date 是规则最近一次产生信号的日子（月末调仓的策略是上个月末，
  当前仓位就是那天的信号），rule_reason 是规则给出的理由；position 是按代码的目标仓位（“全部现金”即空仓）。
  forward：settled_signals 已结算的方向性信号数、hit_rate 与 90% 区间（上限 ≥ 50% 算不劣于掷硬币）、
  只用已记录信号重放的前向收益与简单基准之差 excess_return；还没有结算或重放数据时 hit_rate_not_worse /
  excess_not_worse 为 null（尚无法判断，不是不合格）。next_blockers 是迁到“启用”还缺什么：
  前向跟踪满 6 个月、至少 12 个已结算信号、命中率与超额不劣于基准。
读法：引用前向结果时写明跟踪起始日、已结算信号数与错过的信号日；样本少时只说“跟踪中”。
"""

EPILOGS: dict[str, str] = {
    "strategy-track": _STRATEGY_TRACK,
    "strategy-evaluate": _STRATEGY_EVALUATE,
    "route": _ROUTE,
    "run-recipe": _RUN_RECIPE,
    "record-view": _RECORD_VIEW,
    "market-archive": _ARCHIVE,
    "macro-archive": _ARCHIVE,
    "macro": _MACRO,
    "analog": _ANALOG,
    "industry": _INDUSTRY,
    "cross-section": _CROSS_SECTION,
    "valuation-position": _VALUATION_POSITION,
    "sector-archive": _SECTOR_ARCHIVE,
    "discover": _DISCOVER,
    "valuation-model": _VALUATION_MODEL,
    "record-thesis": _RECORD_THESIS,
    "thesis-check": _THESIS_CHECK,
    "exit-plan": _EXIT_PLAN,
    "record-holding": _RECORD_HOLDING,
    "record-preference": _RECORD_PREFERENCE,
    "portfolio-diagnose": _PORTFOLIO_DIAGNOSE,
    "allocation-proposal": _ALLOCATION,
    "adjustment-proposal": _ADJUSTMENT,
    "portfolio-plan": _PORTFOLIO_PLAN,
    "record-trade": _RECORD_TRADE,
    "decision-review": _DECISION_REVIEW,
    "routine": _ROUTINE,
    "settle-ledger": _SETTLE,
    "ledger": _LEDGER,
    "user-context": _USER_CONTEXT,
    "run-single": _RUN_SINGLE,
    "plan-single": _PLAN_SINGLE,
    "strategy-archive": _STRATEGY_ARCHIVE,
    "strategy-backtest": _STRATEGY_BACKTEST,
}
