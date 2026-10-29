# 探索到最终报告：调用步骤

`mainline.md` 第二、三节的 Python 写法。每一步的字段规则在对应类或方法的文档字符串里，写脚本前用 `python -c "from src.research_orchestration import X; help(X)"` 查看；门禁含义见 `exploration_playbook.md` 与 `final_report_contract.md`。

## 规矩

1. 只用 `create_default_container()` 的公共服务；不直接查 SQLite、repository 或 Provider。
2. 脚本放 `temp/`，必须有 `if __name__ == "__main__":`（部分查询在 Windows 上起子进程，没有它会重跑整个脚本、重复入账）。
3. 一个脚本只做一步（建会话、一批查询、记一轮、装配发布），打印 ID，下一步从 ID 继续；会话与账本已持久化。
4. 所有对象从 `src.research_orchestration` 包顶层导入；另用 `src.research_contracts`（`ResearchHorizon`、`ProbabilityInterval`）和 `src.user_space.UserContext`。

## 1. 取得会话

```python
container = create_default_container()
context = UserContext(user_id="user:default")          # 与请求的 user_id 一致
run = container.workflow_coordinator.get_workflow_run(workflow_run_id)   # run-recipe 基础步骤 artifacts
handoff = container.workflow_coordinator.get_handoff_for_run(workflow_run_id)
brief = container.research_state.get_decision_brief(context, asset_id=run.task.asset_id)
session = container.exploration.start_from_handoff(context, workflow_run_id=workflow_run_id, decision_brief=brief)
```

1. 没有简报（`run-single` 或请求不带 `user_id`）：先 `container.research_state.save_decision_brief(context, asset_id=..., decision_intent=..., decision_lanes=..., horizon=run.task.horizon, as_of_date=run.task.as_of_date)`。会话启动后不能换绑简报。
2. 用户补充组合角色、持仓或约束：用同一方法更新简报，装配时传最新简报。
3. 已有会话：`start_from_handoff` 返回原会话；或 `resume_for_workflow(context, workflow_run_id=...)`。
4. 种子：每个决策轨道一个阻塞问题 `decision-question:suitability|timing|position_sizing`（请求适配时另有 `decision-question:alternative`），候选假设 `candidate-hypothesis:N`，反方 `strongest-counterargument`。

## 2. 查询（`help(ResearchQueryRoute)`）

```python
plan = ResearchQueryPlan(query_id="q-001", session_id=session.session_id,
    question_id="decision-question:suitability", capability_id="official_document_query",
    route=ResearchQueryRoute.RESEARCH_EVIDENCE, expected_evidence=("最近一期定期报告",),
    completion_criteria="拿到报告标题、日期和链接", as_of_date=session.task.as_of_date,
    parameters={"start_date": "2026-01-01", "end_date": session.task.as_of_date})
execution = container.queries.execute(context, session_id=session.session_id, plan=plan)
```

## 3. 记一轮（`help(ResearchFinding)`、`help(ResearchExplorationRound)`）

```python
finding = ResearchFinding(finding_id="f-1", text="……", kind=ResearchFindingKind.FACT,
    as_of_date=session.task.as_of_date, track=ExplorationTrack.SUITABILITY,
    evidence_refs=(report_url,), source_tier=EvidenceTier.PRIMARY, limitations=("……",),
    suitability_dimension=SuitabilityDimension.EXPECTED_RETURN_SOURCE,
    suitability_input_kind=SuitabilityInputKind.COMPANY_FUNDAMENTALS,
    hypothesis_impacts=(HypothesisImpactRecord("candidate-hypothesis:1", HypothesisImpact.SUPPORTS, "……"),))
session = container.exploration.add_round(context, session_id=session.session_id,
    round_result=ResearchExplorationRound(round_id="r-1", objective="……", queries=("q-001",),
        findings=("……",), evidence_refs=(report_url,), structured_findings=(finding,), next_step="……"))
```

## 4. 假设、问题与 ready（`help(container.exploration.mark_ready)`）

```python
container.exploration.add_question(context, session_id=sid, question=ExplorationQuestion(...))
container.exploration.update_hypothesis(context, session_id=sid, reason="……", hypothesis=ResearchHypothesis(...))
container.exploration.answer_question(context, session_id=sid, question_id="...", answer="……", evidence_refs=(...))
container.exploration.defer_question(context, session_id=sid, question_id="...", reason="……")
container.exploration.mark_ready(context, session_id=sid)     # 先做第 5 步的内存装配
```

`update_hypothesis` 同 ID 覆盖并追加历史；假设的支持与反证引用必须出现在某轮 `evidence_refs` 里。阻塞问题的回答必须带引用；仓位问题也要带引用回答（如 `user_context_query` 的简报缺项）。

## 5. 三轨装配

```python
brief = container.research_state.get_decision_brief(context, asset_id=session.task.asset_id)
suitability = container.suitability.assemble(session=session, decision_brief=brief)
entry = container.entry.assemble(session=session, suitability=suitability, decision_brief=brief)
position = container.position.assemble(context=context, session=session, suitability=suitability,
    entry_plan=entry, decision_brief=brief, calculation_inputs=None)   # help(PositionCalculationInput)
```

1. 看每个结果的 `assembly_gaps`；不满足就如实发布对应状态，不为过门禁编造输入。
2. 仓位需要简报里的 `mandate_id`、`portfolio_constraint_snapshot_id` 和 `PositionCalculationInput`；缺任一项传 `None`，结果为 `user_context_required`。

## 6. 发布（`help(FinalReportNarrativeInput)`、`help(container.final_decision.assemble_and_publish)`）

```python
narrative = FinalReportNarrativeInput(scenarios=(...三个 FinalScenarioBlock...),
    evidence_citations=(FinalEvidenceCitation(reference_id=report_url, kind=FinalEvidenceKind.OFFICIAL,
        title="……", source_name="巨潮资讯", published_at="2026-08-15", url=report_url),),
    chart_paths=tuple(item.markdown_path for item in handoff.chart_artifacts), review_date="2026-12-31")
published = container.final_decision.assemble_and_publish(handoff=handoff, session=session,
    decision_brief=brief, suitability=suitability, entry_plan=entry, position_plan=position,
    narrative=narrative, exit_plan=None)   # 持仓复核传 exit_plan
```

1. 先按文档字符串做一次内存装配与复查，无误再正式发布；同一 workflow run 只能发布一次。
2. `published.published` 为 None 时看 `published.review` 修正。
3. 持仓复核的退出计划：`container.exit_plan.assemble(user_id=..., asset_id=..., as_of_date=<与报告相同>, alternatives=(...), intended_action=...)`，或 `ExitPlan.from_dict(<run-recipe exit_plan 步骤的 artifacts["plan"]>)`。
4. derived 引用的 `methodology` 写自然语言，不写接口或函数名。
