# 基金与 ETF 研究数据模块

## 模块职责

本模块负责把场外基金净值、平台估值、申赎状态、费率、规模、配置和 ETF 行情组织为资产类型专用研究数据包，并保存基金管理人与底层指数的最小关系。

## 目录范围

1. `src/fund_research/`：基金关系、repository 和研究 bundle service。
2. `src/tests/fund_research/unit_tests/`：数据隔离、bundle、关系和缓存测试。
3. `src/tests/fund_research/smoke_tests/`：真实基金数据验证。

## 当前 datasets

1. `open_fund_nav`
2. `fund_estimates`
3. `fund_status`
4. `fund_scale`
5. `fund_allocation`
6. ETF 使用已有 `fund_exchange_daily_bars`

## 数据边界

1. 正式净值与平台估值分开返回。
2. 申赎和费率记录观察时间与来源。
3. 规模和配置记录报告期。
4. ETF 行情与场外基金净值不混用。
5. 基金产品指标与基金公司财务指标不混用。
6. 没有底层指数关系时不允许计算跟踪误差。

## 最小关系

1. `manager`：当前保存管理人名称、来源、生效日期和置信度。
2. `underlying_index`：必须引用 `index` 类型的稳定 `asset_id`。

当前关系通过显式注册建立，不从基金名称猜测。

## 当前能力

1. 五类基金数据接入统一访问。
2. 支持本地 coverage、AkShare 补取和旧数据回退。
3. 支持 `FundResearchBundle` 与 `ETFResearchBundle`。
4. 支持管理人与底层指数关系。
5. 支持 `index` 主数据资产类型。
6. 基金状态查询包含 `observed_at`。
7. Provider 原始记录中的日期对象可安全序列化。
8. 研究资产模板使用当前 dataset 名称。
9. ETF 市场价格与 IOPV 溢折价、官方综合费率使用 `exchange_fund_metric_v1` 计算并保存来源。
10. 沪深 300 ETF 中长期跟踪效率策略已完成真实验收。
11. `exchange_fund_flow_v1` 已实现 ETF 份额变化率、估算净申购和宽基篮子加权聚合；结果明确标记为估算，不推断国家队身份。
12. AkShare 适配器已接入沪深交易所 ETF 份额和最新两日参考净值。

代码 `000001` 的股票与场外基金身份隔离测试通过。真实场外基金净值 smoke 返回 2025-01-01 至 2026-08-03 共 382 条正式净值。

## 当前缺口

1. 管理人和底层指数自动来源。
2. ETF 历史溢折价、完整 NAV 与拆分折算序列。
3. 费率对长期收益的累计影响和持仓暴露指标。
4. 基金经理评价。
5. 持仓穿透和历史配置完整性。
6. 官方 PCF 解析、份额/NAV 自动落库和宽基篮子正式目录。
