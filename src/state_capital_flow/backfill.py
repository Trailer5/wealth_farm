"""Historical official fund-report backfill for state-holder evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .report_discovery import OfficialFundReport


@dataclass(frozen=True)
class StateHolderBackfillResult:
    discovered_count: int
    processed_count: int
    evidence_count: int
    failed_count: int
    last_document_id: str | None
    errors: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, name in (
            (self.discovered_count, "discovered_count"),
            (self.processed_count, "processed_count"),
            (self.evidence_count, "evidence_count"),
            (self.failed_count, "failed_count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be int")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.processed_count + self.failed_count > self.discovered_count:
            raise ValueError("processed and failed counts exceed discovered_count")
        if self.last_document_id is not None and (
            not isinstance(self.last_document_id, str)
            or not self.last_document_id.strip()
        ):
            raise ValueError("last_document_id must be non-blank or None")
        errors = tuple(self.errors)
        if any(not isinstance(error, str) or not error for error in errors):
            raise ValueError("errors must contain non-blank strings")
        object.__setattr__(self, "errors", errors)


class StateHolderBackfillService:
    """Run bounded, resumable report-to-holder-evidence backfills."""

    def __init__(
        self,
        discovery: Any,
        parser: Any,
        table_extractor: Any,
        repository: Any,
    ) -> None:
        self.discovery = discovery
        self.parser = parser
        self.table_extractor = table_extractor
        self.repository = repository

    def run(
        self,
        symbol_to_asset_id: Mapping[str, str],
        start_date: str,
        end_date: str,
        root_dir: str | Path,
        max_pages: int = 100,
        max_reports: int = 100,
        resume_after_document_id: str | None = None,
    ) -> StateHolderBackfillResult:
        reports = self.discovery.discover(
            list(symbol_to_asset_id),
            start_date,
            end_date,
            max_pages=max_pages,
            max_reports=max_reports,
        )
        ordered_reports = sorted(
            reports,
            key=lambda item: (
                item.published_at,
                item.symbol,
                item.report_type,
                item.document_id,
            ),
        )
        pending_reports = _reports_after(
            ordered_reports,
            resume_after_document_id,
        )

        processed_count = 0
        evidence_count = 0
        failed_count = 0
        last_document_id: str | None = None
        errors: list[str] = []
        for report in pending_reports:
            last_document_id = report.document_id
            try:
                asset_id = symbol_to_asset_id[report.symbol]
                downloaded = self.discovery.download(report, root_dir)
                tables = self.table_extractor.extract_tables(downloaded)
                evidence = self.parser.parse_document_tables(
                    tables,
                    instrument_asset_id=asset_id,
                    report_period=report.report_period,
                    holding_date=report.report_period,
                    published_at=report.published_at,
                    source=report.source,
                    source_url=report.pdf_url,
                )
                self.repository.upsert_holder_evidence(evidence)
                evidence_count += len(evidence)
                processed_count += 1
            except Exception as exc:
                failed_count += 1
                errors.append(
                    f"{report.document_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

        return StateHolderBackfillResult(
            discovered_count=len(ordered_reports),
            processed_count=processed_count,
            evidence_count=evidence_count,
            failed_count=failed_count,
            last_document_id=last_document_id,
            errors=tuple(errors),
        )


def _reports_after(
    reports: list[OfficialFundReport],
    resume_after_document_id: str | None,
) -> list[OfficialFundReport]:
    if resume_after_document_id is None:
        return reports
    if (
        not isinstance(resume_after_document_id, str)
        or not resume_after_document_id.strip()
    ):
        raise ValueError("resume_after_document_id must be non-blank or None")
    for index, report in enumerate(reports):
        if report.document_id == resume_after_document_id:
            return reports[index + 1 :]
    return reports
