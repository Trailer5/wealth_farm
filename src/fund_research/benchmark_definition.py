"""Resolve official open-fund benchmark text into weighted components."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from src.benchmark_analysis.models import (
    BenchmarkComponent,
    BenchmarkComponentKind,
    CompositeBenchmarkDefinition,
)
from src.benchmark_analysis.sqlite_repository import (
    SQLiteBenchmarkRepository,
)
from src.master_data import ASSET_TYPE_INDEX, Asset, MasterDataService

from .models import (
    FundAssetRelation,
    FundRelationType,
    OpenFundProfileSnapshot,
)
from .sqlite_repository import SQLiteFundResearchRepository


PBOC_DEMAND_DEPOSIT_RATE_URL = (
    "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/"
    "125440/125838/125888/2968982/index.html"
)


@dataclass(frozen=True)
class _IndexCatalogEntry:
    canonical_name: str
    asset_id: str
    symbol: str
    display_name: str
    market: str
    exchange: str
    source: str
    kind: BenchmarkComponentKind = BenchmarkComponentKind.INDEX


_INDEX_CATALOG = {
    "沪深300指数": _IndexCatalogEntry(
        canonical_name="沪深300指数",
        asset_id="index:cn_a:sh:000300",
        symbol="000300",
        display_name="沪深300指数",
        market="cn_a",
        exchange="sh",
        source=(
            "https://www.csindex.com.cn/zh-CN/indices/"
            "index-detail/000300"
        ),
    ),
    "中证360互联网+大数据100指数": _IndexCatalogEntry(
        canonical_name="中证360互联网+大数据100指数",
        asset_id="index:cn_a:csi:930734",
        symbol="930734",
        display_name="中证360互联网+大数据100指数",
        market="cn_a",
        exchange="csi",
        source=(
            "https://www.csindex.com.cn/zh-CN/indices/"
            "index-detail/930734"
        ),
    ),
    "恒生指数": _IndexCatalogEntry(
        canonical_name="恒生指数",
        asset_id="index:hk:hsi:HSI",
        symbol="HSI",
        display_name="恒生指数",
        market="hk",
        exchange="hsi",
        source=(
            "https://www.hsi.com.hk/static/uploads/contents/"
            "zh_hk/dl_centre/factsheets/hsic.pdf"
        ),
    ),
}

_CASH_RATE_ALIASES = {
    "商业银行活期存款利率(税后)",
    "商业银行税后活期存款利率",
    "银行活期存款利率(税后)",
}


class OpenFundBenchmarkParser:
    """Parse weighted components without guessing unknown index codes."""

    def parse(
        self,
        snapshot: OpenFundProfileSnapshot,
    ) -> CompositeBenchmarkDefinition:
        text = _normalize_text(snapshot.benchmark_text)
        matches = list(
            re.finditer(
                r"(.+?)(?:收益率)?[*×]"
                r"(\d+(?:\.\d+)?)%",
                text,
            )
        )
        if not matches:
            raise ValueError(
                "Official benchmark text has no weighted components"
            )
        components = []
        errors = []
        for match in matches:
            label = match.group(1).lstrip("+")
            weight = Decimal(match.group(2)) / Decimal("100")
            if label in _CASH_RATE_ALIASES:
                components.append(
                    BenchmarkComponent(
                        label=label,
                        kind=BenchmarkComponentKind.CASH_RATE,
                        weight=weight,
                        source=PBOC_DEMAND_DEPOSIT_RATE_URL,
                        annual_rate=Decimal("0.0035"),
                        rate_effective_date="2015-10-24",
                    )
                )
                continue
            entry = _INDEX_CATALOG.get(label)
            if entry is None:
                kind = (
                    BenchmarkComponentKind.TOTAL_RETURN_INDEX
                    if "全收益指数" in label
                    else BenchmarkComponentKind.INDEX
                )
                components.append(
                    BenchmarkComponent(
                        label=label,
                        kind=kind,
                        weight=weight,
                        source=snapshot.source_url,
                    )
                )
                errors.append(f"unresolved_index:{label}")
                continue
            components.append(
                BenchmarkComponent(
                    label=label,
                    kind=entry.kind,
                    weight=weight,
                    source=entry.source,
                    index_asset_id=entry.asset_id,
                )
            )
        return CompositeBenchmarkDefinition(
            asset_id=snapshot.fund_asset_id,
            effective_date=snapshot.effective_date,
            source=snapshot.source,
            source_document_id=snapshot.source_document_id,
            benchmark_text=snapshot.benchmark_text,
            components=tuple(components),
            resolution_errors=tuple(errors),
        )


class OpenFundBenchmarkResolver:
    """Register verified component identities and persist one definition."""

    def __init__(
        self,
        *,
        master_data: MasterDataService,
        benchmark_repository: SQLiteBenchmarkRepository,
        fund_repository: SQLiteFundResearchRepository | None = None,
        parser: OpenFundBenchmarkParser | None = None,
    ) -> None:
        self.master_data = master_data
        self.benchmark_repository = benchmark_repository
        self.fund_repository = fund_repository
        self.parser = parser or OpenFundBenchmarkParser()
        self.benchmark_repository.initialize()
        if self.fund_repository is not None:
            self.fund_repository.initialize()

    def resolve(
        self,
        snapshot: OpenFundProfileSnapshot,
    ) -> CompositeBenchmarkDefinition:
        definition = self.parser.parse(snapshot)
        components = tuple(
            self._resolve_component(component)
            for component in definition.components
        )
        errors = tuple(
            f"unresolved_index:{component.label}"
            for component in components
            if (
                component.kind
                is not BenchmarkComponentKind.CASH_RATE
                and component.index_asset_id is None
            )
        )
        resolved = replace(
            definition,
            components=components,
            resolution_errors=errors,
        )
        self.benchmark_repository.save_definition(resolved)
        self._save_underlying_relation(resolved)
        return resolved

    def _resolve_component(
        self,
        component: BenchmarkComponent,
    ) -> BenchmarkComponent:
        if (
            component.kind is BenchmarkComponentKind.CASH_RATE
            or component.index_asset_id is None
        ):
            return component
        entry = next(
            (
                item
                for item in _INDEX_CATALOG.values()
                if item.asset_id == component.index_asset_id
            ),
            None,
        )
        if entry is None:
            return replace(component, index_asset_id=None)
        candidates = [
            asset
            for asset in self.master_data.find_assets(
                standard_symbol=entry.symbol,
                asset_type=ASSET_TYPE_INDEX,
            )
            if (
                asset.market == entry.market
                and asset.exchange == entry.exchange
            )
        ]
        if len(candidates) > 1:
            return replace(component, index_asset_id=None)
        if candidates:
            asset = candidates[0]
        else:
            asset = self.master_data.register_asset(
                Asset(
                    asset_id=entry.asset_id,
                    asset_type=ASSET_TYPE_INDEX,
                    standard_symbol=entry.symbol,
                    display_name=entry.display_name,
                    market=entry.market,
                    exchange=entry.exchange,
                ),
                source=entry.source,
                raw_symbol=entry.symbol,
            )
        return replace(component, index_asset_id=asset.asset_id)

    def _save_underlying_relation(
        self,
        definition: CompositeBenchmarkDefinition,
    ) -> None:
        if self.fund_repository is None or not definition.resolved:
            return
        index_components = [
            component
            for component in definition.components
            if component.kind is not BenchmarkComponentKind.CASH_RATE
        ]
        if len(index_components) != 1:
            return
        component = index_components[0]
        assert component.index_asset_id is not None
        self.fund_repository.save_relation(
            FundAssetRelation(
                fund_asset_id=definition.asset_id,
                relation_type=FundRelationType.UNDERLYING_INDEX,
                related_name=component.label,
                related_asset_id=component.index_asset_id,
                source=definition.source,
                effective_date=definition.effective_date,
                confidence=1.0,
                raw={
                    "benchmark_text": definition.benchmark_text,
                    "weight": str(component.weight),
                    "source_document_id": definition.source_document_id,
                },
            )
        )


def _normalize_text(value: str) -> str:
    return (
        re.sub(r"\s+", "", value)
        .replace("＋", "+")
        .replace("＊", "*")
        .replace("（", "(")
        .replace("）", ")")
    )
