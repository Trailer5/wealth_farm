"""SEC EDGAR: company filings by ticker, full-text search, and the declared User-Agent SEC requires.

SEC rejects anonymous browser-like clients on www.sec.gov; requests carry the user's declared
"<name> <email>". Design notes:
docs/modules/2-世界状态/事件情报与观测模块.md「来源」.
"""

from __future__ import annotations

from typing import Any

from src.data_src.common import DataSourceError, normalize_us_symbol
from src.data_src.http_client import UrlLibHttpClient
from src.data_src.models import NewsItem, SecurityInfo

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
SEC_USER_AGENT = "Vincent qqwe1036@163.com"


class SecEdgarDataSource:
    def __init__(self, http_client: Any | None = None) -> None:
        self.http = http_client or UrlLibHttpClient(headers={"User-Agent": SEC_USER_AGENT})

    def cik_for(self, ticker: str) -> tuple[int, str]:
        code = normalize_us_symbol(ticker).replace(".", "-")
        for item in (self.http.get_json(TICKERS_URL) or {}).values():
            if str(item.get("ticker", "")).upper() == code:
                return int(item["cik_str"]), str(item.get("title") or code)
        raise DataSourceError(f"SEC has no CIK for ticker {ticker}")

    def listing(self, ticker: str) -> SecurityInfo:
        """Operating-company listing from SEC's ticker-exchange file (ETFs are not in it)."""

        code = normalize_us_symbol(ticker)
        payload = self.http.get_json(TICKERS_EXCHANGE_URL) or {}
        fields = payload.get("fields") or []
        for row in payload.get("data") or ():
            item = dict(zip(fields, row))
            if str(item.get("ticker", "")).upper() == code.replace(".", "-"):
                exchange = str(item.get("exchange") or "").lower() or None
                return SecurityInfo(
                    symbol=code,
                    name=str(item.get("name") or code),
                    security_type="stock",
                    exchange={"nasdaq": "nasdaq", "nyse": "nyse"}.get(exchange or "", exchange),
                    source="sec_edgar",
                    raw=item,
                )
        raise DataSourceError(f"SEC ticker file has no listing for {ticker}")

    def filings(
        self,
        ticker: str,
        *,
        forms: tuple[str, ...] = (),
        since: str | None = None,
        until: str | None = None,
    ) -> list[NewsItem]:
        cik, company = self.cik_for(ticker)
        payload = self.http.get_json(SUBMISSIONS_URL.format(cik=cik))
        return parse_submissions(
            payload,
            cik=cik,
            company=f"{company} ({normalize_us_symbol(ticker)})",
            forms=forms,
            since=since,
            until=until,
        )

    def company_facts(self, ticker: str) -> dict[str, Any]:
        """XBRL facts of every filing (us-gaap and dei concepts with period, value and filed date)."""

        cik, company = self.cik_for(ticker)
        payload = self.http.get_json(COMPANY_FACTS_URL.format(cik=cik)) or {}
        return {"cik": cik, "company": company, "facts": payload.get("facts") or {}}

    def full_text_search(
        self,
        query: str,
        *,
        forms: tuple[str, ...] = (),
        since: str | None = None,
        until: str | None = None,
    ) -> list[NewsItem]:
        params: dict[str, Any] = {"q": query}
        if forms:
            params["forms"] = ",".join(forms)
        if since or until:
            params.update({"dateRange": "custom", "startdt": since or "2001-01-01", "enddt": until or since})
        return parse_search(self.http.get_json(SEARCH_URL, params), since=since, until=until)


def parse_submissions(
    payload: dict[str, Any],
    *,
    cik: int,
    company: str,
    forms: tuple[str, ...] = (),
    since: str | None = None,
    until: str | None = None,
) -> list[NewsItem]:
    recent = (payload.get("filings") or {}).get("recent") or {}
    wanted = {item.upper() for item in forms}
    items = []
    for index, form in enumerate(recent.get("form") or ()):
        day = str(recent["filingDate"][index])[:10]
        if wanted and form.upper() not in wanted:
            continue
        if (since and day < since[:10]) or (until and day > until[:10]):
            continue
        accession = str(recent["accessionNumber"][index])
        document = str((recent.get("primaryDocument") or [""] * (index + 1))[index] or "")
        filing_items = str((recent.get("items") or [""] * (index + 1))[index] or "")
        description = str((recent.get("primaryDocDescription") or [""] * (index + 1))[index] or "")
        label = f"items {filing_items}" if filing_items else description
        items.append(
            NewsItem(
                source="sec_filings",
                kind="filing",
                title=f"{company} {form}" + (f"（{label}）" if label and label != form else ""),
                url=ARCHIVE_URL.format(cik=cik, accession=accession.replace("-", ""), document=document),
                published_on=day,
                published_at=str((recent.get("acceptanceDateTime") or [None] * (index + 1))[index] or "") or None,
                publisher="SEC EDGAR",
                language="en",
                raw={
                    "form": form,
                    "accession": accession,
                    "report_date": (recent.get("reportDate") or [None] * (index + 1))[index],
                    "items": filing_items,
                },
            )
        )
    return items


def parse_search(payload: dict[str, Any], *, since: str | None = None, until: str | None = None) -> list[NewsItem]:
    items = []
    for hit in ((payload.get("hits") or {}).get("hits") or ()):
        source = hit.get("_source") or {}
        accession, _, document = str(hit.get("_id") or "").partition(":")
        day = str(source.get("file_date") or "")[:10]
        ciks = source.get("ciks") or []
        if not accession or not day or not ciks:
            continue
        if (since and day < since[:10]) or (until and day > until[:10]):
            continue
        names = "；".join(source.get("display_names") or ())
        form = source.get("form") or source.get("file_type") or ""
        items.append(
            NewsItem(
                source="sec_search",
                kind="filing",
                title=f"{names} {form} {source.get('file_description') or ''}".strip(),
                url=ARCHIVE_URL.format(cik=int(ciks[0]), accession=accession.replace("-", ""), document=document),
                published_on=day,
                publisher="SEC EDGAR",
                language="en",
                raw={"form": form, "accession": accession, "items": source.get("items"), "file_type": source.get("file_type")},
            )
        )
    return items
