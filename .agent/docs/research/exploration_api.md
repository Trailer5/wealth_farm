# 探索到最终报告的调用参考

本文是 `mainline.md` 第 5～13 步的调用手册：基础包完成后，Agent 如何用 Python 调用公共服务完成探索、三轨装配和最终发布，而不用读源码。研究方法和门禁含义见 `exploration_playbook.md` 与 `final_report_contract.md`。

## 一、准备

1. 只用 `create_default_container()` 返回的公共服务；不得直接查 SQLite、调 repository 或 Provider。
2. 脚本放在 `temp/`，必须写 `if __name__ == "__main__":`。部分查询会在 Windows 上启动子进程，没有这层保护时子进程会重跑整个脚本、重复入账。
3. 每个脚本只做一步（建会话、一批查询、记一轮、装配发布）并把 ID 打印出来，下一步脚本从 ID 继续；会话和账本已持久化，进程重启可恢复。
4. 所有对象从包顶层导入：

```python
from src.research_orchestration import (
    create_default_container, ResearchQueryPlan, ResearchQueryRoute,
    ExplorationQuestion, ExplorationTrack, QuestionPriority,
    ResearchHypothesis, HypothesisStatus, HypothesisRole,
    ResearchExplorationRound, ResearchFinding, ResearchFindingKind,
    HypothesisImpactRecord, HypothesisImpact, EvidenceTier,
    SuitabilityDimension, SuitabilityInputKind,
    TimingDimension, TimingInputKind, TimingConditionState,
    FinalReportNarrativeInput, FinalScenarioBlock, FinalScenarioKind,
    FinalEvidenceCitation, FinalEvidenceKind, PositionCalculationInput,
)
from src.research_contracts import ResearchHorizon, ProbabilityInterval
from src.user_space import UserContext
```

## 二、取得会话

```python
container = create_default_container()
context = UserContext(user_id="user:default")          # 与请求的 user_id 一致
run = container.workflow_coordinator.get_workflow_run(workflow_run_id)
handoff = container.workflow_coordinator.get_handoff_for_run(workflow_run_id)
brief = container.research_state.get_decision_brief(context, asset_id=run.task.asset_id)
session = container.exploration.start_from_handoff(
    context, workflow_run_id=workflow_run_id, decision_brief=brief,
)
```

- `workflow_run_id` 来自 `run-recipe` 输出的 baseline 步骤 `artifacts`；同处的 `decision_brief_id` 是 `run-recipe` 按请求保存的决策简报（意图、轨道、期限；缺的组合角色等列在 `missing_user_context`，不编造）。
- 会话启动时绑定简报，之后不能换绑；最终发布要求会话与简报一致。用 `run-single` 或不带 `user_id` 的请求跑基础包时没有简报，必须先调用 `container.research_state.save_decision_brief(context, asset_id=..., decision_intent=..., decision_lanes=..., horizon=run.task.horizon, as_of_date=run.task.as_of_date)` 再启动会话。
- 用户之后补充组合角色、持仓或约束时，用同一方法更新简报（`brief_id` 不变），装配时传入最新简报。
- 会话已存在时 `start_from_handoff` 直接返回原会话；也可用 `container.exploration.resume_for_workflow(context, workflow_run_id=...)` 或 `get_session(context, session_id=...)`。
- 种子内容：每个决策轨道一个阻塞问题（`decision-question:suitability|timing|position_sizing`），请求适配轨道时另有阻塞问题 `decision-question:alternative`；候选假设 `candidate-hypothesis:N` 挂在声明它的轨道；反方假设固定为 `strongest-counterargument`。

## 三、查询

```python
plan = ResearchQueryPlan(
    query_id="q-001", session_id=session.session_id, question_id="decision-question:suitability",
    capability_id="official_document_query", route=ResearchQueryRoute.RESEARCH_EVIDENCE,
    expected_evidence=("最近一期定期报告",), completion_criteria="拿到报告标题、日期和链接",
    as_of_date=session.task.as_of_date,
    parameters={"start_date": "2026-01-01", "end_date": session.task.as_of_date},
)
execution = container.queries.execute(context, session_id=session.session_id, plan=plan)
result = execution.result      # outcome、summary、evidence_refs、metadata、gap
session = execution.session
```

`query_id` 在会话内唯一；问题必须仍为 open；`as_of_date` 不得晚于任务截止日。

| route | capability_id | parameters | 结果值（`result.metadata`） |
| --- | --- | --- | --- |
| `DATA_ACCESS` | `dataset_query` | 必填 `dataset`、`start_date`、`end_date`；可选 `allow_remote`、`remote_sources`、`provider_timeout_seconds` | `latest_records`（最近 5 条）、`coverage_start/end` |
| `RESEARCH_EVIDENCE` | `official_document_query` | 必填 `start_date`、`end_date`；可选 `disclosure_sources`、`research_sources` | `items`（标题、日期、来源、层级、`source_url`，最多 20 条）、`events` |
| `BENCHMARK_ANALYSIS` | `benchmark_comparison_query` | 必填 `start_date`、`end_date`；可选 `benchmark_asset_id`（指数或 ETF 的 asset_id） | `metrics`（超额、跟踪误差、Beta 等）、区间 |
| `EVENT_KNOWLEDGE` | `event_lookup` | 可选 `start_date`、`end_date`、`event_type` | `events` |
| `INDUSTRY_ANALYSIS` | `industry_assignment_query` | 无 | 行业名称；只有画像行业名时为 `partial` |
| `PEER_ANALYSIS` | `peer_comparison_query` | 必填 `peer_set_id`；可选 `metric_names` | `comparisons`（目标值、中位数、分位） |
| `USER_RESEARCH_STATE` | `user_context_query` | `operation`: `decision_brief` 或 `holding` | 简报缺项或持仓 |
| `PORTFOLIO_RISK` | `portfolio_snapshot_query` | `operation`: `list_snapshots`，或 `snapshot` 加 `snapshot_id` | 快照 ID |
| `EXTERNAL_OFFICIAL_WEB` | `external_official_web_research` | 必填 `url`（HTTPS）、`title`、`source_name`、`published_at`；可选 `content_excerpt` | 登记信息；内容由 Agent 自己阅读 |

数据集名：`stock_daily_bars`、`index_daily_bars`、`fund_exchange_daily_bars`、`stock_total_return_bars`、`fund_exchange_total_return_bars`、`disclosures`、`research_reports`、`open_fund_nav`。没有多期财务报表数据集，多期财务要读官方定期报告原文。

价格口径：`*_daily_bars` 是未复权实际成交价，只用于读最新价格、成交量和估值；算区间收益、回撤或比较标的时用 `*_total_return_bars`。后者按交易所前收盘价（除权参考价）链接前复权：相邻收盘比值等于含分红再投资的日总回报，窗口最后一条保持实际成交价，`unadjusted_close` 是当日未复权收盘价；不同查询窗口的绝对水平不同，只能在同一次结果内求比值。指数只有价格指数，不含分红，用它做基准时超额收益偏高约一个区间股息率。

查询只登记证据，不回答问题。`business_empty`、`remote_failure` 等落在阻塞问题上会记为阻塞缺口；该问题之后用其他证据回答，缺口即视为解决。

## 四、记录一轮

```python
finding = ResearchFinding(
    finding_id="f-return-1", text="……", kind=ResearchFindingKind.FACT,
    as_of_date=session.task.as_of_date, track=ExplorationTrack.SUITABILITY,
    evidence_refs=(report_url,), source_tier=EvidenceTier.PRIMARY,
    limitations=("……",),
    suitability_dimension=SuitabilityDimension.EXPECTED_RETURN_SOURCE,
    suitability_input_kind=SuitabilityInputKind.COMPANY_FUNDAMENTALS,
    hypothesis_impacts=(HypothesisImpactRecord("candidate-hypothesis:1", HypothesisImpact.SUPPORTS, "……"),),
)
session = container.exploration.add_round(context, session_id=session.session_id, round_result=ResearchExplorationRound(
    round_id="r-1", objective="……", queries=("q-001",), findings=("……",),
    evidence_refs=(report_url,), structured_findings=(finding,), next_step="……",
))
```

轮次规则：`objective`、`queries`（本轮用到的查询 ID 或来源说明）不能为空；必须有 `structured_findings`、`evidence_refs`、`next_step` 和至少一条假设影响（轮次或发现上）；引用的假设和问题必须已存在；发现日期不得晚于截止日。装配只承认出现在某轮 `evidence_refs` 里的引用。

发现规则：

- `kind`：`fact`、`inference`、`assumption`、`unknown`。fact 必须有引用且 `source_tier` 不是 `unknown`。
- `source_tier`：`primary`（官方原文）、`secondary`（第三方）、`derived`（项目计算）、`user_context`。
- 带 `suitability_dimension` 的发现必须有引用、限制、`suitability_input_kind`、非 unknown 层级。`alternative`、`opportunity_cost` 必须放在 `ExplorationTrack.ALTERNATIVE`，并写 `subject_asset_id`（替代标的的 asset_id，不能是本标的）和 `comparable_exposure`（两者共用同一组值）；其余维度放在 `SUITABILITY`。
- 适配维度与输入类型的对应：`expected_return_source` 用 profile 输入（普通公司 `company_fundamentals`、金融机构 `financial_institution_quality`、周期 `cycle_economics`、ETF `etf_exposure_efficiency`、场外基金 `open_fund_manager_style`）；`downside` 用 `downside_risk`；`priced_expectation` 用 `benchmark_relative`；`alternative` 与 `opportunity_cost` 用 `comparable_alternative`。前两项缺失为核心缺口（结论 insufficient），后三项缺失只把 consider 降为 watch。
- 时点发现放在 `ExplorationTrack.TIMING`，必须写 `timing_dimension`、`timing_input_kind`、`timing_state`、`signal_horizon`、`timing_valid_from/until`（截止日落在有效期内）、`observability_key`、引用、限制和层级。状态取值按维度：`current_regime` 用 supportive/neutral/adverse/unknown；`next_tradable_time` 用 met/unknown 且必须写带时区的 `next_tradable_at`；`catalyst_window` 必须写窗口起止；`execution_constraint` 另可用 adverse；其余用 met/not_met/watch/unknown。
- 时点装配要求九个维度都有发现：`current_regime`、`entry_condition`、`wait_condition`、`tranche_rule`、`avoid_condition`、`catalyst_window`、`invalidation_condition`、`next_tradable_time`、`execution_constraint`；普通公司还要覆盖输入类型 `valuation_regime`、`trend_state`、`catalyst_timeline`、`liquidity_state`、`market_risk_state`、`trading_calendar`（其他可选：`cycle_position`、`etf_premium_discount`、`fund_dealing_rule`、`cross_horizon`）。信号有效期取所有条件有效期的交集，最终报告的复查日也不会晚于它；下一可交易时间等短效条件的有效期要与其他条件一致，否则复查日会被压到当天。
- 证据引用约定：官方原文用其 HTTPS 链接作引用（发布时引用的 `url` 必须与发现中的链接一致）；项目查询结果用 `result.evidence_refs`（如 `evidence:query:<query_id>`）；第三方研报用证据列表里的 `evidence_id`；基础包里的指标可以用基础包相对路径（handoff 的 `report_path`）作 derived 引用；外部官方网页用登记返回的 `evidence:external:*` 并同时写链接。

## 五、假设、问题与 ready

```python
container.exploration.add_question(context, session_id=sid, question=ExplorationQuestion(
    question_id="q:decline", text="……", reason="……", blocking=False, track=ExplorationTrack.SUITABILITY))
container.exploration.update_hypothesis(context, session_id=sid, reason="……", hypothesis=ResearchHypothesis(
    hypothesis_id="candidate-hypothesis:1", statement="……", status=HypothesisStatus.SUPPORTED,
    supporting_evidence_refs=(report_url,), track=ExplorationTrack.SUITABILITY,
    falsification_conditions=("……",)))
container.exploration.answer_question(context, session_id=sid, question_id="decision-question:suitability",
    answer="……", evidence_refs=(report_url,))          # 阻塞问题必须带引用
container.exploration.defer_question(context, session_id=sid, question_id="…", reason="……")
container.exploration.mark_ready(context, session_id=sid)
```

`update_hypothesis` 用同 ID 覆盖当前状态并追加历史；新 ID 会新增假设。假设的支持与反证引用必须全部出现在某轮的 `evidence_refs` 里，否则装配时该假设被丢弃。`mark_ready` 要求：至少一轮；阻塞问题都不为 open；每个请求轨道的问题都不为 open 且该轨道有发现；请求适配时替代方案问题不为 open；所有主假设（含种子假设）都有证伪条件且无未解决冲突；`strongest-counterargument` 已不是 active 且有证据。不打算验证的种子假设改为 `weakened` 或 `rejected` 并写明原因与证伪条件。仓位问题要带引用回答（例如用 `user_context_query` 的简报缺项结果），延后会让仓位装配记为问题未回答。

## 六、三轨装配

```python
brief = container.research_state.get_decision_brief(context, asset_id=session.task.asset_id)
suitability = container.suitability.assemble(session=session, decision_brief=brief)
entry = container.entry.assemble(session=session, suitability=suitability, decision_brief=brief)
position = container.position.assemble(context=context, session=session, suitability=suitability,
    entry_plan=entry, decision_brief=brief, calculation_inputs=None)
```

- 每个结果的 `assembly_gaps` 列出未满足的门禁代码，含义见 `final_report_contract.md`；不满足就如实发布对应状态，不为过门禁编造输入。
- 简报缺组合角色时适配结论为 insufficient（`portfolio_role_missing`），时点和仓位随之不装配；用户补充后更新简报再装配。适配结论：任一适配主假设 supported 且无缺口为考虑，反方 supported 为回避。
- 仓位需要简报中的 `mandate_id`、`portfolio_constraint_snapshot_id`（由 `container.portfolio_risk` 保存用户确认的投资任务、组合估值和约束快照）以及 `PositionCalculationInput`（`asset_id`、`as_of_date`、`subject_price`、`realized_volatility`、`current_drawdown`、可选 `timing_score_percentile`、`benchmark_volatility`、`evidence_refs`、`limitations`；引用必须已入账）。缺任一项时传 `calculation_inputs=None`，结果为 `user_context_required`。

## 七、最终发布

```python
narrative = FinalReportNarrativeInput(
    scenarios=tuple(FinalScenarioBlock(kind=k, assumptions=("……",), observable_variables=("……",),
        decision_impact="……", limitations=("……",), evidence_refs=(...)) for k in FinalScenarioKind),
    evidence_citations=(
        FinalEvidenceCitation(reference_id=report_url, kind=FinalEvidenceKind.OFFICIAL, title="……",
            source_name="巨潮资讯", published_at="2026-08-15", url=report_url),
    ),
    chart_paths=tuple(item.markdown_path for item in handoff.chart_artifacts),
    review_date="2026-12-31", additional_unknowns=("……",),
)
published = container.final_decision.assemble_and_publish(handoff=handoff, session=session,
    decision_brief=brief, suitability=suitability, entry_plan=entry, position_plan=position, narrative=narrative)
```

- 情景必须恰好三个（optimistic、base、pessimistic），每个都要假设、可观察变量、决策影响和限制。
- 报告用到的引用 = 核心论文、三轨陈述、情景、最强反方和监测计划的引用之和；监测计划会带上所有主假设（含时点、仓位假设）的证伪条件及其引用。引用表必须与它完全相等，不相等时报错会列出未引用（uncited）和多余（unused）的引用。
- 引用必须与报告实际用到的引用一一对应，且每个引用都出现在某个发现的 `evidence_refs` 中。引用类型须与发现层级相容：primary→official 或 reported，secondary→reported，derived→derived（须写 `methodology` 和 `limitations`），user_context→user_context。official 必须有 `published_at` 和与发现中一致的 `url`；发布日期不得晚于发现日期。
- `chart_paths` 只能取 handoff 已发布的图表。
- `published.published` 为 None 时表示复查未通过，原因在 `published.review`；成功时报告写入 `report/`，三轨判断自动入观点账本，`published.ledger` 为入账结果。
- 同一 workflow run 只能发布一次最终报告；发布后会话仍显示 ready_for_report，重复发布会报错。先用 `container.final_decision.assembly.assemble(...)`（参数同上）在内存里装配，确认无误再正式发布。
