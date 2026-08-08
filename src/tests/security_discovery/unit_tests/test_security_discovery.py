import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from src.data_src import IndexConstituent
from src.factor_engineering import (
    FactorValue,
    SQLiteFactorRepository,
)
from src.fundamental_analysis import (
    FundamentalMetricResult,
    MetricStatus,
    SQLiteFundamentalMetricRepository,
)
from src.master_data import (
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_universe import (
    ResearchUniverseService,
    SQLiteResearchUniverseRepository,
)
from src.research_workflow import (
    PROFILE_ORDINARY_COMPANY,
    ResearchTaskFactory,
)
from src.security_discovery import (
    DiscoveryMemberInput,
    DiscoveryMemberStatus,
    SQLiteSecurityDiscoveryRepository,
    SecurityDiscoveryService,
    StoredDiscoveryMetricLoader,
    default_quality_value_discovery_spec,
)
from src.strategy import StrategyMetricInput


class _UnusedDataFetch:
    pass


class SecurityDiscoveryServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        db_path = root / "discovery.sqlite3"
        self.db_path = db_path
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        self.master.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_index",
                exchange="csi",
            ),
            source="project:index_catalog",
            raw_symbol="000300",
        )
        self.master.register_asset(
            Asset(
                asset_id="stock:inactive",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600000",
                display_name="退市样本",
                market="cn_a",
                exchange="sh",
                status="delisted",
            ),
            source="project:test_catalog",
            raw_symbol="600000",
        )
        universe_repository = SQLiteResearchUniverseRepository(
            db_path
        )
        self.universe_service = ResearchUniverseService(
            master_data=self.master,
            data_fetch=_UnusedDataFetch(),
            repository=universe_repository,
        )
        self.snapshot = (
            self.universe_service.import_index_constituents(
                universe_id="csi300",
                name="沪深300当前成分",
                index_asset_id="index:csi300",
                records=[
                    self._record("000001", "确定性样本", "sz"),
                    self._record("000002", "弹性样本", "sz"),
                    self._record("000003", "韧性样本", "sz"),
                    self._record("000004", "淘汰样本", "sz"),
                    self._record("000005", "缺失样本", "sz"),
                    self._record("000006", "通过样本", "sz"),
                    self._record("INVALID", "未解析样本", ""),
                    self._record("600000", "退市样本", "sh"),
                ],
                acquired_at="2026-08-03T01:00:00+00:00",
            )
        )
        self.repository = SQLiteSecurityDiscoveryRepository(
            db_path
        )
        self.service = SecurityDiscoveryService(
            universe_service=self.universe_service,
            task_factory=ResearchTaskFactory(self.master),
            repository=self.repository,
        )
        self.spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version=self.snapshot.universe.version,
            as_of_date="2026-08-03",
        )
        self.asset_by_symbol = {
            member.source_symbol: member.asset_id
            for member in self.snapshot.memberships
            if member.asset_id is not None
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_funnel_keeps_every_member_and_is_deterministic(self):
        inputs = self._inputs()
        first = self.service.run(
            self.spec,
            member_inputs=inputs,
        )
        second = self.service.run(
            self.spec,
            member_inputs=inputs,
        )

        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(
            [item.asset_id for item in first.candidates],
            [item.asset_id for item in second.candidates],
        )
        self.assertEqual(len(first.outcomes), 8)
        statuses = {
            outcome.source_symbol: outcome.status
            for outcome in first.outcomes
        }
        self.assertEqual(
            statuses["000001"],
            DiscoveryMemberStatus.CANDIDATE,
        )
        self.assertEqual(
            statuses["000002"],
            DiscoveryMemberStatus.CANDIDATE,
        )
        self.assertEqual(
            statuses["000003"],
            DiscoveryMemberStatus.CANDIDATE,
        )
        self.assertEqual(
            statuses["000004"],
            DiscoveryMemberStatus.ELIMINATED,
        )
        self.assertEqual(
            statuses["000005"],
            DiscoveryMemberStatus.INSUFFICIENT_DATA,
        )
        self.assertEqual(
            statuses["000006"],
            DiscoveryMemberStatus.PASSED,
        )
        self.assertEqual(
            statuses["INVALID"],
            DiscoveryMemberStatus.UNRESOLVED,
        )
        self.assertEqual(
            statuses["600000"],
            DiscoveryMemberStatus.INACTIVE,
        )
        self.assertFalse(first.is_buy_recommendation)

    def test_missing_data_is_not_recorded_as_elimination(self):
        run = self.service.run(
            self.spec,
            member_inputs=self._inputs(),
        )
        missing = next(
            item
            for item in run.outcomes
            if item.source_symbol == "000005"
        )
        eliminated = next(
            item
            for item in run.outcomes
            if item.source_symbol == "000004"
        )

        self.assertEqual(
            missing.missing_metrics,
            ("fcf_yield",),
        )
        self.assertEqual(missing.failed_rules, ())
        self.assertIn("quality_roe_floor", eliminated.failed_rules)
        self.assertEqual(eliminated.missing_metrics, ())

    def test_candidates_have_restorable_research_task_drafts(self):
        run = self.service.run(
            self.spec,
            member_inputs=self._inputs(),
        )

        self.assertEqual(len(run.candidates), 3)
        for candidate in run.candidates:
            restored = ResearchTaskFactory(self.master).restore(
                candidate.research_task_draft.to_dict()
            )
            self.assertEqual(restored.asset_id, candidate.asset_id)
            self.assertEqual(
                restored.benchmark_asset_id,
                "index:csi300",
            )

        stored = self.service.get(run.run_id)
        self.assertIsNotNone(stored)
        self.assertEqual(
            stored.to_dict(),
            run.to_dict(),
        )
        self.assertEqual(
            len(self.repository.list_outcomes(run.run_id)),
            8,
        )

    def test_track_round_robin_preserves_dimension_diversity(self):
        limited_spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version=self.snapshot.universe.version,
            as_of_date="2026-08-03",
            max_candidates=2,
            max_per_track=2,
        )
        run = self.service.run(
            limited_spec,
            member_inputs=self._inputs(),
        )

        self.assertEqual(
            {
                candidate.selected_track
                for candidate in run.candidates
            },
            {"certainty", "valuation_elasticity"},
        )

    def test_stored_metric_loader_preserves_status_and_ambiguity(self):
        asset_id = self.asset_by_symbol["000001"]
        fundamental = SQLiteFundamentalMetricRepository(
            self.db_path
        )
        factor = SQLiteFactorRepository(self.db_path)
        fundamental.initialize()
        factor.initialize()
        fundamental.upsert_results(
            [
                FundamentalMetricResult(
                    asset_id=asset_id,
                    metric_name="roe",
                    as_of_date="2026-08-01",
                    report_period="2025-12-31",
                    status=MetricStatus.AVAILABLE,
                    value=Decimal("0.18"),
                    unit="ratio",
                    formula="net_income/equity",
                    numerator=Decimal("18"),
                    denominator=Decimal("100"),
                    reason=None,
                    calculation_version="fundamental_v1",
                    parameters={},
                    inputs=(),
                    computed_at="2026-08-01T00:00:00+00:00",
                )
            ]
        )
        factor.upsert_values(
            [
                FactorValue(
                    asset_id=asset_id,
                    factor_name="max_drawdown",
                    as_of_date="2026-08-02",
                    value=-0.2,
                    calculation_version="risk_v1",
                    parameters={"window": 60},
                ),
                FactorValue(
                    asset_id=asset_id,
                    factor_name="max_drawdown",
                    as_of_date="2026-08-02",
                    value=-0.3,
                    calculation_version="risk_v1",
                    parameters={"window": 120},
                ),
            ]
        )

        loaded = StoredDiscoveryMetricLoader(
            fundamental_repository=fundamental,
            factor_repository=factor,
        ).load(asset_id=asset_id, spec=self.spec)

        self.assertEqual(
            loaded.metrics["roe"].value,
            Decimal("0.18"),
        )
        self.assertFalse(
            loaded.metrics["max_drawdown"].applicable
        )
        self.assertEqual(
            loaded.metrics["max_drawdown"].reason,
            "ambiguous_factor_parameters",
        )
        filtered = StoredDiscoveryMetricLoader(
            fundamental_repository=fundamental,
            factor_repository=factor,
            factor_parameter_filters={
                "max_drawdown": {"window": 60}
            },
        ).load(asset_id=asset_id, spec=self.spec)
        self.assertTrue(
            filtered.metrics["max_drawdown"].applicable
        )
        self.assertEqual(
            filtered.metrics["max_drawdown"].value,
            Decimal("-0.2"),
        )

    def _inputs(self):
        values = {
            "000001": {
                "roe": "0.20",
                "roic": "0.15",
                "debt_to_assets": "0.30",
                "pe_ttm": "25",
                "fcf_yield": "0.02",
                "excess_total_return": "0",
                "max_drawdown": "-0.20",
                "annualized_volatility": "0.30",
            },
            "000002": {
                "roe": "0.10",
                "roic": "0.06",
                "debt_to_assets": "0.60",
                "pe_ttm": "12",
                "fcf_yield": "0.05",
                "excess_total_return": "0",
                "max_drawdown": "-0.40",
                "annualized_volatility": "0.50",
            },
            "000003": {
                "roe": "0.10",
                "roic": "0.06",
                "debt_to_assets": "0.60",
                "pe_ttm": "30",
                "fcf_yield": "0.01",
                "excess_total_return": "0",
                "max_drawdown": "-0.10",
                "annualized_volatility": "0.20",
            },
            "000004": {
                "roe": "0.04",
                "roic": "0.03",
                "debt_to_assets": "0.60",
                "pe_ttm": "20",
                "fcf_yield": "0.01",
                "excess_total_return": "0",
                "max_drawdown": "-0.20",
                "annualized_volatility": "0.30",
            },
            "000005": {
                "roe": "0.12",
                "roic": "0.08",
                "debt_to_assets": "0.60",
                "pe_ttm": "20",
                "excess_total_return": "0",
                "max_drawdown": "-0.20",
                "annualized_volatility": "0.30",
            },
            "000006": {
                "roe": "0.10",
                "roic": "0.05",
                "debt_to_assets": "0.65",
                "pe_ttm": "30",
                "fcf_yield": "0.01",
                "excess_total_return": "-0.20",
                "max_drawdown": "-0.40",
                "annualized_volatility": "0.50",
            },
        }
        return {
            self.asset_by_symbol[symbol]: DiscoveryMemberInput(
                asset_id=self.asset_by_symbol[symbol],
                profile_id=PROFILE_ORDINARY_COMPANY,
                metrics={
                    name: StrategyMetricInput(
                        metric_name=name,
                        value=Decimal(value),
                        as_of_date="2026-08-03",
                        calculation_version="test_v1",
                    )
                    for name, value in metrics.items()
                },
            )
            for symbol, metrics in values.items()
        }

    @staticmethod
    def _record(
        symbol: str,
        name: str,
        exchange: str,
    ) -> IndexConstituent:
        return IndexConstituent(
            index_symbol="000300",
            index_name="沪深300",
            constituent_symbol=symbol,
            constituent_name=name,
            exchange=exchange,
            effective_date="2026-07-31",
            source="akshare:index_stock_cons_csindex",
            raw={"symbol": symbol},
        )


if __name__ == "__main__":
    unittest.main()
