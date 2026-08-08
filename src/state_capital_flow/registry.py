"""Versioned exact-name registry for disclosed state entities."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .models import StateEntity


STATE_ENTITY_REGISTRY_VERSION = "state_entity_registry_v1"

CORE_STATE_ENTITIES = (
    StateEntity(
        entity_id="state:central_huijin_investment",
        legal_name="中央汇金投资有限责任公司",
        aliases=("中央汇金投资公司", "中央汇金"),
        group="stabilization_core",
        source_url="https://www.huijin-inv.cn/",
        effective_date="2003-12-16",
    ),
    StateEntity(
        entity_id="state:central_huijin_asset_management",
        legal_name="中央汇金资产管理有限责任公司",
        aliases=("中央汇金资产管理公司", "汇金资管"),
        group="stabilization_core",
        source_url="https://www.huijin-inv.cn/",
        effective_date="2015-11-06",
    ),
    StateEntity(
        entity_id="state:china_securities_finance",
        legal_name="中国证券金融股份有限公司",
        aliases=("中国证券金融公司", "证金公司", "中证金"),
        group="stabilization_core",
        source_url="https://www.csf.com.cn/",
        effective_date="2011-10-28",
    ),
)

LONG_TERM_STATE_ENTITIES = (
    StateEntity(
        entity_id="state:national_social_security_fund_council",
        legal_name="全国社会保障基金理事会",
        aliases=("社保基金会",),
        group="state_long_term",
        source_url="https://www.ssf.gov.cn/",
        effective_date="2000-08-01",
    ),
    StateEntity(
        entity_id="state:basic_pension_insurance_fund",
        legal_name="基本养老保险基金",
        aliases=("基本养老基金", "养老保险基金"),
        group="state_long_term",
        source_url="https://www.mohrss.gov.cn/",
        effective_date="2015-08-17",
    ),
)

DEFAULT_STATE_ENTITIES = CORE_STATE_ENTITIES + LONG_TERM_STATE_ENTITIES


class StateEntityRegistry:
    """Resolve only normalized exact legal names or registered aliases."""

    version = STATE_ENTITY_REGISTRY_VERSION

    def __init__(
        self,
        entities: Iterable[StateEntity] = DEFAULT_STATE_ENTITIES,
    ) -> None:
        self._entities = tuple(entities)
        if not self._entities:
            raise ValueError("registry must contain at least one entity")
        by_id: dict[str, StateEntity] = {}
        by_name: dict[str, StateEntity] = {}
        for entity in self._entities:
            if entity.entity_id in by_id:
                raise ValueError(f"duplicate entity_id: {entity.entity_id}")
            by_id[entity.entity_id] = entity
            for candidate in (entity.legal_name, *entity.aliases):
                normalized = normalize_entity_name(candidate)
                existing = by_name.get(normalized)
                if existing is not None and existing.entity_id != entity.entity_id:
                    raise ValueError(f"entity name collision: {candidate}")
                by_name[normalized] = entity
        self._by_id = by_id
        self._by_name = by_name

    @property
    def entities(self) -> tuple[StateEntity, ...]:
        return self._entities

    def get(self, entity_id: str) -> StateEntity | None:
        return self._by_id.get(entity_id)

    def match(self, name: str) -> StateEntity | None:
        """Return an exact/alias match; never use substring matching."""

        if not isinstance(name, str) or not name.strip():
            return None
        return self._by_name.get(normalize_entity_name(name))

    def match_entity(self, name: str) -> StateEntity | None:
        return self.match(name)

    def is_core(self, entity_id: str) -> bool:
        entity = self.get(entity_id)
        return entity is not None and entity.group == "stabilization_core"


def normalize_entity_name(name: str) -> str:
    """Normalize presentation differences without broadening identity."""

    if not isinstance(name, str):
        raise TypeError("name must be str")
    normalized = unicodedata.normalize("NFKC", name).strip()
    return re.sub(r"\s+", "", normalized)


DEFAULT_STATE_ENTITY_REGISTRY = StateEntityRegistry()
