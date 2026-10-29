"""Point-in-time stock pools and fundamentals for selection backtests (stage 9 slice 3).

A pool is a scope (a Shenwan industry's current members, or an explicit list of codes) filtered on
each date to the stocks already listed and not yet delisted, by the exchange listing directory.
Fundamentals on a date are each stock's latest annual report announced by then.

Design notes: docs/modules/3-分析工具/低频回测模块.md「时点股票池与选股回测」.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.cross_section import CrossSectionService
from src.data_src.service import DataFetchService
from src.sector_data import SectorDataService

MAX_POOL = 300


class StockPoolError(ValueError):
    pass


@dataclass(frozen=True)
class StockPool:
    label: str
    listing: dict[str, tuple[str | None, str | None]]
    notes: tuple[str, ...]
    gaps: tuple[str, ...] = field(default=())

    @property
    def symbols(self) -> list[str]:
        return sorted(self.listing)

    def members_on(self, day: str) -> list[str]:
        """Codes listed on or before day and not delisted by then; unknown listing dates count as listed."""

        return [
            code
            for code, (listed, delisted) in sorted(self.listing.items())
            if (listed is None or listed <= day) and (delisted is None or delisted > day)
        ][:MAX_POOL]


class StockPoolService:
    def __init__(
        self,
        *,
        sector_data: SectorDataService,
        data_fetch: DataFetchService,
        cross_section: CrossSectionService,
    ) -> None:
        self.sector_data = sector_data
        self.data_fetch = data_fetch
        self.cross_section = cross_section

    def resolve(self, scope: str, *, allow_remote: bool = True) -> StockPool:
        """scope: sw:<Shenwan code> (current members) or symbols:<code,code,...> (may include delisted codes)."""

        kind, _, value = scope.partition(":")
        notes: list[str] = []
        if kind == "sw":
            industry = self.sector_data.industry(value, allow_remote=allow_remote)
            if industry is None:
                raise StockPoolError(f"未知申万行业：{value}；用 industry 查代码")
            codes = sorted({member.symbol for member in self.sector_data.members(value, allow_remote=allow_remote)})
            label = f"申万{industry.name}（当前成分）"
            notes.append(
                "范围按当前申万成分确定：历史上曾属于该行业、后来调出或退市的股票不在其中（幸存者偏差，结果偏乐观）；"
                "当时尚未上市的成分按上市日加入"
            )
        elif kind == "symbols":
            codes = sorted({code.strip().zfill(6) for code in value.split(",") if code.strip()})
            label = f"指定名单（{len(codes)} 只）"
        else:
            raise StockPoolError("scope 写成 sw:<申万代码> 或 symbols:<代码,...>")
        if not codes:
            raise StockPoolError(f"{scope} 没有成分")
        if allow_remote:
            self.data_fetch.refresh_exchange_listings(("sh_stock", "sz_stock", "bj_stock", "sh_delisted", "sz_delisted"))
        listing: dict[str, tuple[str | None, str | None]] = {}
        unknown = []
        for code in codes:
            rows = [row for row in self.data_fetch.store.get_exchange_listings(code=code) if row["asset_type"] == "stock"]
            if not rows:
                unknown.append(code)
                listing[code] = (None, None)
                continue
            listed = min((row["listed_on"] for row in rows if row["listed_on"]), default=None)
            active = any(not row["delisted_on"] for row in rows if row["segment"].endswith("_stock"))
            delisted = None if active else max((row["delisted_on"] for row in rows if row["delisted_on"]), default=None)
            listing[code] = (listed, delisted)
        gaps = [f"交易所名录里找不到 {', '.join(unknown)}，按一直在市处理"] if unknown else []
        if len(codes) > MAX_POOL:
            gaps.append(f"范围有 {len(codes)} 只，每次调仓只取前 {MAX_POOL} 只（按代码）")
        return StockPool(label=label, listing=listing, notes=tuple(notes), gaps=tuple(gaps))

    def annual_fundamentals(self, first_year: int, last_year: int, *, allow_remote: bool = True) -> "AnnualFundamentals":
        rows: dict[str, list[dict[str, Any]]] = {}
        errors = []
        for year in range(first_year, last_year + 1):
            result = self.cross_section.report_period(f"{year}-12-31", allow_remote=allow_remote)
            if result["provider_error"]:
                errors.append(f"{year} 年报截面：{result['provider_error']}")
            for row in result["rows"]:
                if row.get("announced_on"):
                    rows.setdefault(row["symbol"], []).append(row)
        for items in rows.values():
            items.sort(key=lambda row: row["report_period"])
        return AnnualFundamentals(rows, tuple(errors))


@dataclass(frozen=True)
class AnnualFundamentals:
    rows: dict[str, list[dict[str, Any]]]
    errors: tuple[str, ...]

    def as_of(self, code: str, day: str) -> dict[str, Any] | None:
        """Latest annual report announced on or before day (revised reports count from their revision date)."""

        latest = None
        for row in self.rows.get(code, ()):
            if row["announced_on"] <= day:
                latest = row
        return latest
