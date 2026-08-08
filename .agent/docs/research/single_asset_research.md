# 单标的研究执行

## 输入

必须先得到完整 `ResearchTaskSpec`：资产、profile、问题、horizon、`as_of_date`、适用 benchmark 和 peers。

## 正式执行入口

- 普通公司 medium_long：`ResearchExecutionService.execute()`
- ETF medium_long/long：`ETFResearchExecutionService.execute()`
- 金融机构 medium_long：`FinancialInstitutionResearchExecutionService.execute()`
- 周期资源 long：`CyclicalResourceResearchExecutionService.execute()`，并显式提供商品权重

四个入口统一执行身份、行情、质量、指标、基准、证据、图表和策略，返回阶段状态与 gap。

## 同行

普通公司同行使用 `PeerMetricExecutionService.execute()`：

1. 只处理 PeerSet 纳入成员。
2. 自动选择最新共同报告期。
3. 使用同一 cutoff、版本、单位和币种。
4. 排除成员不抓取财务数据。

## 报告

使用 `ResearchReportAssemblyService.assemble()` 将执行结果和可选同行结果转换为七步报告、三情景、最强反方、失效条件和缺口。

## Profile 边界

- 银行禁用 EV/EBITDA 和自由现金流收益率。
- ETF 禁用 ROE、ROIC 和普通公司估值。
- 商品敏感度只是历史共同变化。
- 黄金 ETF 长期规则只评价载体准备度。

## 时间窗口

默认 `end_date=as_of_date`，起点由 `StrategySpec.data_window_days` 推导。财务仍按发布日期过滤；更短窗口必须报告覆盖不足。

## 输出

任务规格、数据覆盖、指标值与版本、图表、基准/同行、策略状态、三情景、反方、失效条件、gap 和非投资建议声明。

不得使用测试 fixture 作为正式研究输入。
