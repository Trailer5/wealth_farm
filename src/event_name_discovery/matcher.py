"""Deterministic Chinese name and phonetic candidate generation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from decimal import Decimal

from .models import (
    AssetNameSnapshot,
    EventPhrase,
    NameMatchEvidence,
    NameMatchType,
)

MATCHER_VERSION = "event_name_matcher_v1"
_LOW_INFORMATION_COMPONENTS = frozenset(
    ("中国", "上海", "深圳", "北京", "科技", "股份", "集团")
)


class ChinesePhoneticEncoder:
    """Thin reviewed adapter around optional python-pinyin."""

    def __init__(
        self,
        converter: Callable[[str, bool], str] | None = None,
    ) -> None:
        self.converter = converter

    def full(self, text: str) -> str | None:
        return self._convert(text, initials=False)

    def initials(self, text: str) -> str | None:
        return self._convert(text, initials=True)

    def _convert(self, text: str, *, initials: bool) -> str | None:
        if self.converter is not None:
            value = self.converter(text, initials)
            return _ascii_key(value) or None
        try:
            from pypinyin import Style, lazy_pinyin
        except ImportError:
            return None
        style = Style.FIRST_LETTER if initials else Style.NORMAL
        return _ascii_key(
            "".join(
                lazy_pinyin(
                    text,
                    style=style,
                    errors="ignore",
                )
            )
        ) or None


class EventNameMatcher:
    """Generate explainable lexical candidates without a weighted score."""

    def __init__(
        self,
        phonetic_encoder: ChinesePhoneticEncoder | None = None,
        *,
        max_phonetic_distance: int = 2,
    ) -> None:
        if max_phonetic_distance < 0:
            raise ValueError(
                "max_phonetic_distance must be non-negative"
            )
        self.phonetic_encoder = (
            phonetic_encoder or ChinesePhoneticEncoder()
        )
        self.max_phonetic_distance = max_phonetic_distance

    def match(
        self,
        *,
        phrases: Sequence[EventPhrase],
        assets: Sequence[AssetNameSnapshot],
        event_date: str,
    ) -> dict[str, tuple[NameMatchEvidence, ...]]:
        matches: dict[str, tuple[NameMatchEvidence, ...]] = {}
        for asset in assets:
            if not asset.active_on(event_date):
                continue
            values = []
            for (
                stock_name,
                name_source_type,
                name_confidence,
            ) in asset.name_variants:
                if (
                    name_source_type != "official"
                    and name_confidence < 0.8
                ):
                    continue
                for phrase in phrases:
                    evidence = self._match_one(
                        asset.asset_id,
                        stock_name,
                        phrase,
                        name_source_type=name_source_type,
                        name_confidence=name_confidence,
                    )
                    if evidence is not None:
                        values.append(evidence)
            if values:
                values.sort(
                    key=lambda item: (
                        item.tier,
                        item.phrase_priority,
                        (
                            item.phonetic_distance
                            if item.phonetic_distance is not None
                            else 999
                        ),
                        item.stock_name,
                        item.phrase_id,
                    )
                )
                matches[asset.asset_id] = tuple(values)
        return matches

    def _match_one(
        self,
        asset_id: str,
        stock_name: str,
        phrase: EventPhrase,
        *,
        name_source_type: str,
        name_confidence: Decimal,
    ) -> NameMatchEvidence | None:
        stock_key = normalize_name(stock_name)
        phrase_key = normalize_name(phrase.text)
        components = tuple(
            component
            for item in phrase.components
            if (component := normalize_name(item))
        )
        if (
            len(phrase_key) >= 2
            and (
                phrase_key in stock_key
            )
        ):
            return _evidence(
                asset_id=asset_id,
                stock_name=stock_name,
                phrase=phrase,
                match_type=NameMatchType.EXACT_SUBSTRING,
                tier=1,
                matched_components=(phrase.text,),
                stock_key=stock_key,
                phrase_key=phrase_key,
                explanation=(
                    f"事件短语“{phrase.text}”与证券名称"
                    f"“{stock_name}”存在直接包含关系"
                ),
                name_source_type=name_source_type,
                name_confidence=name_confidence,
            )
        if (
            len(components) >= 2
            and sum(len(item) for item in components) >= 2
            and any(
                item not in _LOW_INFORMATION_COMPONENTS
                for item in components
            )
            and _components_in_order(stock_key, components)
        ):
            return _evidence(
                asset_id=asset_id,
                stock_name=stock_name,
                phrase=phrase,
                match_type=NameMatchType.COMPOSITIONAL,
                tier=2,
                matched_components=phrase.components,
                stock_key=stock_key,
                phrase_key=phrase_key,
                explanation=(
                    f"证券名称“{stock_name}”同时包含事件组合片段"
                    f"{'、'.join(phrase.components)}"
                ),
                name_source_type=name_source_type,
                name_confidence=name_confidence,
            )

        stock_phonetic = self.phonetic_encoder.full(stock_name)
        phrase_phonetic = (
            _ascii_key(phrase.phonetic_form)
            if phrase.phonetic_form is not None
            else self.phonetic_encoder.full(phrase.text)
        )
        if stock_phonetic and phrase_phonetic:
            distance = levenshtein(stock_phonetic, phrase_phonetic)
            longest = max(len(stock_phonetic), len(phrase_phonetic))
            if (
                longest >= 4
                and distance <= self.max_phonetic_distance
                and _distance_ratio(distance, longest) <= 0.34
            ):
                return _evidence(
                    asset_id=asset_id,
                    stock_name=stock_name,
                    phrase=phrase,
                    match_type=NameMatchType.PHONETIC,
                    tier=3,
                    matched_components=(phrase.text,),
                    stock_key=stock_key,
                    phrase_key=phrase_key,
                    stock_phonetic=stock_phonetic,
                    phrase_phonetic=phrase_phonetic,
                    phonetic_distance=distance,
                    explanation=(
                        f"“{stock_name}”拼音 {stock_phonetic} 与"
                        f"“{phrase.text}”拼音 {phrase_phonetic} "
                        f"编辑距离为 {distance}"
                    ),
                    name_source_type=name_source_type,
                    name_confidence=name_confidence,
                )

        stock_initials = self.phonetic_encoder.initials(stock_name)
        phrase_initials = (
            _ascii_key(phrase.initials)
            if phrase.initials is not None
            else None
        )
        if (
            stock_initials
            and phrase_initials
            and len(stock_initials) >= 3
            and stock_initials == phrase_initials
        ):
            return _evidence(
                asset_id=asset_id,
                stock_name=stock_name,
                phrase=phrase,
                match_type=NameMatchType.PINYIN_INITIALS,
                tier=4,
                matched_components=(phrase.text,),
                stock_key=stock_key,
                phrase_key=phrase_key,
                stock_phonetic=stock_initials,
                phrase_phonetic=phrase_initials,
                phonetic_distance=0,
                explanation=(
                    f"“{stock_name}”与“{phrase.text}”"
                    f"拼音首字母均为 {stock_initials}"
                ),
                name_source_type=name_source_type,
                name_confidence=name_confidence,
            )
        return None


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"[^\u4e00-\u9fff0-9a-z]+", "", normalized)


def levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def _distance_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator


def _components_in_order(
    stock_name: str,
    components: tuple[str, ...],
) -> bool:
    position = 0
    for component in components:
        found = stock_name.find(component, position)
        if found < 0:
            return False
        position = found + len(component)
    return True


def _ascii_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _evidence(
    *,
    asset_id: str,
    stock_name: str,
    phrase: EventPhrase,
    match_type: NameMatchType,
    tier: int,
    matched_components: tuple[str, ...],
    stock_key: str,
    phrase_key: str,
    explanation: str,
    name_source_type: str,
    name_confidence: Decimal,
    stock_phonetic: str | None = None,
    phrase_phonetic: str | None = None,
    phonetic_distance: int | None = None,
) -> NameMatchEvidence:
    return NameMatchEvidence(
        asset_id=asset_id,
        stock_name=stock_name,
        phrase_id=phrase.phrase_id,
        phrase_text=phrase.text,
        phrase_priority=phrase.priority,
        attention_channel=phrase.channel,
        direction=phrase.direction,
        match_type=match_type,
        tier=tier,
        matched_components=matched_components,
        normalized_stock_name=stock_key,
        normalized_phrase=phrase_key,
        stock_phonetic=stock_phonetic,
        phrase_phonetic=phrase_phonetic,
        phonetic_distance=phonetic_distance,
        name_source_type=name_source_type,
        name_confidence=name_confidence,
        explanation=explanation,
    )
