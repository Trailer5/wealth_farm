import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from src.research_ledger import ResearchLedgerService, SQLiteResearchLedgerRepository
from src.research_orchestration.stock_pool import AnnualFundamentals, StockPool, StockPoolService
from src.research_orchestration.strategy_archive import StrategyArchiveService
from src.research_orchestration.strategy_backtest import StrategyBacktestService
from src.strategy import SQLiteStrategyArchiveRepository
from src.strategy.selection import factor_selection_weights

DAYS = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(150)]
# "000003" is delisted on 2020-03-15; "000004" lists on 2020-04-01.
LISTING = {
    "000001": ("2000-01-01", None),
    "000002": ("2000-01-01", None),
    "000003": ("2000-01-01", "2020-03-15"),
    "000004": ("2020-04-01", None),
}


class _Store:
    def get_exchange_listings(self, *, code=None, asset_type=None):
        listed, delisted = LISTING.get(code, (None, None))
        if code not in LISTING:
            return []
        segment = "sz_delisted" if delisted else "sz_stock"
        return [{"code": code, "asset_type": "stock", "segment": segment, "listed_on": listed, "delisted_on": delisted}]


class _Fetch:
    store = _Store()

    def refresh_exchange_listings(self, segments):
        return {}

    def fetch_and_store_identities(self, symbol, asset_type=None):
        raise RuntimeError("offline")


class _CrossSection:
    def report_period(self, period, *, as_of_date=None, allow_remote=True):
        rows = [
            {"symbol": code, "report_period": period, "announced_on": f"{int(period[:4]) + 1}-01-02",
             "eps": eps, "roe_percent": 15.0, "debt_ratio_percent": 30.0, "net_profit_yoy_percent": 5.0}
            for code, eps in (("000001", 0.5), ("000002", 1.0), ("000003", 3.0), ("000004", 2.0))
        ]
        return {"rows": rows, "provider_error": None}


def _records(code):
    listed, delisted = LISTING[code]
    return [
        {"trade_date": day, "open": 10.0, "close": 10.0, "unadjusted_close": 10.0, "preclose": 10.0,
         "amount": 1e8, "volume": 1}
        for day in DAYS
        if day >= listed and (delisted is None or day < delisted)
    ]


class _DataAccess:
    def get(self, request):
        code = request.asset.asset_id.split(":")[1]
        if code not in LISTING:
            raise RuntimeError("no index data offline")
        rows = [row for row in _records(code) if request.start_date <= row["trade_date"] <= request.end_date]
        return SimpleNamespace(records=rows, error=None)


class _Master:
    def find_assets(self, *, standard_symbol, asset_type):
        return [SimpleNamespace(asset_id=f"asset:{standard_symbol}", display_name=standard_symbol)]


class StockPoolTest(unittest.TestCase):
    def test_members_follow_listing_and_delisting_dates(self):
        pool = StockPoolService(sector_data=None, data_fetch=_Fetch(), cross_section=_CrossSection()).resolve(
            "symbols:000001,000002,000003,000004", allow_remote=False
        )
        self.assertEqual(pool.members_on("2020-03-01"), ["000001", "000002", "000003"])
        self.assertEqual(pool.members_on("2020-03-15"), ["000001", "000002"])
        self.assertEqual(pool.members_on("2020-04-01"), ["000001", "000002", "000004"])

    def test_unknown_codes_count_as_listed_with_a_gap(self):
        pool = StockPool("x", {"999999": (None, None)}, ())
        self.assertEqual(pool.members_on("2020-01-01"), ["999999"])

    def test_fundamentals_use_the_latest_announced_annual_report(self):
        rows = {"000001": [
            {"report_period": "2018-12-31", "announced_on": "2019-04-20"},
            {"report_period": "2019-12-31", "announced_on": "2020-04-28"},
        ]}
        fundamentals = AnnualFundamentals(rows, ())
        self.assertEqual(fundamentals.as_of("000001", "2020-04-27")["report_period"], "2018-12-31")
        self.assertEqual(fundamentals.as_of("000001", "2020-04-28")["report_period"], "2019-12-31")
        self.assertIsNone(fundamentals.as_of("000001", "2019-01-01"))


class QualityValueWeightsTest(unittest.TestCase):
    def test_quality_screen_then_cheapest(self):
        candidates = [
            {"asset_id": "a", "roe_percent": 20, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.05},
            {"asset_id": "b", "roe_percent": 20, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.08},
            {"asset_id": "c", "roe_percent": 5, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.20},
            {"asset_id": "d", "roe_percent": 20, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": -0.01},
            {"asset_id": "e", "roe_percent": None, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.3},
        ]
        weights, counts = factor_selection_weights(candidates, {"top_n": "1"})
        self.assertEqual(weights, {"b": 1.0})
        self.assertEqual(
            counts, {"candidates": 5, "no_data": 1, "failed_quality": 1, "no_earnings": 1, "selected": 1}
        )

    def test_rank_by_roe_keeps_loss_free_quality_names(self):
        candidates = [
            {"asset_id": "a", "roe_percent": 25, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.01},
            {"asset_id": "b", "roe_percent": 15, "debt_ratio_percent": 30, "net_profit_yoy_percent": 5, "earnings_yield": 0.08},
        ]
        weights, _ = factor_selection_weights(candidates, {"top_n": "1", "rank_by": "roe"})
        self.assertEqual(weights, {"a": 1.0})


class SelectionBacktestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        db = Path(self.temp.name) / "db.sqlite3"
        repository = SQLiteStrategyArchiveRepository(db)
        archives = StrategyArchiveService(
            repository=repository, ledger=ResearchLedgerService(SQLiteResearchLedgerRepository(db)),
            project_root=Path(self.temp.name),
        )
        archives.register(
            {
                "strategy_id": "qv", "version": "v1", "name": "质量价值", "kind": "selection",
                "mechanism": "质量筛选后取最便宜", "universe": "名单", "horizons": ["medium_long"],
                "parameters": {"scope": "symbols:000001,000002,000003,000004", "top_n": "1"},
                "source_refs": [], "failure_conditions": ["跑输等权"], "limitations": [],
                "template_id": "factor_selection",
            },
            "2020-06-01",
        )
        self.service = StrategyBacktestService(
            archives=archives, repository=repository, data_access=_DataAccess(), data_fetch=_Fetch(),
            master_data=_Master(), macro_data=SimpleNamespace(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))),
            pools=StockPoolService(sector_data=None, data_fetch=_Fetch(), cross_section=_CrossSection()),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_delisted_stock_takes_part_only_while_listed(self):
        result = self.service.run(
            "qv", as_of_date="2020-06-01", start_date="2020-01-01", end_date=DAYS[-1], allow_remote=False
        )
        self.assertEqual(result["selection"]["rebalances"], 5)
        self.assertIn("equal_weight", result["benchmarks"])
        self.assertEqual(len(self.service.repository.backtests("qv")), 1)
        archive, _, _ = self.service.repository.get("qv")
        inputs = self.service._selection_inputs(
            archive, dict(archive.parameters), "2020-01-01", DAYS[-1], False, []
        )
        held = [(signal.as_of_date, sorted(signal.weights)) for signal in inputs.signals]
        # The cheapest stock (000003) is held until it delists, then 000002, then the new listing 000004.
        self.assertEqual(
            held,
            [
                ("2020-01-31", ["asset:000003"]),
                ("2020-02-29", ["asset:000003"]),
                ("2020-03-31", ["asset:000002"]),
                ("2020-04-30", ["asset:000004"]),
                ("2020-05-29", ["asset:000004"]),
            ],
        )
        self.assertEqual(inputs.members_on("2020-03-01"), ["asset:000001", "asset:000002", "asset:000003"])


if __name__ == "__main__":
    unittest.main()
