# 研究任务路由

## 固定起点

1. 明确标的代码、市场和资产类型。
2. 明确问题、时间尺度和 `as_of_date`。
3. 使用 `MasterDataService` 确认唯一 `asset_id`。
4. 使用 `ResearchTaskFactory.create()`。
5. 单标的进入 `mainline.md`；universe 请求进入 `asset_discovery.md`。

## Profile

- 普通经营公司：`ordinary_company`
- 银行及金融机构：`financial_institution`
- 周期与资源公司：`cyclical_resource`
- ETF、LOF 与封闭式基金：`exchange_fund`
- 场外基金：`open_fund`
- 高成长未盈利公司：`high_growth_unprofitable`

股票默认 profile 只是普通公司。银行、资源和高成长未盈利必须显式指定。

## 身份

先用 `find_assets(name=..., asset_type=...)`。来源代码未登记时才用 `resolve_identifier()`。

代码 `000001` 同时存在股票与场外基金。平安银行必须满足 `standard_symbol="000001"`、`exchange="sz"` 和股票类型。

沪深300指数必须按指数类型、`exchange_hint="sh"` 解析 `000300`，不能用 ETF `510300` 替代。

## 问题与时间尺度

- timing → short
- speculation → medium_short
- investment → medium_long
- cycle_reading → long
- thesis_review → medium_long 或 long
- understanding、comparison、risk_review → 用户声明的时间尺度

“中长期风险研究”使用 `risk_review + medium_long`；“长期周期准备度”使用 `cycle_reading + long`。

## Benchmark

可向任务工厂注入 `DefaultBenchmarkResolver`：

- 普通公司 broad_market → 唯一沪深300指数
- ETF underlying_index → 已审阅 `FundAssetRelation`

金融行业、周期行业、成长风格和基金分类没有明确资产时保持空，必须人工指定。显式 benchmark 始终优先。

## 已验收关系

- 平安银行 peers：招商银行、兴业银行、浦发银行
- 黄金 ETF：`518880.SH`；黄金敏感度使用 `Au99.99`
- 沪深300ETF：`510300.SH`；底层指数 `000300`

平安银行正式 PeerSet 使用 `peer-set:pingan:bank:v1`，比较 ROE、资本充足率、净息差和不良贷款率。

## 输出深度

用户要求完整研究时使用 `output_depth=deep`；只在用户明确缩小范围时减少 requested outputs。

## 停止条件

身份歧义、时间尺度不兼容、必需数据不完整、指标不可用、策略 `insufficient_data`、事实无来源或晚于 `as_of_date` 时停止并报告缺口。
