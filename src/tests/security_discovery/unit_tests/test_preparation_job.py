import tempfile
import unittest
from pathlib import Path

from src.research_workflow import ResearchExecutionOptions
from src.security_discovery import (
    DiscoveryPreparationJobService,
    MemberMetricPreparation,
    MetricPreparationStatus,
    SQLiteDiscoveryPreparationJobRepository,
    UniverseMetricPreparationResult,
    default_quality_value_discovery_spec,
)


class _Preparation:
    def __init__(self):
        self.calls = 0

    def prepare_universe(self, spec, **kwargs):
        self.calls += 1
        if self.calls == 1:
            members = (
                _member("A", MetricPreparationStatus.PREPARED, True),
                _member("B", MetricPreparationStatus.MISSING),
                _member(
                    "C",
                    MetricPreparationStatus.UNSUPPORTED_PROFILE,
                ),
            )
        else:
            members = (
                _member("A", MetricPreparationStatus.CACHED),
                _member("B", MetricPreparationStatus.PREPARED, True),
                _member(
                    "C",
                    MetricPreparationStatus.UNSUPPORTED_PROFILE,
                ),
            )
        return UniverseMetricPreparationResult(
            universe_id=spec.universe_id,
            universe_version=spec.universe_version,
            as_of_date=spec.as_of_date,
            remote_member_limit=kwargs["remote_member_limit"],
            remote_attempted=1,
            members=members,
        )


class _FailingPreparation:
    def prepare_universe(self, spec, **kwargs):
        return UniverseMetricPreparationResult(
            universe_id=spec.universe_id,
            universe_version=spec.universe_version,
            as_of_date=spec.as_of_date,
            remote_member_limit=kwargs["remote_member_limit"],
            remote_attempted=1,
            members=(
                MemberMetricPreparation(
                    source_symbol="A",
                    asset_id="asset:A",
                    status=MetricPreparationStatus.FAILED,
                    missing_metrics=("roe",),
                    error="provider down",
                    remote_attempted=True,
                ),
            ),
        )


class PreparationJobTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "jobs.sqlite3"
        self.spec = default_quality_value_discovery_spec(
            universe_id="csi300",
            universe_version="2026-07-31:test",
            as_of_date="2026-08-03",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_two_batches_resume_without_repeating_ready_member(self):
        repository = SQLiteDiscoveryPreparationJobRepository(
            self.db_path
        )
        preparation = _Preparation()
        service = DiscoveryPreparationJobService(
            preparation_service=preparation,
            repository=repository,
        )
        service.create_job(job_id="job:test", spec=self.spec)
        first = service.run_batch(
            job_id="job:test",
            spec=self.spec,
            profile_by_asset_id={},
            batch_size=1,
            options=ResearchExecutionOptions(allow_remote=False),
        )
        second = service.run_batch(
            job_id="job:test",
            spec=self.spec,
            profile_by_asset_id={},
            batch_size=1,
            options=ResearchExecutionOptions(allow_remote=False),
        )

        self.assertEqual(first.counts["ready"], 1)
        self.assertEqual(first.counts["pending"], 1)
        self.assertEqual(second.counts["ready"], 2)
        self.assertEqual(second.job.status, "complete")
        states = repository.member_states("job:test")
        self.assertEqual(states["asset:A"]["attempts"], 1)
        self.assertEqual(states["asset:B"]["attempts"], 1)

    def test_failure_reaches_retry_limit(self):
        service = DiscoveryPreparationJobService(
            preparation_service=_FailingPreparation(),
            repository=SQLiteDiscoveryPreparationJobRepository(
                self.db_path
            ),
        )
        service.create_job(
            job_id="job:failure",
            spec=self.spec,
            max_attempts_per_member=1,
        )
        progress = service.run_batch(
            job_id="job:failure",
            spec=self.spec,
            profile_by_asset_id={},
            batch_size=1,
        )

        self.assertEqual(progress.counts["exhausted"], 1)
        self.assertEqual(
            progress.job.status,
            "completed_with_failures",
        )


def _member(symbol, status, remote=False):
    return MemberMetricPreparation(
        source_symbol=symbol,
        asset_id=f"asset:{symbol}",
        status=status,
        missing_metrics=(() if status.value in {"prepared", "cached"} else ("roe",)),
        remote_attempted=remote,
    )


if __name__ == "__main__":
    unittest.main()
