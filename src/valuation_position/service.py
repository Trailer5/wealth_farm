"""Where a stock's valuation sits against its own history and its Shenwan peers."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from src.cross_section import CrossSectionService
from src.market_analysis import historical_percentile
from src.master_data import Asset
from src.sector_data import SectorDataService

VALUATION_METRICS = ("pe_ttm", "pb", "ps")
_PEER_LOOKBACK_DAYS = 14
_MIN_PEER_VALUES = 5


class ValuationPositionService:
    def __init__(
        self,
        *,
        cross_section: CrossSectionService,
        sector_data: SectorDataService,
    ) -> None:
        self.cross_section = cross_section
        self.sector_data = sector_data

    def position(
        self,
        symbol: str,
        *,
        as_of_date: str,
        years: int = 8,
        allow_remote: bool = True,
    ) -> dict[str, Any]:
        """Own-history percentiles from month-end samples, peer percentiles on the as-of date.

        PE percentiles use positive values only; loss-making samples and peers are counted apart.
        """

        code = symbol.strip().zfill(6)
        start = (date.fromisoformat(as_of_date) - timedelta(days=366 * years)).isoformat()
        own = self.cross_section.valuation_history(
            [code],
            start_date=start,
            end_date=as_of_date,
            allow_remote=allow_remote,
        )
        series = own["series"].get(code, [])
        samples = _month_end_samples(series)
        result: dict[str, Any] = {
            "symbol": code,
            "as_of_date": as_of_date,
            "own_history": {},
            "peers": None,
            "provider_errors": dict(own["provider_errors"]),
            "note": (
                "自身历史为月末样本加最新一天（东方财富，2018 年起）；同行为申万当前成分，"
                "取截止日或之前最近一天的估值；PE 只在盈利样本中排位"
            ),
        }
        current = series[-1] if series else None
        for metric in VALUATION_METRICS:
            points = [
                (row["trade_date"], float(row[metric]))
                for row in samples
                if _valid(row.get(metric))
            ]
            stat = historical_percentile(points, end_date=as_of_date, years=years)
            negative = sum(
                1 for row in samples if row.get(metric) is not None and float(row[metric]) <= 0
            )
            entry: dict[str, Any] = {
                "current": None if current is None else current.get(metric),
                "current_date": None if current is None else current["trade_date"],
            }
            if stat is not None and current is not None and _valid(current.get(metric)):
                entry.update(stat.to_dict())
                entry["median"] = _median([value for _, value in points])
            if negative:
                entry["non_positive_samples"] = negative
            result["own_history"][metric] = entry
        result["peers"] = self._peer_position(code, as_of_date, current, allow_remote, result)
        return result

    def industry_index(
        self,
        symbol: str,
        *,
        level: str = "l1",
        allow_remote: bool = True,
    ) -> tuple[Asset, str] | None:
        """The Shenwan index of a stock's industry at the given level."""

        path = self.sector_data.classify(symbol, allow_remote=allow_remote)
        if path is None:
            return None
        node = getattr(path, level)
        return self.sector_data.index_asset(node.code, allow_remote=allow_remote), f"申万{node.name}"

    def _peer_position(
        self,
        code: str,
        as_of_date: str,
        current: dict[str, Any] | None,
        allow_remote: bool,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        peers = self.sector_data.peers(code, allow_remote=allow_remote)
        if peers is None:
            return None
        symbols = [str(item["symbol"]) for item in peers["peers"]]
        start = (date.fromisoformat(as_of_date) - timedelta(days=_PEER_LOOKBACK_DAYS)).isoformat()
        history = self.cross_section.valuation_history(
            symbols,
            start_date=start,
            end_date=as_of_date,
            allow_remote=allow_remote,
        )
        result["provider_errors"].update(history["provider_errors"])
        latest = {
            symbol: rows[-1] for symbol, rows in history["series"].items() if rows
        }
        metrics = {}
        for metric in VALUATION_METRICS:
            values = [
                float(row[metric]) for row in latest.values() if _valid(row.get(metric))
            ]
            target = None if current is None else current.get(metric)
            entry: dict[str, Any] = {
                "peer_count": len(values),
                "median": _median(values),
                "target": target,
            }
            if _valid(target) and len(values) >= _MIN_PEER_VALUES:
                below = sum(1 for value in values if value <= float(target))
                entry["percentile"] = round(below / len(values), 4)
            elif len(values) < _MIN_PEER_VALUES:
                entry["note"] = f"同行有效样本少于 {_MIN_PEER_VALUES} 个，不给分位"
            if metric == "pe_ttm":
                entry["loss_making_peers"] = sum(
                    1
                    for row in latest.values()
                    if row.get(metric) is not None and float(row[metric]) <= 0
                )
            metrics[metric] = entry
        return {
            "industry": peers["peer_industry"],
            "path": {level: peers["path"][level]["name"] for level in ("l1", "l2", "l3")},
            "peer_symbols": len(symbols),
            "with_valuation": len(latest),
            "metrics": metrics,
        }


def _valid(value: Any) -> bool:
    return value is not None and float(value) > 0


def _month_end_samples(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last row of each month; the current month's last row is the latest day."""

    by_month: dict[str, dict[str, Any]] = {}
    for row in series:
        by_month[row["trade_date"][:7]] = row
    return [by_month[month] for month in sorted(by_month)]


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    value = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return round(value, 4)
