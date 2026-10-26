"""Reconstruct SZSE point-in-time short names for historical discovery."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from src.research_contracts import PROFILE_ORDINARY_COMPANY

from .models import AssetNameSnapshot


def build_shenzhen_name_snapshots(
    *,
    catalog: list[dict[str, object]],
    name_changes: list[dict[str, object]],
    event_date: str,
) -> tuple[AssetNameSnapshot, ...]:
    target = date.fromisoformat(event_date)
    changes_by_symbol = defaultdict(list)
    for record in name_changes:
        symbol = str(record.get("symbol") or "")
        change_date = str(record.get("change_date") or "")
        old_name = str(record.get("old_name") or "").strip()
        new_name = str(record.get("new_name") or "").strip()
        if symbol and change_date and old_name and new_name:
            changes_by_symbol[symbol].append(
                (
                    date.fromisoformat(change_date),
                    old_name,
                    new_name,
                    str(record.get("source") or ""),
                )
            )
    snapshots = []
    for record in catalog:
        symbol = str(record.get("symbol") or "")
        listed_at = str(record.get("listed_at") or "")
        if not symbol or not listed_at:
            continue
        listed = date.fromisoformat(listed_at)
        delisted_value = record.get("delisted_at")
        delisted = (
            date.fromisoformat(str(delisted_value))
            if delisted_value
            else None
        )
        if target < listed or (
            delisted is not None and target > delisted
        ):
            continue
        changes = sorted(changes_by_symbol.get(symbol, []))
        name = str(record.get("name") or "").strip()
        effective_from = listed
        effective_to = delisted
        sources = [str(record.get("source") or "")]
        if changes:
            name = changes[0][1]
            for index, (
                change_date,
                old_name,
                new_name,
                source,
            ) in enumerate(changes):
                if target < change_date:
                    effective_to = min(
                        (
                            change_date - timedelta(days=1),
                            effective_to,
                        )
                        if effective_to is not None
                        else (change_date - timedelta(days=1),)
                    )
                    break
                name = new_name
                effective_from = change_date
                sources.append(source)
                next_change = (
                    changes[index + 1][0]
                    if index + 1 < len(changes)
                    else None
                )
                effective_to = (
                    next_change - timedelta(days=1)
                    if next_change is not None
                    else delisted
                )
        if not name:
            continue
        snapshots.append(
            AssetNameSnapshot(
                asset_id=f"asset:stock:sz:{symbol}",
                standard_symbol=symbol,
                name=name,
                effective_from=effective_from.isoformat(),
                effective_to=(
                    effective_to.isoformat()
                    if effective_to is not None
                    else None
                ),
                aliases=(),
                profile_id=PROFILE_ORDINARY_COMPANY,
                source="|".join(
                    value
                    for value in dict.fromkeys(sources)
                    if value
                ),
            )
        )
    return tuple(
        sorted(snapshots, key=lambda item: item.standard_symbol)
    )
