import tempfile
import unittest
from pathlib import Path

from src.data_src.models import DisclosureDocument
from src.event_knowledge import (
    EVENT_DIVIDEND,
    EVENT_FINANCIAL_REPORT,
    EVENT_HOLDING_CHANGE,
    EVENT_LOCK_UP_RELEASE,
    EVENT_MANAGEMENT_CHANGE,
    EVENT_OTHER,
    DisclosureEventExtractor,
    EventKnowledgeService,
    SQLiteEventRepository,
)
from src.master_data import MasterDataService, SQLiteMasterDataRepository


class DisclosureEventExtractorTest(unittest.TestCase):
    def setUp(self):
        self.extractor = DisclosureEventExtractor()

    def test_one_title_can_produce_multiple_events(self):
        events = self.extractor.extract(
            "asset:test",
            _document(
                "贵州茅台2025年年度报告及利润分配方案公告"
            ),
        )

        self.assertEqual(
            {event.event_type for event in events},
            {EVENT_FINANCIAL_REPORT, EVENT_DIVIDEND},
        )

    def test_management_change_requires_subject_and_action(self):
        changed = self.extractor.extract(
            "asset:test",
            _document("关于高级管理人员辞职的公告"),
        )
        unrelated = self.extractor.extract(
            "asset:test",
            _document("高级管理人员薪酬说明"),
        )

        self.assertEqual(changed[0].event_type, EVENT_MANAGEMENT_CHANGE)
        self.assertEqual(unrelated, [])

    def test_other_is_optional(self):
        document = _document("关于召开股东大会的通知")

        self.assertEqual(self.extractor.extract("asset:test", document), [])
        self.assertEqual(
            self.extractor.extract(
                "asset:test",
                document,
                include_other=True,
            )[0].event_type,
            EVENT_OTHER,
        )

    def test_holder_change_and_lockup_titles_are_classified(self):
        holder_change = self.extractor.extract(
            "asset:test",
            _document("关于持股5%以上股东权益变动的公告"),
        )
        lockup = self.extractor.extract(
            "asset:test",
            _document("首次公开发行限售股份上市流通公告"),
        )

        self.assertEqual(
            holder_change[0].event_type,
            EVENT_HOLDING_CHANGE,
        )
        self.assertEqual(
            lockup[0].event_type,
            EVENT_LOCK_UP_RELEASE,
        )


class EventKnowledgeServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "events.sqlite3"
        self.master_data = MasterDataService(
            SQLiteMasterDataRepository(self.db_path)
        )
        self.asset = self.master_data.resolve_identifier(
            source="unit-test",
            raw_symbol="600519",
            name="贵州茅台",
            asset_type_hint="股票",
        ).asset
        self.service = EventKnowledgeService(
            SQLiteEventRepository(self.db_path),
            self.master_data,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_ingest_is_idempotent_and_queryable(self):
        document = _document("贵州茅台2025年年度报告")

        first = self.service.ingest_disclosure(
            self.asset.asset_id,
            document,
        )
        second = self.service.ingest_disclosure(
            self.asset.asset_id,
            document,
        )
        events = self.service.list_events(
            self.asset.asset_id,
            event_type=EVENT_FINANCIAL_REPORT,
        )

        self.assertEqual(first[0].event_id, second[0].event_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].source_record_id, document.document_id)
        self.assertEqual(events[0].source_url, document.file_url)


def _document(title):
    return DisclosureDocument(
        document_id="cninfo:announcement:1",
        symbol="600519",
        title=title,
        announcement_date="2026-03-31",
        category="annual",
        source="cninfo",
        file_url="https://example.com/report.pdf",
        page_url="https://example.com/report",
        raw={"title": title},
    )


if __name__ == "__main__":
    unittest.main()
