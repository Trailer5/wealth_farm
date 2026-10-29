"""Shenwan 2021 industries: catalog, current members, peers and valuation history."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from src.data_access import (
    DATASET_INDEX_DAILY_BARS,
    AssetInput,
    DataAccessRequest,
    DataAccessService,
)
from src.data_src.process_executor import run_provider_task
from src.master_data import (
    ASSET_TYPE_INDEX,
    SW_INDEX_EXCHANGE,
    Asset,
    MasterDataService,
)

from .models import IndustryMember, IndustryPath, IndustryValuationPoint, SwIndustry
from .sqlite_repository import SQLiteSectorDataRepository

CLASSIFICATION = "shenwan_2021"
# Memberships and the catalog change a few times a year; refresh monthly.
REFERENCE_MAX_AGE_DAYS = 30
_VALUATION_CATEGORY = {1: "一级行业", 2: "二级行业"}
_TASKS = "src.data_src.provider_tasks"


class SectorFetcher(Protocol):
    def catalog(self, level: int) -> list[dict[str, Any]]: ...

    def constituents(self, codes: list[str]) -> dict[str, Any]: ...

    def level_metrics(self, category: str, trade_dates: list[str]) -> dict[str, Any]: ...


class ProviderSectorFetcher:
    def catalog(self, level: int) -> list[dict[str, Any]]:
        return run_provider_task(
            _TASKS, "fetch_sw_industry_catalog", {"level": level}, timeout_seconds=60.0
        )

    def constituents(self, codes: list[str]) -> dict[str, Any]:
        return run_provider_task(
            _TASKS, "fetch_sw_constituents", {"codes": codes}, timeout_seconds=900.0
        )

    def level_metrics(self, category: str, trade_dates: list[str]) -> dict[str, Any]:
        return run_provider_task(
            _TASKS,
            "fetch_sw_level_metrics",
            {"category": category, "trade_dates": trade_dates},
            timeout_seconds=900.0,
        )


class SectorDataUnavailable(RuntimeError):
    """Reference data is missing locally and could not be fetched."""


class SectorDataService:
    def __init__(
        self,
        repository: SQLiteSectorDataRepository,
        *,
        master_data: MasterDataService,
        data_access: DataAccessService | None = None,
        fetcher: SectorFetcher | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.repository = repository
        self.master_data = master_data
        self.data_access = data_access
        self.fetcher = fetcher or ProviderSectorFetcher()
        self.clock = clock
        self.repository.initialize()

    def industries(
        self,
        *,
        level: int | None = None,
        parent_code: str | None = None,
        allow_remote: bool = True,
    ) -> tuple[SwIndustry, ...]:
        items = self._catalog(allow_remote)
        return tuple(
            item
            for item in items
            if (level is None or item.level == level)
            and (parent_code is None or item.parent_code == parent_code)
        )

    def industry(self, code: str, *, allow_remote: bool = True) -> SwIndustry | None:
        return {item.code: item for item in self._catalog(allow_remote)}.get(code)

    def classify(self, symbol: str, *, allow_remote: bool = True) -> IndustryPath | None:
        """Current Shenwan path of one stock; None when it is not a member.

        Fetches the 31 level-1 lists, then only the level-3 lists under the
        stock's level-1 industry.
        """

        catalog = {item.code: item for item in self._catalog(allow_remote)}
        code = symbol.strip().zfill(6)
        level1 = [item.code for item in catalog.values() if item.level == 1]
        self._ensure_lists(level1, allow_remote)
        l1_member = next(
            (m for m in self.repository.memberships(code) if m.industry_code in level1),
            None,
        )
        if l1_member is None:
            return None
        children = _l3_codes(l1_member.industry_code, catalog)
        self._ensure_lists(children, allow_remote)
        member = next(
            (m for m in self.repository.memberships(code) if m.industry_code in children),
            None,
        )
        if member is None:
            return None
        l3 = catalog[member.industry_code]
        l2 = catalog.get(l3.parent_code or "")
        l1 = catalog.get(l2.parent_code or "") if l2 is not None else None
        if l2 is None or l1 is None:
            return None
        return IndustryPath(member.symbol, member.name, l1, l2, l3, member.included_on)

    def members(self, code: str, *, allow_remote: bool = True) -> tuple[IndustryMember, ...]:
        """Level-1 and level-3 lists come from the source; level 2 is the union of its level-3 lists."""

        catalog = {item.code: item for item in self._catalog(allow_remote)}
        if code not in catalog:
            raise KeyError(f"Unknown Shenwan industry: {code}")
        codes = [code] if catalog[code].level != 2 else _l3_codes(code, catalog)
        self._ensure_lists(codes, allow_remote)
        missing = [item for item in codes if self.repository.last_success("members", item) is None]
        if missing:
            raise SectorDataUnavailable(
                f"申万成分列表未取到：{', '.join(missing)}；稍后重试，不要使用不完整的成分"
            )
        return tuple(self.repository.list_members(codes))

    def level1_by_symbol(self, *, allow_remote: bool = True) -> dict[str, SwIndustry]:
        """Current level-1 industry of every member stock (31 list fetches at most)."""

        catalog = {item.code: item for item in self._catalog(allow_remote) if item.level == 1}
        codes = sorted(catalog)
        self._ensure_lists(codes, allow_remote)
        missing = [item for item in codes if self.repository.last_success("members", item) is None]
        if missing:
            raise SectorDataUnavailable(f"申万一级行业成分未取到：{', '.join(missing)}")
        return {
            member.symbol: catalog[member.industry_code]
            for member in self.repository.list_members(codes)
        }

    def peers(
        self,
        symbol: str,
        *,
        min_peers: int = 5,
        allow_remote: bool = True,
    ) -> dict[str, object] | None:
        """Same level-3 industry; level 2 when level 3 has fewer than min_peers others."""

        path = self.classify(symbol, allow_remote=allow_remote)
        if path is None:
            return None
        industry = path.l3
        peers = [m for m in self.members(path.l3.code) if m.symbol != path.symbol]
        if len(peers) < min_peers:
            industry = path.l2
            peers = [m for m in self.members(path.l2.code) if m.symbol != path.symbol]
        return {
            "classification": CLASSIFICATION,
            "path": path.to_dict(),
            "peer_industry": industry.to_dict(),
            "peers": [item.to_dict() for item in peers],
            "note": "申万当前成分；历史上的行业调整不追溯",
        }

    def index_asset(self, code: str, *, allow_remote: bool = True) -> Asset:
        """The Shenwan index of an industry, registered in master data on first use."""

        industry = self.industry(code, allow_remote=allow_remote)
        if industry is None:
            raise KeyError(f"Unknown Shenwan industry: {code}")
        for asset in self.master_data.find_assets(
            standard_symbol=code,
            asset_type=ASSET_TYPE_INDEX,
        ):
            if asset.exchange == SW_INDEX_EXCHANGE:
                return asset
        resolution = self.master_data.resolve_identifier(
            source=CLASSIFICATION,
            raw_symbol=code,
            name=f"申万{industry.name}",
            asset_type_hint=ASSET_TYPE_INDEX,
            market_hint="cn_a",
            exchange_hint=SW_INDEX_EXCHANGE,
        )
        if resolution.asset is None:
            raise SectorDataUnavailable(f"Could not register Shenwan index {code}")
        return resolution.asset

    def valuation_history(
        self,
        code: str,
        *,
        start_date: str,
        end_date: str,
        allow_remote: bool = True,
    ) -> dict[str, object]:
        """Month-end and latest valuation samples, fetched once per date and cached."""

        industry = self.industry(code, allow_remote=allow_remote)
        if industry is None:
            raise KeyError(f"Unknown Shenwan industry: {code}")
        result: dict[str, object] = {
            "industry": industry.to_dict(),
            "start_date": start_date,
            "end_date": end_date,
            "sampling": "month_end_and_latest",
            "points": [],
            "provider_errors": {},
        }
        category = _VALUATION_CATEGORY.get(industry.level)
        if category is None:
            result["note"] = "申万三级行业没有估值历史来源；用所属二级行业或成分股估值"
            return result
        dates = self._sample_dates(code, start_date, end_date, allow_remote)
        kind = f"valuation:{category}"
        missing = sorted(set(dates) - self.repository.successful_keys(kind))
        if missing and allow_remote:
            result["provider_errors"] = self._fetch_valuations(category, kind, missing)
        cached = self.repository.list_valuations(code, start_date, end_date)
        points = [item for item in cached if item.trade_date in set(dates)]
        # The newest day is often not published yet; keep the latest cached day instead of a month-old one.
        if cached and (not points or cached[-1].trade_date > points[-1].trade_date):
            points.append(cached[-1])
        result["points"] = [item.to_dict() for item in points]
        result["sample_count"] = len(points)
        if industry.level == 2:
            result["note"] = "二级行业估值来源在 2021-12 申万行业调整前没有本代码，历史样本可能较短"
        return result

    def _sample_dates(
        self,
        code: str,
        start_date: str,
        end_date: str,
        allow_remote: bool,
    ) -> list[str]:
        if self.data_access is None:
            raise SectorDataUnavailable("Valuation sampling needs data access for trading dates")
        response = self.data_access.get(
            DataAccessRequest(
                asset=AssetInput(asset_id=self.index_asset(code).asset_id),
                dataset=DATASET_INDEX_DAILY_BARS,
                start_date=start_date,
                end_date=end_date,
                as_of_date=end_date,
                allow_remote=allow_remote,
                allow_stale_on_error=True,
            )
        )
        days = sorted({str(item["trade_date"]) for item in response.records})
        if not days:
            raise SectorDataUnavailable(
                response.error or f"No Shenwan index bars for {code}"
            )
        by_month: dict[str, str] = {}
        for day in days:
            by_month[day[:7]] = day
        return sorted({*by_month.values(), days[-1]})

    def _fetch_valuations(
        self,
        category: str,
        kind: str,
        trade_dates: list[str],
    ) -> dict[str, str]:
        errors: dict[str, str] = {}
        try:
            outcomes = self.fetcher.level_metrics(category, trade_dates)
        except Exception as exc:  # noqa: BLE001 - the whole batch failed
            return {"batch": f"{type(exc).__name__}: {exc}"}
        now = self.clock().isoformat()
        for trade_date, outcome in outcomes.items():
            if isinstance(outcome, str):
                errors[trade_date] = outcome
                self.repository.log_fetch(kind, trade_date, fetched_at=now, record_count=0, error=outcome)
                continue
            points = [
                IndustryValuationPoint(
                    code=str(item["index_code"]),
                    trade_date=str(item["trade_date"]),
                    pe=item.get("pe"),
                    pb=item.get("pb"),
                    dividend_yield_percent=item.get("dividend_yield_percent"),
                    amount_share_percent=item.get("amount_share_percent"),
                    turnover_rate_percent=item.get("turnover_rate_percent"),
                    float_market_cap=item.get("float_market_cap"),
                    source=str(item.get("source") or ""),
                )
                for item in outcome
            ]
            self.repository.upsert_valuations(points)
            # An empty day (holiday in the source) is logged as an error so it is retried.
            self.repository.log_fetch(
                kind,
                trade_date,
                fetched_at=now,
                record_count=len(points),
                error=None if points else "no rows",
            )
        return errors

    def _catalog(self, allow_remote: bool) -> list[SwIndustry]:
        items = self.repository.list_industries()
        if items and not self._stale("catalog", "all"):
            return items
        if not allow_remote:
            if items:
                return items
            raise SectorDataUnavailable("Shenwan catalog is not cached and remote is disabled")
        try:
            records = [
                record for level in (1, 2, 3) for record in self.fetcher.catalog(level)
            ]
        except Exception as exc:  # noqa: BLE001 - fall back to a stale cache
            self._log("catalog", "all", 0, f"{type(exc).__name__}: {exc}")
            if items:
                return items
            raise SectorDataUnavailable(f"Shenwan catalog fetch failed: {exc}") from exc
        industries = _link_parents(records)
        self.repository.replace_industries(industries)
        self._log("catalog", "all", len(industries), None)
        return industries

    def _ensure_lists(self, codes: list[str], allow_remote: bool) -> None:
        """Fetch member lists that are missing or stale; a failed list keeps its old rows."""

        stale = [code for code in codes if self._stale("members", code)]
        if not stale or not allow_remote:
            return
        try:
            outcomes = self.fetcher.constituents(stale)
        except Exception as exc:  # noqa: BLE001 - lists stay as cached
            for code in stale:
                self._log("members", code, 0, f"{type(exc).__name__}: {exc}")
            return
        for code, outcome in outcomes.items():
            if isinstance(outcome, str) or not outcome:
                self._log("members", code, 0, outcome if isinstance(outcome, str) else "no rows")
                continue
            self.repository.replace_members(
                code,
                [
                    IndustryMember(
                        symbol=str(item["symbol"]),
                        name=str(item["name"]),
                        industry_code=code,
                        included_on=item.get("included_on"),
                    )
                    for item in outcome
                ],
            )
            self._log("members", code, len(outcome), None)

    def _stale(self, kind: str, key: str) -> bool:
        last = self.repository.last_success(kind, key)
        if last is None:
            return True
        return self.clock() - datetime.fromisoformat(last) > timedelta(days=REFERENCE_MAX_AGE_DAYS)

    def _log(self, kind: str, key: str, count: int, error: str | None) -> None:
        self.repository.log_fetch(
            kind,
            key,
            fetched_at=self.clock().isoformat(),
            record_count=count,
            error=error,
        )


def _link_parents(records: list[dict[str, Any]]) -> list[SwIndustry]:
    code_by_level_name = {
        (int(item["level"]), str(item["name"])): str(item["code"]) for item in records
    }
    return [
        SwIndustry(
            code=str(item["code"]),
            name=str(item["name"]),
            level=int(item["level"]),
            parent_code=code_by_level_name.get(
                (int(item["level"]) - 1, str(item.get("parent_name") or ""))
            ),
            constituent_count=int(item.get("constituent_count") or 0),
        )
        for item in records
    ]


def _l3_codes(code: str, catalog: dict[str, SwIndustry]) -> list[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for item in catalog.values():
        if item.parent_code:
            children[item.parent_code].append(item.code)
    frontier = [code]
    leaves = []
    while frontier:
        current = frontier.pop()
        if catalog[current].level == 3:
            leaves.append(current)
        frontier.extend(children.get(current, []))
    return sorted(leaves)
