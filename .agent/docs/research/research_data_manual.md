# 研究数据调用手册

## 统一入口

研究数据先经过 `DataAccessService`。不得直接查内部表、手工拼 Provider 或绕过主数据。

每次 `DataAccessRequest` 显式提供资产、dataset、起止日期、`as_of_date`、远程许可、来源顺序和超时。

## 常用数据

- 股票：`DATASET_STOCK_DAILY_BARS`
- ETF：`DATASET_EXCHANGE_FUND_DAILY_BARS`
- 指数：`DATASET_INDEX_DAILY_BARS`
- 财务：`get_financial_bundle()`
- 公告研报：`ResearchEvidenceService.get_bundle()`
- 场外基金：`get_open_fund_bundle()`
- ETF：`get_etf_bundle()`
- 黄金：`fetch_gold_spot_history()`
- 铜连续：`fetch_copper_continuous_history()`

商品序列是 DataAccess-first 的明确例外，只能通过上述显式 `DataFetchService` 方法取得，不使用动态 `call_api()`。

## 截止日期

1. 记录发布日期不晚于 `as_of_date`。
2. 财务指标从发布日期后可用。
3. 当前成分不得用于历史回测。
4. 商品和行情只比较共同日期。
5. stale 回退必须保留原始失败。

## ETF 证据

ETF 不走股票公告证据。ETF 报告引用：

- 交易所或基金公司费率文件
- 带日期的市场价与 IOPV
- 正式底层关系
- 黄金现货来源
- 版本化 derived 指标

费率使用 `official_etf_expense_schedule()`；市场价和 IOPV 使用 `fetch_exchange_fund_spot()`。

## 质量

检查 `complete`、coverage、sources、stale、error 和 attempted sources。日线必须通过 `DataQualityService`；`can_publish=False` 时停止完整结论。

## 证据层级

official 优先，third_party 明确来源，derived 保留版本和输入范围。

远程失败不得写成“没有数据”，必须报告异常、已尝试来源、旧数据状态和安全重试入口。
