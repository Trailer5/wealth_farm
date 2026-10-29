# 怎样查当前能力

本文不维护能力清单。能力是否存在、本次是否取得，按以下顺序查：

1. **问题层**：`route --request-json <path>` 的 `steps` 与 `gaps`。步骤状态不是 available 的就是当前缺口，`description` 说明缺什么、属于哪个系统模块和路线图阶段。
2. **命令层**：`python -m src.research_orchestration.cli --help` 列出全部命令；`<命令> --help` 给出参数、字段、示例和读法。
3. **标的研究层**：`container.capability_registry.resolve(profile_id=..., question_type=..., decision_lane=...)`。
   - `automated_capabilities`：确定性流水线已声明的能力，不表示本次一定取得数据。
   - `agent_query_capabilities`：基础包后可安排的探索查询（`ResearchQueryPlan` 的 `capability_id`）。
   - `required_user_context`：该决策轨道需要的用户上下文。
   - `unsupported_capabilities`、`question_supported=false`、`lane_unsupported_capabilities`：正式缺口，保留，不用 prompt 补。
4. **本次运行**：以 `ResearchRunResult` 和 `BaselineResearchHandoff`（事实章节、阶段状态、非阻塞缺口、阻塞缺口、能力缺口）为准。注册表不替代运行结果，运行结果也不能扩张注册表标注的不支持项；不用其他 profile 的指标补当前 profile 的缺口。
5. **口径与设计**：`docs/modules/README.md` 找到所属模块文档，看“设计说明”。

## 遇到缺口

1. 按工程阻塞或正式不可用状态处理，照实告诉用户缺什么、影响哪部分结论。
2. 不手工串 service、不直接查 SQLite 或 Provider、不复刻 smoke、不编造不存在的入口（如 `run-bootstrap`）。
3. 可做的补充只有：研究截止日之前的官方公开资料（登记 URL、标题、来源和发布日期），按回答纪律 3 标注。

## 主要公共服务

探索与发布脚本只用 `create_default_container()` 的公共服务：`ResearchRequestPlanner`、`PersistentResearchExplorationService`、`ResearchExplorationQueryService`、`SuitabilityAssessmentService`、`EntryPlanService`、`PositionPlanService`、`FinalDecisionReportService`、`MonitoringHandoffService`、`ResearchLedgerService`、`NewConversationAcceptanceRunner`。调用顺序见 `mainline.md`，调用写法见 `exploration_api.md`。
