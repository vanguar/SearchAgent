"""Один работодатель — один ключ, как бы его ни записал источник.

«Deutsche Post AG», «DHL», «Deutsche Post und DHL - Niederlassung Betrieb
Berlin 1» — одна группа; «PerZukunft Arbeitsvermittlung GmbH & Co. KG» и
«perZukunft» — один работодатель. Ключ нужен дедупликации, кластеризации
выдачи и поиску шаблонного текста работодателя.

Правила целиком в конфиге (employer_aliases, employer_legal_suffix_patterns):
кода под конкретные компании здесь нет.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.core.relevance_config import RelevanceConfig, get_relevance_config
from app.services.hashers import normalize_text_for_fingerprint

_SPACES_RE = re.compile(r"\s+")


@lru_cache(maxsize=4)
def _compiled(config: RelevanceConfig) -> tuple[tuple[tuple[re.Pattern[str], str], ...], tuple[re.Pattern[str], ...]]:
    aliases = tuple((re.compile(pattern), canonical) for pattern, canonical in config.employer_aliases)
    suffixes = tuple(re.compile(pattern) for pattern in config.employer_legal_suffix_patterns)
    return aliases, suffixes


@lru_cache(maxsize=4096)
def _employer_key_cached(name: str, config: RelevanceConfig) -> str:
    normalized = normalize_text_for_fingerprint(name)
    if not normalized:
        return ""
    aliases, suffixes = _compiled(config)
    for pattern, canonical in aliases:
        if pattern.search(normalized):
            return canonical
    stripped = normalized
    for pattern in suffixes:
        stripped = pattern.sub(" ", stripped)
    stripped = _SPACES_RE.sub(" ", stripped).strip()
    return stripped or normalized


def employer_key(name: str | None, *, config: RelevanceConfig | None = None) -> str:
    """Канонический ключ работодателя; пустая строка — работодатель не указан."""
    if not name:
        return ""
    return _employer_key_cached(name, config or get_relevance_config())
