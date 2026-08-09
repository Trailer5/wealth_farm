# Research 唯一主线

## Agent 只做什么

先读 `agent_charter.md`。Agent 负责澄清问题、解释证据、推演情景、寻找反方、确认研究方向和协助用户决策。

## 确定性流程

身份解析、任务创建、profile 分发、第一阶段数据获取、质量检查、基础指标、基础图表、规则计算和基础研究包落盘必须由 `src/research_orchestration/` 的公共入口完成。

Agent 不得在对话中手工拼装这些服务，不得复制 smoke test 的依赖注入，不得逐项执行固定 checklist，也不得自行决定报告文件位置。

## 研究策略选择

在调用入口前读取 `research_strategy_playbook.md`，按事实理解、比较、中长期投资、短期择时、事件投机、发现探索、市场指数或报告复核选择不同研究策略。任务类型改变时必须重新路由，不能沿用上一任务的假设和证据门槛。

## 单标的研究

1. 从用户话语中确认标的、问题、时间尺度、截止日期和输出深度。
2. 有实质歧义时只询问影响结论的最小问题。
3. 调用统一研究编排入口。CLI 形式：

```text
python -m src.research_orchestration.cli run-single
  --symbol <代码>
  --asset-type <资产类型>
  --exchange <交易所>
  --profile <profile_id>
  --question <question>
  --horizon <horizon>
  --as-of-date <YYYY-MM-DD>
```

数据库、Provider、四类执行服务、基础复查和 `report/` 发布由默认生产容器统一装配。该命令只生成基础研究包，不生成最终报告。
4. 基础收集为 `blocked` 时，先修复底层数据或确认用户选择；不得用推测填补。
5. 创建 `ResearchExplorationSession`，记录核心假设、关键问题和阻塞问题。
6. Agent进行多轮探索，每轮必须包含：
   - 本轮要验证或证伪的目标；
   - 针对性查询、联网研究或项目数据调用；
   - 新发现及其证据；
   - 对原假设的支持、削弱或否定；
   - 下一轮问题。
7. 遇到会改变研究范围、时间成本或决策目标的分歧时与用户交互；例行查询不逐项请示。
8. 当关键阻塞问题已回答、反方已建立、主要情景可解释时，将探索会话标记为可出报告。
9. Agent根据全部探索轮次撰写最终结论，发布服务只负责图表、路径和落盘。

## 标的发现

发现流程由 discovery 编排入口运行。Agent只接收带入选、淘汰、数据缺口和理由的候选结果，按 `discovery_handoff.md` 做优先级判断。

## 禁止回退

统一入口缺失或失败时，应把它作为工程阻塞修复；不得退回旧式手工 service-by-service 流程。
