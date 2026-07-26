import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from src.daily_data import (
    DailyDataPublishError,
    DailyDataWriter,
    TrackedAssetManifestError,
    load_tracked_assets,
)
from src.daily_data.models import TrackedAsset
from src.daily_data.runner import DailyDataUpdateRunner, main
from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DataAccessResponse,
    DailyDataReader,
)
from src.data_quality import (
    DataQualityService,
    SQLiteDataQualityRepository,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)


class FakeDataAccess:
    def __init__(self, master_data, records):
        self.master_data = master_data
        self.records = records
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        asset = self.master_data.get_asset(request.asset.asset_id)
        return DataAccessResponse(
            asset=asset,
            dataset=request.dataset,
            records=tuple(self.records),
            sources=("unit:runner",),
            coverage_start=request.start_date,
            coverage_end=request.end_date,
            complete=bool(self.records),
            fetched_remote=True,
        )


class DailyDataAutomationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "daily_data"
        self.asset = Asset(
            asset_id="asset:gold",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            standard_symbol="518880",
            display_name="黄金ETF",
            market="cn_a",
            exchange="sh",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_writer_splits_months_upserts_and_reader_round_trips(self):
        writer = DailyDataWriter(self.root)
        first_records = [
            _bar("2026-01-31", 8.1),
            _bar("2026-02-01", 8.2),
        ]
        first_response = _response(self.asset, first_records)
        first_quality = _quality(
            first_response,
            self.temp_dir.name,
        )

        first = writer.publish_daily_bars(
            first_response,
            first_quality,
            checked_at="2026-02-01T12:00:00+00:00",
        )

        self.assertIn(
            "assets/cn_a/sh/518880/daily_bars/2026/01.csv",
            first.changed_paths,
        )
        self.assertIn(
            "assets/cn_a/sh/518880/daily_bars/2026/02.csv",
            first.changed_paths,
        )
        reader = DailyDataReader(self.root)
        stored_asset = reader.get_asset(
            market="cn_a",
            exchange="sh",
            standard_symbol="518880",
        )
        self.assertEqual(stored_asset.asset_id, self.asset.asset_id)
        self.assertEqual(
            len(
                reader.get_records(
                    self.asset,
                    DATASET_EXCHANGE_FUND_DAILY_BARS,
                    "2026-01-01",
                    "2026-02-28",
                )
            ),
            2,
        )

        corrected_response = _response(
            self.asset,
            [
                _bar("2026-01-31", 8.15),
                _bar("2026-02-02", 8.3),
            ],
            coverage_end="2026-02-02",
        )
        corrected_quality = _quality(
            corrected_response,
            self.temp_dir.name,
        )
        writer.publish_daily_bars(
            corrected_response,
            corrected_quality,
            checked_at="2026-02-02T12:00:00+00:00",
        )
        records = reader.get_records(
            self.asset,
            DATASET_EXCHANGE_FUND_DAILY_BARS,
            "2026-01-01",
            "2026-02-28",
        )
        self.assertEqual(len(records), 3)
        corrected = next(
            record
            for record in records
            if record["trade_date"] == "2026-01-31"
        )
        self.assertEqual(corrected["close"], 8.15)
        manifest_path = (
            self.root
            / "assets/cn_a/sh/518880/daily_bars/manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["coverage"]), 1)
        self.assertEqual(
            manifest["coverage"][0]["range_end"],
            "2026-02-02",
        )

        unchanged = writer.publish_daily_bars(
            corrected_response,
            corrected_quality,
            checked_at="2026-02-02T12:00:00+00:00",
        )
        self.assertEqual(unchanged.changed_paths, ())

    def test_writer_rejects_failed_quality_without_writing(self):
        response = _response(self.asset, [])
        quality = _quality(response, self.temp_dir.name)

        with self.assertRaises(DailyDataPublishError):
            DailyDataWriter(self.root).publish_daily_bars(
                response,
                quality,
            )

        self.assertFalse(self.root.exists())

    def test_tracked_asset_manifest_validation_and_empty_cli(self):
        manifest = self.root / "manifests/tracked_assets.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {"schema_version": 1, "assets": []}
            ),
            encoding="utf-8",
        )

        self.assertEqual(load_tracked_assets(manifest), ())
        with redirect_stdout(io.StringIO()):
            exit_code = main(
                [
                    "--daily-data-root",
                    str(self.root),
                    "--manifest",
                    str(manifest),
                    "--end-date",
                    "2026-07-27",
                ]
            )
        self.assertEqual(exit_code, 0)

        duplicate = _tracked_dict()
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "assets": [duplicate, duplicate],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(TrackedAssetManifestError):
            load_tracked_assets(manifest)

    def test_runner_publishes_enabled_assets_and_reports_failures(self):
        db_path = Path(self.temp_dir.name) / "runner.sqlite3"
        master_data = MasterDataService(
            SQLiteMasterDataRepository(db_path)
        )
        records = [_bar("2026-07-27", 8.4)]
        data_access = FakeDataAccess(master_data, records)
        writer = DailyDataWriter(self.root)
        runner = DailyDataUpdateRunner(
            master_data=master_data,
            data_access=data_access,
            data_quality=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            writer=writer,
        )
        tracked = TrackedAsset(
            asset_id=self.asset.asset_id,
            standard_symbol=self.asset.standard_symbol,
            display_name=self.asset.display_name,
            asset_type=self.asset.asset_type,
            market=self.asset.market,
            exchange=self.asset.exchange,
            dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
            initial_start_date="2026-07-01",
            overlap_days=3,
        )

        result = runner.run(
            (
                tracked,
                replace(
                    tracked,
                    asset_id="asset:disabled",
                    standard_symbol="510300",
                    display_name="沪深300ETF",
                    enabled=False,
                ),
            ),
            end_date="2026-07-27",
            checked_at="2026-07-27T12:00:00+00:00",
        )

        self.assertTrue(result.successful)
        self.assertEqual(len(result.assets), 1)
        self.assertEqual(
            result.skipped_asset_ids,
            ("asset:disabled",),
        )
        self.assertEqual(data_access.requests[0].allow_remote, True)
        self.assertEqual(
            writer.latest_coverage_end(
                self.asset,
                DATASET_EXCHANGE_FUND_DAILY_BARS,
            ),
            "2026-07-27",
        )

        failing_access = FakeDataAccess(master_data, [])
        failing_runner = DailyDataUpdateRunner(
            master_data=master_data,
            data_access=failing_access,
            data_quality=DataQualityService(
                SQLiteDataQualityRepository(db_path)
            ),
            writer=writer,
        )
        failed = failing_runner.run(
            (tracked,),
            end_date="2026-07-28",
        )
        self.assertFalse(failed.successful)
        self.assertIn(tracked.asset_id, failed.failures)

    def test_repository_manifest_schemas_and_workflow_are_present(self):
        project_root = Path(__file__).resolve().parents[4]
        daily_root = project_root / "data/daily_data"

        manifest_path = (
            daily_root / "manifests/tracked_assets.json"
        )
        if manifest_path.exists():
            tracked_assets = load_tracked_assets(manifest_path)
            self.assertEqual(len(tracked_assets), 1)
            self.assertEqual(
                tracked_assets[0].standard_symbol,
                "518880",
            )
            self.assertEqual(
                tracked_assets[0].dataset,
                DATASET_EXCHANGE_FUND_DAILY_BARS,
            )
            for schema_name in (
                "asset.schema.json",
                "daily_bar_record.schema.json",
                "daily_bars_manifest.schema.json",
                "tracked_assets.schema.json",
            ):
                schema = json.loads(
                    (
                        daily_root / "schemas" / schema_name
                    ).read_text(encoding="utf-8")
                )
                self.assertIn("$schema", schema)
                self.assertEqual(schema["type"], "object")
        workflow = (
            project_root / ".github/workflows/update-daily-data.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("DAILY_DATA_TOKEN", workflow)
        self.assertIn("src.daily_data.runner", workflow)
        self.assertIn("git diff --cached --quiet", workflow)


def _bar(trade_date, close):
    return {
        "symbol": "518880",
        "trade_date": trade_date,
        "open": close - 0.1,
        "high": close + 0.1,
        "low": close - 0.2,
        "close": close,
        "volume": 1000.0,
        "amount": close * 1000,
        "source": "unit:writer",
        "raw_json": {"verified": True},
    }


def _response(
    asset,
    records,
    *,
    coverage_end="2026-02-01",
):
    return DataAccessResponse(
        asset=asset,
        dataset=DATASET_EXCHANGE_FUND_DAILY_BARS,
        records=tuple(records),
        sources=("unit:writer",),
        coverage_start="2026-01-31",
        coverage_end=coverage_end,
        complete=bool(records),
        fetched_remote=True,
    )


def _quality(response, temp_dir):
    service = DataQualityService(
        SQLiteDataQualityRepository(
            Path(temp_dir) / f"quality-{id(response)}.sqlite3"
        )
    )
    return service.evaluate_data_access_response(
        task_name="daily-data-writer-test",
        response=response,
        expected_start="2026-01-31",
        expected_end=response.coverage_end,
    )


def _tracked_dict():
    return {
        "asset_id": "asset:gold",
        "standard_symbol": "518880",
        "display_name": "黄金ETF",
        "asset_type": "exchange_fund",
        "market": "cn_a",
        "exchange": "sh",
        "dataset": "fund_exchange_daily_bars",
        "initial_start_date": "2026-01-01",
        "enabled": True,
    }


if __name__ == "__main__":
    unittest.main()
