"""Read asset-oriented Git-friendly snapshots from the daily_data submodule."""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.master_data import Asset

from .models import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    DataCoverage,
)


class DailyDataFormatError(ValueError):
    """Raised when a daily_data file violates its declared structure."""


class DailyDataReader:
    """Read monthly asset files and dataset coverage manifests."""

    def __init__(self, root: str | Path = "data/daily_data") -> None:
        self.root = Path(root)

    def get_asset(
        self,
        *,
        market: str,
        exchange: str,
        standard_symbol: str,
    ) -> Asset | None:
        """Read canonical asset metadata from one asset directory."""

        asset_dir = self._asset_dir(market, exchange, standard_symbol)
        path = asset_dir / "asset.json"
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise DailyDataFormatError(
                f"Unsupported asset schema_version in {path}"
            )
        for field, expected in (
            ("market", market),
            ("exchange", exchange),
            ("standard_symbol", standard_symbol),
        ):
            if payload.get(field) != expected:
                raise DailyDataFormatError(f"{field} mismatch in {path}")
        asset_id = str(payload.get("asset_id", "")).strip()
        asset_type = str(payload.get("asset_type", "")).strip()
        if not asset_id or not asset_type:
            raise DailyDataFormatError(
                f"{path} must contain asset_id and asset_type"
            )
        return Asset(
            asset_id=asset_id,
            asset_type=asset_type,
            standard_symbol=standard_symbol,
            display_name=payload.get("display_name"),
            market=market,
            exchange=exchange,
            status=str(payload.get("status") or "active"),
        )

    def get_records(
        self,
        asset: Asset,
        dataset: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Read one supported dataset across all intersecting month files."""

        if dataset not in {
            DATASET_STOCK_DAILY_BARS,
            DATASET_EXCHANGE_FUND_DAILY_BARS,
        }:
            raise ValueError(f"Unsupported daily_data dataset: {dataset}")
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        records: list[dict[str, Any]] = []
        for year, month in _iter_months(start, end):
            path = self._dataset_dir(asset, "daily_bars") / f"{year:04d}" / f"{month:02d}.csv"
            if not path.exists():
                continue
            records.extend(self._read_daily_bars_file(path, asset, start_date, end_date))
        records.sort(key=lambda record: (str(record["trade_date"]), str(record["source"])))
        return records

    def find_fresh_coverage(
        self,
        *,
        asset: Asset,
        dataset: str,
        start_date: str,
        end_date: str,
        max_age_seconds: int | None,
        now: datetime | None = None,
    ) -> DataCoverage | None:
        """Return a manifest coverage entry containing the requested range."""

        manifest_path = self._manifest_path(asset, dataset)
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            raise DailyDataFormatError(
                f"Unsupported manifest schema_version in {manifest_path}"
            )
        if manifest.get("dataset") != dataset:
            raise DailyDataFormatError(f"Dataset mismatch in {manifest_path}")
        manifest_asset_id = manifest.get("asset_id")
        if manifest_asset_id and manifest_asset_id != asset.asset_id:
            raise DailyDataFormatError(f"Asset mismatch in {manifest_path}")

        threshold = None
        if max_age_seconds is not None:
            threshold = (now or datetime.now(timezone.utc)) - timedelta(
                seconds=max_age_seconds
            )
        candidates: list[DataCoverage] = []
        for item in manifest.get("coverage", []):
            if item.get("range_start", "") > start_date:
                continue
            if item.get("range_end", "") < end_date:
                continue
            checked_at = str(item.get("checked_at", ""))
            if threshold is not None and _parse_datetime(checked_at) < threshold:
                continue
            candidates.append(
                DataCoverage(
                    asset_id=asset.asset_id,
                    dataset=dataset,
                    backend="daily_data",
                    range_start=str(item["range_start"]),
                    range_end=str(item["range_end"]),
                    source=str(item["source"]),
                    checked_at=checked_at,
                )
            )
        if not candidates:
            return None
        candidates.sort(key=lambda coverage: coverage.checked_at, reverse=True)
        return candidates[0]

    def _manifest_path(self, asset: Asset, dataset: str) -> Path:
        dataset_dir = (
            "daily_bars"
            if dataset in {
                DATASET_STOCK_DAILY_BARS,
                DATASET_EXCHANGE_FUND_DAILY_BARS,
            }
            else dataset
        )
        return self._dataset_dir(asset, dataset_dir) / "manifest.json"

    def _dataset_dir(self, asset: Asset, dataset_dir: str) -> Path:
        return self._asset_dir(
            asset.market or "unknown_market",
            asset.exchange or "none",
            asset.standard_symbol,
        ) / _safe_path_part(dataset_dir)

    def _asset_dir(
        self,
        market: str,
        exchange: str,
        standard_symbol: str,
    ) -> Path:
        return (
            self.root
            / "assets"
            / _safe_path_part(market)
            / _safe_path_part(exchange)
            / _safe_path_part(standard_symbol)
        )

    def _read_daily_bars_file(
        self,
        path: Path,
        asset: Asset,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            required = {"trade_date", "source"}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise DailyDataFormatError(
                    f"{path} must contain columns: {sorted(required)}"
                )
            records: list[dict[str, Any]] = []
            for row in reader:
                trade_date = str(row.get("trade_date", "")).strip()
                if trade_date < start_date or trade_date > end_date:
                    continue
                symbol = str(row.get("symbol") or asset.standard_symbol).strip()
                if symbol != asset.standard_symbol:
                    raise DailyDataFormatError(
                        f"Unexpected symbol {symbol} in {path}"
                    )
                records.append(
                    {
                        "symbol": symbol,
                        "trade_date": trade_date,
                        "open": _optional_float(row.get("open")),
                        "high": _optional_float(row.get("high")),
                        "low": _optional_float(row.get("low")),
                        "close": _optional_float(row.get("close")),
                        "volume": _optional_float(row.get("volume")),
                        "amount": _optional_float(row.get("amount")),
                        "source": str(row.get("source", "")).strip(),
                        "raw_json": row.get("raw_json") or None,
                    }
                )
        return records


def _iter_months(start: date, end: date):
    year = start.year
    month = start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _safe_path_part(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"Unsafe daily_data path part: {value}")
    return value


def _parse_datetime(value: str) -> datetime:
    if not value:
        raise DailyDataFormatError("Coverage checked_at must not be empty")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)
