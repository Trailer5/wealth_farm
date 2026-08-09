"""Allow-list renderer for user-facing research Markdown."""

from __future__ import annotations

import re

from src.research_acceptance import ScenarioKind
from src.research_workflow import ResearchExecutionResult


_SCENARIO_LABELS = {
    ScenarioKind.OPTIMISTIC: "乐观情景",
    ScenarioKind.BASE: "基准情景",
    ScenarioKind.PESSIMISTIC: "悲观情景",
}
_RAW_VALUE_LABELS = {
    "complete": "完整",
    "partial": "部分完成",
    "blocked": "已阻断",
    "passed": "已通过",
    "pass": "满足规则条件",
    "fail": "未满足规则条件",
    "insufficient_data": "数据不足",
    "optimistic": "乐观",
    "base": "基准",
    "pessimistic": "悲观",
    "fact": "事实",
    "inference": "推断",
    "assumption": "假设",
    "unknown": "未知",
    "short": "短期",
    "medium_short": "中短期",
    "medium_long": "中长期",
    "long": "长期",
    "understanding": "理解",
    "comparison": "比较",
    "timing": "择时",
    "speculation": "投机",
    "investment": "投资",
    "cycle_reading": "周期判断",
    "thesis_review": "论点复查",
    "risk_review": "风险复查",
    "warning": "预警",
    "thesis_break": "核心前提失效",
}


class ResearchReportRenderer:
    """Render only fields approved for the user report."""

    def render(self, report: object) -> str:
        task = getattr(report, "task")
        lines = [
            f"# {getattr(report, 'asset_name')} 基础研究包",
            "",
            f"- 截止日：{task.as_of_date}",
            "",
            "## 自动收集后的初步判断",
        ]
        lines.extend(
            self._claim_lines(getattr(report, "conclusions", ()), report)
        )
        lines.extend(("", "## 初始三情景"))
        scenarios = {
            getattr(item, "kind", None): item
            for item in getattr(report, "scenarios", ())
        }
        for kind in (
            ScenarioKind.OPTIMISTIC,
            ScenarioKind.BASE,
            ScenarioKind.PESSIMISTIC,
        ):
            lines.extend(("", f"### {_SCENARIO_LABELS[kind]}"))
            scenario = scenarios.get(kind)
            claims = getattr(scenario, "claims", ()) if scenario else ()
            lines.extend(self._claim_lines(claims, report))

        lines.extend(("", "## 最强反方"))
        lines.extend(
            self._claim_lines(
                (getattr(report, "strongest_counterargument"),),
                report,
            )
        )

        lines.extend(("", "## 失效条件"))
        for item in getattr(report, "invalidation_conditions", ()):
            condition = self._sanitize(
                getattr(item, "condition", ""),
                report,
            )
            severity = _RAW_VALUE_LABELS.get(
                getattr(item, "severity", ""),
                "需复查",
            )
            lines.append(f"- {condition}（{severity}）")

        lines.extend(("", "## 缺口"))
        gaps = tuple(getattr(report, "gaps", ()))
        if gaps:
            lines.extend(
                f"- {self._sanitize(getattr(gap, 'message', ''), report)}"
                for gap in gaps
            )
        else:
            lines.append("- 暂无已识别缺口。")
        return "\n".join(lines).strip() + "\n"

    def render_blocked(
        self,
        execution: ResearchExecutionResult,
    ) -> str:
        messages = [
            self._sanitize(gap.message, execution)
            for gap in execution.gaps
            if gap.blocking
        ]
        lines = [
            "# 基础研究包未完成",
            "",
            f"- 截止日：{execution.task.as_of_date}",
            "",
            "自动收集已阻断，未生成基础研究包。",
            "",
            "## 缺口",
            *[f"- {message}" for message in messages],
        ]
        return "\n".join(lines).strip() + "\n"

    def render_review_blocked(
        self,
        review: object,
        report: object | None = None,
    ) -> str:
        messages = [
            self._sanitize(getattr(item, "message", ""), report)
            for item in getattr(review, "findings", ())
            if getattr(item, "blocking", False)
        ]
        return "\n".join(
            [
                "# 基础研究包未发布",
                "",
                "程序化复查未通过，未发布基础研究包。",
                "",
                "## 待修复项",
                *[f"- {message}" for message in messages],
            ]
        ).strip() + "\n"

    def _claim_lines(
        self,
        claims: object,
        report: object,
    ) -> list[str]:
        values = tuple(claims)
        if not values:
            return ["- 当前没有可靠结论。"]
        return [
            f"- {self._sanitize(getattr(claim, 'text', ''), report)}"
            for claim in values
        ]

    def _sanitize(self, text: str, report: object) -> str:
        cleaned = str(text)
        task = getattr(report, "task", None)
        internal_values = [
            getattr(report, "acceptance_version", None),
            getattr(report, "report_id", None),
            getattr(report, "structure_digest", None),
            *(
                [
                    getattr(task, "task_id", None),
                    getattr(task, "asset_id", None),
                    getattr(task, "profile_id", None),
                    getattr(task, "benchmark_asset_id", None),
                    *getattr(task, "peer_asset_ids", ()),
                ]
                if task is not None
                else []
            ),
        ]
        for value in sorted(
            {
                item
                for item in internal_values
                if isinstance(item, str) and item
            },
            key=len,
            reverse=True,
        ):
            cleaned = cleaned.replace(value, "内部标识")
        for raw, label in _RAW_VALUE_LABELS.items():
            cleaned = re.sub(
                rf"(?<![\w]){re.escape(raw)}(?![\w])",
                label,
                cleaned,
                flags=re.IGNORECASE,
            )
        cleaned = re.sub(
            r"\b[\w.-]+_v\d+\b|\bv\d+\b",
            "内部版本",
            cleaned,
            flags=re.IGNORECASE,
        )
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
        return cleaned.strip() or "暂无可展示内容。"
