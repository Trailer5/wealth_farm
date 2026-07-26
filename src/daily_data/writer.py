"""Deterministic writer for Git-friendly daily market data."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_STOCK_DAILY_BARS,
    DataAccessResponse,
)
from src.data_quality import QualitySummary
from src.master_data import Asset

from .models import DailyDataWriteResult


DAILY_BAR_FIELDS = (
    "symbol",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "source",
    "raw_json",
)


class DailyDataPublishError(ValueError):
    """Raised when records are not safe to publish."""


class DailyDataWriter:
    """Write one complete, quality-approved daily-bar response."""

    def __init__(self, root: str | Path = "data/daily_data") -> None:
        self.root = Path(root)

    def publish_daily_bars(
        self,
        response: DataAccessResponse,
        quality: QualitySummary,
        *,
        checked_at: str | None = None,
    ) -> DailyDataWriteResult:
        self._validate_publishable(response, quality)
        checked = checked_at or _utc_now()
        asset = response.asset
        changed_paths: list[str] = []

        asset_path = self._asset_dir(asset) / "asset.json"
        asset_payload: dict[str, object] = {
            "schema_version": 1,
            "asset_id": asset.asset_id,
            "asset_type": asset.asset_type,
            "standard_symbol": asset.standard_symbol,
            "market": asset.market,
            "exchange": asset.exchange,
            "status": asset.status,
        }
        if asset.display_name:
            asset_payload["display_name"] = asset.display_name
        if _write_json_if_changed(asset_path, asset_payload):
            changed_paths.append(self._relative(asset_path))

        records_by_month: dict[tuple[int, int], list[dict[str, str]]] = {}
        for record in response.records:
            normalized = _normalize_daily_bar(record, asset)
            trade_date = date.fromisoformat(normalized["trade_date"])
            records_by_month.setdefault(
                (trade_date.year, trade_date.month),
                [],
            ).append(normalized)
        for (year, month), records in sorted(records_by_month.items()):
            csv_path = (
                self._dataset_dir(asset)
                / f"{year:04d}"
                / f"{month:02d}.csv"
            )
            existing = _read_existing_csv(csv_path)
            merged = {
                record["trade_date"]: record
                for record in existing
            }
            for record in records:
                merged[record["trade_date"]] = record
            ordered = [merged[key] for key in sorted(merged)]
            if _write_csv_if_changed(csv_path, ordered):
                changed_paths.append(self._relative(csv_path))

        manifest_path = self._dataset_dir(asset) / "manifest.json"
        manifest = self._load_manifest(
            manifest_path,
            asset,
            response.dataset,
        )
        source = ",".join(response.sources) or "unknown"
        manifest["coverage"] = _merge_coverage(
            list(manifest["coverage"]),
            {
                "range_start": str(response.coverage_start),
                "range_end": str(response.coverage_end),
                "source": source,
                "checked_at": checked,
            },
        )
        if _write_json_if_changed(manifest_path, manifest):
            changed_paths.append(self._relative(manifest_path))
        return DailyDataWriteResult(
            asset_id=asset.asset_id,
            dataset=response.dataset,
            input_record_count=len(response.records),
            changed_paths=tuple(sorted(changed_paths)),
            coverage_start=str(response.coverage_start),
            coverage_end=str(response.coverage_end),
        )

    def latest_coverage_end(
        self,
        asset: Asset,
        dataset: str,
    ) -> str | None:
        manifest_path = self._dataset_dir(asset) / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = self._load_manifest(
            manifest_path,
            asset,
            dataset,
        )
        ends = [
            str(item.get("range_end", ""))
            for item in manifest["coverage"]
            if item.get("range_end")
        ]
        return max(ends) if ends else None

    def _validate_publishable(
        self,
        response: DataAccessResponse,
        quality: QualitySummary,
    ) -> None:
        if response.dataset not in {
            DATASET_STOCK_DAILY_BARS,
            DATASET_EXCHANGE_FUND_DAILY_BARS,
        }:
            raise DailyDataPublishError(
                f"Unsupported daily_data dataset: {response.dataset}"
            )
        if not response.complete:
            raise DailyDataPublishError(
                "Incomplete data access response cannot be published"
            )
        if not response.records:
            raise DailyDataPublishError("Empty response cannot be published")
        if not quality.can_publish:
            raise DailyDataPublishError(
                f"Quality gate blocked publication: "
                f"{quality.fail_count} failed checks"
            )
        if quality.run.asset_id != response.asset.asset_id:
            raise DailyDataPublishError(
                "Quality task asset does not match data response"
            )
        if not response.coverage_start or not response.coverage_end:
            raise DailyDataPublishError(
                "Complete response must include coverage"
            )
        if not response.asset.market or not response.asset.exchange:
            raise DailyDataPublishError(
                "Asset market and exchange are required"
            )

    def _load_manifest(
        self,
        path: Path,
        asset: Asset,
        dataset: str,
    ) -> dict[str, Any]:
        if not path.exists():
            return {
                "schema_version": 1,
                "dataset": dataset,
                "asset_id": asset.asset_id,
                "coverage": [],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise DailyDataPublishError(
                f"Unsupported manifest schema_version: {path}"
            )
        if payload.get("dataset") != dataset:
            raise DailyDataPublishError(f"Dataset mismatch: {path}")
        if payload.get("asset_id") != asset.asset_id:
            raise DailyDataPublishError(f"Asset mismatch: {path}")
        if not isinstance(payload.get("coverage"), list):
            raise DailyDataPublishError(
                f"Manifest coverage must be a list: {path}"
            )
        return payload

    def _dataset_dir(self, asset: Asset) -> Path:
        return self._asset_dir(asset) / "daily_bars"

    def _asset_dir(self, asset: Asset) -> Path:
        return (
            self.root
            / "assets"
            / _safe_part(str(asset.market))
            / _safe_part(str(asset.exchange))
            / _safe_part(asset.standard_symbol)
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _normalize_daily_bar(
    record: dict[str, object],
    asset: Asset,
) -> dict[str, str]:
    trade_date = str(record.get("trade_date") or "").strip()
    try:
        date.fromisoformat(trade_date)
    except ValueError as exc:
        raise DailyDataPublishError(
            f"Invalid trade_date: {trade_date}"
        ) from exc
    symbol = str(
        record.get("symbol") or asset.standard_symbol
    ).strip()
    if symbol != asset.standard_symbol:
        raise DailyDataPublishError(
            f"Record symbol {symbol} does not match {asset.standard_symbol}"
        )
    source = str(record.get("source") or "").strip()
    if not source:
        raise DailyDataPublishError(
            f"Record source is required for {trade_date}"
        )
    raw_json = record.get("raw_json")
    if isinstance(raw_json, (dict, list)):
        raw_text = json.dumps(
            raw_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    else:
        raw_text = str(raw_json or "")
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "open": _format_number(record.get("open")),
        "high": _format_number(record.get("high")),
        "low": _format_number(record.get("low")),
        "close": _format_number(record.get("close")),
        "volume": _format_number(record.get("volume")),
        "amount": _format_number(record.get("amount")),
        "source": source,
        "raw_json": raw_text,
    }


def _read_existing_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != DAILY_BAR_FIELDS:
            raise DailyDataPublishError(
                f"Unexpected daily bar columns: {path}"
            )
        return [
            {
                field: str(row.get(field) or "")
                for field in DAILY_BAR_FIELDS
            }
            for row in reader
        ]


def _write_csv_if_changed(
    path: Path,
    rows: list[dict[str, str]],
) -> bool:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=DAILY_BAR_FIELDS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return _write_text_if_changed(path, buffer.getvalue())


def _write_json_if_changed(path: Path, payload: object) -> bool:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    return _write_text_if_changed(path, text)


def _write_text_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return True


def _merge_coverage(
    existing: list[dict[str, object]],
    new_item: dict[str, str],
) -> list[dict[str, str]]:
    new_start = date.fromisoformat(new_item["range_start"])
    new_end = date.fromisoformat(new_item["range_end"])
    if new_start > new_end:
        raise DailyDataPublishError(
            "Coverage start must not be after end"
        )
    merged_item = dict(new_item)
    remaining: list[dict[str, str]] = []
    for raw in existing:
        item = {
            "range_start": str(raw.get("range_start") or ""),
            "range_end": str(raw.get("range_end") or ""),
            "source": str(raw.get("source") or ""),
            "checked_at": str(raw.get("checked_at") or ""),
        }
        try:
            item_start = date.fromisoformat(item["range_start"])
            item_end = date.fromisoformat(item["range_end"])
        except ValueError as exc:
            raise DailyDataPublishError(
                "Existing coverage contains invalid dates"
            ) from exc
        touches = (
            item_start <= new_end + timedelta(days=1)
            and new_start <= item_end + timedelta(days=1)
        )
        if item["source"] == new_item["source"] and touches:
            new_start = min(new_start, item_start)
            new_end = max(new_end, item_end)
            merged_item["range_start"] = new_start.isoformat()
            merged_item["range_end"] = new_end.isoformat()
            merged_item["checked_at"] = max(
                merged_item["checked_at"],
                item["checked_at"],
            )
        else:
            remaining.append(item)
    remaining.append(merged_item)
    remaining.sort(
        key=lambda item: (
            item["range_start"],
            item["range_end"],
            item["source"],
        )
    )
    return remaining


def _safe_part(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise DailyDataPublishError(
            f"Unsafe daily_data path part: {value}"
        )
    return value


def _format_number(value: object) -> str:
    if value is None or value == "":
        return ""
    number = float(value)
    return format(number, ".15g")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
