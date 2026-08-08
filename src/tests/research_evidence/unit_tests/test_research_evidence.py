import tempfile
import unittest
from pathlib import Path

from src.data_access import (
    DATASET_DISCLOSURES,
    DATASET_RESEARCH_REPORTS,
    DataAccessService,
    SQLiteDataAccessRepository,
)
from src.data_src.models import (
    DisclosureDocument,
    DocumentText,
    DownloadedFile,
    FinancialField,
    ResearchReport,
)
from src.data_store import SQLiteDataStore
from src.event_knowledge import (
    EVENT_FINANCIAL_REPORT,
    EventKnowledgeService,
    SQLiteEventRepository,
)
from src.master_data import (
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
    SQLiteMasterDataRepository,
)
from src.research_evidence import (
    DocumentContentStatus,
    EvidenceTier,
)
from src.research_evidence.service import (
    ResearchEvidenceService,
)


class FakeEvidenceFetchService:
    def __init__(self):
        self.disclosure_calls = 0
        self.research_calls = 0

    def fetch_disclosure_documents_by_source(
        self,
        symbol,
        start_date,
        end_date,
        source,
        timeout_seconds=None,
    ):
        self.disclosure_calls += 1
        return [
            DisclosureDocument(
                document_id="doc:annual",
                symbol=symbol,
                title="贵州茅台2025年年度报告",
                announcement_date="2026-03-31",
                category="annual",
                source="cninfo",
                file_url="https://example.com/annual.pdf",
                page_url="https://example.com/annual",
            ),
            DisclosureDocument(
                document_id="doc:future",
                symbol=symbol,
                title="贵州茅台2026年半年度报告",
                announcement_date="2026-08-30",
                category="semi_annual",
                source="cninfo",
                file_url="https://example.com/future.pdf",
            ),
        ]

    def fetch_stock_research_reports(
        self,
        symbol,
        start_date,
        end_date,
        source="eastmoney",
        timeout_seconds=None,
    ):
        self.research_calls += 1
        return [
            ResearchReport(
                report_id="report:1",
                symbol=symbol,
                title="贵州茅台盈利能力跟踪",
                publish_date="2026-04-10",
                institution="测试机构",
                rating="买入",
                industry="白酒",
                pdf_url="https://example.com/report.pdf",
                source="eastmoney_research",
            )
        ]


class ResearchEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "evidence.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master_data.register_asset(
            Asset(
                asset_id="asset:maotai",
                asset_type=ASSET_TYPE_STOCK,
                standard_symbol="600519",
                display_name="贵州茅台",
                market="cn_a",
                exchange="sh",
            ),
            source="unit-test",
            raw_symbol="600519",
        )
        self.store = SQLiteDataStore(self.db_path)
        self.fetch_service = FakeEvidenceFetchService()
        self.data_access = DataAccessService(
            master_data=self.master_data,
            repository=SQLiteDataAccessRepository(self.store),
            fetch_service=self.fetch_service,
        )
        self.events = EventKnowledgeService(
            SQLiteEventRepository(self.db_path),
            self.master_data,
        )
        self.service = ResearchEvidenceService(
            master_data=self.master_data,
            data_access=self.data_access,
            event_knowledge=self.events,
            store=self.store,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _bundle(self, allow_remote=True):
        return self.service.get_bundle(
            asset_id=self.asset.asset_id,
            start_date="2026-01-01",
            end_date="2026-12-31",
            as_of_date="2026-06-30",
            allow_remote=allow_remote,
            disclosure_sources=("cninfo",),
            research_sources=("eastmoney",),
        )

    def test_bundle_fetches_metadata_and_ingests_events(self):
        bundle = self._bundle()
        evidence_types = {
            item.evidence_type
            for item in bundle.evidence
        }

        self.assertTrue(bundle.complete)
        self.assertIn("disclosure", evidence_types)
        self.assertIn("research_report", evidence_types)
        self.assertIn("research_event", evidence_types)
        self.assertEqual(len(bundle.events), 1)
        self.assertEqual(
            bundle.events[0].event_type,
            EVENT_FINANCIAL_REPORT,
        )
        disclosure = next(
            item
            for item in bundle.evidence
            if item.evidence_type == "disclosure"
        )
        research = next(
            item
            for item in bundle.evidence
            if item.evidence_type == "research_report"
        )
        self.assertEqual(disclosure.tier, EvidenceTier.OFFICIAL)
        self.assertEqual(
            research.tier,
            EvidenceTier.THIRD_PARTY,
        )
        self.assertEqual(
            bundle.document_states[0].status,
            DocumentContentStatus.NOT_DOWNLOADED,
        )
        self.assertNotIn(
            "doc:future",
            {
                item.source_record_id
                for item in bundle.evidence
            },
        )

    def test_bundle_is_idempotent_and_uses_cached_metadata(self):
        first = self._bundle()
        second = self._bundle()

        self.assertEqual(len(first.events), len(second.events))
        self.assertEqual(
            len(self.events.list_events(self.asset.asset_id)),
            1,
        )
        self.assertEqual(self.fetch_service.disclosure_calls, 1)
        self.assertEqual(self.fetch_service.research_calls, 1)

    def test_document_text_and_fields_become_evidence(self):
        self._bundle()
        self.store.upsert_document_files(
            [
                DownloadedFile(
                    document_id="doc:annual",
                    file_path="temp/annual.pdf",
                    file_url="https://example.com/annual.pdf",
                    sha256="abc",
                    size_bytes=100,
                    source="cninfo",
                    status="downloaded",
                )
            ]
        )
        self.store.upsert_document_texts(
            [
                DocumentText(
                    document_id="doc:annual",
                    text_content="年度报告正文" * 500,
                    extractor="pdfplumber",
                    page_count=100,
                    status="extracted",
                )
            ]
        )
        self.store.upsert_financial_fields(
            [
                FinancialField(
                    document_id="doc:annual",
                    field_name="revenue",
                    field_label="营业收入",
                    value=100.0,
                    unit="元",
                    source="pdf_table",
                )
            ]
        )

        bundle = self._bundle(allow_remote=False)
        state = bundle.document_states[0]
        text_item = next(
            item
            for item in bundle.evidence
            if item.evidence_type == "document_text"
        )

        self.assertEqual(
            state.status,
            DocumentContentStatus.TEXT_AVAILABLE,
        )
        self.assertEqual(state.financial_field_count, 1)
        self.assertEqual(len(text_item.content), 2000)
        self.assertTrue(text_item.metadata["truncated"])

    def test_document_failure_states_are_distinct(self):
        self._bundle()
        failed_file = DownloadedFile(
            document_id="doc:annual",
            file_path="temp/annual.pdf",
            file_url="https://example.com/annual.pdf",
            sha256="",
            size_bytes=0,
            source="cninfo",
            status="failed",
            error="download failed",
        )
        self.store.upsert_document_files([failed_file])
        download_failed = self._bundle(allow_remote=False)
        self.assertEqual(
            download_failed.document_states[0].status,
            DocumentContentStatus.DOWNLOAD_FAILED,
        )

        self.store.upsert_document_files(
            [
                DownloadedFile(
                    document_id="doc:annual",
                    file_path="temp/annual.pdf",
                    file_url="https://example.com/annual.pdf",
                    sha256="abc",
                    size_bytes=100,
                    source="cninfo",
                    status="downloaded",
                )
            ]
        )
        no_text = self._bundle(allow_remote=False)
        self.assertEqual(
            no_text.document_states[0].status,
            DocumentContentStatus.DOWNLOADED_NO_TEXT,
        )

        self.store.upsert_document_texts(
            [
                DocumentText(
                    document_id="doc:annual",
                    text_content="",
                    extractor="pdfplumber",
                    page_count=None,
                    status="failed",
                    error="parse failed",
                )
            ]
        )
        extraction_failed = self._bundle(allow_remote=False)
        self.assertEqual(
            extraction_failed.document_states[0].status,
            DocumentContentStatus.EXTRACTION_FAILED,
        )

    def test_data_access_exposes_two_metadata_datasets(self):
        self._bundle()
        disclosure_rows = self.store.get_disclosure_documents(
            "600519",
            end_date="2026-06-30",
        )
        research_rows = self.store.get_research_reports(
            "600519",
            end_date="2026-06-30",
        )

        self.assertEqual(len(disclosure_rows), 1)
        self.assertEqual(len(research_rows), 1)
        self.assertEqual(
            DATASET_DISCLOSURES,
            "disclosures",
        )
        self.assertEqual(
            DATASET_RESEARCH_REPORTS,
            "research_reports",
        )


if __name__ == "__main__":
    unittest.main()
