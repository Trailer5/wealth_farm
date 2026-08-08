import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from src.data_access import (
    DATASET_STOCK_DAILY_BARS,
    DataAccessResponse,
)
from src.data_src import IndexConstituent
from src.master_data import (
    ASSET_TYPE_INDEX,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_universe import (
    ResearchUniverseService,
    SQLiteResearchUniverseRepository,
)
from src.research_workflow import (
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    ResearchExecutionOptions,
)
from src.security_discovery import (
    DiscoveryMemberInput,
    DiscoveryMetricPreparationService,
    MetricPreparationStatus,
    default_quality_value_discovery_spec,
)
from src.strategy import StrategyMetricInput


class _UnusedFetch:
    pass


class _MetricLoader:
    def __init__(self):
        self.ready = set()

    def load(self, *, asset_id, spec):
        metrics = (
            {
                name: StrategyMetricInput(
                    metric_name=name,
                    value=Decimal("1"),
                    as_of_date=spec.as_of_date,
                    calculation_version="unit-v1",
                )
                for name in spec.required_metrics
            }
            if asset_id in self.ready
            else {}
        )
        return DiscoveryMemberInput(
            asset_id=asset_id,
            profile_id=spec.profile_id,
            metrics=metrics,
        )


class MetricPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "preparation.sqlite3"
        self.master = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        index = self.master.register_asset(
            Asset(
                asset_id="index:csi300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="000300",
        )
        self.universe = ResearchUniverseService(
            master_data=self.master,
            data_fetch=_UnusedFetch(),
            repository=SQLiteResearchUniverseRepository(db_path),
        )
        snapshot = self.universe.import_index_constituents(
            universe_id="csi300",
            name="沪深300",
            index_asset_id=index.asset_id,
            records=[
                _record("600519", "贵州茅台"),
                _record("000001", "平安银行"),
                _record("000002", "万科A"),
            ],
        )
        self.spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version=snapshot.universe.version,
            as_of_date="2026-08-03",
            benchmark_asset_id=index.asset_id,
        )
        self.asset_by_symbol = {
            item.source_symbol: item.asset_id
            for item in snapshot.memberships
        }
        self.loader = _MetricLoader()
        bars = DataAccessResponse(
            asset=self.master.get_asset(
                self.asset_by_symbol["600519"]
            ),
            dataset=DATASET_STOCK_DAILY_BARS,
            records=(
                {
                    "trade_date": "2026-08-03",
                    "close": 1500,
                },
            ),
            sources=("unit-test",),
            complete=True,
        )
        self.data_access = Mock()
        self.data_access.get.return_value = bars
        self.data_access.get_financial_bundle.return_value = object()
        self.fundamental = Mock()
        self.fundamental.analyze.side_effect = (
            lambda bundle, market_price: self.loader.ready.add(
                self.asset_by_symbol["600519"]
            )
        )
        self.service = DiscoveryMetricPreparationService(
            master_data=self.master,
            universe_service=self.universe,
            metric_loader=self.loader,
            data_access=self.data_access,
            factor_service=Mock(),
            fundamental_service=self.fundamental,
            benchmark_service=Mock(),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_profile_gate_and_remote_limit_are_explicit(self):
        result = self.service.prepare_universe(
            self.spec,
            profile_by_asset_id={
                self.asset_by_symbol[
                    "600519"
                ]: PROFILE_ORDINARY_COMPANY,
                self.asset_by_symbol[
                    "000001"
                ]: PROFILE_FINANCIAL_INSTITUTION,
            },
            remote_member_limit=1,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(result.remote_attempted, 1)
        self.assertEqual(
            result.count(MetricPreparationStatus.PREPARED),
            1,
        )
        self.assertEqual(
            result.count(
                MetricPreparationStatus.UNSUPPORTED_PROFILE
            ),
            1,
        )
        self.assertEqual(
            result.count(MetricPreparationStatus.UNCLASSIFIED),
            1,
        )

    def test_ready_member_is_cached_without_remote_call(self):
        asset_id = self.asset_by_symbol["600519"]
        self.loader.ready.add(asset_id)
        result = self.service.prepare_universe(
            self.spec,
            profile_by_asset_id={
                asset_id: PROFILE_ORDINARY_COMPANY,
            },
            remote_member_limit=0,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(
            result.count(MetricPreparationStatus.CACHED),
            1,
        )
        self.assertEqual(result.remote_attempted, 0)
        self.data_access.get.assert_not_called()


def _record(symbol, name):
    return IndexConstituent(
        index_symbol="000300",
        index_name="沪深300",
        constituent_symbol=symbol,
        constituent_name=name,
        exchange=("sh" if symbol.startswith("6") else "sz"),
        effective_date="2026-07-31",
        source="akshare:index_stock_cons_csindex",
    )


if __name__ == "__main__":
    unittest.main()
