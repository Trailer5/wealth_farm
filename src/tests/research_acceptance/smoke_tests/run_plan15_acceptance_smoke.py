"""Run the frozen Plan 15 acceptance tasks with real public APIs."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.benchmark_analysis import (
        BENCHMARK_CALCULATION_VERSION,
        BenchmarkAnalysisService,
        BenchmarkKind,
        SQLiteBenchmarkRepository,
    )
    from src.data_access import (
        DATASET_EXCHANGE_FUND_DAILY_BARS,
        DATASET_STOCK_FINANCIAL_INDICATORS,
        DATASET_STOCK_DAILY_BARS,
        AssetInput,
        DataAccessRequest,
        DataAccessService,
        SQLiteDataAccessRepository,
    )
    from src.data_quality import (
        DataQualityService,
        SQLiteDataQualityRepository,
    )
    from src.data_src.service import DataFetchService
    from src.data_store import SQLiteDataStore
    from src.event_knowledge import (
        EventKnowledgeService,
        SQLiteEventRepository,
    )
    from src.factor_engineering import (
        COMMODITY_SENSITIVITY_VERSION,
        RISK_CALCULATION_VERSION,
        CommoditySensitivityService,
        FactorService,
        RiskMetricParameters,
        SQLiteFactorRepository,
    )
    from src.financial_data import SQLiteFinancialDataRepository
    from src.fund_research import (
        ETFMarketSnapshot,
        ExchangeFundMetricService,
        official_etf_expense_schedule,
    )
    from src.fundamental_analysis import (
        FUNDAMENTAL_CALCULATION_VERSION,
        FINANCIAL_INSTITUTION_VERSION,
        FinancialInstitutionAnalysisService,
        FundamentalAnalysisService,
        FundamentalMetricResult,
        MetricStatus,
        SQLiteFundamentalMetricRepository,
    )
    from src.master_data import (
        ASSET_TYPE_EXCHANGE_FUND,
        MasterDataService,
        SQLiteMasterDataRepository,
    )
    from src.research_acceptance import (
        AcceptanceStepResult,
        AcceptanceStepStatus,
        ClaimKind,
        CrossAssetAcceptanceService,
        InvalidationCondition,
        PLAN15_ACCEPTANCE_VERSION,
        PLAN15_AS_OF_DATE,
        PLAN15_BAR_END,
        PLAN15_BAR_START,
        PLAN15_FINANCIAL_END,
        PLAN15_FINANCIAL_START,
        RepresentativeResearchAssembler,
        ResearchClaim,
        ResearchGap,
        ResearchScenario,
        ScenarioKind,
        build_plan15_tasks,
        register_plan15_assets,
    )
    from src.research_evidence import ResearchEvidenceService
    from src.peer_analysis import (
        IndustryAssignment,
        PeerAnalysisService,
        PeerSet,
        PeerSetMember,
        SQLitePeerAnalysisRepository,
    )
    from src.research_visualization import (
        CHART_VERSION,
        ChartRenderer,
        ChartType,
        build_factor_series_spec,
        build_financial_trends_spec,
        build_price_volume_spec,
        build_valuation_history_spec,
        chart_types_for_profile,
    )
    from src.research_workflow import default_profile_registry
    from src.strategy import (
        StrategyExecutor,
        StrategyInputBuilder,
        StrategyRunStatus,
        cycle_strategy_specs,
        default_strategy_specs,
        exchange_fund_strategy_specs,
        financial_institution_strategy_specs,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        db_path = root / "plan15-acceptance.sqlite3"
        store = SQLiteDataStore(db_path)
        master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        assets = register_plan15_assets(master)
        tasks = build_plan15_tasks(master, assets)
        fetch_service = DataFetchService(
            store=store,
            master_data_service=master,
        )
        data_access = DataAccessService(
            master_data=master,
            repository=SQLiteDataAccessRepository(store),
            fetch_service=fetch_service,
            financial_repository=SQLiteFinancialDataRepository(
                db_path
            ),
        )
        quality_service = DataQualityService(
            SQLiteDataQualityRepository(db_path)
        )
        factor_repository = SQLiteFactorRepository(db_path)
        factor_service = FactorService(
            factor_repository,
            master,
        )

        responses = {}
        quality = {}
        factor_values = {}
        risk_results = {}
        fetch_errors = {}
        for name, task in tasks.items():
            asset = assets[name]
            dataset = (
                DATASET_EXCHANGE_FUND_DAILY_BARS
                if asset.asset_type
                == ASSET_TYPE_EXCHANGE_FUND
                else DATASET_STOCK_DAILY_BARS
            )
            try:
                response = data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(asset_id=asset.asset_id),
                        dataset=dataset,
                        start_date=PLAN15_BAR_START,
                        end_date=PLAN15_BAR_END,
                        as_of_date=PLAN15_AS_OF_DATE,
                        allow_remote=True,
                        remote_sources=(
                            "mootdx",
                            "tencent",
                            "sina",
                        ),
                        provider_timeout_seconds=30,
                    )
                )
                responses[name] = response
                quality[name] = (
                    quality_service.evaluate_data_access_response(
                        task_name=f"plan15-{name}-bars",
                        response=response,
                        expected_start=PLAN15_BAR_START,
                        expected_end=PLAN15_BAR_END,
                    )
                )
                risk = factor_service.compute_risk_from_response(
                    response,
                    parameters=RiskMetricParameters(
                        rolling_windows=(5, 10),
                        price_adjustment="none",
                    ),
                )
                risk_results[name] = risk
                factor_values[name] = [
                    *factor_service.compute_from_response(response),
                    *risk.values,
                ]
            except Exception as exc:
                fetch_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )
                factor_values[name] = []

        benchmark_service = BenchmarkAnalysisService(
            master_data=master,
            data_access=data_access,
            factor_repository=factor_repository,
            benchmark_repository=SQLiteBenchmarkRepository(db_path),
        )
        comparison_results = {}
        comparison_errors = {}
        for name in tasks:
            if name not in responses:
                continue
            try:
                benchmark_service.set_benchmark(
                    asset_id=assets[name].asset_id,
                    benchmark_asset_id=assets[
                        "csi300_index"
                    ].asset_id,
                    benchmark_kind=BenchmarkKind.INDEX,
                    source="plan15",
                    effective_date="2026-01-01",
                    reason="Frozen broad-market comparison",
                )
                result = benchmark_service.compare(
                    asset_id=assets[name].asset_id,
                    start_date=PLAN15_BAR_START,
                    end_date=PLAN15_BAR_END,
                    as_of_date=PLAN15_AS_OF_DATE,
                    provider_timeout_seconds=90,
                )
                comparison_results[name] = result
                factor_values[name].extend(result.values)
            except Exception as exc:
                comparison_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )

        etf_metric_errors = {}
        expense_schedules = {
            name: official_etf_expense_schedule(
                asset_id=assets[name].asset_id,
                standard_symbol=assets[name].standard_symbol,
            )
            for name in ("gold_etf", "csi300_etf")
        }
        try:
            spot_rows = fetch_service.fetch_exchange_fund_spot(
                "akshare",
                symbols=["518880", "510300"],
            )
        except Exception as exc:
            spot_rows = []
            spot_error = f"{type(exc).__name__}: {exc}"
        else:
            spot_error = None
        etf_metric_service = ExchangeFundMetricService(
            master_data=master,
            factor_repository=factor_repository,
        )
        for name in ("gold_etf", "csi300_etf"):
            try:
                if spot_error:
                    raise RuntimeError(spot_error)
                symbol = assets[name].standard_symbol
                row = next(
                    item
                    for item in spot_rows
                    if str(item.get("代码") or item.get("symbol"))
                    == symbol
                )
                market_date = str(
                    row.get("数据日期") or PLAN15_AS_OF_DATE
                )[:10]
                metric_set = (
                    etf_metric_service.calculate_and_store(
                        asset_id=assets[name].asset_id,
                        as_of_date=PLAN15_AS_OF_DATE,
                        market=ETFMarketSnapshot(
                            asset_id=assets[name].asset_id,
                            as_of_date=market_date,
                            market_price=Decimal(
                                str(row["最新价"])
                            ),
                            iopv=Decimal(
                                str(row["IOPV实时估值"])
                            ),
                            source=(
                                "akshare:fund_etf_spot_em"
                            ),
                            raw=dict(row),
                        ),
                        expenses=expense_schedules[name],
                    )
                )
                factor_values[name].extend(metric_set.values)
            except Exception as exc:
                etf_metric_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )

        commodity_error = None
        try:
            copper_records = [
                row
                for row in (
                    fetch_service.fetch_copper_continuous_history()
                )
                if PLAN15_BAR_START
                <= str(row.get("date"))[:10]
                <= PLAN15_BAR_END
            ]
            gold_records = [
                row
                for row in fetch_service.fetch_gold_spot_history()
                if PLAN15_BAR_START
                <= str(row.get("date"))[:10]
                <= PLAN15_BAR_END
            ]
            commodity_service = CommoditySensitivityService(
                factor_repository
            )
            factor_values["zijin"].append(
                commodity_service.calculate_and_store(
                    asset_id=assets["zijin"].asset_id,
                    asset_records=list(
                        responses["zijin"].records
                    ),
                    benchmarks={
                        "sina:CU0": copper_records,
                        "sge:Au99.99": gold_records,
                    },
                    weights={
                        "sina:CU0": 0.7,
                        "sge:Au99.99": 0.3,
                    },
                    as_of_date=PLAN15_BAR_END,
                )
            )
            factor_values["gold_etf"].append(
                commodity_service.calculate_and_store(
                    asset_id=assets["gold_etf"].asset_id,
                    asset_records=list(
                        responses["gold_etf"].records
                    ),
                    benchmarks={"sge:Au99.99": gold_records},
                    weights={"sge:Au99.99": 1.0},
                    as_of_date=PLAN15_BAR_END,
                )
            )
        except Exception as exc:
            commodity_error = f"{type(exc).__name__}: {exc}"

        fundamental = None
        fundamental_error = None
        evidence = None
        evidence_error = None
        bank_evidence = None
        bank_evidence_error = None
        zijin_fundamental = None
        zijin_error = None
        zijin_evidence = None
        zijin_evidence_error = None
        fundamental_repository = (
            SQLiteFundamentalMetricRepository(db_path)
        )
        if "maotai" in responses:
            try:
                bundle = data_access.get_financial_bundle(
                    asset_id=assets["maotai"].asset_id,
                    start_date=PLAN15_FINANCIAL_START,
                    end_date=PLAN15_FINANCIAL_END,
                    as_of_date=PLAN15_AS_OF_DATE,
                    allow_remote=True,
                    provider_timeout_seconds=120,
                )
                fundamental = FundamentalAnalysisService(
                    master_data=master,
                    repository=fundamental_repository,
                ).analyze(
                    bundle,
                    market_price=Decimal(
                        str(
                            responses["maotai"].records[-1][
                                "close"
                            ]
                        )
                    ),
                )
            except Exception as exc:
                fundamental_error = (
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                evidence = ResearchEvidenceService(
                    master_data=master,
                    data_access=data_access,
                    event_knowledge=EventKnowledgeService(
                        SQLiteEventRepository(db_path),
                        master,
                    ),
                    store=store,
                ).get_bundle(
                    asset_id=assets["maotai"].asset_id,
                    start_date="2025-01-01",
                    end_date=PLAN15_AS_OF_DATE,
                    as_of_date=PLAN15_AS_OF_DATE,
                    allow_remote=True,
                    disclosure_sources=("cninfo",),
                    research_sources=("eastmoney",),
                    provider_timeout_seconds=90,
                )
            except Exception as exc:
                evidence_error = (
                    f"{type(exc).__name__}: {exc}"
                )
        if "pingan_bank" in responses:
            try:
                bank_evidence = ResearchEvidenceService(
                    master_data=master,
                    data_access=data_access,
                    event_knowledge=EventKnowledgeService(
                        SQLiteEventRepository(db_path),
                        master,
                    ),
                    store=store,
                ).get_bundle(
                    asset_id=assets["pingan_bank"].asset_id,
                    start_date="2025-01-01",
                    end_date=PLAN15_AS_OF_DATE,
                    as_of_date=PLAN15_AS_OF_DATE,
                    allow_remote=True,
                    disclosure_sources=("cninfo",),
                    research_sources=("eastmoney",),
                    provider_timeout_seconds=90,
                )
            except Exception as exc:
                bank_evidence_error = (
                    f"{type(exc).__name__}: {exc}"
                )
        if "zijin" in responses:
            try:
                zijin_bundle = data_access.get_financial_bundle(
                    asset_id=assets["zijin"].asset_id,
                    start_date=PLAN15_FINANCIAL_START,
                    end_date=PLAN15_FINANCIAL_END,
                    as_of_date=PLAN15_AS_OF_DATE,
                    allow_remote=True,
                    provider_timeout_seconds=120,
                )
                zijin_fundamental = FundamentalAnalysisService(
                    master_data=master,
                    repository=fundamental_repository,
                ).analyze(
                    zijin_bundle,
                    market_price=Decimal(
                        str(responses["zijin"].records[-1]["close"])
                    ),
                )
                zijin_evidence = ResearchEvidenceService(
                    master_data=master,
                    data_access=data_access,
                    event_knowledge=EventKnowledgeService(
                        SQLiteEventRepository(db_path),
                        master,
                    ),
                    store=store,
                ).get_bundle(
                    asset_id=assets["zijin"].asset_id,
                    start_date="2025-01-01",
                    end_date=PLAN15_AS_OF_DATE,
                    as_of_date=PLAN15_AS_OF_DATE,
                    allow_remote=True,
                    disclosure_sources=("cninfo",),
                    research_sources=("eastmoney",),
                    provider_timeout_seconds=90,
                )
            except Exception as exc:
                zijin_error = f"{type(exc).__name__}: {exc}"
                zijin_evidence_error = zijin_error

        peer_comparison = None
        peer_error = None
        try:
            for peer_name in (
                "maotai",
                "wuliangye",
                "luzhou_laojiao",
                "shanxi_fenjiu",
            ):
                response = data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(
                            asset_id=assets[peer_name].asset_id
                        ),
                        dataset=(
                            DATASET_STOCK_FINANCIAL_INDICATORS
                        ),
                        start_date="2024-01-01",
                        end_date=PLAN15_FINANCIAL_END,
                        as_of_date=PLAN15_AS_OF_DATE,
                        allow_remote=True,
                        remote_sources=("akshare",),
                        provider_timeout_seconds=120,
                    )
                )
                roe_records = [
                    record
                    for record in response.records
                    if record["field_name"] == "roe"
                ]
                if not roe_records:
                    raise RuntimeError(
                        "No normalized ROE for "
                        f"{assets[peer_name].standard_symbol}"
                    )
                latest = max(
                    roe_records,
                    key=lambda record: str(
                        record["report_period"]
                    ),
                )
                value = Decimal(
                    str(latest["normalized_value"])
                )
                fundamental_repository.upsert_results(
                    [
                        FundamentalMetricResult(
                            asset_id=assets[peer_name].asset_id,
                            metric_name="roe",
                            as_of_date=PLAN15_AS_OF_DATE,
                            report_period=str(
                                latest["report_period"]
                            ),
                            status=MetricStatus.AVAILABLE,
                            value=value,
                            unit="ratio",
                            formula="provider_reported_roe",
                            numerator=value,
                            denominator=Decimal("1"),
                            reason=None,
                            calculation_version=(
                                FUNDAMENTAL_CALCULATION_VERSION
                            ),
                            parameters={"currency": "CNY"},
                            inputs=(),
                            computed_at=datetime.now(
                                timezone.utc
                            ).isoformat(),
                        )
                    ]
                )
            peer_service = PeerAnalysisService(
                master_data=master,
                metric_repository=fundamental_repository,
                peer_repository=SQLitePeerAnalysisRepository(
                    db_path
                ),
            )
            peer_service.assign_industry(
                IndustryAssignment(
                    asset_id=assets["maotai"].asset_id,
                    industry_code="baijiu",
                    industry_name="白酒",
                    classification_source="manual_review",
                    classification_version="plan15_v1",
                    effective_date="2026-01-01",
                )
            )
            peer_service.save_peer_set(
                PeerSet(
                    peer_set_id="peer-set:plan15:maotai:baijiu",
                    target_asset_id=assets["maotai"].asset_id,
                    name="贵州茅台白酒可比组",
                    source="manual_review",
                    version="plan15_v1",
                    effective_date="2026-01-01",
                    members=(
                        PeerSetMember(
                            assets["wuliangye"].asset_id,
                            True,
                            "全国高端白酒龙头",
                        ),
                        PeerSetMember(
                            assets["luzhou_laojiao"].asset_id,
                            True,
                            "全国性浓香白酒",
                        ),
                        PeerSetMember(
                            assets["shanxi_fenjiu"].asset_id,
                            True,
                            "次高端白酒全国化",
                        ),
                        PeerSetMember(
                            assets["tsingtao"].asset_id,
                            False,
                            "啤酒商业模式与利润结构不可比",
                        ),
                    ),
                )
            )
            peer_comparison = peer_service.compare(
                peer_set_id="peer-set:plan15:maotai:baijiu",
                as_of_date=PLAN15_AS_OF_DATE,
                metric_names=("roe",),
            )
            if (
                peer_comparison.comparisons[0].status.value
                != "available"
            ):
                raise RuntimeError(
                    "Maotai peer comparison was not available"
                )
        except Exception as exc:
            peer_error = f"{type(exc).__name__}: {exc}"

        bank_metrics = {}
        bank_peer_comparison = None
        bank_error = None
        try:
            bank_service = FinancialInstitutionAnalysisService(
                master_data=master,
                repository=fundamental_repository,
            )
            for bank_name in (
                "pingan_bank",
                "cmb",
                "industrial_bank",
                "spdb",
            ):
                if bank_name == "pingan_bank":
                    bar_response = responses[bank_name]
                else:
                    bar_response = data_access.get(
                        DataAccessRequest(
                            asset=AssetInput(
                                asset_id=assets[bank_name].asset_id
                            ),
                            dataset=DATASET_STOCK_DAILY_BARS,
                            start_date=PLAN15_BAR_START,
                            end_date=PLAN15_BAR_END,
                            as_of_date=PLAN15_AS_OF_DATE,
                            allow_remote=True,
                            remote_sources=(
                                "mootdx",
                                "tencent",
                                "sina",
                            ),
                            provider_timeout_seconds=30,
                        )
                    )
                indicator_response = data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(
                            asset_id=assets[bank_name].asset_id
                        ),
                        dataset=(
                            DATASET_STOCK_FINANCIAL_INDICATORS
                        ),
                        start_date="2024-01-01",
                        end_date=PLAN15_FINANCIAL_END,
                        as_of_date=PLAN15_AS_OF_DATE,
                        allow_remote=True,
                        remote_sources=("akshare",),
                        provider_timeout_seconds=120,
                    )
                )
                bank_metrics[bank_name] = bank_service.analyze(
                    asset_id=assets[bank_name].asset_id,
                    as_of_date=PLAN15_AS_OF_DATE,
                    market_price=Decimal(
                        str(bar_response.records[-1]["close"])
                    ),
                    indicator_records=list(
                        indicator_response.records
                    ),
                )
            bank_peer_service = PeerAnalysisService(
                master_data=master,
                metric_repository=fundamental_repository,
                peer_repository=SQLitePeerAnalysisRepository(
                    db_path
                ),
            )
            bank_peer_service.assign_industry(
                IndustryAssignment(
                    asset_id=assets["pingan_bank"].asset_id,
                    industry_code="commercial_bank",
                    industry_name="商业银行",
                    classification_source="manual_review",
                    classification_version="plan15_v1",
                    effective_date="2026-01-01",
                )
            )
            bank_peer_service.save_peer_set(
                PeerSet(
                    peer_set_id="peer-set:plan15:pingan:bank",
                    target_asset_id=assets[
                        "pingan_bank"
                    ].asset_id,
                    name="平安银行股份制银行可比组",
                    source="manual_review",
                    version="plan15_v1",
                    effective_date="2026-01-01",
                    members=(
                        PeerSetMember(
                            assets["cmb"].asset_id,
                            True,
                            "全国性股份制商业银行",
                        ),
                        PeerSetMember(
                            assets["industrial_bank"].asset_id,
                            True,
                            "全国性股份制商业银行",
                        ),
                        PeerSetMember(
                            assets["spdb"].asset_id,
                            True,
                            "全国性股份制商业银行",
                        ),
                    ),
                )
            )
            bank_peer_comparison = bank_peer_service.compare(
                peer_set_id="peer-set:plan15:pingan:bank",
                as_of_date=PLAN15_AS_OF_DATE,
                metric_names=(
                    "roe",
                    "capital_adequacy",
                    "net_interest_margin",
                    "non_performing_loan_ratio",
                ),
            )
            if any(
                comparison.status.value != "available"
                for comparison in bank_peer_comparison.comparisons
            ):
                raise RuntimeError(
                    "Bank peer metrics were not all comparable"
                )
        except Exception as exc:
            bank_error = f"{type(exc).__name__}: {exc}"

        resource_peer_comparison = None
        resource_peer_error = None
        try:
            for resource_name in (
                "zijin",
                "jiangxi_copper",
                "shandong_gold",
                "cmoc",
            ):
                response = data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(
                            asset_id=assets[resource_name].asset_id
                        ),
                        dataset=DATASET_STOCK_FINANCIAL_INDICATORS,
                        start_date="2024-01-01",
                        end_date=PLAN15_FINANCIAL_END,
                        as_of_date=PLAN15_AS_OF_DATE,
                        allow_remote=True,
                        remote_sources=("akshare",),
                        provider_timeout_seconds=120,
                    )
                )
                records = [
                    record
                    for record in response.records
                    if record["field_name"] == "roic"
                ]
                latest = max(
                    records,
                    key=lambda record: str(
                        record["report_period"]
                    ),
                )
                value = Decimal(str(latest["normalized_value"]))
                fundamental_repository.upsert_results(
                    [
                        FundamentalMetricResult(
                            asset_id=assets[
                                resource_name
                            ].asset_id,
                            metric_name="roic",
                            as_of_date=PLAN15_AS_OF_DATE,
                            report_period=str(
                                latest["report_period"]
                            ),
                            status=MetricStatus.AVAILABLE,
                            value=value,
                            unit="ratio",
                            formula="provider_reported_roic",
                            numerator=value,
                            denominator=Decimal("1"),
                            reason=None,
                            calculation_version=(
                                FUNDAMENTAL_CALCULATION_VERSION
                            ),
                            parameters={"currency": "CNY"},
                            inputs=(),
                            computed_at=datetime.now(
                                timezone.utc
                            ).isoformat(),
                        )
                    ]
                )
            resource_peer_service = PeerAnalysisService(
                master_data=master,
                metric_repository=fundamental_repository,
                peer_repository=SQLitePeerAnalysisRepository(
                    db_path
                ),
            )
            resource_peer_service.assign_industry(
                IndustryAssignment(
                    asset_id=assets["zijin"].asset_id,
                    industry_code="diversified_metals",
                    industry_name="有色金属资源",
                    classification_source="manual_review",
                    classification_version="plan15_v1",
                    effective_date="2026-01-01",
                )
            )
            resource_peer_service.save_peer_set(
                PeerSet(
                    peer_set_id="peer-set:plan15:zijin:resources",
                    target_asset_id=assets["zijin"].asset_id,
                    name="紫金矿业资源公司可比组",
                    source="manual_review",
                    version="plan15_v1",
                    effective_date="2026-01-01",
                    members=tuple(
                        PeerSetMember(
                            assets[item].asset_id,
                            True,
                            "A股大型有色金属资源公司",
                        )
                        for item in (
                            "jiangxi_copper",
                            "shandong_gold",
                            "cmoc",
                        )
                    ),
                )
            )
            resource_peer_comparison = (
                resource_peer_service.compare(
                    peer_set_id=(
                        "peer-set:plan15:zijin:resources"
                    ),
                    as_of_date=PLAN15_AS_OF_DATE,
                    metric_names=("roic",),
                )
            )
        except Exception as exc:
            resource_peer_error = (
                f"{type(exc).__name__}: {exc}"
            )

        chart_artifacts = {}
        chart_errors = {}
        renderer = ChartRenderer(root / "research")
        for name, task in tasks.items():
            if name not in responses or name not in risk_results:
                continue
            response = responses[name]
            risk = risk_results[name]
            specs = [
                build_price_volume_spec(
                    task=task,
                    asset=assets[name],
                    records=list(response.records),
                    source_dataset=response.dataset,
                    price_adjustment="none",
                ),
                build_factor_series_spec(
                    task=task,
                    asset=assets[name],
                    chart_type=ChartType.CUMULATIVE_RETURN,
                    values=list(risk.series),
                    factor_names=("cumulative_return",),
                    source_dataset=RISK_CALCULATION_VERSION,
                ),
                build_factor_series_spec(
                    task=task,
                    asset=assets[name],
                    chart_type=ChartType.DRAWDOWN,
                    values=list(risk.series),
                    factor_names=("drawdown_series",),
                    source_dataset=RISK_CALCULATION_VERSION,
                ),
                build_factor_series_spec(
                    task=task,
                    asset=assets[name],
                    chart_type=ChartType.ROLLING_RISK,
                    values=list(risk.series),
                    factor_names=(
                        "rolling_annualized_volatility_5d",
                        "rolling_sharpe_5d",
                    ),
                    source_dataset=RISK_CALCULATION_VERSION,
                ),
            ]
            if name == "maotai" and fundamental is not None:
                specs.extend(
                    [
                        build_valuation_history_spec(
                            task=task,
                            asset=assets[name],
                            results=list(fundamental.metrics),
                        ),
                        build_financial_trends_spec(
                            task=task,
                            asset=assets[name],
                            snapshots=[fundamental.snapshot],
                            metric_results=list(
                                fundamental.metrics
                            ),
                        ),
                    ]
                )
            try:
                chart_artifacts[name] = [
                    renderer.render(spec) for spec in specs
                ]
            except Exception as exc:
                chart_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )

        strategy_specs = (
            *cycle_strategy_specs(),
            *default_strategy_specs(),
            *exchange_fund_strategy_specs(),
            *financial_institution_strategy_specs(),
        )
        strategy_results = {}
        strategy_errors = {}
        input_builder = StrategyInputBuilder()
        executor = StrategyExecutor()
        for name, task in tasks.items():
            compatible_specs = [
                spec
                for spec in strategy_specs
                if task.horizon in spec.horizons
                and task.profile_id in spec.supported_profiles
            ]
            if not compatible_specs:
                strategy_errors[name] = (
                    "No strategy supports the frozen profile and "
                    f"horizon: {task.profile_id}/{task.horizon.value}"
                )
                continue
            strategy_spec = compatible_specs[0]
            try:
                strategy_input = input_builder.build(
                    asset_id=assets[name].asset_id,
                    profile_id=task.profile_id,
                    horizon=task.horizon,
                    as_of_date=PLAN15_AS_OF_DATE,
                    factor_values=list(
                        factor_values.get(name, ())
                    ),
                    fundamental_results=(
                        list(fundamental.metrics)
                        if name == "maotai"
                        and fundamental is not None
                        else (
                            list(bank_metrics["pingan_bank"])
                            if name == "pingan_bank"
                            and "pingan_bank" in bank_metrics
                            else (
                                list(zijin_fundamental.metrics)
                                if name == "zijin"
                                and zijin_fundamental is not None
                                else []
                            )
                        )
                    ),
                    evidence_bundle=(
                        evidence
                        if name == "maotai"
                        else (
                            bank_evidence
                            if name == "pingan_bank"
                            else (
                                zijin_evidence
                                if name == "zijin"
                                else None
                            )
                        )
                    ),
                    factor_parameter_filters={
                        "max_drawdown": {"window": "input"},
                        "annualized_volatility": {
                            "window": "input"
                        },
                    },
                    extra_evidence_types=(
                        ("financial_statement",)
                        if name == "zijin"
                        and zijin_fundamental is not None
                        else ()
                    ),
                )
                strategy_results[name] = executor.execute(
                    strategy_spec,
                    strategy_input,
                )
            except Exception as exc:
                strategy_errors[name] = (
                    f"{type(exc).__name__}: {exc}"
                )

        profile_registry = default_profile_registry()
        assembler = RepresentativeResearchAssembler()
        reports = []
        for name, task in tasks.items():
            profile = profile_registry.require(task.profile_id)
            available_metrics = {
                value.factor_name
                for value in factor_values.get(name, ())
            }
            if name == "maotai" and fundamental is not None:
                available_metrics.update(
                    metric.metric_name
                    for metric in fundamental.metrics
                    if metric.value is not None
                )
            if name == "pingan_bank" and name in bank_metrics:
                available_metrics.update(
                    metric.metric_name
                    for metric in bank_metrics[name]
                    if metric.value is not None
                )
            if name == "zijin" and zijin_fundamental is not None:
                available_metrics.update(
                    metric.metric_name
                    for metric in zijin_fundamental.metrics
                    if metric.value is not None
                )
            missing_profile_metrics = tuple(
                sorted(
                    set(profile.required_metrics)
                    - available_metrics
                )
            )
            gaps = _gaps(
                name=name,
                missing_profile_metrics=missing_profile_metrics,
                fetch_error=fetch_errors.get(name),
                comparison_error=comparison_errors.get(name),
                peer_comparison=(
                    peer_comparison
                    if name == "maotai"
                    else (
                        bank_peer_comparison
                        if name == "pingan_bank"
                        else (
                            resource_peer_comparison
                            if name == "zijin"
                            else None
                        )
                    )
                ),
                peer_error=(
                    peer_error
                    if name == "maotai"
                    else (
                        bank_error
                        if name == "pingan_bank"
                        else (
                            resource_peer_error
                            if name == "zijin"
                            else None
                        )
                    )
                ),
                chart_error=chart_errors.get(name),
                strategy_error=strategy_errors.get(name),
                fundamental_error=(
                    fundamental_error
                    if name == "maotai"
                    else None
                ),
                evidence_error=(
                    evidence_error
                    if name == "maotai"
                    else (
                        bank_evidence_error
                        if name == "pingan_bank"
                        else (
                            zijin_evidence_error
                            if name == "zijin"
                            else None
                        )
                    )
                ),
                etf_metric_error=etf_metric_errors.get(name),
                commodity_error=(
                    commodity_error
                    if name in {"gold_etf", "zijin"}
                    else None
                ),
            )
            steps = _steps(
                name=name,
                task=task,
                response=responses.get(name),
                quality=quality.get(name),
                available_metrics=available_metrics,
                missing_profile_metrics=missing_profile_metrics,
                artifacts=chart_artifacts.get(name, ()),
                expected_chart_types=chart_types_for_profile(
                    task.profile_id
                ),
                comparison=comparison_results.get(name),
                peer_comparison=(
                    peer_comparison
                    if name == "maotai"
                    else (
                        bank_peer_comparison
                        if name == "pingan_bank"
                        else (
                            resource_peer_comparison
                            if name == "zijin"
                            else None
                        )
                    )
                ),
                strategy_result=strategy_results.get(name),
                evidence=(
                    evidence
                    if name == "maotai"
                    else (
                        bank_evidence
                        if name == "pingan_bank"
                        else (
                            zijin_evidence
                            if name == "zijin"
                            else None
                        )
                    )
                ),
                errors={
                    "fetch": fetch_errors.get(name),
                    "comparison": comparison_errors.get(name),
                    "peer": (
                        peer_error
                        if name == "maotai"
                        else (
                            bank_error
                            if name == "pingan_bank"
                            else (
                                resource_peer_error
                                if name == "zijin"
                                else None
                            )
                        )
                    ),
                    "chart": chart_errors.get(name),
                    "strategy": strategy_errors.get(name),
                    "evidence": (
                        evidence_error
                        if name == "maotai"
                        else (
                            bank_evidence_error
                            if name == "pingan_bank"
                            else (
                                zijin_evidence_error
                                if name == "zijin"
                                else None
                            )
                        )
                    ),
                },
            )
            scenarios = _scenarios(
                strategy_results.get(name)
            )
            report = assembler.assemble(
                acceptance_version=PLAN15_ACCEPTANCE_VERSION,
                task=task,
                asset_name=assets[name].display_name or name,
                asset_type=assets[name].asset_type,
                steps=steps,
                scenarios=scenarios,
                strongest_counterargument=_counterargument(
                    strategy_results.get(name)
                ),
                invalidation_conditions=(
                    InvalidationCondition(
                        condition=(
                            "A major profile risk becomes a verified "
                            "thesis-breaking event"
                        ),
                        evidence_required=(
                            ",".join(profile.major_risks)
                        ),
                        severity="thesis_break",
                    ),
                ),
                conclusions=(
                    ResearchClaim(
                        kind=ClaimKind.FACT,
                        text=(
                            "The frozen acceptance task was executed "
                            "through public service boundaries."
                        ),
                        evidence_refs=(
                            f"task:{task.task_id}",
                            "acceptance:steps",
                        ),
                    ),
                    ResearchClaim(
                        kind=(
                            ClaimKind.UNKNOWN
                            if gaps
                            else ClaimKind.INFERENCE
                        ),
                        text=(
                            "Research remains incomplete where the "
                            "gap list identifies missing capabilities."
                            if gaps
                            else (
                                "The current module chain supports "
                                "this frozen research task."
                            )
                        ),
                        evidence_refs=("acceptance:gaps",),
                    ),
                ),
                gaps=gaps,
            )
            reports.append(report)

        summary = CrossAssetAcceptanceService().summarize(
            acceptance_version=PLAN15_ACCEPTANCE_VERSION,
            as_of_date=PLAN15_AS_OF_DATE,
            reports=tuple(reports),
        )
        if len(summary.reports) != 5:
            raise AssertionError("Expected five acceptance reports")
        if not summary.consistency_verified:
            raise AssertionError(
                "Frozen report structures are not replayable"
            )
        incomplete = [
            report.asset_name
            for report in reports
            if report.overall_status.value != "complete"
        ]
        if incomplete:
            raise AssertionError(
                f"Incomplete acceptance reports: {incomplete}"
            )
        if not summary.agent_guidance_admitted:
            raise AssertionError(
                "Agent guidance admission criteria were not met"
            )
        print(
            json.dumps(
                {
                    "acceptance_version": (
                        summary.acceptance_version
                    ),
                    "as_of_date": summary.as_of_date,
                    "reports": [
                        {
                            "asset": report.asset_name,
                            "profile": report.task.profile_id,
                            "status": report.overall_status.value,
                            "step_status": {
                                step.step_id: step.status.value
                                for step in report.steps
                            },
                            "gap_codes": [
                                gap.code for gap in report.gaps
                            ],
                            "metric_versions": sorted(
                                {
                                    version
                                    for step in report.steps
                                    for version in (
                                        step.metric_versions
                                    )
                                }
                            ),
                            "chart_count": len(
                                next(
                                    step.artifacts
                                    for step in report.steps
                                    if step.step_id == "charts"
                                )
                            ),
                            "replay_verified": (
                                report.replay_verified
                            ),
                        }
                        for report in reports
                    ],
                    "complete_profile_count": (
                        summary.complete_profile_count
                    ),
                    "required_complete_profiles": (
                        summary.required_complete_profiles
                    ),
                    "agent_guidance_admitted": (
                        summary.agent_guidance_admitted
                    ),
                    "admission_reasons": list(
                        summary.admission_reasons
                    ),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
    return 0


def _steps(
    *,
    name,
    task,
    response,
    quality,
    available_metrics,
    missing_profile_metrics,
    artifacts,
    expected_chart_types,
    comparison,
    peer_comparison,
    strategy_result,
    evidence,
    errors,
):
    from src.research_acceptance import (
        AcceptanceStepResult,
        AcceptanceStepStatus,
    )

    task_step = AcceptanceStepResult(
        step_id="task",
        status=AcceptanceStepStatus.PASS,
        summary="Frozen ResearchTaskSpec restored successfully",
        sources=("research_workflow:ResearchTaskSpec",),
        artifacts=(f"task:{task.task_id}",),
    )
    if response is None or quality is None:
        data_step = AcceptanceStepResult(
            step_id="data_quality",
            status=AcceptanceStepStatus.FAIL,
            summary=errors["fetch"] or "Daily bars unavailable",
        )
    else:
        data_step = AcceptanceStepResult(
            step_id="data_quality",
            status=(
                AcceptanceStepStatus.PASS
                if quality.can_publish
                else AcceptanceStepStatus.GAP
            ),
            summary=(
                f"{len(response.records)} daily bars; "
                f"quality publish={quality.can_publish}"
            ),
            sources=tuple(response.sources),
            limitations=tuple(
                check.message
                for check in quality.checks
                if check.level != "pass"
            ),
        )
    metrics_step = AcceptanceStepResult(
        step_id="metrics",
        status=(
            AcceptanceStepStatus.PASS
            if not missing_profile_metrics
            else AcceptanceStepStatus.GAP
        ),
        summary=(
            f"{len(available_metrics)} metrics available; "
            f"missing={list(missing_profile_metrics)}"
        ),
        sources=(
            "factor_engineering",
            "fundamental_analysis",
            "benchmark_analysis",
        ),
        metric_versions=tuple(
            version
            for version in (
                RISK_CALCULATION_VERSION,
                (
                    FUNDAMENTAL_CALCULATION_VERSION
                    if name == "maotai"
                    else None
                ),
                (
                    FUNDAMENTAL_CALCULATION_VERSION
                    if name == "zijin"
                    and {"pb", "roic", "net_debt"}.issubset(
                        available_metrics
                    )
                    else None
                ),
                (
                    BENCHMARK_CALCULATION_VERSION
                    if comparison is not None
                    else None
                ),
                (
                    "exchange_fund_metric_v1"
                    if {
                        "premium_discount",
                        "expense_ratio",
                    }.issubset(available_metrics)
                    else None
                ),
                (
                    FINANCIAL_INSTITUTION_VERSION
                    if name == "pingan_bank"
                    and {
                        "pb",
                        "capital_adequacy",
                        "net_interest_margin",
                        "non_performing_loan_ratio",
                    }.issubset(available_metrics)
                    else None
                ),
                (
                    COMMODITY_SENSITIVITY_VERSION
                    if "commodity_sensitivity"
                    in available_metrics
                    else None
                ),
            )
            if version is not None
        ),
        limitations=missing_profile_metrics,
    )
    actual_chart_types = {
        artifact.chart_type for artifact in artifacts
    }
    missing_charts = tuple(
        item.value
        for item in expected_chart_types
        if item not in actual_chart_types
    )
    chart_step = AcceptanceStepResult(
        step_id="charts",
        status=(
            AcceptanceStepStatus.PASS
            if not missing_charts and not errors["chart"]
            else AcceptanceStepStatus.GAP
        ),
        summary=(
            f"{len(artifacts)} charts rendered; "
            f"missing={list(missing_charts)}"
        ),
        sources=(("research_visualization",) if artifacts else ()),
        metric_versions=(
            ("research_chart_v1",) if artifacts else ()
        ),
        artifacts=tuple(
            f"chart:{item.chart_type.value}:{item.input_digest}"
            for item in artifacts
        ),
        limitations=(
            *((errors["chart"],) if errors["chart"] else ()),
            *missing_charts,
        ),
    )
    comparison_step = AcceptanceStepResult(
        step_id="comparison",
        status=(
            AcceptanceStepStatus.PASS
            if (
                comparison is not None
                and name in {"gold_etf", "csi300_etf"}
            )
            or (
                name in {"maotai", "pingan_bank", "zijin"}
                and comparison is not None
                and peer_comparison is not None
            )
            else AcceptanceStepStatus.GAP
        ),
        summary=(
            (
                "Benchmark and reviewed industry peer comparison "
                "completed"
                if name in {"maotai", "pingan_bank", "zijin"}
                and comparison is not None
                and peer_comparison is not None
                else "Underlying/broad benchmark comparison completed"
            )
            if comparison is not None
            else (
                errors["comparison"]
                or errors["peer"]
                or "Comparison unavailable"
            )
        ),
        sources=(
            (
                "benchmark_analysis",
                *(
                    ("peer_analysis",)
                    if peer_comparison is not None
                    else ()
                ),
            )
            if comparison is not None
            else ()
        ),
        metric_versions=(
            (BENCHMARK_CALCULATION_VERSION,)
            if comparison is not None
            else ()
        ),
        limitations=(
            ()
            if name in {"gold_etf", "csi300_etf"}
            or peer_comparison is not None
            else (
                errors["peer"]
                or "Industry and peer comparison not executed",
            )
        ),
    )
    strategy_step = AcceptanceStepResult(
        step_id="strategy",
        status=(
            AcceptanceStepStatus.PASS
            if strategy_result is not None
            and strategy_result.status.value
            != "insufficient_data"
            else AcceptanceStepStatus.GAP
        ),
        summary=(
            (
                f"{strategy_result.strategy_id}:"
                f"{strategy_result.status.value}"
            )
            if strategy_result is not None
            else (errors["strategy"] or "Strategy unavailable")
        ),
        sources=(
            ("strategy:StrategyExecutor",)
            if strategy_result is not None
            else ()
        ),
        metric_versions=(
            tuple(
                sorted(
                    set(
                        strategy_result.metric_versions.values()
                    )
                )
            )
            if strategy_result is not None
            else ()
        ),
        artifacts=(
            (
                f"strategy:{strategy_result.strategy_id}:"
                f"{strategy_result.status.value}"
            ,)
            if strategy_result is not None
            else ()
        ),
        limitations=(
            tuple(strategy_result.limitations)
            if strategy_result is not None
            else ((errors["strategy"],) if errors["strategy"] else ())
        ),
    )
    if name in {"gold_etf", "csi300_etf"}:
        evidence_step = AcceptanceStepResult(
            step_id="evidence",
            status=AcceptanceStepStatus.NOT_APPLICABLE,
            summary=(
                "Stock disclosure evidence is not applicable to "
                "this ETF acceptance slice"
            ),
            limitations=(
                "Fund disclosure evidence requires a separate bundle",
            ),
        )
    else:
        evidence_step = AcceptanceStepResult(
            step_id="evidence",
            status=(
                AcceptanceStepStatus.PASS
                if evidence is not None and evidence.complete
                else AcceptanceStepStatus.GAP
            ),
            summary=(
                f"{len(evidence.evidence)} evidence items"
                if evidence is not None
                else (errors["evidence"] or "Evidence not executed")
            ),
            sources=(
                tuple(evidence.sources)
                if evidence is not None
                else ()
            ),
            limitations=(
                tuple(evidence.errors.values())
                if evidence is not None
                else ()
            ),
        )
    return (
        task_step,
        data_step,
        metrics_step,
        chart_step,
        comparison_step,
        strategy_step,
        evidence_step,
    )


def _gaps(
    *,
    name,
    missing_profile_metrics,
    fetch_error,
    comparison_error,
    peer_comparison,
    peer_error,
    chart_error,
    strategy_error,
    fundamental_error,
    evidence_error,
    etf_metric_error,
    commodity_error,
):
    from src.research_acceptance import ResearchGap

    gaps = []
    if fetch_error:
        gaps.append(
            ResearchGap(
                module="data_access",
                code="daily_bars_unavailable",
                message=fetch_error,
                blocking=True,
            )
        )
    if missing_profile_metrics:
        gaps.append(
            ResearchGap(
                module="factor_engineering",
                code="profile_metrics_missing",
                message=(
                    "Missing required profile metrics: "
                    f"{list(missing_profile_metrics)}"
                ),
                blocking=True,
            )
        )
    if etf_metric_error:
        gaps.append(
            ResearchGap(
                module="fund_research",
                code="etf_metric_calculation_failed",
                message=etf_metric_error,
                blocking=True,
            )
        )
    if commodity_error:
        gaps.append(
            ResearchGap(
                module="factor_engineering",
                code="commodity_sensitivity_failed",
                message=commodity_error,
                blocking=True,
            )
        )
    if comparison_error:
        gaps.append(
            ResearchGap(
                module="benchmark_analysis",
                code="benchmark_comparison_failed",
                message=comparison_error,
                blocking=False,
            )
        )
    if (
        name not in {"gold_etf", "csi300_etf"}
        and peer_comparison is None
    ):
        gaps.append(
            ResearchGap(
                module="peer_analysis",
                code="peer_comparison_not_executed",
                message=(
                    peer_error
                    or (
                        "Frozen acceptance lacks an approved peer "
                        "set for this task."
                    )
                ),
                blocking=False,
            )
        )
    if chart_error:
        gaps.append(
            ResearchGap(
                module="research_visualization",
                code="chart_render_failed",
                message=chart_error,
                blocking=False,
            )
        )
    if strategy_error:
        gaps.append(
            ResearchGap(
                module="strategy",
                code="horizon_strategy_unavailable",
                message=strategy_error,
                blocking=True,
            )
        )
    if fundamental_error:
        gaps.append(
            ResearchGap(
                module="fundamental_analysis",
                code="fundamental_analysis_failed",
                message=fundamental_error,
                blocking=True,
            )
        )
    if evidence_error:
        gaps.append(
            ResearchGap(
                module="research_evidence",
                code="evidence_fetch_failed",
                message=evidence_error,
                blocking=False,
            )
        )
    if name in {"zijin", "pingan_bank"} and evidence_error:
        gaps.append(
            ResearchGap(
                module="research_evidence",
                code="profile_evidence_not_executed",
                message=(
                    "Profile-specific evidence was not assembled."
                ),
                blocking=False,
            )
        )
    return tuple(gaps)


def _scenarios(strategy_result):
    from src.research_acceptance import (
        ClaimKind,
        ResearchClaim,
        ResearchScenario,
        ScenarioKind,
    )

    if strategy_result is None:
        return tuple(
            ResearchScenario(
                kind=kind,
                claims=(
                    ResearchClaim(
                        kind=ClaimKind.UNKNOWN,
                        text=(
                            "Scenario cannot be assessed without a "
                            "horizon-matched strategy result."
                        ),
                    ),
                ),
            )
            for kind in ScenarioKind
        )
    reference = (
        f"strategy:{strategy_result.strategy_id}:"
        f"{strategy_result.status.value}"
    )
    return (
        ResearchScenario(
            kind=ScenarioKind.OPTIMISTIC,
            claims=(
                ResearchClaim(
                    kind=ClaimKind.ASSUMPTION,
                    text=(
                        "Matched conditions persist through the "
                        "strategy validity window."
                    ),
                    evidence_refs=(reference,),
                ),
            ),
        ),
        ResearchScenario(
            kind=ScenarioKind.BASE,
            claims=(
                ResearchClaim(
                    kind=ClaimKind.FACT,
                    text=(
                        "Current strategy result is "
                        f"{strategy_result.status.value}."
                    ),
                    evidence_refs=(reference,),
                ),
            ),
        ),
        ResearchScenario(
            kind=ScenarioKind.PESSIMISTIC,
            claims=(
                ResearchClaim(
                    kind=ClaimKind.ASSUMPTION,
                    text=(
                        "Counterevidence or failed conditions become "
                        "economically material."
                    ),
                    evidence_refs=(reference,),
                ),
            ),
        ),
    )


def _counterargument(strategy_result):
    from src.research_acceptance import ClaimKind, ResearchClaim

    if strategy_result is None:
        return ResearchClaim(
            kind=ClaimKind.UNKNOWN,
            text=(
                "No horizon-matched strategy exists to identify the "
                "strongest counterargument."
            ),
        )
    evidence = (
        *strategy_result.counterevidence,
        *strategy_result.failed_conditions,
        *strategy_result.missing_metrics,
        *strategy_result.missing_evidence_types,
    )
    return ResearchClaim(
        kind=(
            ClaimKind.INFERENCE
            if evidence
            else ClaimKind.ASSUMPTION
        ),
        text=(
            "The strongest counterargument is represented by "
            f"strategy evidence: {list(evidence) or ['not observed']}."
        ),
        evidence_refs=(
            f"strategy:{strategy_result.strategy_id}",
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
