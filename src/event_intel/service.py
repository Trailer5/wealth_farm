"""Search dated global news, SEC filings and Federal Register documents; track observables by evidence.

Searches return what was published, when and where; they do not judge. Observations of tracked
observables are append-only and must cite stored evidence published no later than the date the
value is claimed to be public. Design notes: docs/modules/2-世界状态/事件情报与观测模块.md.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from src.data_src.models import NewsItem
from src.data_src.process_executor import run_provider_task

from .models import (
    KIND_EVENT,
    OBS_PREFIX,
    IntelItem,
    Observable,
    Observation,
    intel_item_id,
    watch_query,
)
from .sqlite_repository import SQLiteEventIntelRepository
from .text import html_to_text

SOURCE_NEWS = "news"
SOURCE_SEC_FILINGS = "sec-filings"
SOURCE_SEC_SEARCH = "sec-search"
SOURCE_FEDERAL_REGISTER = "federal-register"
SOURCES = (SOURCE_NEWS, SOURCE_SEC_FILINGS, SOURCE_SEC_SEARCH, SOURCE_FEDERAL_REGISTER)
_DEFAULT_WINDOW_DAYS = 30
_TASKS = {
    SOURCE_NEWS: "search_news",
    SOURCE_SEC_FILINGS: "sec_filings",
    SOURCE_SEC_SEARCH: "sec_full_text_search",
    SOURCE_FEDERAL_REGISTER: "federal_register_documents",
}
_STORED_SOURCE = {
    SOURCE_NEWS: "google_news",
    SOURCE_SEC_FILINGS: "sec_filings",
    SOURCE_SEC_SEARCH: "sec_search",
    SOURCE_FEDERAL_REGISTER: "federal_register",
}

Fetch = Callable[[str, dict[str, Any]], Any]


def _provider_fetch(task: str, payload: dict[str, Any]) -> Any:
    return run_provider_task("src.data_src.provider_tasks", task, payload, timeout_seconds=60.0)


class EventIntelService:
    def __init__(
        self,
        repository: SQLiteEventIntelRepository,
        *,
        fetch: Fetch | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.fetch = fetch or _provider_fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.repository.initialize()

    def search(
        self,
        source: str,
        *,
        query: str | None = None,
        ticker: str | None = None,
        forms: tuple[str, ...] = (),
        agency: str | None = None,
        since: str | None = None,
        until: str | None = None,
        language: str = "en",
        limit: int = 20,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Items published in [since, until]; defaults to the 30 days up to today."""

        if source not in SOURCES:
            raise ValueError(f"source 只能是 {'、'.join(SOURCES)}")
        until = (until or self._today())[:10]
        since = (since or (date.fromisoformat(until) - timedelta(days=_DEFAULT_WINDOW_DAYS)).isoformat())[:10]
        if since > until:
            raise ValueError("since 不能晚于 until")
        params, payload = self._params(source, query, ticker, forms, agency, since, until, language, limit)
        error = None
        items: list[IntelItem] = []
        search_id = None
        if allow_remote:
            try:
                items = [_stored(item) for item in self.fetch(_TASKS[source], payload)]
            except Exception as exc:  # noqa: BLE001 - remote sources fail in many ways; report, do not crash
                error = f"{type(exc).__name__}: {exc}"
            items = [item for item in items if since <= item.published_on <= until]
            items.sort(key=lambda item: (item.published_at or item.published_on, item.item_id), reverse=True)
            now = self.clock().isoformat()
            self.repository.save_items(items, now)
            search_id = self.repository.record_search(source, params, now, [item.item_id for item in items], error)
        if not allow_remote or (error and not items):
            items = self.repository.find_items(
                source=_STORED_SOURCE[source], text=query or ticker, since=since, until=until, limit=limit
            )
        return {
            "source": source,
            "params": params,
            "search_id": search_id,
            "count": len(items),
            "items": [item.to_dict() for item in items[:limit]],
            "error": error,
            "notes": _notes(source, error, allow_remote),
        }

    def items(self, item_ids: list[str]) -> list[dict[str, Any]]:
        found = self.repository.get_items(item_ids)
        return [found[item].to_dict() for item in item_ids if item in found]

    def page_text(self, url: str, *, max_chars: int = 20000) -> dict[str, Any]:
        page = self.fetch("fetch_page", {"url": url})
        html = str(page.get("html") or "")
        if "html" in str(page.get("content_type", "")).lower() or html.lstrip().startswith("<"):
            title, text = html_to_text(html)
        else:
            title, text = "", html
        notes = ["正文由 HTML 去掉脚本、样式和导航后得到，表格按行展开；引用时附原链接与发布日期。"]
        if page.get("resolve_error"):
            notes.append("Google 新闻跳转链接没能解析到原文地址；按标题与媒体名在媒体站点或其他来源核对。")
        elif text.strip():
            self.repository.save_page(
                url, final_url=page.get("final_url"), title=title, text=text, fetched_at=self.clock().isoformat()
            )
        if page.get("truncated") or len(text) > max_chars:
            notes.append(f"正文超过 {max_chars} 字符，已截断；需要后文时加大 --max-chars。")
        return {
            "url": url,
            "final_url": page.get("final_url"),
            "title": title,
            "chars": len(text),
            "text": text[:max_chars],
            "notes": notes,
            "resolved": not page.get("resolve_error"),
        }

    def read_pages(
        self,
        *,
        urls: list[str] = (),
        item_ids: list[str] = (),
        max_chars: int = 6000,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Read many articles under rate limits: cached pages first, then fetch until a source cools down.

        Pages are cached by URL, so a rerun after the cooldown only fetches what is still missing. When
        a source signals a rate limit, the remaining links of that host are returned as ``deferred``
        instead of being sent and tripping the limit again.
        """

        targets: list[tuple[str, str | None]] = [(url, None) for url in urls]
        found = self.repository.get_items(item_ids)
        targets += [(found[item].url, item) for item in item_ids if item in found]
        missing = [item for item in item_ids if item not in found]
        pages, deferred, failed = [], [], []
        cooling: set[str] = set()
        for url, item_id in targets:
            host = (urlsplit(url).hostname or "").lower()
            cached = None if refresh else self.repository.get_page(url)
            if cached:
                pages.append(_page_row(url, item_id, cached["final_url"], cached["title"], cached["text"], max_chars, cached=True))
                continue
            if host in cooling:
                deferred.append({"url": url, "item_id": item_id, "reason": "同一来源刚触发限流，稍后重跑会只取剩余链接"})
                continue
            try:
                page = self.page_text(url, max_chars=max_chars)
            except Exception as exc:  # noqa: BLE001 - one bad page must not stop the batch
                message = f"{type(exc).__name__}: {exc}"
                if _rate_limited(message):
                    cooling.add(host)
                    deferred.append({"url": url, "item_id": item_id, "reason": message[:300]})
                else:
                    failed.append({"url": url, "item_id": item_id, "error": message[:300]})
                continue
            if not page["resolved"]:
                cooling.add(host)
                deferred.append({"url": url, "item_id": item_id, "reason": "Google 新闻链接未解析到原文（多半是限流）"})
                continue
            pages.append(_page_row(url, item_id, page["final_url"], page["title"], page["text"], max_chars, cached=False))
        notes = ["正文按链接缓存，重跑只取未读到的；引用时附原链接与发布日期。"]
        if deferred:
            notes.append("deferred 是触发限流后没有发送的链接：等冷却结束（约 2 分钟）后用同样的参数重跑。")
        if missing:
            notes.append("这些条目编号本地没有：" + "、".join(missing))
        return {"pages": pages, "deferred": deferred, "failed": failed, "notes": notes}

    # Observables -----------------------------------------------------------------------------

    def define_observable(self, observable: Observable, baseline: dict[str, Any]) -> dict[str, Any]:
        """Store the definition and its baseline observation (the value known when tracking starts)."""

        observation = self._observation(observable, baseline)
        created = self.repository.save_observable(observable, self.clock().isoformat())
        appended = self.repository.append_observation(observation)
        return {
            "observable": observable.to_dict(),
            "created": created,
            "baseline": observation.to_dict() | {"observation_id": observation.observation_id, "appended": appended},
            "metric_id": observable.observable_id,
        }

    def record_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        observable = self.repository.get_observable(str(payload.get("observable_id") or ""))
        if observable is None:
            raise ValueError(f"未登记的可观测量 {payload.get('observable_id')}；先 define-observable")
        observation = self._observation(observable, payload)
        appended = self.repository.append_observation(observation)
        return observation.to_dict() | {"observation_id": observation.observation_id, "appended": appended}

    def observable(self, observable_id: str) -> Observable | None:
        return self.repository.get_observable(observable_id)

    def observables(self) -> list[Observable]:
        return self.repository.list_observables()

    def observations(self, observable_id: str) -> list[Observation]:
        """One observation per period_end, the latest recorded (corrections supersede)."""

        latest: dict[str, Observation] = {}
        for item in self.repository.list_observations(observable_id):
            current = latest.get(item.period_end)
            if current is None or item.recorded_at >= current.recorded_at:
                latest[item.period_end] = item
        return [latest[key] for key in sorted(latest)]

    def watch(
        self,
        observable: Observable,
        *,
        since: str,
        until: str,
        limit_per_query: int = 5,
        allow_remote: bool = True,
    ) -> list[dict[str, Any]]:
        hits = []
        for text in observable.watch_queries:
            language, query = watch_query(text)
            result = self.search(
                SOURCE_NEWS,
                query=query,
                since=since,
                until=until,
                language=language,
                limit=limit_per_query,
                allow_remote=allow_remote,
            )
            hits.append(
                {
                    "query": text,
                    "search_id": result["search_id"],
                    "error": result["error"],
                    "items": result["items"],
                }
            )
        return hits

    def _observation(self, observable: Observable, payload: dict[str, Any]) -> Observation:
        observation = Observation.from_dict(
            {**payload, "observable_id": observable.observable_id, "recorded_at": self.clock().isoformat()}
        )
        if observable.kind == KIND_EVENT and observation.value not in (Decimal(0), Decimal(1)):
            raise ValueError("事件类可观测量的值只能是 0（截至 period_end 未发生）或 1（已发生）")
        published = []
        for ref in observation.evidence_refs:
            if ref.startswith("search:"):
                search = self.repository.get_search(int(ref.split(":", 1)[1]))
                if search is None:
                    raise ValueError(f"证据 {ref} 不存在")
                published.append(search["searched_at"][:10])
            else:
                item = self.repository.get_items([ref]).get(ref)
                if item is None:
                    raise ValueError(f"证据 {ref} 不存在；先用 intel 检索，或用 intel --add-evidence 登记链接")
                published.append(item.published_on)
        if min(published) > observation.available_at:
            raise ValueError(
                f"available_at {observation.available_at} 早于所有证据的发布日期（最早 {min(published)}）；"
                "可得日期取证据最早的公开日期"
            )
        return observation

    def add_evidence(self, *, url: str, title: str, published_on: str, publisher: str | None = None) -> dict[str, Any]:
        """Register a link the Agent found elsewhere (official site, filing) as citable evidence."""

        day = date.fromisoformat(published_on[:10]).isoformat()
        if day > self._today():
            raise ValueError("published_on 不能晚于今天")
        item = IntelItem(
            item_id=intel_item_id(url),
            source="agent_evidence",
            kind="evidence",
            title=title.strip() or url,
            url=url.strip(),
            published_on=day,
            publisher=publisher,
        )
        self.repository.save_items([item], self.clock().isoformat())
        stored = self.repository.get_items([item.item_id])[item.item_id]
        return stored.to_dict()

    def _params(self, source, query, ticker, forms, agency, since, until, language, limit):
        if source == SOURCE_NEWS:
            if not query:
                raise ValueError("news 需要 --query")
            payload = {"query": query, "since": since, "until": until, "language": language}
        elif source == SOURCE_SEC_FILINGS:
            if not ticker:
                raise ValueError("sec-filings 需要 --ticker")
            payload = {"ticker": ticker, "forms": tuple(forms), "since": since, "until": until}
        elif source == SOURCE_SEC_SEARCH:
            if not query:
                raise ValueError("sec-search 需要 --query")
            payload = {"query": query, "forms": tuple(forms), "since": since, "until": until}
        else:
            if not query and not agency:
                raise ValueError("federal-register 需要 --query 或 --agency")
            payload = {"query": query, "agency": agency, "since": since, "until": until, "limit": max(limit, 20)}
        params = {key: list(value) if isinstance(value, tuple) else value for key, value in payload.items()}
        return params, payload

    def _today(self) -> str:
        return self.clock().date().isoformat()


def is_observable_metric(metric_id: str) -> bool:
    return metric_id.startswith(OBS_PREFIX)


def _stored(item: NewsItem) -> IntelItem:
    return IntelItem(
        item_id=intel_item_id(item.url),
        source=item.source,
        kind=item.kind,
        title=item.title,
        url=item.url,
        published_on=item.published_on,
        published_at=item.published_at,
        publisher=item.publisher,
        summary=item.summary,
        language=item.language,
        raw=dict(item.raw),
    )


def _rate_limited(message: str) -> bool:
    lowered = message.lower()
    return any(mark in lowered for mark in ("429", "冷却", "cooling", "too many requests", "rate limit"))


def _page_row(
    url: str, item_id: str | None, final_url: str | None, title: str, text: str, max_chars: int, *, cached: bool
) -> dict[str, Any]:
    return {
        "url": url,
        "item_id": item_id,
        "final_url": final_url,
        "title": title,
        "chars": len(text),
        "text": text[:max_chars],
        "cached": cached,
    }


def _notes(source: str, error: str | None, allow_remote: bool) -> list[str]:
    notes = ["条目是检索结果，不是判断：引用时写标题、媒体、发布日期和链接；同一事件以官方来源（申报、公报、公司公告）为准。"]
    if source == SOURCE_NEWS:
        notes.append("新闻来自 Google 新闻（英文或中文版），按发布日期过滤；链接是跳转页，原文需按标题在媒体站点核对。")
    if source in (SOURCE_SEC_FILINGS, SOURCE_SEC_SEARCH):
        notes.append("SEC 申报链接可用 intel --fetch-url 读取正文；8-K 的 items 表示披露事项（如 5.02 高管变动）。")
    if source == SOURCE_FEDERAL_REGISTER:
        notes.append("联邦公报是美国法规的正式发布处；出口管制规则看 --agency industry-and-security-bureau。")
    if error:
        notes.append(f"远程检索失败（{error}），返回的是本地已存的匹配条目，可能不完整。")
    elif not allow_remote:
        notes.append("未联网：返回本地已存的匹配条目。")
    return notes
