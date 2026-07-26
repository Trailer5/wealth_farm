"""In-memory master asset identity resolver."""

from __future__ import annotations

import re
from collections.abc import Iterable
from uuid import uuid4

from src.data_src.common import infer_a_share_exchange, normalize_a_share_code
from src.data_src.models import SecurityInfo

from .models import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    ASSET_TYPE_UNKNOWN,
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_CREATED,
    RESOLUTION_MATCHED,
    RESOLUTION_UNRESOLVED,
    Asset,
    AssetIdentifier,
    AssetMatchCandidate,
    AssetResolution,
    AssetSourceObservation,
)


class MasterDataResolver:
    """Resolve external source identities to canonical project assets.

    This resolver is intentionally small and in-memory. It defines the matching
    behavior before the module is wired into SQLite persistence.
    """

    def __init__(
        self,
        assets: Iterable[Asset] | None = None,
        identifiers: Iterable[AssetIdentifier] | None = None,
    ) -> None:
        self.assets: dict[str, Asset] = {asset.asset_id: asset for asset in assets or []}
        self.identifiers: list[AssetIdentifier] = list(identifiers or [])

    def resolve_identifier(
        self,
        source: str,
        raw_symbol: str,
        name: str | None = None,
        asset_type_hint: str | None = None,
        market_hint: str | None = None,
        exchange_hint: str | None = None,
        raw: dict[str, object] | None = None,
        create: bool = True,
    ) -> AssetResolution:
        """Resolve a source identity into an existing or newly created asset."""

        standard_symbol = normalize_master_symbol(raw_symbol)
        observation = AssetSourceObservation(
            source=source,
            raw_symbol=raw_symbol,
            standard_symbol=standard_symbol,
            name=name,
            asset_type_hint=asset_type_hint,
            market_hint=market_hint,
            exchange_hint=exchange_hint,
            raw=dict(raw or {}),
        )
        if not standard_symbol:
            return AssetResolution(
                status=RESOLUTION_UNRESOLVED,
                observation=observation,
                needs_review=True,
                reason="missing_or_invalid_symbol",
            )

        asset_type = normalize_asset_type(asset_type_hint)
        exchange = normalize_exchange(exchange_hint)
        if exchange is None and asset_type in {ASSET_TYPE_STOCK, ASSET_TYPE_EXCHANGE_FUND}:
            exchange = infer_exchange_safely(standard_symbol)
        market = normalize_market(market_hint, exchange, asset_type)

        identifier_match = self._find_identifier_match(source, raw_symbol, standard_symbol)
        if identifier_match is not None:
            asset = self.assets.get(identifier_match.asset_id)
            if asset is not None:
                return AssetResolution(
                    status=RESOLUTION_MATCHED,
                    observation=observation,
                    asset=asset,
                    identifier=identifier_match,
                    confidence=identifier_match.confidence,
                    reason="matched_existing_identifier",
                )

        candidates = self._find_asset_candidates(
            standard_symbol=standard_symbol,
            name=name,
            asset_type=asset_type,
            market=market,
            exchange=exchange,
        )
        strong_candidates = [candidate for candidate in candidates if candidate.score >= 0.85]
        if len(strong_candidates) == 1:
            asset = self.assets[strong_candidates[0].asset_id]
            identifier = self._add_identifier(
                asset=asset,
                source=source,
                raw_symbol=raw_symbol,
                standard_symbol=standard_symbol,
                name=name,
                asset_type=asset_type,
                market=market,
                exchange=exchange,
                confidence=strong_candidates[0].score,
            )
            return AssetResolution(
                status=RESOLUTION_MATCHED,
                observation=observation,
                asset=asset,
                candidates=tuple(candidates),
                identifier=identifier,
                confidence=strong_candidates[0].score,
                reason=strong_candidates[0].reason,
            )
        if len(strong_candidates) > 1:
            return AssetResolution(
                status=RESOLUTION_AMBIGUOUS,
                observation=observation,
                candidates=tuple(candidates),
                confidence=strong_candidates[0].score,
                needs_review=True,
                reason="multiple_strong_candidates",
            )

        if not create:
            return AssetResolution(
                status=RESOLUTION_UNRESOLVED,
                observation=observation,
                candidates=tuple(candidates),
                needs_review=True,
                reason="no_match_and_create_disabled",
            )

        asset = Asset(
            asset_id=build_asset_id(),
            asset_type=asset_type,
            standard_symbol=standard_symbol,
            display_name=name,
            market=market,
            exchange=exchange,
        )
        self.assets[asset.asset_id] = asset
        identifier = self._add_identifier(
            asset=asset,
            source=source,
            raw_symbol=raw_symbol,
            standard_symbol=standard_symbol,
            name=name,
            asset_type=asset_type,
            market=market,
            exchange=exchange,
            confidence=0.8,
        )
        return AssetResolution(
            status=RESOLUTION_CREATED,
            observation=observation,
            asset=asset,
            candidates=tuple(candidates),
            identifier=identifier,
            confidence=0.8,
            reason="created_from_observation",
        )

    def ingest_security_info(self, security: SecurityInfo, create: bool = True) -> AssetResolution:
        """Resolve and optionally create an asset from `SecurityInfo`."""

        return self.resolve_identifier(
            source=security.source,
            raw_symbol=security.symbol,
            name=security.name,
            asset_type_hint=security.security_type,
            exchange_hint=security.exchange,
            raw=security.raw,
            create=create,
        )

    def get_asset(self, asset_id: str) -> Asset | None:
        """Return one canonical asset by id."""

        return self.assets.get(asset_id)

    def list_unresolved_observations(self) -> list[AssetSourceObservation]:
        """Return unresolved observations once persistence is added.

        The in-memory resolver returns an empty list because callers receive
        unresolved observations directly in `AssetResolution`.
        """

        return []

    def _find_identifier_match(
        self,
        source: str,
        raw_symbol: str,
        standard_symbol: str,
    ) -> AssetIdentifier | None:
        raw_key = raw_symbol.strip().lower()
        for identifier in self.identifiers:
            if not identifier.active:
                continue
            if identifier.source == source and identifier.raw_symbol.strip().lower() == raw_key:
                return identifier
            if identifier.source == source and identifier.standard_symbol == standard_symbol:
                return identifier
        return None

    def _find_asset_candidates(
        self,
        standard_symbol: str,
        name: str | None,
        asset_type: str,
        market: str | None,
        exchange: str | None,
    ) -> list[AssetMatchCandidate]:
        candidates: list[AssetMatchCandidate] = []
        comparable_name = normalize_name(name)
        for asset in self.assets.values():
            if asset.standard_symbol != standard_symbol:
                continue
            score = 0.7
            reasons = ["same_standard_symbol"]
            if asset.asset_type == asset_type or asset_type == ASSET_TYPE_UNKNOWN:
                score += 0.1
                reasons.append("compatible_asset_type")
            if asset.market == market or market is None or asset.market is None:
                score += 0.05
                reasons.append("compatible_market")
            if asset.exchange == exchange or exchange is None or asset.exchange is None:
                score += 0.05
                reasons.append("compatible_exchange")
            if comparable_name and normalize_name(asset.display_name) == comparable_name:
                score += 0.1
                reasons.append("same_name")
            candidates.append(
                AssetMatchCandidate(
                    asset_id=asset.asset_id,
                    score=min(score, 1.0),
                    reason="+".join(reasons),
                )
            )
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return candidates

    def _add_identifier(
        self,
        asset: Asset,
        source: str,
        raw_symbol: str,
        standard_symbol: str,
        name: str | None,
        asset_type: str,
        market: str | None,
        exchange: str | None,
        confidence: float,
    ) -> AssetIdentifier:
        identifier = AssetIdentifier(
            asset_id=asset.asset_id,
            source=source,
            raw_symbol=raw_symbol,
            standard_symbol=standard_symbol,
            name=name,
            asset_type=asset_type,
            market=market,
            exchange=exchange,
            confidence=confidence,
        )
        self.identifiers.append(identifier)
        return identifier


def normalize_master_symbol(raw_symbol: str) -> str | None:
    """Normalize common stock and fund symbols to a six-digit code."""

    value = raw_symbol.strip()
    if not value:
        return None
    try:
        return normalize_a_share_code(value)
    except ValueError:
        pass
    compact = re.sub(r"[^0-9]", "", value)
    if len(compact) == 6:
        return compact
    return value.upper()


def normalize_asset_type(asset_type_hint: str | None) -> str:
    """Normalize provider asset type hints to project asset types."""

    if asset_type_hint is None:
        return ASSET_TYPE_UNKNOWN
    value = str(asset_type_hint).strip().lower()
    if value in {"stock", "a_share", "a-share", "1", "股票"}:
        return ASSET_TYPE_STOCK
    if "etf" in value or "lof" in value or "封闭" in value or "封基" in value:
        return ASSET_TYPE_EXCHANGE_FUND
    if "基金" in value or "混合" in value or "债券" in value or "指数" in value:
        return ASSET_TYPE_OPEN_FUND
    return ASSET_TYPE_UNKNOWN


def normalize_exchange(exchange_hint: str | None) -> str | None:
    """Normalize exchange hints to short lowercase exchange codes."""

    if exchange_hint is None:
        return None
    value = str(exchange_hint).strip().lower()
    if value in {"sh", "sse", "上海", "上交所"}:
        return "sh"
    if value in {"sz", "szse", "深圳", "深交所"}:
        return "sz"
    if value in {"bj", "bse", "北京", "北交所"}:
        return "bj"
    return value or None


def normalize_market(
    market_hint: str | None,
    exchange: str | None,
    asset_type: str,
) -> str | None:
    """Normalize market hints, falling back to A-share for known exchanges."""

    if market_hint:
        return str(market_hint).strip().lower()
    if asset_type == ASSET_TYPE_OPEN_FUND:
        return "cn_fund"
    if exchange in {"sh", "sz", "bj"}:
        return "cn_a"
    return None


def infer_exchange_safely(standard_symbol: str) -> str | None:
    """Infer exchange for six-digit mainland market symbols when possible."""

    try:
        return infer_a_share_exchange(standard_symbol)
    except ValueError:
        return None


def normalize_name(name: str | None) -> str | None:
    """Normalize display names for conservative equality checks."""

    if name is None:
        return None
    compact = re.sub(r"\s+", "", str(name)).strip().lower()
    return compact or None


def build_asset_id() -> str:
    """Build an opaque asset id that remains stable as metadata changes."""

    return f"asset:{uuid4()}"
