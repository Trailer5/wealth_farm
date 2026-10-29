import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.research_ledger import ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.strategy_archive import StrategyArchiveError, StrategyArchiveService
from src.research_orchestration.strategy_backtest import StrategyBacktestService
from src.strategy import SQLiteStrategyArchiveRepository

DAYS = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(400)]


def _records(symbol):
    base = {"000300": 100.0, "510300": 1.0}[symbol]
    rows = []
    for i, day in enumerate(DAYS):
        # Up for 250 days, then down: the 200-day average strategy should leave before the bottom.
        level = base * (1 + 0.002 * i if i < 250 else 1.5 - 0.003 * (i - 250))
        rows.append({"trade_date": day, "open": level, "close": level, "preclose": level, "amount": 1e8, "volume": 1})
    return rows


class _DataAccess:
    def __init__(self):
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        symbol = {"asset:idx": "000300", "asset:etf": "510300"}[request.asset.asset_id]
        rows = [row for row in _records(symbol) if request.start_date <= row["trade_date"] <= request.end_date]
        return SimpleNamespace(records=rows, error=None)


class _Master:
    def find_assets(self, *, standard_symbol, asset_type):
        ids = {("000300", "index"): "asset:idx", ("510300", "exchange_fund"): "asset:etf"}
        key = (standard_symbol, asset_type)
        return [SimpleNamespace(asset_id=ids[key], display_name=standard_symbol)] if key in ids else []


class _Fetch:
    store = SimpleNamespace(get_exchange_listings=lambda code: [])

    def fetch_and_store_identities(self, symbol, asset_type=None):
        raise RuntimeError("offline")


class _Macro:
    def get(self, series_id, **kwargs):
        return SimpleNamespace(to_dict=lambda limit=None: {"points": [{"period": "2019-01-01", "value": 2.0}]})


ARCHIVE = {
    "strategy_id": "etf_ma",
    "version": "v1",
    "name": "ETF 均线择时",
    "kind": "timing",
    "mechanism": "收盘价在均线之上持有",
    "universe": "510300",
    "horizons": ["medium_short"],
    "parameters": {"window": "50"},
    "source_refs": [],
    "failure_conditions": ["跑输买入持有"],
    "limitations": [],
    "template_id": "ma_timing",
    "universe_assets": ["510300:exchange_fund"],
}


class StrategyBacktestServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = Path(self.temp.name) / "db.sqlite3"
        repository = SQLiteStrategyArchiveRepository(db)
        self.archives = StrategyArchiveService(
            repository=repository, ledger=ResearchLedgerService(SQLiteResearchLedgerRepository(db)),
            project_root=Path(self.temp.name),
        )
        self.data = _DataAccess()
        self.service = StrategyBacktestService(
            archives=self.archives, repository=repository, data_access=self.data,
            data_fetch=_Fetch(), master_data=_Master(), macro_data=_Macro(),
        )
        self.archives.register(ARCHIVE, "2021-02-01")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, **kwargs):
        return self.service.run("etf_ma", as_of_date="2021-02-01", start_date="2020-01-01", end_date=DAYS[-1], **kwargs)

    def test_runs_with_benchmarks_and_logs_attempts(self):
        first = self._run()
        self.assertEqual(set(first["benchmarks"]), {"buy_and_hold", "csi300", "cash"})
        self.assertGreater(first["metrics"]["max_drawdown"], first["benchmarks"]["buy_and_hold"]["metrics"]["max_drawdown"])
        self.assertIn("回测从", first["gaps"][0])
        self.assertEqual(first["family_attempts"], 1)
        second = self._run(overrides={"window": "30"})
        self.assertEqual(second["parameters"]["window"], "30")
        self.assertEqual(second["family_attempts"], 2)
        self.assertIsNotNone(second["capacity"]["capacity"])
        self.assertEqual(first["significance"]["trials"], 1)
        self.assertEqual(second["significance"]["trials"], 2)
        rerun = self._run(overrides={"window": "30"})
        self.assertEqual(rerun["family_attempts"], 3)
        self.assertEqual(rerun["significance"]["trials"], 2)

    def test_unknown_parameter_and_unbacktestable_strategy_are_refused(self):
        with self.assertRaisesRegex(StrategyArchiveError, "没有这些参数"):
            self._run(overrides={"magic": "1"})
        with self.assertRaisesRegex(StrategyArchiveError, "没有可回测的信号实现"):
            self.service.run("quality_value", as_of_date="2021-02-01")


if __name__ == "__main__":
    unittest.main()
