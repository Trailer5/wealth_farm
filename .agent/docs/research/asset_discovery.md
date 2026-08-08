# 标的发现入口

## 定位

标的发现生成研究候选，不生成买入建议。候选最终回到 `mainline.md`。

## 流程

1. `ResearchUniverseService.get()` 读取明确版本。
2. 检查 `historical_membership` 和 effective date。
3. `ResearchProfileAssignmentService` 获取逐成员 profile。
4. `DiscoveryPreparationJobService` 手动分批准备真实指标。
5. `StoredDiscoveryMetricLoader` 读取已存指标。
6. 创建 `default_quality_value_discovery_spec()`。
7. `SecurityDiscoveryService.run()`。
8. 只把 candidate 的 `research_task_draft` 交回研究主线。

沪深300 universe ID 为 `csi300`。没有 snapshot 时，只有用户允许远程获取后才 refresh。

## 批处理

job 必须显式 batch size、profile map 和重试上限。ready 使用 cache，exhausted 不自动重试。不得在一次 Agent 运行中循环到完成。

当前验证状态：300 个成员已分类；剩余普通公司指标按用户指令分批续跑。

## 状态

- candidate
- passed_not_selected
- eliminated
- insufficient_data
- unresolved
- inactive

数据不足不等于淘汰。所有成员必须有最终状态并保存原因、指标版本和任务草稿。

当前成分不得用于历史回测；发现结果不得声明未来收益或交易动作。
