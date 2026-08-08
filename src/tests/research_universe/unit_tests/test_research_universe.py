import tempfile
import unittest
from pathlib import Path

from src.data_src import IndexConstituent
from src.master_data import (
    ASSET_TYPE_INDEX,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_universe import (
    HistoricalUniverseUsageError,
    MembershipStatus,
    ResearchUniverseService,
    SQLiteResearchUniverseRepository,
)


class _UnusedDataFetch:
    pass


class ResearchUniverseServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.master = MasterDataService(
            SQLiteMasterDataRepository(root / "master.sqlite3")
        )
        self.master.register_asset(
            Asset(
                asset_id="index:000300",
                asset_type=ASSET_TYPE_INDEX,
                standard_symbol="000300",
                display_name="沪深300",
                market="cn_index",
                exchange="csi",
            ),
            source="project:index_catalog",
            raw_symbol="000300",
        )
        self.repository = SQLiteResearchUniverseRepository(
            root / "universe.sqlite3"
        )
        self.service = ResearchUniverseService(
            master_data=self.master,
            data_fetch=_UnusedDataFetch(),
            repository=self.repository,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_import_is_idempotent_and_reports_duplicates(self):
        records = [
            self._record("000001", "平安银行", "sz"),
            self._record("600519", "贵州茅台", "sh"),
            self._record("000001", "平安银行", "sz"),
        ]
        first = self.service.import_index_constituents(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id="index:000300",
            records=records,
            acquired_at="2026-08-03T01:00:00+00:00",
        )
        second = self.service.import_index_constituents(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id="index:000300",
            records=records,
            acquired_at="2026-08-03T01:00:00+00:00",
        )

        self.assertEqual(first.universe.version, second.universe.version)
        self.assertEqual(second.quality.total_source_records, 3)
        self.assertEqual(second.quality.unique_source_symbols, 2)
        self.assertEqual(second.quality.resolved_count, 2)
        self.assertEqual(second.quality.duplicate_count, 1)
        stored = self.repository.get(
            "csi300",
            second.universe.version,
        )
        self.assertIsNotNone(stored)
        self.assertEqual(len(stored.memberships), 2)

    def test_current_snapshot_is_rejected_for_historical_backtest(self):
        snapshot = self.service.import_index_constituents(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id="index:000300",
            records=[self._record("000001", "平安银行", "sz")],
        )

        with self.assertRaises(HistoricalUniverseUsageError):
            self.service.members(
                "csi300",
                version=snapshot.universe.version,
                for_historical_backtest=True,
            )

    def test_invalid_symbol_is_kept_as_unresolved(self):
        snapshot = self.service.import_index_constituents(
            universe_id="csi300",
            name="沪深300当前成分",
            index_asset_id="index:000300",
            records=[self._record("INVALID", "未知成员", "")],
        )

        self.assertEqual(snapshot.quality.unresolved_count, 1)
        self.assertEqual(len(snapshot.memberships), 1)
        self.assertEqual(
            snapshot.memberships[0].status,
            MembershipStatus.UNRESOLVED,
        )
        self.assertIsNone(snapshot.memberships[0].asset_id)

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
