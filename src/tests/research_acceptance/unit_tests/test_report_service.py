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
from src.factor_engineering import (
    FactorValue,
    OpenFundStandardMetricCalculator,
    RiskCalculationResult,
    RiskMetricParameters,
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
from src.research_workflow import (
    PROFILE_OPEN_FUND,
    PROFILE_ORDINARY_COMPANY,
    ResearchExecutionGap,
    ResearchExecutionResult,
    ResearchExecutionStatus,
    ResearchHorizon,
    ResearchPhaseResult,
    ResearchPhaseStatus,
    ResearchQuestionType,
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
