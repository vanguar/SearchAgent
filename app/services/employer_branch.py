"""Филиал в названии работодателя против места, которое указал источник.

«Deutsche Post und DHL - Niederlassung Betrieb Ravensburg» с местом «Berlin»:
в тексте Illertissen и Buch (Бавария), а агрегатор принял Buch за район
Берлина. Филиал называет регион надёжнее, чем угаданное место. Точный адрес с
почтовым индексом (например, координаты BA) надёжнее филиала: агентство из
Plauen вполне публикует работу в Ростоке.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.core.relevance_config import get_relevance_config
from app.services.geo_distance import distance_km, resolve_point
from app.services.hashers import normalize_text_for_fingerprint

_BRANCH_RE = re.compile(r"\bniederlassung\s+(?:betrieb\s+)?(?P<city>[a-z][a-z ]*)")


@lru_cache(maxsize=8192)
def branch_location_conflict(
    company: str | None,
    *,
    city: str | None,
    postal_code: str | None,
    location_text: str | None,
) -> tuple[str, float] | None:
    """(город филиала, км), если филиал далеко от указанного места и адрес неточный."""
    if postal_code:
        return None
    match = _BRANCH_RE.search(normalize_text_for_fingerprint(company))
    if match is None:
        return None
    vacancy_point = resolve_point(city=city, location_text=location_text)
    if vacancy_point is None:
        return None
    words = match.group("city").split()
    for size in range(min(3, len(words)), 0, -1):
        branch = " ".join(words[:size])
        point = resolve_point(city=branch)
        if point is None:
            continue
        distance = distance_km(point, vacancy_point)
        if distance is not None and distance > get_relevance_config().branch_city_conflict_km:
            return branch.title(), float(round(distance))
        return None
    return None
