# 研究指标与图表

## 指标入口

- 市场风险：`FactorService`
- 普通公司：`FundamentalAnalysisService`
- 金融机构：`FinancialInstitutionAnalysisService`
- ETF：`ExchangeFundMetricService`
- 商品敏感度：`CommoditySensitivityService`
- 相对表现：`BenchmarkAnalysisService`

指标必须保留截止日期、输入区间、单位、版本和来源。参数化因子必须显式选择参数。

历史普通公司指标使用 `analyze_history()`；每个 cutoff 显式提供市场价格，`flow_basis` 区分 annual 与 ttm。

## Profile 指标

- 普通公司：累计收益、最大回撤、PE、ROE、ROIC、FCF Yield
- 金融机构：累计收益、最大回撤、PB、ROE、资本充足率、净息差、不良率
- 周期资源：累计收益、最大回撤、PB、ROIC、净负债、商品敏感度
- ETF：累计收益、最大回撤、跟踪误差、溢折价、综合费率
- 黄金 ETF long：额外 `commodity_sensitivity`

不适用指标显示 not_applicable，不显示为零。

## 图表

使用 `research_visualization` builders 和 `ChartRenderer`。普通公司六类图表；ETF、金融机构和周期资源四类图表。

规则：

1. 图表截止日期与任务一致。
2. 点位不超过截止日期。
3. metadata 包含数据集、版本和 input digest。
4. benchmark 图声明 benchmark asset。
5. 空图和无单位图不进入报告。

## 特殊边界

- 商品 beta 不是经营因果。
- ETF 溢折价是时点值。
- 黄金 ETF 宽基 benchmark 与 `Au99.99` 商品 benchmark 分开报告。
- 周期资源商品权重必须显式提供且总和为 1。
