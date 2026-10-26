"""Run the formal open-fund pipeline for the two acceptance funds."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SMOKE_FUNDS = (
    ("163417", "兴全合宜混合(LOF)A"),
    ("002236", "大成360互联网+大数据100A"),
)
REMOTE_EXIT = 3
BUSINESS_DATA_EXIT = 4


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.master_data import ASSET_TYPE_OPEN_FUND
    from src.research_orchestration import (
        ResearchPlanningRequest,
        ResearchPlanningStatus,
        ResearchRunRequest,
        ResearchRunStatus,
        create_default_container,
    )
    from src.research_contracts import (
        PROFILE_OPEN_FUND,
        ResearchHorizon,
        ResearchQuestionType,
    )
    from src.research_workflow import ResearchExecutionOptions

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    parser.add_argument(
        "--seed-db-path",
        default=None,
        help=(
            "Optional source database copied with SQLite backup before "
            "the smoke, so the source remains unchanged."
        ),
    )
    parser.add_argument("--report-root", default="report")
    parser.add_argument("--as-of-date", default="2026-08-22")
    parser.add_argument("--provider-timeout", type=float, default=120.0)
    args = parser.parse_args()

    if args.seed_db_path is not None:
        _seed_database(args.seed_db_path, args.db_path)
    container = create_default_container(
        db_path=args.db_path,
        report_root=args.report_root,
    )
    summaries = []
    exit_code = 0
    for symbol, expected_name in SMOKE_FUNDS:
        planning = container.request_planner.plan(
            ResearchPlanningRequest(
                symbol=symbol,
                asset_type=ASSET_TYPE_OPEN_FUND,
                as_of_date=args.as_of_date,
                horizon=ResearchHorizon.LONG,
            )
        )
        if (
            planning.status is not ResearchPlanningStatus.READY
            or planning.task is None
            or planning.identity is None
        ):
            summaries.append(
                {
                    "symbol": symbol,
                    "classification": "business_data_missing",
                    "detail": (
                        "public request planning did not resolve one "
                        f"open-fund identity: {planning.issues}"
                    ),
                }
            )
            exit_code = max(exit_code, BUSINESS_DATA_EXIT)
            continue
        task = planning.task
        if (
            task.profile_id != PROFILE_OPEN_FUND
            or task.question_type
            is not ResearchQuestionType.UNDERSTANDING
        ):
            raise AssertionError(
                "Open-fund public planning must preserve the profile "
                "and use an understanding baseline"
            )
        run = container.pipeline.run(
            ResearchRunRequest(
                task=task,
                execution_options=ResearchExecutionOptions(
                    provider_timeout_seconds=args.provider_timeout,
                ),
            )
        )
        gap_codes = tuple(gap.code for gap in run.execution.gaps)
        classification = _classification(gap_codes)
        if run.status is ResearchRunStatus.BLOCKED:
            classification = (
                classification
                if classification != "accepted_with_explicit_gaps"
                else "code_regression"
            )
        if classification == "remote_source_unavailable":
            exit_code = max(exit_code, REMOTE_EXIT)
        elif classification == "business_data_missing":
            exit_code = max(exit_code, BUSINESS_DATA_EXIT)
        elif classification == "code_regression":
            exit_code = 1

        detail = None
        try:
            _assert_package(
                run,
                symbol=symbol,
                expected_name=expected_name,
            )
        except AssertionError as exc:
            classification = "code_regression"
            detail = str(exc)
            exit_code = 1
        summaries.append(
            {
                "symbol": symbol,
                "classification": classification,
                "run_status": run.status.value,
                "gap_codes": gap_codes,
                "report_path": (
                    str(run.report_path)
                    if run.report_path is not None
                    else None
                ),
                "detail": detail,
            }
        )

    print(json.dumps(summaries, ensure_ascii=True, indent=2))
    return exit_code


def _seed_database(source_path: str, target_path: str) -> None:
    source = sqlite3.connect(
        f"file:{Path(source_path).resolve().as_posix()}?mode=ro",
        uri=True,
    )
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _classification(gap_codes: tuple[str, ...]) -> str:
    if any(
        code.endswith("_calculation_failed")
        or code.endswith("_collection_failed")
        or code.endswith("_render_failed")
        for code in gap_codes
    ):
        return "code_regression"
    if any(
        code.endswith("_source_unavailable")
        or (
            code.endswith("_unavailable")
            and "definition" not in code
        )
        for code in gap_codes
    ):
        return "remote_source_unavailable"
    if any(
        code.startswith("open_fund_required_")
        or code == "open_fund_nav_business_empty"
        or code.startswith("open_fund_standard_window_")
        or code.endswith("_extraction_failed")
        for code in gap_codes
    ):
        return "business_data_missing"
    return "accepted_with_explicit_gaps"


def _assert_package(run, *, symbol: str, expected_name: str) -> None:
    from src.research_acceptance import ResearchFactKind

    if run.report is None:
        raise AssertionError("formal report was not assembled")
    if run.report_path is None or not Path(run.report_path).is_file():
        raise AssertionError("Markdown report was not published")
    if expected_name not in run.user_markdown:
        raise AssertionError("requested fund identity is absent from Markdown")
    if "内部字段" in run.user_markdown:
        raise AssertionError("sanitized internal field leaked into Markdown")
    for forbidden in ("建议买入", "建议卖出", "投资评级"):
        if forbidden in run.user_markdown:
            raise AssertionError(
                "baseline package contains an investment recommendation"
            )

    facts = tuple(
        fact
        for section in run.report.fact_sections
        for fact in section.facts
    )
    labels = {fact.label for fact in facts}
    required_labels = {
        "基金身份",
        "最新单位净值",
        "净值覆盖",
        "成立以来收益率",
        "区间年化波动率",
        "区间最大回撤",
        "基金经理与任期",
        "持续运作费率",
        "业绩比较基准",
        "期末份额与净资产",
        "资产配置（占总资产）",
    }
    missing = required_labels - labels
    if missing:
        raise AssertionError(f"required fact labels missing: {sorted(missing)}")
    if any(
        fact.kind is ResearchFactKind.OFFICIAL
        and not any(
            ref.startswith(("http://", "https://"))
            for ref in fact.evidence_refs
        )
        for fact in facts
    ):
        raise AssertionError("official fact lacks external document reference")
    if any(
        fact.kind is ResearchFactKind.DERIVED
        and not fact.limitations
        for fact in facts
    ):
        raise AssertionError("derived fact lacks methodology or scope")

    image_lines = tuple(
        line.strip()
        for line in run.user_markdown.splitlines()
        if line.strip().startswith("![")
    )
    if len(image_lines) != 3:
        raise AssertionError("open-fund package requires three static charts")
    report_parent = Path(run.report_path).parent
    for line in image_lines:
        relative = line.split("](", 1)[1].rsplit(")", 1)[0]
        chart_png = report_parent / Path(relative)
        if not chart_png.is_file():
            raise AssertionError(
                f"referenced chart does not exist: {relative}"
            )
        for sibling in (
            "chart.svg",
            "data.json",
            "metadata.json",
        ):
            if not (chart_png.parent / sibling).is_file():
                raise AssertionError(
                    "chart audit artifact does not exist: "
                    f"{chart_png.parent / sibling}"
                )

    benchmark_sections = {
        section.section_id for section in run.report.fact_sections
    }
    benchmark_gaps = {
        gap.code
        for gap in run.report.gaps
        if gap.code.startswith("open_fund_benchmark_")
    }
    if (
        "benchmark_metrics" not in benchmark_sections
        and not benchmark_gaps
    ):
        raise AssertionError(
            f"{symbol} has neither benchmark facts nor an explicit gap"
        )


if __name__ == "__main__":
    raise SystemExit(main())
