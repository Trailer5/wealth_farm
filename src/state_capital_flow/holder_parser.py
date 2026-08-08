"""Parser for official ETF holder tables with row-level failure isolation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from src.data_src.models import DocumentTable

from .models import StateHolderEvidence
from .registry import DEFAULT_STATE_ENTITY_REGISTRY, StateEntityRegistry


_HOLDER_FIELDS = ("holder_name", "持有人名称", "持有人")
_SHARE_FIELDS = (
    "holding_shares",
    "持有份额",
    "持有基金份额",
    "持股数量",
)
_RATIO_FIELDS = (
    "holding_ratio",
    "占总份额比例",
    "占上市总份额比例",
    "持股比例",
)


class OfficialHolderTableParser:
    """Extract registered entities without inferring identities."""

    def __init__(
        self,
        registry: StateEntityRegistry = DEFAULT_STATE_ENTITY_REGISTRY,
    ) -> None:
        self.registry = registry

    def parse(
        self,
        rows: Sequence[Mapping[str, Any]],
        instrument_asset_id: str,
        report_period: str,
        holding_date: str,
        published_at: str,
        source: str,
        source_url: str,
    ) -> list[StateHolderEvidence]:
        _validate_context(
            instrument_asset_id,
            report_period,
            holding_date,
            published_at,
            source,
            source_url,
        )
        evidence: list[StateHolderEvidence] = []
        seen_ids: set[str] = set()
        for row in rows:
            try:
                if not isinstance(row, Mapping):
                    continue
                raw_holder = _first_value(row, _HOLDER_FIELDS)
                entity = self.registry.match(str(raw_holder))
                if entity is None:
                    continue
                raw_shares = _first_value(row, _SHARE_FIELDS)
                holding_shares = _decimal_value(raw_shares, "holding_shares")
                if holding_shares < 0:
                    raise ValueError("holding_shares must be non-negative")
                raw_ratio = _optional_first_value(row, _RATIO_FIELDS)
                holding_ratio, normalized_percent = _ratio_value(raw_ratio)
                parameters = {
                    "raw_holder_name": str(raw_holder),
                    "raw_holding_shares": str(raw_shares),
                    "raw_holding_ratio": (
                        None if raw_ratio is None else str(raw_ratio)
                    ),
                    "holding_ratio_normalized_from_percent": normalized_percent,
                    "table_index": row.get("_table_index"),
                    "row_index": row.get("_row_index"),
                    "page_number": row.get("_page_number"),
                }
                evidence_id = _evidence_id(
                    entity.entity_id,
                    instrument_asset_id,
                    report_period,
                    holding_date,
                    published_at,
                    holding_shares,
                    holding_ratio,
                    source,
                    source_url,
                )
                if evidence_id in seen_ids:
                    continue
                evidence.append(
                    StateHolderEvidence(
                        evidence_id=evidence_id,
                        entity_id=entity.entity_id,
                        instrument_asset_id=instrument_asset_id,
                        report_period=report_period,
                        holding_date=holding_date,
                        published_at=published_at,
                        holding_shares=holding_shares,
                        holding_ratio=holding_ratio,
                        source_tier="official",
                        source=source,
                        source_url=source_url,
                        parameters=parameters,
                    )
                )
                seen_ids.add(evidence_id)
            except (InvalidOperation, TypeError, ValueError):
                continue
        return evidence

    def parse_document_tables(
        self,
        tables: Sequence[DocumentTable],
        instrument_asset_id: str,
        report_period: str,
        holding_date: str,
        published_at: str,
        source: str,
        source_url: str,
    ) -> list[StateHolderEvidence]:
        """Extract registered holders from official PDF tables."""

        rows: list[dict[str, Any]] = []
        for table in tables:
            if table.status not in {"extracted", "success"}:
                continue
            header_index, headers = _holder_headers(table.rows)
            if header_index is None:
                continue
            for row_index, values in enumerate(
                table.rows[header_index + 1 :],
                start=header_index + 1,
            ):
                compact_values = [
                    value
                    for value in values
                    if value is not None and str(value).strip()
                ]
                row = {
                    header: compact_values[index]
                    for index, header in enumerate(headers)
                    if index < len(compact_values)
                }
                row["_table_index"] = table.table_index
                row["_row_index"] = row_index
                row["_page_number"] = table.page_number
                rows.append(row)
        return self.parse(
            rows,
            instrument_asset_id,
            report_period,
            holding_date,
            published_at,
            source,
            source_url,
        )


def _validate_context(
    instrument_asset_id: str,
    report_period: str,
    holding_date: str,
    published_at: str,
    source: str,
    source_url: str,
) -> None:
    for value, name in (
        (instrument_asset_id, "instrument_asset_id"),
        (report_period, "report_period"),
        (source, "source"),
        (source_url, "source_url"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must not be blank")
    holding = date.fromisoformat(holding_date)
    published = date.fromisoformat(published_at)
    if published < holding:
        raise ValueError("published_at must not be earlier than holding_date")


def _first_value(row: Mapping[str, Any], fields: tuple[str, ...]) -> Any:
    value = _optional_first_value(row, fields)
    if value is None:
        raise ValueError(f"missing required field: {fields[0]}")
    return value


def _optional_first_value(
    row: Mapping[str, Any],
    fields: tuple[str, ...],
) -> Any | None:
    for field_name in fields:
        if field_name in row:
            value = row[field_name]
            if value is not None and str(value).strip():
                return value
    return None


def _holder_headers(
    rows: Sequence[Sequence[str | None]],
) -> tuple[int | None, tuple[str, ...]]:
    for row_index, row in enumerate(rows):
        headers = tuple(
            header
            for header in (
                _normalize_header(value) for value in row
            )
            if header
        )
        if (
            any(header in _HOLDER_FIELDS for header in headers)
            and any(header in _SHARE_FIELDS for header in headers)
        ):
            return row_index, headers
    return None, ()


def _normalize_header(value: str | None) -> str:
    text = str(value or "")
    for character in (
        "\n",
        "\r",
        " ",
        "　",
        "（份）",
        "(份)",
        "（%）",
        "(%)",
    ):
        text = text.replace(character, "")
    return text


def _decimal_value(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be numeric")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int, float)):
        text = str(value).strip().replace(",", "").replace("，", "")
        result = Decimal(text)
    else:
        raise TypeError(f"{field_name} must be numeric")
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return result


def _ratio_value(value: Any | None) -> tuple[Decimal | None, bool]:
    if value is None:
        return None, False
    text = str(value).strip()
    has_percent_sign = text.endswith("%") or text.endswith("％")
    if has_percent_sign:
        text = text[:-1].strip()
    ratio = _decimal_value(text, "holding_ratio")
    normalized = has_percent_sign or ratio > 1
    if normalized:
        ratio /= Decimal("100")
    if ratio < 0 or ratio > 1:
        raise ValueError("holding_ratio must normalize into [0, 1]")
    return ratio, normalized


def _evidence_id(
    entity_id: str,
    instrument_asset_id: str,
    report_period: str,
    holding_date: str,
    published_at: str,
    holding_shares: Decimal,
    holding_ratio: Decimal | None,
    source: str,
    source_url: str,
) -> str:
    payload = "\x1f".join(
        (
            entity_id,
            instrument_asset_id,
            report_period,
            holding_date,
            published_at,
            str(holding_shares),
            "" if holding_ratio is None else str(holding_ratio),
            source,
            source_url,
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"state-holder:{digest}"
