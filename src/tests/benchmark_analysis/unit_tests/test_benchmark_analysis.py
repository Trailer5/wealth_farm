import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.benchmark_analysis import (
    BenchmarkAnalysisService,
    BenchmarkKind,
    BenchmarkMetricCalculator,
    BenchmarkMetricParameters,
    SQLiteBenchmarkRepository,
)
from src.data_access import (
    DATASET_INDEX_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
    SQLiteDataAccessRepository,
)
from src.data_src.models import DailyBar
from src.data_store import SQLiteDataStore
from src.factor_engineering import SQLiteFactorRepository
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class FakeIndexFetchService:
    def __init__(self, store):
        self.store = store
        self.calls = 0

    def fetch_and_store_index_daily_bars(
        self,
        symbol,
        start_date,
        end_date,
        exchange,
        source="akshare",
        timeout_seconds=None,
    ):
        self.calls += 1
        bars = [
            DailyBar(
                symbol=symbol,
                trade_date=start_date,
                open=4000,
                high=4050,
                low=3980,
                close=4020,
                volume=1000,
                amount=4_020_000,
                source="akshare:index-test",
            )
        ]
        stored = self.store.upsert_index_daily_bars(bars)
        return SimpleNamespace(
            fetched_count=1,
            stored_count=stored,
        )


class BenchmarkMetricCalculatorTest(unittest.TestCase):
    def test_alignment_excludes_mismatched_return_intervals(self):
        result = BenchmarkMetricCalculator().calculate(
            asset_id="asset:stock",
            benchmark_asset_id="asset:index",
            benchmark_kind=BenchmarkKind.INDEX,
            asset_records=_records(
                {
                    "2026-01-01": 100,
                    "2026-01-02": 110,
                    "2026-01-03": 90,
                    "2026-01-04": 121,
                }
            ),
            benchmark_records=_records(
                {
                    "2026-01-01": 100,
                    "2026-01-02": 105,
                    "2026-01-04": 110,
                }
            ),
            asset_dataset="stock_daily_bars",
            benchmark_dataset="index_daily_bars",
            parameters=BenchmarkMetricParameters(
                annualization_days=2,
                risk_free_rate=0,
            ),
        )
        metrics = {
            value.factor_name: value.value
            for value in result.metrics
        }

        self.assertEqual(
            result.aligned.dates,
            ("2026-01-02",),
        )
        self.assertNotIn("2026-01-03", result.aligned.dates)
        self.assertNotIn("2026-01-04", result.aligned.dates)
        self.assertAlmostEqual(
            result.aligned.asset_returns[0],
            0.1,
        )
        self.assertAlmostEqual(
            metrics["asset_total_return"],
            0.1,
        )
        self.assertAlmostEqual(
            metrics["benchmark_total_return"],
            0.05,
        )
        self.assertNotIn("beta", metrics)
        self.assertEqual(
            next(iter(result.metrics)).parameters[
                "benchmark_asset_id"
            ],
            "asset:index",
        )
        self.assertEqual(
            next(iter(result.metrics)).parameters["alignment"],
            "matching_observation_intervals",
        )

    def test_relative_statistics_match_independent_expected_values(self):
        asset_returns = [0.10, -0.10, 0.20]
        benchmark_returns = [0.05, -0.10, 0.10]
        result = BenchmarkMetricCalculator().calculate(
            asset_id="asset:stock",
            benchmark_asset_id="asset:index",
            benchmark_kind=BenchmarkKind.INDEX,
            asset_records=_records_from_returns(asset_returns),
            benchmark_records=_records_from_returns(
                benchmark_returns
            ),
            asset_dataset="stock_daily_bars",
            benchmark_dataset="index_daily_bars",
            parameters=BenchmarkMetricParameters(
                annualization_days=3,
                risk_free_rate=0,
            ),
        )
        metrics = {
            value.factor_name: value.value
            for value in result.metrics
        }
        asset_mean = sum(asset_returns) / 3
        benchmark_mean = sum(benchmark_returns) / 3
        active_returns = [
            asset - benchmark
            for asset, benchmark in zip(
                asset_returns,
                benchmark_returns,
            )
        ]
        active_mean = sum(active_returns) / 3
        tracking_error = math.sqrt(
            sum(
                (value - active_mean) ** 2
                for value in active_returns
            )
            / 3
        ) * math.sqrt(3)
        benchmark_variance = sum(
            (value - benchmark_mean) ** 2
            for value in benchmark_returns
        ) / 3
        covariance = sum(
            (asset - asset_mean) * (benchmark - benchmark_mean)
            for asset, benchmark in zip(
                asset_returns,
                benchmark_returns,
            )
        ) / 3
        asset_std = math.sqrt(
            sum(
                (value - asset_mean) ** 2
                for value in asset_returns
            )
            / 3
        )
        benchmark_std = math.sqrt(benchmark_variance)
        beta = covariance / benchmark_variance

        self.assertAlmostEqual(
            metrics["asset_total_return"],
            1.1 * 0.9 * 1.2 - 1,
        )
        self.assertAlmostEqual(
            metrics["benchmark_total_return"],
            1.05 * 0.9 * 1.1 - 1,
        )
        self.assertAlmostEqual(
            metrics["tracking_error"],
            tracking_error,
        )
        self.assertAlmostEqual(
            metrics["information_ratio"],
            active_mean * 3 / tracking_error,
        )
        self.assertAlmostEqual(metrics["beta"], beta)
        self.assertAlmostEqual(
            metrics["alpha"],
            (asset_mean - beta * benchmark_mean) * 3,
        )
        self.assertAlmostEqual(
            metrics["correlation"],
            covariance / (asset_std * benchmark_std),
        )

    def test_zero_variance_benchmark_omits_undefined_metrics(self):
        result = BenchmarkMetricCalculator().calculate(
            asset_id="asset:stock",
            benchmark_asset_id="asset:index",
            benchmark_kind=BenchmarkKind.INDEX,
            asset_records=_records(
                {
                    "2026-01-01": 100,
                    "2026-01-02": 101,
                    "2026-01-03": 103,
                }
            ),
            benchmark_records=_records(
                {
                    "2026-01-01": 100,
                    "2026-01-02": 100,
                    "2026-01-03": 100,
                }
            ),
            asset_dataset="stock_daily_bars",
            benchmark_dataset="index_daily_bars",
        )
        metric_names = {
            value.factor_name
            for value in result.metrics
        }

        self.assertNotIn("beta", metric_names)
        self.assertNotIn("alpha", metric_names)
        self.assertNotIn("correlation", metric_names)
        self.assertIn("tracking_error", metric_names)


class BenchmarkAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "benchmark.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.stock = self._asset(
            "asset:stock",
            ASSET_TYPE_STOCK,
            "600519",
            "贵州茅台",
        )
        self.etf = self._asset(
            "asset:etf",
            ASSET_TYPE_EXCHANGE_FUND,
            "510300",
            "沪深300ETF",
        )
        self.index = self._asset(
            "asset:index",
            ASSET_TYPE_INDEX,
            "000300",
            "沪深300",
        )
        self.store = SQLiteDataStore(self.db_path)
        self.data_repository = SQLiteDataAccessRepository(
            self.store
        )
        self.data_access = DataAccessService(
            master_data=self.master_data,
            repository=self.data_repository,
            fetch_service=FakeIndexFetchService(self.store),
        )
        self.factor_repository = SQLiteFactorRepository(
            self.db_path
        )
        self.service = BenchmarkAnalysisService(
            master_data=self.master_data,
            data_access=self.data_access,
            factor_repository=self.factor_repository,
            benchmark_repository=SQLiteBenchmarkRepository(
                self.db_path
            ),
        )
        stock_bars = _daily_bars("600519", [100, 102, 101, 105])
        index_bars = _daily_bars("000300", [100, 101, 102, 103])
        self.store.upsert_stock_daily_bars(stock_bars)
        self.store.upsert_index_daily_bars(index_bars)
        for asset, dataset in (
            (self.stock, "stock_daily_bars"),
            (self.index, "index_daily_bars"),
        ):
            self.data_repository.mark_coverage(
                asset_id=asset.asset_id,
                dataset=dataset,
                start_date="2026-01-01",
                end_date="2026-01-04",
                source="unit-test",
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _asset(self, asset_id, asset_type, symbol, name):
        return self.master_data.register_asset(
            Asset(
                asset_id=asset_id,
                asset_type=asset_type,
                standard_symbol=symbol,
                display_name=name,
                market="cn_a",
                exchange="sh",
            ),
            source=f"unit-test:{asset_type}",
            raw_symbol=symbol,
        )

    def test_default_benchmark_comparison_persists_values(self):
        self.service.set_benchmark(
            asset_id=self.stock.asset_id,
            benchmark_asset_id=self.index.asset_id,
            benchmark_kind=BenchmarkKind.INDEX,
            source="unit-test",
            effective_date="2026-01-01",
            reason="Broad market benchmark",
        )
        result = self.service.compare(
            asset_id=self.stock.asset_id,
            start_date="2026-01-01",
            end_date="2026-01-04",
            as_of_date="2026-01-04",
            allow_remote=False,
        )
        stored = self.factor_repository.list_values(
            self.stock.asset_id,
            factor_name="beta",
        )

        self.assertEqual(
            result.benchmark_asset_id,
            self.index.asset_id,
        )
        self.assertEqual(len(stored), 1)
        self.assertEqual(
            stored[0].parameters["benchmark_kind"],
            "index",
        )

    def test_benchmark_kind_validates_asset_type(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires asset_type index",
        ):
            self.service.set_benchmark(
                asset_id=self.stock.asset_id,
                benchmark_asset_id=self.etf.asset_id,
                benchmark_kind=BenchmarkKind.INDEX,
                source="unit-test",
                effective_date="2026-01-01",
                reason="Wrong kind",
            )
        relation = self.service.set_benchmark(
            asset_id=self.stock.asset_id,
            benchmark_asset_id=self.etf.asset_id,
            benchmark_kind=BenchmarkKind.ETF_PROXY,
            source="unit-test",
            effective_date="2026-01-01",
            reason="Tradable proxy",
        )
        self.assertEqual(
            relation.benchmark_kind,
            BenchmarkKind.ETF_PROXY,
        )

    def test_index_dataset_can_fetch_and_cache(self):
        store = SQLiteDataStore(
            Path(self.temp_dir.name) / "remote-index.sqlite3"
        )
        repository = SQLiteDataAccessRepository(store)
        fetch = FakeIndexFetchService(store)
        service = DataAccessService(
            master_data=self.master_data,
            repository=repository,
            fetch_service=fetch,
        )
        request = DataAccessRequest(
            asset=AssetInput(asset_id=self.index.asset_id),
            dataset=DATASET_INDEX_DAILY_BARS,
            start_date="2026-02-01",
            end_date="2026-02-01",
            allow_remote=True,
        )
        first = service.get(request)
        second = service.get(request)

        self.assertTrue(first.fetched_remote)
        self.assertFalse(second.fetched_remote)
        self.assertEqual(fetch.calls, 1)


def _records(values):
    return [
        {
            "trade_date": trade_date,
            "close": close,
        }
        for trade_date, close in values.items()
    ]


def _records_from_returns(returns):
    close = 100.0
    closes = [close]
    for value in returns:
        close *= 1.0 + value
        closes.append(close)
    return _records(
        {
            f"2026-01-{index:02d}": value
            for index, value in enumerate(closes, start=1)
        }
    )


def _daily_bars(symbol, closes):
    return [
        DailyBar(
            symbol=symbol,
            trade_date=f"2026-01-{index:02d}",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=100,
            amount=close * 100,
            source="unit-test",
        )
        for index, close in enumerate(closes, start=1)
    ]


if __name__ == "__main__":
    unittest.main()
