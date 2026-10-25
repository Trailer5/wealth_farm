# 研究任务与 Profile 路由

## 先确定对象

先判断用户研究的是单标的、多个标的、指数/市场、发现候选还是外部事件。本文件只负责单标的正式基础任务；发现任务见 `discovery_handoff.md`，事件名称注意力见 `event_name_discovery.md`。

标的身份必须唯一。代码可能跨股票、ETF、场外基金或市场重复时，询问资产类型/市场，不按代码外形猜测。

## 当前 Profile

- 普通股票默认 `ordinary_company`。
- 银行、保险、证券和多元金融使用 `financial_institution`。
- 贵金属、工业金属、煤炭、油气、钢铁等强周期资源公司使用 `cyclical_resource`。
- ETF、LOF 和封闭式基金使用 `exchange_fund`。
- 场外开放式基金使用 `open_fund`。
- `high_growth_unprofitable` 已有 profile 定义，但尚未接入默认执行 dispatcher，不得作为可运行入口。

股票专业 profile 应依据项目已验证的行业 assignment 或明确业务事实选择；不确定时不得为了运行而套普通公司指标。

## 问题与时间尺度

项目任务类型和推荐映射：

- 事实理解：`understanding`；时间尺度按问题选择。
- 横向比较：`comparison`；比较窗口必须一致。
- 短期择时：`timing` + `short`。
- 事件投机：`speculation` + `medium_short`。
- 中长期投资：`investment` + `medium_long`。
- 周期位置：`cycle_reading` + `long`。
- 论文复核：`thesis_review` + `medium_long` 或 `long`。
- 风险复核：`risk_review`；按风险暴露选择尺度。

用户只给代码时，先以 `understanding` 建立基础事实，不把默认基础任务解释为用户只关心理解。基础包后按 `user_decision_model.md` 推进真正决策问题。

场外基金当前 execution 只支持 `understanding`。即使用户问“是否值得买”，基础任务仍使用理解型收集，投资、时点和仓位结论在探索阶段形成；不得把用户决策目标错误塞入不支持的基础 question。

## 当前公共入口

高层规划入口：

```text
python -m src.research_orchestration.cli plan-single
  --symbol <代码>
  --asset-type <资产类型，可选>
  --market <市场，可选>
  --exchange <交易所，可选>
  --decision-intent <understanding|evaluate_purchase|review_holding>
  --decision-lane <suitability|timing|position_sizing，可重复>
  --horizon <horizon，可选>
  --as-of-date <YYYY-MM-DD，可选>
```

代码唯一时，规划器返回身份、profile 和 `ResearchTaskSpec` 草稿；存在同代码股票、ETF 或基金时返回候选，不猜测。`plan-single` 不执行基础研究。

基础执行入口：

```text
python -m src.research_orchestration.cli run-single
  --symbol <代码>
  --asset-type <资产类型>
  --exchange <交易所，可选>
  --profile <profile_id>
  --question <question>
  --horizon <horizon>
  --as-of-date <YYYY-MM-DD>
```

也可使用 `--asset-id` 代替 symbol 身份参数。数据库、Provider、执行服务、基础装配、复查、图表和报告发布由默认容器统一管理。

Agent 使用规划器返回的 profile、question 和 horizon，不自行重选 execution service。当前仍没有将规划结果直接执行并创建探索 handoff 的 `run-bootstrap`；不得声称一次 `plan-single` 已生成报告。

## 状态处理

- `complete`：可以进入多轮探索，但仍不是最终建议。
- `partial`：读取每个 gap，判断是否阻塞当前决策轨道；可继续的轨道保留限制。
- `blocked`：停止 Agent 补算，修复底层数据/入口或正式报告不能继续。

任何 profile 的必需数据、指标或策略都不能由其他 profile 的近似项静默替代。

## 路由完成条件

- 身份唯一且 profile 与资产类型相容；
- question 与 horizon 合法；
- 用户决策目标和基础收集任务已分开；
- 使用统一编排入口，不手工构造 execution service；
- 已知道输出是基础研究包，并计划后续探索。
