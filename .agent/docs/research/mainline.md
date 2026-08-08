# Research 唯一主线

## 入口

所有真实研究先进入本文件。标的发现只负责生成候选任务，最终仍回到本主线。

## 主线

```text
确认身份与问题
  → 创建 ResearchTaskSpec
  → 按 profile 运行正式执行服务
  → 可选同行比较
  → ResearchReportAssemblyService
  → 报告复查与用户决策
```

## 1. 任务

1. 使用 `MasterDataService` 得到唯一 `asset_id`。
2. 明确 `profile_id`、`question_type`、`horizon` 和 `as_of_date`。
3. 使用 `ResearchTaskFactory.create()`。
4. benchmark 只有在唯一可靠身份时自动绑定；否则人工指定。

## 2. Profile 执行

- 普通公司中长期：`ResearchExecutionService`
- ETF 中长期或长期：`ETFResearchExecutionService`
- 金融机构中长期：`FinancialInstitutionResearchExecutionService`
- 周期资源长期：`CyclicalResourceResearchExecutionService`

执行结果必须包含固定阶段、来源、指标版本、图表、策略和 gap。`blocked` 时停止报告结论。

## 3. 比较

- 基准比较由 profile 执行服务完成。
- 普通公司同行使用 `PeerMetricExecutionService`，统一 cutoff 和共同报告期。
- 没有审阅 PeerSet 时报告缺口，不自动猜 peers。

## 4. 报告

使用 `ResearchReportAssemblyService.assemble()` 生成七步结构、三情景、最强反方、失效条件和缺口。

报告规则：

1. fact 必须有 evidence ref。
2. assumption 和 inference 不得伪装成事实。
3. `insufficient_data` 不得写成指标不合格。
4. 策略 pass 不等于买入建议。
5. 最终明确 `is_investment_advice=false`。

## 5. 复查

按 `research_report_review.md` 检查身份、截止日期、profile、单位、版本、证据、时间尺度和来源失败。

## Discovery 前置入口

Universe 请求进入 `asset_discovery.md`。只把 `research_task_draft` 交回本主线；不得直接从候选生成投资结论。

## 停止条件

- 身份不唯一。
- 必需数据、指标或证据缺失。
- 执行状态 blocked。
- 策略 `insufficient_data`。
- 事实晚于 `as_of_date`。
- profile 或时间尺度不匹配。
