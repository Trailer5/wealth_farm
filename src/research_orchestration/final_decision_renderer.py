"""Allow-list Markdown renderer for final decision research reports."""

from __future__ import annotations

import re

from src.research_contracts import (
    DecisionIntent,
    DecisionLane,
    PositionState,
    ResearchHorizon,
)

from .final_decision_models import (
    FinalDecisionOutcome,
    FinalDecisionResearchReport,
    FinalEvidenceKind,
    FinalScenarioKind,
    FinalStatement,
    FinalStatementKind,
    MonitoringConditionKind,
)


_OUTCOME_LABELS = {
    FinalDecisionOutcome.SUITABILITY_CONSIDER: "考虑配置",
    FinalDecisionOutcome.SUITABILITY_WATCH: "继续观察",
    FinalDecisionOutcome.SUITABILITY_AVOID: "回避",
    FinalDecisionOutcome.SUITABILITY_HOLD_REVIEW: "持仓复核",
    FinalDecisionOutcome.SUITABILITY_INSUFFICIENT: "证据不足",
    FinalDecisionOutcome.TIMING_ACT: "行动",
    FinalDecisionOutcome.TIMING_STAGE: "分批规划",
    FinalDecisionOutcome.TIMING_WAIT: "等待",
    FinalDecisionOutcome.TIMING_NOT_APPLICABLE: "不适用",
    FinalDecisionOutcome.TIMING_INSUFFICIENT: "证据不足",
    FinalDecisionOutcome.POSITION_AVAILABLE: "可计算区间",
    FinalDecisionOutcome.POSITION_USER_CONTEXT_REQUIRED: (
        "需要用户上下文"
    ),
    FinalDecisionOutcome.POSITION_UNSUPPORTED: "暂不可用",
}
_LANE_LABELS = {
    DecisionLane.SUITABILITY: "是否购买",
    DecisionLane.TIMING: "何时购买",
    DecisionLane.POSITION_SIZING: "购买多少",
}
_HOLDING_LANE_LABELS = {
    DecisionLane.SUITABILITY: "是否继续持有",
    DecisionLane.TIMING: "何时调整",
    DecisionLane.POSITION_SIZING: "持有多少",
}
DECISION_HEADINGS = ("## 是否值得买", "## 何时行动", "## 购买多少")
HOLDING_DECISION_HEADINGS = (
    "## 是否继续持有",
    "## 何时调整",
    "## 持有多少",
)
_STATEMENT_LABELS = {
    FinalStatementKind.FACT: "事实",
    FinalStatementKind.INFERENCE: "推断",
    FinalStatementKind.HYPOTHESIS: "假设",
    FinalStatementKind.UNKNOWN: "未知",
}
_SCENARIO_LABELS = {
    FinalScenarioKind.OPTIMISTIC: "乐观情景",
    FinalScenarioKind.BASE: "基准情景",
    FinalScenarioKind.PESSIMISTIC: "悲观情景",
}
_EVIDENCE_LABELS = {
    FinalEvidenceKind.OFFICIAL: "官方来源",
    FinalEvidenceKind.REPORTED: "来源记录",
    FinalEvidenceKind.DERIVED: "项目派生",
    FinalEvidenceKind.USER_CONTEXT: "用户确认",
}
_INTENT_LABELS = {
    DecisionIntent.UNDERSTANDING: "理解标的",
    DecisionIntent.EVALUATE_PURCHASE: "评估购买",
    DecisionIntent.REVIEW_HOLDING: "复核持仓",
}
_HORIZON_LABELS = {
    ResearchHorizon.SHORT: "短期",
    ResearchHorizon.MEDIUM_SHORT: "中短期",
    ResearchHorizon.MEDIUM_LONG: "中长期",
    ResearchHorizon.LONG: "长期",
}
_POSITION_LABELS = {
    PositionState.NOT_HELD: "尚未持有",
    PositionState.HELD: "已经持有",
    PositionState.UNKNOWN: "尚未确认",
}
_LABELED_LIMITATION_PREFIXES = ("证伪条件：", "预期观察：", "未解决冲突：")


class FinalDecisionReportRenderer:
    """Render the ten-section user contract without internal fields."""

    def render(self, report: FinalDecisionResearchReport) -> str:
        citation_numbers = {
            item.reference_id: index
            for index, item in enumerate(
                report.evidence_citations,
                start=1,
            )
        }
        sections = {
            item.lane: item for item in report.decision_sections
        }
        holding = (
            report.user_boundary.decision_intent
            is DecisionIntent.REVIEW_HOLDING
        )
        lane_labels = _HOLDING_LANE_LABELS if holding else _LANE_LABELS
        headings = (
            HOLDING_DECISION_HEADINGS if holding else DECISION_HEADINGS
        )
        shown: set[str] = set()
        lines = [
            f"# {self._sanitize(report.asset_name, report)} 完整决策报告",
            "",
            f"- 截止日：{report.as_of_date}",
            "- 角色：本报告是在基础研究包与多轮探索完成后形成的决策文档；仓位结果是规划边界，不是交易订单。",
            "",
            "## 决策摘要",
        ]
        for lane in DecisionLane:
            section = sections[lane]
            lines.append(
                f"- **{lane_labels[lane]}："
                f"{_OUTCOME_LABELS[section.outcome]}。** "
                f"{self._sanitize(section.summary, report)}"
            )

        boundary = report.user_boundary
        lines.extend(
            [
                "",
                "## 用户目标与适用边界",
                f"- 决策目标：{_INTENT_LABELS[boundary.decision_intent]}",
                f"- 研究期限：{_HORIZON_LABELS[boundary.horizon]}",
                f"- 持仓状态：{_POSITION_LABELS[boundary.position_state]}",
                (
                    "- 组合角色："
                    + self._sanitize(
                        boundary.portfolio_role,
                        report,
                    )
                    if boundary.portfolio_role is not None
                    else "- 组合角色：尚未确认。"
                ),
            ]
        )
        lines.extend(
            self._named_values(
                "用户约束",
                boundary.constraints,
                report,
            )
        )
        lines.extend(
            self._named_values(
                "研究假设",
                boundary.assumptions,
                report,
            )
        )
        lines.extend(
            self._named_values(
                "仍缺少的用户信息",
                boundary.missing_context,
                report,
            )
        )

        lines.extend(
            [
                "",
                "## 标的与核心论文",
                (
                    "- 基础事实包："
                    f"[查看基础研究包]({report.baseline_report_path})"
                ),
                (
                    "- 基础包状态："
                    + (
                        "完整"
                        if report.baseline_status.value == "complete"
                        else "部分完成；未闭合缺口保留在未知项中"
                    )
                ),
            ]
        )
        lines.extend(
            self._statement_lines(
                (report.thesis,),
                report,
                citation_numbers,
                shown,
            )
        )
        if report.chart_paths:
            lines.extend(("", "### 关键图表"))
            for index, path in enumerate(report.chart_paths, start=1):
                lines.append(f"![关键图表{index}]({path})")

        lines.extend(("", headings[0]))
        lines.extend(
            self._decision_lines(
                sections[DecisionLane.SUITABILITY],
                report,
                citation_numbers,
                shown,
            )
        )
        if report.claim_probability is not None:
            direction = (
                "跑赢"
                if sections[DecisionLane.SUITABILITY].outcome
                is FinalDecisionOutcome.SUITABILITY_CONSIDER
                else "跑输"
            )
            lines.append(
                f"- 发布前概率区间：{report.claim_probability.low:.0%}"
                f"—{report.claim_probability.high:.0%}（研究期限内{direction}基准，"
                "到期后按发布后的真实价格结算）。"
            )
        lines.extend(("", headings[1]))
        lines.extend(
            self._decision_lines(
                sections[DecisionLane.TIMING],
                report,
                citation_numbers,
                shown,
            )
        )
        lines.extend(("", headings[2]))
        position_section = sections[DecisionLane.POSITION_SIZING]
        lines.extend(
            self._decision_lines(
                position_section,
                report,
                citation_numbers,
                shown,
            )
        )
        if (
            position_section.outcome
            is not FinalDecisionOutcome.POSITION_AVAILABLE
            and boundary.missing_context
        ):
            lines.append(
                "- 补齐后才能计算仓位："
                + "、".join(
                    self._sanitize(item, report)
                    for item in boundary.missing_context
                )
            )

        scenarios = {
            item.kind: item for item in report.scenarios
        }
        lines.extend(("", "## 三情景"))
        for kind in (
            FinalScenarioKind.OPTIMISTIC,
            FinalScenarioKind.BASE,
            FinalScenarioKind.PESSIMISTIC,
        ):
            scenario = scenarios[kind]
            lines.extend(("", f"### {_SCENARIO_LABELS[kind]}"))
            lines.extend(
                f"- 假设：{self._sanitize(item, report)}"
                for item in scenario.assumptions
            )
            lines.extend(
                f"- 观察变量：{self._sanitize(item, report)}"
                for item in scenario.observable_variables
            )
            lines.append(
                "- 决策影响："
                + self._sanitize(
                    scenario.decision_impact,
                    report,
                )
                + self._evidence_suffix(
                    scenario.evidence_refs,
                    citation_numbers,
                )
            )
            lines.extend(
                f"- 限制：{self._sanitize(item, report)}"
                for item in scenario.limitations
            )

        lines.extend(("", "## 最强反方"))
        lines.extend(
            self._statement_lines(
                (report.strongest_counterargument,),
                report,
                citation_numbers,
                shown,
            )
        )

        lines.extend(("", "## 失效、退出与复查"))
        if report.monitoring_plan.review_date is not None:
            lines.append(
                f"- 建议最晚复查日：{report.monitoring_plan.review_date}"
            )
        if report.monitoring_plan.conditions:
            shown_limits: set[str] = set()
            for item in report.monitoring_plan.conditions:
                kind_label = (
                    "待确认的机器观察候选"
                    if item.kind
                    is MonitoringConditionKind.MACHINE_OBSERVABLE
                    else "需要人工复查"
                )
                text = self._sanitize(item.text, report)
                if (
                    item.review_by is not None
                    and item.review_by
                    != report.monitoring_plan.review_date
                ):
                    text = f"{text}（最晚复查日：{item.review_by}）"
                lines.append(
                    f"- {kind_label}：{text}"
                    f"{self._evidence_suffix(item.evidence_refs, citation_numbers)}"
                )
                for value in item.limitations:
                    if value in shown_limits:
                        continue
                    shown_limits.add(value)
                    lines.append(
                        f"  - 限制：{self._sanitize(value, report)}"
                    )
        else:
            lines.append("- 当前未形成可执行监测条件。")
        lines.extend(
            f"- 监测边界：{self._sanitize(item, report)}"
            for item in report.monitoring_plan.limitations
        )

        lines.extend(("", "## 关键证据与未知"))
        lines.extend(("", "### 关键证据"))
        for index, citation in enumerate(
            report.evidence_citations,
            start=1,
        ):
            title = self._sanitize(citation.title, report)
            source = self._sanitize(citation.source_name, report)
            linked_title = (
                f"[{title}]({citation.url})"
                if citation.url is not None
                else title
            )
            metadata = [_EVIDENCE_LABELS[citation.kind], source]
            if citation.published_at is not None:
                metadata.append(citation.published_at)
            if citation.page is not None:
                metadata.append(
                    f"第{self._sanitize(citation.page, report)}页"
                )
            lines.append(
                f"- 证据 {index}："
                f"{linked_title}（{'，'.join(metadata)}）"
            )
            if citation.methodology is not None:
                lines.append(
                    "  - 派生方法："
                    + self._sanitize(
                        citation.methodology,
                        report,
                    )
                )
            lines.extend(
                f"  - 限制：{self._sanitize(item, report)}"
                for item in citation.limitations
            )
        lines.extend(("", "### 仍可能改变结论的未知"))
        unknowns = tuple(
            dict.fromkeys(
                (
                    *report.key_unknowns,
                    *report.baseline_gaps,
                )
            )
        )
        if unknowns:
            lines.extend(
                f"- {self._sanitize(item, report)}"
                for item in unknowns
            )
        else:
            lines.append("- 暂无额外未知项；仍需按失效条件复查。")
        lines.extend(
            f"- 总体限制：{self._sanitize(item, report)}"
            for item in report.limitations
        )
        return "\n".join(lines).strip() + "\n"

    def render_review_blocked(self, review: object) -> str:
        messages = tuple(
            getattr(item, "message", "")
            for item in getattr(review, "findings", ())
            if getattr(item, "blocking", False)
        )
        return "\n".join(
            [
                "# 完整决策报告未发布",
                "",
                "程序化复查未通过，未发布用户决策报告。",
                "",
                "## 待修复项",
                *[f"- {item}" for item in messages],
            ]
        ).strip() + "\n"

    def _decision_lines(
        self,
        section: object,
        report: FinalDecisionResearchReport,
        citation_numbers: dict[str, int],
        shown: set[str],
    ) -> list[str]:
        lines = [
            "- 当前答案："
            + _OUTCOME_LABELS[getattr(section, "outcome")],
            "- 结论边界："
            + self._sanitize(
                getattr(section, "summary"),
                report,
            ),
        ]
        lines.extend(
            self._statement_lines(
                getattr(section, "statements"),
                report,
                citation_numbers,
                shown,
            )
        )
        gaps = tuple(getattr(section, "gaps"))
        if gaps:
            lines.extend(
                f"- 不可用原因：{self._sanitize(item, report)}"
                for item in gaps
            )
        for item in getattr(section, "limitations"):
            # Hypothesis conditions are shown with the thesis and the
            # strongest counterargument themselves.
            if item in shown or item.startswith(
                _LABELED_LIMITATION_PREFIXES
            ):
                continue
            shown.add(item)
            lines.append(self._limitation_line(item, report, ""))
        return lines

    def _statement_lines(
        self,
        statements: tuple[FinalStatement, ...],
        report: FinalDecisionResearchReport,
        citation_numbers: dict[str, int],
        shown: set[str],
    ) -> list[str]:
        lines: list[str] = []
        for statement in statements:
            lines.append(
                f"- {_STATEMENT_LABELS[statement.kind]}："
                f"{self._sanitize(statement.text, report)}"
                f"{self._evidence_suffix(statement.evidence_refs, citation_numbers)}"
            )
            for item in statement.limitations:
                shown.add(item)
                lines.append(self._limitation_line(item, report, "  "))
        return lines

    def _limitation_line(
        self,
        item: str,
        report: FinalDecisionResearchReport,
        indent: str,
    ) -> str:
        text = self._sanitize(item, report)
        if text.startswith(_LABELED_LIMITATION_PREFIXES):
            return f"{indent}- {text}"
        return f"{indent}- 限制：{text}"

    @staticmethod
    def _evidence_suffix(
        evidence_refs: tuple[str, ...],
        citation_numbers: dict[str, int],
    ) -> str:
        if not evidence_refs:
            return ""
        numbers = "、".join(
            str(citation_numbers[item]) for item in evidence_refs
        )
        return f"（证据 {numbers}）"

    def _named_values(
        self,
        label: str,
        values: tuple[str, ...],
        report: FinalDecisionResearchReport,
    ) -> list[str]:
        if not values:
            return [f"- {label}：未提供。"]
        return [
            f"- {label}：{self._sanitize(item, report)}"
            for item in values
        ]

    @staticmethod
    def _sanitize(
        text: str | None,
        report: FinalDecisionResearchReport,
    ) -> str:
        cleaned = str(text or "")
        exact_values = {
            report.report_id,
            report.user_id,
            report.workflow_run_id,
            report.session_id,
            report.decision_brief_id,
            report.handoff_id,
            report.suitability_assessment_id,
            report.entry_plan_id,
            report.position_plan_id,
            report.asset_id,
            report.profile_id,
            report.monitoring_plan.plan_id,
            report.baseline_report_digest,
            *(
                item.reference_id
                for item in report.evidence_citations
            ),
            *(
                item.condition_id
                for item in report.monitoring_plan.conditions
            ),
        }
        for value in sorted(exact_values, key=len, reverse=True):
            cleaned = cleaned.replace(value, "内部标识")
        cleaned = re.sub(
            r"(?<![\w])src[\\/][^\s，。；;)]*",
            "内部实现",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b[a-f0-9]{24,64}\b",
            "内部摘要",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b",
            "内部字段",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:assessment|plan|session|workflow|schema|digest)_id\b",
            "内部字段",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip() or "暂无可展示内容。"
