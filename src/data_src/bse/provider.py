"""Beijing Stock Exchange disclosure adapter via CNINFO."""

from __future__ import annotations

from typing import Any

from src.data_src.cninfo import CninfoDataSource
from src.data_src.models import DisclosureDocument


class BseDisclosureDataSource:
    """Fetch BSE disclosure metadata through CNINFO's official A-share portal.

    A stable independent BSE public JSON announcement API has not been
    confirmed. CNINFO is used here because it is the official disclosure portal
    that covers A-share filings, including BSE companies.
    """

    source = "bse_cninfo"

    def __init__(self, cninfo_source: CninfoDataSource | None = None, http_client: Any | None = None) -> None:
        self.cninfo = cninfo_source or CninfoDataSource(http_client=http_client)

    def search_announcements(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        category: str = "",
        search_key: str = "",
    ) -> list[DisclosureDocument]:
        """Search BSE announcements through CNINFO and relabel source."""

        documents = self.cninfo.search_announcements(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            category=category,
            search_key=search_key,
        )
        return [
            DisclosureDocument(
                document_id=document.document_id,
                symbol=document.symbol,
                title=document.title,
                announcement_date=document.announcement_date,
                category=document.category,
                source=self.source,
                file_url=document.file_url,
                page_url=document.page_url,
                raw={**document.raw, "delegated_source": document.source},
            )
            for document in documents
        ]
