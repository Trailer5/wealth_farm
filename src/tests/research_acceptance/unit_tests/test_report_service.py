import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from src.benchmark_analysis import (
    BenchmarkComponent,
    BenchmarkComponentKind,
    BenchmarkKind,
    BenchmarkMetricCalculator,
    CompositeBenchmarkDefinition,
)
from src.data_access import DataAccessResponse
from src.factor_engineering import (
    FactorValue,
    OpenFundStandardMetricCalculator,
    RiskCalculationResult,
    RiskMetricParameters,
)
from src.fundamental_analysis import (
    FundamentalMetricResult,
    FundamentalMetricSet,
    FundamentalSnapshot,
    MetricStatus,
)
from src.fund_research import (
    DisclosureExtractionStatus,
    FundResearchBundle,
    OpenFundHistoricalSummaryCalculator,
    OpenFundAssetAllocation,
    OpenFundHolding,
    OpenFundIndustryAllocation,
    OpenFundManagerTenure,
    OpenFundPerformanceComparison,
    OpenFundPeriodicSnapshot,
    OpenFundProfileSnapshot,
    OpenFundShareClassSnapshot,
    OpenFundShareFlow,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_acceptance import (
    AcceptanceStatus,
    ClaimKind,
    ResearchAcceptanceReport,
    ResearchFactKind,
    ResearchReportAssemblyService,
    ScenarioKind,
)
from src.research_contracts import (
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
)
from src.research_evidence import (
    EvidenceTier,
    ResearchEvidenceBundle,
    ResearchEvidenceItem,
)
from src.research_orchestration.renderer import ResearchReportRenderer
from src.research_orchestration.review import ResearchReportReviewer
from src.research_workflow import (
    ResearchExecutionGap,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchTaskFactory,
    ResearchWindows,
)
from src.strategy import (
    StrategyMaturity,
    StrategyResult,
    StrategyRunStatus,
)


class ResearchReportAssemblyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "report.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        asset = self._register(
            "stock:company",
            ASSET_TYPE_STOCK,
            "600519",
        )
        index = self._register(
            "index:csi300",
            ASSET_TYPE_INDEX,
            "000300",
        )
        self.task = ResearchTaskFactory(self.master).create(
            asset_id=asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        self.service = ResearchReportAssemblyService(
            master_data=self.master
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_complete_execution_generates_replayable_report(self):
        report = self.service.assemble(_execution(self.task))

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.COMPLETE,
        )
        self.assertTrue(report.replay_verified)
        self.assertEqual(
            {item.kind for item in report.scenarios},
            set(ScenarioKind),
        )

    def test_stock_report_publishes_facts_and_rule_checks(self):
        asset = self.master.register_asset(
            Asset(
                asset_id="stock:named",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600000",
                display_name="测试公司",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="600000",
        )
        index = self.master.register_asset(
            Asset(
                asset_id="index:named",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000905",
                display_name="中证500",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="000905",
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=asset.asset_id,
            profile_id=PROFILE_ORDINARY_COMPANY,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        base = _execution(task)
        execution = replace(
            base,
            bar_response=DataAccessResponse(
                asset=asset,
                dataset="stock_daily_bars",
                records=(
                    {"trade_date": "2026-07-31", "close": 1500.0},
                    {"trade_date": "2026-08-03", "close": 1520.5},
                ),
            ),
            risk_result=_stock_risk_result(asset.asset_id),
            benchmark_result=BenchmarkMetricCalculator().calculate(
                asset_id=asset.asset_id,
                benchmark_asset_id=index.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                asset_records=[
                    {"trade_date": "2026-07-30", "close": 100},
                    {"trade_date": "2026-07-31", "close": 102},
                    {"trade_date": "2026-08-03", "close": 103},
                ],
                benchmark_records=[
                    {"trade_date": "2026-07-30", "close": 100},
                    {"trade_date": "2026-07-31", "close": 101},
                    {"trade_date": "2026-08-03", "close": 100},
                ],
                asset_dataset="stock_daily_bars",
                benchmark_dataset="index_daily_bars",
            ),
            fundamental_result=_fundamental_result(asset.asset_id),
            evidence_bundle=_evidence_bundle(asset),
            strategy_result=replace(
                base.strategy_result,
                status=StrategyRunStatus.FAIL,
                score=Decimal("0.5"),
                matched_conditions=(
                    "quality_value_hard_gate",
                    "quality_value_roe",
                    "quality_value_roic_gate",
                ),
                failed_conditions=("quality_value_pe",),
                limitations=("阈值是初始研究假设",),
            ),
        )

        report = self.service.assemble(execution)
        facts = {
            fact.label: fact
            for section in report.fact_sections
            for fact in section.facts
        }
        sections = {
            section.section_id for section in report.fact_sections
        }

        self.assertTrue(
            {
                "market_data",
                "risk_metrics",
                "benchmark_metrics",
                "valuation",
                "profitability",
                "research_rule",
                "evidence_overview",
            }.issubset(sections)
        )
        self.assertEqual(facts["最新收盘价"].value, "1,520.5")
        self.assertEqual(facts["最新收盘价"].as_of_date, "2026-08-03")
        self.assertEqual(facts["比较基准"].value, "中证500")
        self.assertEqual(facts["市盈率（TTM）"].value, "35倍")
        self.assertEqual(facts["ROE"].value, "30.5%")
        self.assertIn("未复权", facts["区间最大回撤"].limitations[0])
        self.assertIn(
            "市盈率（TTM） ≤ 30倍（当前35倍，未满足",
            facts["评分条件"].value,
        )
        self.assertIn(
            "ROIC ≥ 10%（当前缺失，满足",
            facts["硬性门槛"].value,
        )
        self.assertIn(
            "未通过（中长期质量与估值规则；得分0.5",
            facts["规则结论"].value,
        )
        self.assertEqual(facts["公告"].kind, ResearchFactKind.OFFICIAL)
        self.assertIn(
            "未满足的规则条件",
            report.strongest_counterargument.text,
        )
        self.assertIn("市盈率", report.strongest_counterargument.text)
        texts = [
            *(claim.text for claim in report.conclusions),
            *(
                claim.text
                for scenario in report.scenarios
                for claim in scenario.claims
            ),
            report.strongest_counterargument.text,
            *(item.condition for item in report.invalidation_conditions),
            *(fact.value for fact in facts.values()),
        ]
        for text in texts:
            self.assertNotRegex(text, r"[a-z]+_[a-z]+")
            self.assertNotRegex(
                text,
                r"\b(?:strategy|Strategy|condition|Rerun)\b",
            )
        markdown = ResearchReportRenderer().render(report)
        self.assertNotIn("内部字段", markdown)
        self.assertIn("## 研究规则检查", markdown)
        self.assertIn("- 本节口径：按执行窗口内的日频未复权收盘价", markdown)
        review = ResearchReportReviewer().review(
            report,
            expected_task=task,
            user_markdown=markdown,
        )
        self.assertTrue(review.passed, review.findings)

    def test_financial_report_discloses_broad_market_fallback(self):
        task = ResearchTaskFactory(self.master).create(
            asset_id=self.task.asset_id,
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            question_type=ResearchQuestionType.INVESTMENT,
            horizon=ResearchHorizon.MEDIUM_LONG,
            as_of_date="2026-08-03",
            benchmark_asset_id=self.task.benchmark_asset_id,
        )
        execution = replace(
            _execution(task),
            benchmark_result=BenchmarkMetricCalculator().calculate(
                asset_id=task.asset_id,
                benchmark_asset_id=task.benchmark_asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                asset_records=[
                    {"trade_date": "2026-07-31", "close": 10},
                    {"trade_date": "2026-08-03", "close": 11},
                ],
                benchmark_records=[
                    {"trade_date": "2026-07-31", "close": 100},
                    {"trade_date": "2026-08-03", "close": 101},
                ],
                asset_dataset="stock_daily_bars",
                benchmark_dataset="index_daily_bars",
            ),
        )

        report = self.service.assemble(execution)
        facts = {
            fact.label: fact
            for section in report.fact_sections
            for fact in section.facts
        }

        self.assertIn("沪深300", facts["相对基准超额收益"].limitations[-1])

    def test_blocking_execution_gap_blocks_report(self):
        execution = _execution(self.task)
        phases = tuple(
            replace(
                phase,
                status=ResearchPhaseStatus.FAILED,
                error="provider down",
            )
            if phase.phase_id == "bars"
            else phase
            for phase in execution.phases
        )
        execution = replace(
            execution,
            status=ResearchExecutionStatus.BLOCKED,
            phases=phases,
            gaps=(
                ResearchExecutionGap(
                    "data_access",
                    "daily_bars_unavailable",
                    "provider down",
                    True,
                ),
            ),
        )
        report = self.service.assemble(execution)

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.BLOCKED,
        )
        self.assertEqual(
            report.gaps[0].code,
            "daily_bars_unavailable",
        )

    def test_understanding_report_allows_not_applicable_strategy(self):
        fund = self._register(
            "fund:163417",
            ASSET_TYPE_OPEN_FUND,
            "163417",
            market="cn_fund",
            exchange=None,
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=fund.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )
        execution = _execution(task)
        bundle = _fund_bundle(fund)
        standard_metrics = _standard_metrics(fund.asset_id)
        historical_values = (
            OpenFundHistoricalSummaryCalculator().calculate(
                asset_id=fund.asset_id,
                as_of_date=task.as_of_date,
                share_class_code=fund.standard_symbol,
                profile_snapshot=bundle.profile_snapshot,
                periodic_snapshot=bundle.periodic_snapshot,
                scale_records=bundle.scale_records,
            )
        )
        execution = replace(
            execution,
            phases=tuple(
                replace(
                    phase,
                    status=ResearchPhaseStatus.NOT_APPLICABLE,
                    sources=(),
                )
                if phase.phase_id in {
                    "fundamental",
                    "benchmark",
                    "strategy",
                }
                else phase
                for phase in execution.phases
            ),
            strategy_result=None,
            fund_bundle=bundle,
            factor_values=historical_values,
            risk_result=_risk_result(fund.asset_id),
            open_fund_standard_metrics=standard_metrics,
        )

        report = self.service.assemble(execution)
        replay = ResearchAcceptanceReport.from_dict(report.to_dict())
        fact_by_label = {
            fact.label: fact
            for section in report.fact_sections
            for fact in section.facts
        }

        self.assertIsNot(
            report.overall_status,
            AcceptanceStatus.BLOCKED,
        )
        self.assertNotIn(
            "strategy_result_missing",
            {gap.code for gap in report.gaps},
        )
        self.assertIn(
            "open_fund_benchmark_definition_missing",
            {gap.code for gap in report.gaps},
        )
        self.assertEqual(
            fact_by_label["最新单位净值"].value,
            "1.52",
        )
        self.assertEqual(
            fact_by_label["区间年化波动率"].value,
            "25",
        )
        self.assertEqual(
            fact_by_label["区间年化波动率"].kind,
            ResearchFactKind.DERIVED,
        )
        self.assertEqual(
            fact_by_label["最新配置摘要"].value,
            "制造业 80%",
        )
        self.assertEqual(
            fact_by_label["业绩比较基准"].kind,
            ResearchFactKind.OFFICIAL,
        )
        self.assertEqual(
            fact_by_label["官方披露区间业绩"].kind,
            ResearchFactKind.OFFICIAL,
        )
        self.assertIn(
            "测试股票",
            fact_by_label["前十大股票"].value,
        )
        self.assertIn("成立以来收益率", fact_by_label)
        self.assertIn("前十大持仓集中度", fact_by_label)
        self.assertIn("报告期净份额流量", fact_by_label)
        self.assertIn(
            "1.2%/年",
            fact_by_label["持续运作费率"].value,
        )
        self.assertTrue(
            all(
                claim.kind is ClaimKind.NOT_APPLICABLE
                for scenario in report.scenarios
                for claim in scenario.claims
            )
        )
        self.assertIs(
            report.strongest_counterargument.kind,
            ClaimKind.NOT_APPLICABLE,
        )
        self.assertEqual(replay.fact_sections, report.fact_sections)

    def test_open_fund_report_distinguishes_tracking_difference_and_error(
        self,
    ):
        fund = self._register(
            "fund:002236",
            ASSET_TYPE_OPEN_FUND,
            "002236",
            market="cn_fund",
            exchange=None,
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=fund.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )
        execution = _execution(task)
        execution = replace(
            execution,
            phases=tuple(
                replace(
                    phase,
                    status=(
                        ResearchPhaseStatus.NOT_APPLICABLE
                        if phase.phase_id
                        in {"fundamental", "strategy"}
                        else ResearchPhaseStatus.SUCCESS
                    ),
                )
                for phase in execution.phases
            ),
            strategy_result=None,
            fund_bundle=_fund_bundle(fund),
            risk_result=_risk_result(fund.asset_id),
            benchmark_result=_benchmark_result(fund.asset_id),
        )

        report = self.service.assemble(execution)
        facts = {
            fact.label: fact
            for section in report.fact_sections
            for fact in section.facts
        }

        self.assertIn("累计跟踪差值", facts)
        self.assertIn("年化跟踪误差", facts)
        self.assertNotEqual(
            facts["累计跟踪差值"].value,
            facts["年化跟踪误差"].value,
        )
        self.assertEqual(
            facts["累计跟踪差值"].kind,
            ResearchFactKind.DERIVED,
        )
        self.assertEqual(
            facts["业绩比较基准"].kind,
            ResearchFactKind.OFFICIAL,
        )

    def test_open_fund_fact_matrix_records_missing_official_disclosures(self):
        fund = self._register(
            "fund:missing-disclosure",
            ASSET_TYPE_OPEN_FUND,
            "160001",
            market="cn_fund",
            exchange=None,
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=fund.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )
        execution = _execution(task)
        bundle = replace(
            _fund_bundle(fund),
            profile_snapshot=None,
            periodic_snapshot=None,
        )
        execution = replace(
            execution,
            phases=tuple(
                replace(
                    phase,
                    status=ResearchPhaseStatus.NOT_APPLICABLE,
                    sources=(),
                )
                if phase.phase_id in {
                    "fundamental",
                    "benchmark",
                    "strategy",
                }
                else phase
                for phase in execution.phases
            ),
            strategy_result=None,
            fund_bundle=bundle,
            risk_result=_risk_result(fund.asset_id),
            open_fund_standard_metrics=_standard_metrics(
                fund.asset_id
            ),
        )

        report = self.service.assemble(execution)
        codes = {gap.code for gap in report.gaps}

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.PARTIAL,
        )
        self.assertIn("open_fund_required_official_profile_missing", codes)
        self.assertIn("open_fund_required_official_periodic_missing", codes)
        self.assertIn("open_fund_benchmark_definition_missing", codes)

    def test_open_fund_decision_facts_render_as_sections(self):
        fund = self._register(
            "fund:decision-facts",
            ASSET_TYPE_OPEN_FUND,
            "160003",
            market="cn_fund",
            exchange=None,
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=fund.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )
        execution = replace(
            _execution(task),
            strategy_result=None,
            fund_bundle=_fund_bundle(fund),
            risk_result=_risk_result(fund.asset_id),
            open_fund_standard_metrics=_standard_metrics(fund.asset_id),
            open_fund_decision={
                "as_of_date": "2026-08-22",
                "dealing": {
                    "fetched_on": "2026-10-28",
                    "purchase_status": "开放申购",
                    "redemption_status": "开放赎回",
                    "min_purchase_amount": 10.0,
                    "daily_purchase_limit": "无限额",
                    "purchase_confirm_days": 1,
                    "redemption_confirm_days": 1,
                    "purchase_rate_original": "0.015",
                    "purchase_rate_discounted": "0.0015",
                    "redemption_fees": [
                        {"period_text": "小于7天", "min_days": 0, "max_days": 7, "rate": "0.015"},
                        {"period_text": "大于等于7天", "min_days": 7, "max_days": None, "rate": "0"},
                    ],
                    "fee_free_from_days": 7,
                    "ongoing_fee_rate": "0.014",
                    "round_trips": [
                        {"holding_days": 7, "purchase_rate": "0.0015", "redemption_rate": "0", "round_trip_cost": "0.0015"}
                    ],
                    "fee_drag": [{"years": 10, "ongoing_drag": "0.1316", "total_drag": "0.1329"}],
                    "source_urls": ["https://fundf10.eastmoney.com/jjfl_160003.html"],
                    "parse_issues": [],
                },
                "manager_tenure": {
                    "current_managers": ["甲"],
                    "lineup_start": "2020-01-02",
                    "periods": [
                        {
                            "label": "现任经理组合",
                            "start_date": "2020-01-02",
                            "first_nav_date": "2020-01-02",
                            "end_date": "2026-08-21",
                            "years": 6.63,
                            "total_return": 0.8,
                            "annualized_return": 0.093,
                            "max_drawdown": -0.35,
                        }
                    ],
                },
                "underlying_valuation": {
                    "basis": "benchmark_component",
                    "component_weight": 0.6,
                    "index": {
                        "name": "沪深300",
                        "pe_ttm": {"value": 13.8, "percentile": 0.55, "sample_count": 121, "window_start": "2016-08-31", "window_end": "2026-08-21", "series_id": "csi300_pe_ttm"},
                        "source": "乐咕乐股",
                    },
                    "note": "主动基金的持仓可以偏离业绩比较基准",
                },
                "gaps": [],
            },
        )

        report = self.service.assemble(execution)
        sections = {section.section_id: section for section in report.fact_sections}

        self.assertIn("免赎回费", [fact.label for fact in sections["open_fund_dealing"].facts])
        self.assertIn("10 年 13.16%", sections["open_fund_fee_drag"].facts[1].value)
        self.assertIn("年化 9.30%", sections["open_fund_manager_tenure"].facts[0].value)
        self.assertIn("近 10 年分位 55%", sections["open_fund_underlying_valuation"].facts[0].value)

    def test_open_fund_fact_matrix_blocks_missing_core_risk(self):
        fund = self._register(
            "fund:missing-risk",
            ASSET_TYPE_OPEN_FUND,
            "160002",
            market="cn_fund",
            exchange=None,
        )
        task = ResearchTaskFactory(self.master).create(
            asset_id=fund.asset_id,
            profile_id=PROFILE_OPEN_FUND,
            question_type=ResearchQuestionType.UNDERSTANDING,
            horizon=ResearchHorizon.LONG,
            as_of_date="2026-08-22",
        )
        execution = replace(
            _execution(task),
            phases=tuple(
                replace(
                    phase,
                    status=ResearchPhaseStatus.NOT_APPLICABLE,
                    sources=(),
                )
                if phase.phase_id in {
                    "fundamental",
                    "benchmark",
                    "strategy",
                }
                else phase
                for phase in _execution(task).phases
            ),
            strategy_result=None,
            fund_bundle=_fund_bundle(fund),
            risk_result=None,
            open_fund_standard_metrics=_standard_metrics(
                fund.asset_id
            ),
        )

        report = self.service.assemble(execution)

        self.assertEqual(
            report.overall_status,
            AcceptanceStatus.BLOCKED,
        )
        self.assertIn(
            "open_fund_required_risk_missing",
            {gap.code for gap in report.gaps},
        )

    def _register(
        self,
        asset_id,
        asset_type,
        symbol,
        *,
        market="cn_a",
        exchange="sh",
    ):
        return self.master.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=asset_id,
                market=market,
                exchange=exchange,
            ),
            source="unit-test",
            raw_symbol=symbol,
        )


def _fund_bundle(asset):
    return FundResearchBundle(
        asset=asset,
        start_date="2016-08-24",
        end_date="2026-08-22",
        as_of_date="2026-08-22",
        nav_records=(
            {
                "nav_date": "2026-08-20",
                "unit_nav": 1.50,
                "source": "unit-test:nav",
            },
            {
                "nav_date": "2026-08-21",
                "unit_nav": 1.52,
                "source": "unit-test:nav",
            },
        ),
        estimate_records=(),
        status_records=(
            {
                "purchase_status": "开放申购",
                "redemption_status": "开放赎回",
                "source": "unit-test:status",
            },
        ),
        scale_records=(
            {
                "report_date": "2026-06-30",
                "item_name": "基金总份额",
                "item_value": 100.0,
                "source": "unit-test:scale",
            },
        ),
        allocation_records=(
            {
                "report_date": "2026-06-30",
                "item_name": "制造业",
                "ratio": 80.0,
                "source": "unit-test:allocation",
            },
        ),
        relations=(),
        sources=("unit-test:nav",),
        complete=True,
        profile_snapshot=_profile_snapshot(asset.asset_id),
        periodic_snapshot=_periodic_snapshot(asset.asset_id),
    )


def _profile_snapshot(asset_id):
    return OpenFundProfileSnapshot(
        fund_asset_id=asset_id,
        effective_date="2026-06-11",
        announcement_date="2026-06-12",
        fund_name="测试基金",
        share_class_name="测试基金A",
        share_class_code="163417",
        manager_name="测试管理人",
        custodian_name="测试托管人",
        fund_managers=(
            OpenFundManagerTenure(
                name="测试经理",
                start_date="2020-01-01",
                page_number=1,
            ),
        ),
        benchmark_text="测试指数收益率×100%",
        management_fee_rate=Decimal("0.012"),
        custody_fee_rate=Decimal("0.002"),
        sales_service_fee_rate=None,
        source_document_id="profile-doc",
        source_title="测试基金产品资料概要",
        source_url="https://example.test/profile.pdf",
        source="official-unit-test",
        parser_version="unit-test-v1",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
        page_numbers={
            "benchmark_text": 2,
            "management_fee_rate": 4,
            "custody_fee_rate": 4,
        },
    )


def _periodic_snapshot(asset_id):
    return OpenFundPeriodicSnapshot(
        fund_asset_id=asset_id,
        report_period_start="2026-04-01",
        report_period_end="2026-06-30",
        announcement_date="2026-07-21",
        share_classes=(
            OpenFundShareClassSnapshot(
                share_class_name="测试基金A",
                share_class_code="163417",
                ending_shares=Decimal("100000000"),
                unit_nav=Decimal("1.2"),
                net_asset_value=Decimal("120000000"),
                page_number=2,
            ),
        ),
        share_flows=(
            OpenFundShareFlow(
                share_class_name="测试基金A",
                share_class_code="163417",
                opening_shares=Decimal("90000000"),
                subscriptions=Decimal("20000000"),
                redemptions=Decimal("10000000"),
                ending_shares=Decimal("100000000"),
                page_number=12,
            ),
        ),
        asset_allocations=(
            OpenFundAssetAllocation(
                category="权益投资",
                amount=Decimal("90000000"),
                ratio=Decimal("0.75"),
                basis="total_assets",
                page_number=8,
            ),
        ),
        industry_allocations=(
            OpenFundIndustryAllocation(
                category="制造业",
                fair_value=Decimal("60000000"),
                net_asset_ratio=Decimal("0.5"),
                market_scope="domestic",
                portfolio_scope="all",
                page_number=9,
            ),
        ),
        top_holdings=(
            OpenFundHolding(
                rank=1,
                security_code="000001",
                security_name="测试股票",
                quantity=Decimal("1000"),
                fair_value=Decimal("10000000"),
                net_asset_ratio=Decimal("0.08"),
                page_number=10,
            ),
        ),
        performance_comparisons=(
            OpenFundPerformanceComparison(
                period_label="过去三个月",
                fund_return=Decimal("0.05"),
                benchmark_return=Decimal("0.04"),
                tracking_difference=Decimal("0.01"),
                page_number=4,
                share_class_name="测试基金A",
                share_class_code="163417",
            ),
        ),
        source_document_id="periodic-doc",
        source_title="测试基金2026年第2季度报告",
        source_url="https://example.test/periodic.pdf",
        source="official-unit-test",
        parser_version="unit-test-v1",
        extraction_status=DisclosureExtractionStatus.EXTRACTED,
    )


def _stock_risk_result(asset_id):
    metrics = tuple(
        FactorValue(
            asset_id=asset_id,
            factor_name=name,
            as_of_date="2026-08-03",
            value=value,
            calculation_version="unit-v1",
            parameters={"window": "input"},
            input_start_date="2026-07-31",
            input_end_date="2026-08-03",
            source_dataset="stock_daily_bars",
            computed_at="2026-08-03T00:00:00+00:00",
        )
        for name, value in (
            ("annualized_return", 0.08),
            ("annualized_volatility", 0.22),
            ("max_drawdown", -0.18),
        )
    )
    return RiskCalculationResult(
        asset_id=asset_id,
        parameters=RiskMetricParameters(price_adjustment="none"),
        series=(),
        metrics=metrics,
        drawdown_episode=None,
    )


def _fundamental_result(asset_id):
    snapshot = FundamentalSnapshot(
        asset_id=asset_id,
        as_of_date="2026-08-03",
        report_period="2026-06-30",
        currency="CNY",
        market_price=Decimal("1520.5"),
        shares_outstanding=Decimal("1000"),
        fields={},
        prior_fields={},
    )
    metrics = tuple(
        FundamentalMetricResult(
            asset_id=asset_id,
            metric_name=name,
            as_of_date="2026-08-03",
            report_period="2026-06-30",
            status=MetricStatus.AVAILABLE,
            value=Decimal(value),
            unit=unit,
            formula="unit",
            numerator=None,
            denominator=None,
            reason=None,
            calculation_version="unit-v1",
            parameters={},
            inputs=(),
            computed_at="2026-08-03T00:00:00+00:00",
        )
        for name, value, unit in (
            ("pe_ttm", "35", "multiple"),
            ("roe", "0.305", "ratio"),
        )
    )
    return FundamentalMetricSet(snapshot=snapshot, metrics=metrics)


def _evidence_bundle(asset):
    return ResearchEvidenceBundle(
        asset=asset,
        start_date="2025-02-01",
        end_date="2026-08-03",
        as_of_date="2026-08-03",
        evidence=(
            ResearchEvidenceItem(
                evidence_id="evidence:disclosure:cninfo:1",
                asset_id=asset.asset_id,
                evidence_type="disclosure",
                tier=EvidenceTier.OFFICIAL,
                title="2026年半年度报告",
                event_date="2026-07-30",
                source="cninfo",
                source_record_id="1",
                source_url="https://example.test/report.pdf",
                content="2026年半年度报告",
            ),
        ),
        document_states=(),
        events=(),
        sources=("cninfo",),
        complete=True,
    )


def _risk_result(asset_id):
    metrics = tuple(
        FactorValue(
            asset_id=asset_id,
            factor_name=name,
            as_of_date="2026-08-21",
            value=value,
            calculation_version="unit-v1",
            parameters={"window": "input"},
            input_start_date="2026-08-20",
            input_end_date="2026-08-21",
            source_dataset="open_fund_nav",
            computed_at="2026-08-22T00:00:00+00:00",
        )
        for name, value in (
            ("annualized_return", 0.12),
            ("annualized_volatility", 0.25),
            ("max_drawdown", -0.10),
        )
    )
    return RiskCalculationResult(
        asset_id=asset_id,
        parameters=RiskMetricParameters(
            rolling_windows=(5, 10),
            price_adjustment="none",
        ),
        series=(),
        metrics=metrics,
        drawdown_episode=None,
    )


def _standard_metrics(asset_id):
    return OpenFundStandardMetricCalculator().calculate(
        asset_id=asset_id,
        records=[
            {
                "nav_date": "2025-08-21",
                "unit_nav": 1.0,
                "source": "unit-test:nav",
            },
            {
                "nav_date": "2026-08-21",
                "unit_nav": 1.2,
                "source": "unit-test:nav",
            },
        ],
        source_dataset="open_fund_nav",
        as_of_date="2026-08-22",
    )


def _benchmark_result(asset_id):
    definition = CompositeBenchmarkDefinition(
        asset_id=asset_id,
        effective_date="2026-06-11",
        source="official-unit-test",
        source_document_id="profile-doc",
        benchmark_text="测试指数收益率×100%",
        components=(
            BenchmarkComponent(
                label="测试指数",
                kind=BenchmarkComponentKind.INDEX,
                weight=Decimal("1"),
                source="https://example.test/index",
                index_asset_id="index:test",
            ),
        ),
    )
    return BenchmarkMetricCalculator().calculate(
        asset_id=asset_id,
        benchmark_asset_id=definition.synthetic_asset_id,
        benchmark_kind=BenchmarkKind.COMPOSITE,
        asset_records=[
            {"trade_date": "2026-08-19", "close": 100},
            {"trade_date": "2026-08-20", "close": 102},
            {"trade_date": "2026-08-21", "close": 101},
        ],
        benchmark_records=[
            {"trade_date": "2026-08-19", "close": 100},
            {"trade_date": "2026-08-20", "close": 101},
            {"trade_date": "2026-08-21", "close": 102},
        ],
        asset_dataset="open_fund_nav",
        benchmark_dataset="composite_benchmark",
        benchmark_definition=definition,
    )


def _execution(task):
    phases = tuple(
        ResearchPhaseResult(
            phase_id=phase_id,
            status=ResearchPhaseStatus.SUCCESS,
            summary=f"{phase_id} complete",
            sources=(f"unit:{phase_id}",),
        )
        for phase_id in (
            "task",
            "bars",
            "quality",
            "factors",
            "fundamental",
            "benchmark",
            "evidence",
            "charts",
            "strategy",
        )
    )
    return ResearchExecutionResult(
        task=task,
        windows=ResearchWindows(
            bar_start="2024-08-03",
            bar_end="2026-08-03",
            financial_start="2023-08-04",
            financial_end="2026-08-03",
            evidence_start="2025-02-01",
            evidence_end="2026-08-03",
            strategy_window_days=730,
        ),
        status=ResearchExecutionStatus.COMPLETE,
        phases=phases,
        gaps=(),
        strategy_result=StrategyResult(
            strategy_id="quality_value",
            strategy_version="v1",
            maturity=StrategyMaturity.RESEARCH_RULE,
            asset_id=task.asset_id,
            horizon=task.horizon,
            as_of_date=task.as_of_date,
            valid_until="2026-11-01",
            status=StrategyRunStatus.PASS,
            signal="research_pass",
            score=Decimal("1"),
            confidence=Decimal("1"),
            input_completeness=Decimal("1"),
            matched_conditions=("quality_gate",),
            failed_conditions=(),
            vetoed_conditions=(),
            missing_metrics=(),
            missing_evidence_types=(),
            metric_versions={"roe": "fundamental_v1"},
            counterevidence=(),
            limitations=("Thresholds are research assumptions",),
            input_effective_dates={"roe": "2026-08-03"},
        ),
    )


if __name__ == "__main__":
    unittest.main()
