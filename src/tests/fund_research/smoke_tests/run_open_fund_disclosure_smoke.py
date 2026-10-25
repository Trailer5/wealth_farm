"""Real-document smoke for open-fund disclosure extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import DisclosureDocument, DownloadedFile
from src.data_src.service import DataFetchService
from src.data_store import SQLiteDataStore
from src.document_processing.open_fund_disclosure import (
    OpenFundDisclosureParser,
)
from src.fund_research import SQLiteFundResearchRepository
from src.master_data import (
    ASSET_TYPE_OPEN_FUND,
    MasterDataService,
    SQLiteMasterDataRepository,
)


@dataclass(frozen=True)
class SmokeDocument:
    symbol: str
    kind: str
    title: str
    announcement_date: str
    source: str
    url: str


DOCUMENTS = (
    SmokeDocument(
        symbol="163417",
        kind="profile",
        title=(
            "兴全合宜灵活配置混合型证券投资基金(LOF)"
            "（A类份额）基金产品资料概要更新"
        ),
        announcement_date="2026-06-12",
        source="verified_official_document_mirror",
        url=(
            "http://notice.10jqka.com.cn/api/pdf/"
            "63f7106557b9a465.pdf"
        ),
    ),
    SmokeDocument(
        symbol="163417",
        kind="periodic",
        title=(
            "兴全合宜灵活配置混合型证券投资基金(LOF)"
            "2026年第2季度报告"
        ),
        announcement_date="2026-07-21",
        source="cninfo",
        url=(
            "https://static.cninfo.com.cn/finalpage/"
            "2026-07-21/1225434119.PDF"
        ),
    ),
    SmokeDocument(
        symbol="002236",
        kind="profile",
        title=(
            "大成中证360互联网+大数据100指数型证券投资基金"
            "（A类份额）基金产品资料概要更新"
        ),
        announcement_date="2026-06-30",
        source="verified_official_document_mirror",
        url=(
            "https://pdf.dfcfw.com/pdf/"
            "H2_AN202606301826565642_1.pdf"
        ),
    ),
    SmokeDocument(
        symbol="002236",
        kind="periodic",
        title=(
            "大成中证360互联网+大数据100指数型证券投资基金"
            "2026年第2季度报告"
        ),
        announcement_date="2026-07-21",
        source="verified_official_document_mirror",
        url=(
            "http://notice.10jqka.com.cn/api/pdf/"
            "f710d5ecca7eae8.pdf"
        ),
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-path",
        default="data/database/wealth_farm.sqlite3",
    )
    parser.add_argument(
        "--download-root",
        default="temp/fund_disclosure_smoke",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    download_root = Path(args.download_root)
    store = SQLiteDataStore(db_path)
    fetch_service = DataFetchService(store=store)
    master_data = MasterDataService(
        SQLiteMasterDataRepository(db_path)
    )
    repository = SQLiteFundResearchRepository(db_path)
    repository.initialize()
    disclosure_parser = OpenFundDisclosureParser()
    http = UrlLibHttpClient()
    results = []

    for spec in DOCUMENTS:
        assets = master_data.find_assets(
            standard_symbol=spec.symbol,
            asset_type=ASSET_TYPE_OPEN_FUND,
        )
        if len(assets) != 1:
            raise RuntimeError(
                f"Expected one open-fund asset for {spec.symbol}, "
                f"got {len(assets)}"
            )
        document_id = hashlib.sha256(
            spec.url.encode("utf-8")
        ).hexdigest()[:16]
        document = DisclosureDocument(
            document_id=document_id,
            symbol=spec.symbol,
            title=spec.title,
            announcement_date=spec.announcement_date,
            category=spec.kind,
            source=spec.source,
            file_url=spec.url,
        )
        downloaded = _download(
            document,
            download_root,
            http,
        )
        store.upsert_disclosure_documents([document])
        store.upsert_document_files([downloaded])
        text = fetch_service.extract_and_store_document_texts(
            [downloaded],
            enqueue_ocr=False,
        )[0]
        tables = fetch_service.extract_and_store_document_tables(
            [downloaded]
        )
        if spec.kind == "profile":
            snapshot = disclosure_parser.parse_profile(
                fund_asset_id=assets[0].asset_id,
                document=document,
                text=text,
                tables=tables,
            )
            repository.save_profile_snapshot(snapshot)
            summary = {
                "status": snapshot.extraction_status.value,
                "share_class_code": snapshot.share_class_code,
                "manager_count": len(snapshot.fund_managers),
                "management_fee_rate": _string(
                    snapshot.management_fee_rate
                ),
                "custody_fee_rate": _string(
                    snapshot.custody_fee_rate
                ),
            }
        else:
            snapshot = disclosure_parser.parse_periodic(
                fund_asset_id=assets[0].asset_id,
                document=document,
                text=text,
                tables=tables,
            )
            repository.save_periodic_snapshot(snapshot)
            summary = {
                "status": snapshot.extraction_status.value,
                "report_period_end": snapshot.report_period_end,
                "share_class_count": len(snapshot.share_classes),
                "asset_allocation_count": len(
                    snapshot.asset_allocations
                ),
                "industry_allocation_count": len(
                    snapshot.industry_allocations
                ),
                "performance_comparison_count": len(
                    snapshot.performance_comparisons
                ),
                "top_holding_count": len(snapshot.top_holdings),
                "share_flow_count": len(snapshot.share_flows),
            }
            if not snapshot.performance_comparisons:
                raise RuntimeError(
                    f"No official performance comparisons extracted for "
                    f"{spec.symbol}"
                )
        results.append(
            {
                "symbol": spec.symbol,
                "kind": spec.kind,
                "source": spec.source,
                **summary,
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))


def _download(
    document: DisclosureDocument,
    root: Path,
    http: UrlLibHttpClient,
) -> DownloadedFile:
    target = (
        root
        / document.symbol
        / f"{document.document_id}-{document.category}.pdf"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    content = http.download_bytes(document.file_url)
    if (
        content.startswith(b"<script>")
        and "pdf.dfcfw.com" in document.file_url
    ):
        challenge = content.decode("utf-8", errors="ignore")
        status_parts = [
            int(match.group(1))
            for key in ("WTKkN", "bOYDu", "wyeCN")
            if (
                match := re.search(
                    rf"{key}:(\d+)",
                    challenge,
                )
            )
        ]
        session_match = re.search(
            r't,(\d+)\);continue;case"4"',
            challenge,
        )
        if len(status_parts) == 3 and session_match:
            challenge_http = UrlLibHttpClient(
                headers={
                    "Cookie": (
                        f"__tst_status={sum(status_parts)}#; "
                        f"EO_Bot_Ssid={session_match.group(1)}"
                    )
                }
            )
            content = challenge_http.download_bytes(
                document.file_url
            )
    sha256 = hashlib.sha256(content).hexdigest()
    if (
        not target.exists()
        or hashlib.sha256(target.read_bytes()).hexdigest() != sha256
    ):
        target.write_bytes(content)
    return DownloadedFile(
        document_id=document.document_id,
        file_path=str(target),
        file_url=document.file_url,
        sha256=sha256,
        size_bytes=len(content),
        source=document.source,
    )


def _string(value: object | None) -> str | None:
    return str(value) if value is not None else None


if __name__ == "__main__":
    main()
