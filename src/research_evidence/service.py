"""Assemble disclosures, research metadata, documents, and events."""

from __future__ import annotations

from src.data_access import (
    DATASET_DISCLOSURES,
    DATASET_RESEARCH_REPORTS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_src.models import DisclosureDocument
from src.data_store import SQLiteDataStore
from src.event_knowledge import EventKnowledgeService
from src.master_data import ASSET_TYPE_OPEN_FUND, MasterDataService

from .models import (
    DocumentContentStatus,
    DocumentEvidenceState,
    EvidenceTier,
    ResearchEvidenceBundle,
    ResearchEvidenceItem,
)


class ResearchEvidenceService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService,
        event_knowledge: EventKnowledgeService,
        store: SQLiteDataStore,
    ) -> None:
        self.master_data = master_data
        self.data_access = data_access
        self.event_knowledge = event_knowledge
        self.store = store
        self.store.initialize()

    def get_bundle(
        self,
        *,
        asset_id: str,
        start_date: str,
        end_date: str,
        as_of_date: str,
        allow_remote: bool = True,
        disclosure_sources: tuple[str, ...] = (),
        research_sources: tuple[str, ...] = (),
        provider_timeout_seconds: float = 30.0,
    ) -> ResearchEvidenceBundle:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        responses = {}
        errors: dict[str, str] = {}
        requested_datasets = [
            (DATASET_DISCLOSURES, disclosure_sources),
        ]
        if asset.asset_type != ASSET_TYPE_OPEN_FUND:
            requested_datasets.append(
                (DATASET_RESEARCH_REPORTS, research_sources)
            )
        for dataset, sources in requested_datasets:
            try:
                responses[dataset] = self.data_access.get(
                    DataAccessRequest(
                        asset=AssetInput(asset_id=asset_id),
                        dataset=dataset,
                        start_date=start_date,
                        end_date=end_date,
                        as_of_date=as_of_date,
                        allow_remote=allow_remote,
                        allow_stale_on_error=True,
                        remote_sources=sources,
                        provider_timeout_seconds=(
                            provider_timeout_seconds
                        ),
                    )
                )
            except Exception as exc:
                errors[dataset] = f"{type(exc).__name__}: {exc}"

        evidence: list[ResearchEvidenceItem] = []
        document_states: list[DocumentEvidenceState] = []
        disclosure_response = responses.get(DATASET_DISCLOSURES)
        if disclosure_response is not None:
            for record in disclosure_response.records:
                document = _disclosure_from_record(record)
                self.event_knowledge.ingest_disclosure(
                    asset_id,
                    document,
                    include_other=False,
                )
                evidence.append(
                    ResearchEvidenceItem(
                        evidence_id=(
                            f"evidence:disclosure:"
                            f"{document.source}:"
                            f"{document.document_id}"
                        ),
                        asset_id=asset_id,
                        evidence_type="disclosure",
                        tier=EvidenceTier.OFFICIAL,
                        title=document.title,
                        event_date=document.announcement_date,
                        source=document.source,
                        source_record_id=document.document_id,
                        source_url=(
                            document.file_url
                            or document.page_url
                            or ""
                        ),
                        content=document.title,
                        metadata={
                            "category": document.category,
                            "page_url": document.page_url,
                        },
                    )
                )
                state, text_item = self._document_evidence(
                    asset_id,
                    document,
                )
                document_states.append(state)
                if text_item is not None:
                    evidence.append(text_item)

        research_response = responses.get(
            DATASET_RESEARCH_REPORTS
        )
        if research_response is not None:
            for record in research_response.records:
                report_id = str(record["report_id"])
                source = str(record["source"])
                evidence.append(
                    ResearchEvidenceItem(
                        evidence_id=(
                            f"evidence:research:"
                            f"{source}:{report_id}"
                        ),
                        asset_id=asset_id,
                        evidence_type="research_report",
                        tier=EvidenceTier.THIRD_PARTY,
                        title=str(record["title"]),
                        event_date=str(record["publish_date"]),
                        source=source,
                        source_record_id=report_id,
                        source_url=str(
                            record.get("pdf_url")
                            or "https://data.eastmoney.com/report/"
                        ),
                        content=str(record["title"]),
                        metadata={
                            "institution": record.get("institution"),
                            "rating": record.get("rating"),
                            "industry": record.get("industry"),
                        },
                    )
                )

        events = self.event_knowledge.list_events(
            asset_id,
            start_date=start_date,
            end_date=min(end_date, as_of_date),
        )
        for event in events:
            evidence.append(
                ResearchEvidenceItem(
                    evidence_id=f"evidence:event:{event.event_id}",
                    asset_id=asset_id,
                    evidence_type="research_event",
                    tier=EvidenceTier.DERIVED,
                    title=event.title,
                    event_date=event.event_date,
                    source=event.source,
                    source_record_id=event.source_record_id,
                    source_url=event.source_url or "",
                    content=event.summary,
                    metadata={
                        "event_type": event.event_type,
                        "confidence": event.confidence,
                        "extraction_method": (
                            event.extraction_method
                        ),
                        "rule_version": event.rule_version,
                    },
                )
            )
        evidence.sort(
            key=lambda item: (
                item.event_date,
                item.evidence_type,
                item.evidence_id,
            ),
            reverse=True,
        )
        sources = {
            source
            for response in responses.values()
            for source in response.sources
        }
        return ResearchEvidenceBundle(
            asset=asset,
            start_date=start_date,
            end_date=end_date,
            as_of_date=as_of_date,
            evidence=tuple(evidence),
            document_states=tuple(document_states),
            events=tuple(events),
            sources=tuple(sorted(sources)),
            complete=(
                len(responses) == len(requested_datasets)
                and all(
                    response.complete
                    for response in responses.values()
                )
                and not errors
            ),
            errors=errors,
        )

    def _document_evidence(
        self,
        asset_id: str,
        document: DisclosureDocument,
    ) -> tuple[
        DocumentEvidenceState,
        ResearchEvidenceItem | None,
    ]:
        file_state = self.store.get_document_file(
            document.document_id
        )
        text_state = self.store.get_document_text(
            document.document_id
        )
        fields = self.store.get_financial_fields(
            document.document_id
        )
        if text_state is not None:
            text = str(text_state.get("text_content") or "").strip()
            status = str(text_state.get("status") or "")
            if (
                status in {"extracted", "success", "completed"}
                and text
            ):
                content_status = (
                    DocumentContentStatus.TEXT_AVAILABLE
                )
            elif text_state.get("error"):
                content_status = (
                    DocumentContentStatus.EXTRACTION_FAILED
                )
            else:
                content_status = (
                    DocumentContentStatus.DOWNLOADED_NO_TEXT
                )
        elif (
            file_state is not None
            and file_state.get("status") not in {
                "downloaded",
                "success",
            }
        ):
            text = ""
            content_status = DocumentContentStatus.DOWNLOAD_FAILED
        elif file_state is not None:
            text = ""
            content_status = (
                DocumentContentStatus.DOWNLOADED_NO_TEXT
            )
        else:
            text = ""
            content_status = DocumentContentStatus.NOT_DOWNLOADED
        state = DocumentEvidenceState(
            document_id=document.document_id,
            status=content_status,
            extractor=(
                str(text_state.get("extractor"))
                if text_state is not None
                and text_state.get("extractor")
                else None
            ),
            page_count=(
                int(text_state["page_count"])
                if text_state is not None
                and text_state.get("page_count") is not None
                else None
            ),
            error=(
                str(
                    (text_state or file_state or {}).get("error")
                )
                if (text_state or file_state or {}).get("error")
                else None
            ),
            financial_field_count=len(fields),
        )
        if content_status is not DocumentContentStatus.TEXT_AVAILABLE:
            return state, None
        return (
            state,
            ResearchEvidenceItem(
                evidence_id=(
                    f"evidence:document_text:"
                    f"{document.document_id}"
                ),
                asset_id=asset_id,
                evidence_type="document_text",
                tier=EvidenceTier.OFFICIAL,
                title=f"全文片段：{document.title}",
                event_date=document.announcement_date,
                source=document.source,
                source_record_id=document.document_id,
                source_url=document.file_url,
                content=text[:2000],
                metadata={
                    "extractor": text_state.get("extractor"),
                    "page_count": text_state.get("page_count"),
                    "financial_field_count": len(fields),
                    "truncated": len(text) > 2000,
                },
            ),
        )


def _disclosure_from_record(
    record: dict[str, object],
) -> DisclosureDocument:
    raw = record.get("raw")
    if not isinstance(raw, dict):
        raw = {}
    return DisclosureDocument(
        document_id=str(record["document_id"]),
        symbol=str(record["symbol"]),
        title=str(record["title"]),
        announcement_date=str(record["announcement_date"]),
        category=(
            str(record["category"])
            if record.get("category") is not None
            else None
        ),
        source=str(record["source"]),
        file_url=str(record.get("file_url") or ""),
        page_url=(
            str(record["page_url"])
            if record.get("page_url") is not None
            else None
        ),
        raw=dict(raw),
    )
