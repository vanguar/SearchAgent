"""Место работы, названное в тексте, против места, которое указал источник.

Агентство публикует вакансию со своего адреса, а работа у клиента в другом
городе: Adzuna даёт «Kassebohm, Rostock», а в тексте — «für einen Kunden … in
Güstrow». Если такой город далеко от указанного места, верить нужно тексту.

Правило осторожное: только явные формулировки («für einen Kunden … in X»,
«Einsatzort: X», «Arbeitsort: X»), только известный населённый пункт и только
когда у источника нет почтового индекса — точный адрес (BA) надёжнее текста.
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.core.relevance_config import get_relevance_config
from app.services.geo_distance import canonical_city, distance_km, is_known_place, location_matches_city, resolve_point
from app.services.hashers import normalize_text_for_fingerprint

# Название населённого пункта: слово с заглавной, через дефис или с уточнением
# «an der», «am», «bei», «im» («Brandenburg an der Havel», «Roggentin bei Rostock»).
_PLACE = r"(?P<place>[A-ZÄÖÜ][\wäöüß]+(?:-[A-ZÄÖÜ][\wäöüß]+)?(?:\s(?:an\sder|am|bei|im)\s[A-ZÄÖÜ][\wäöüß]+)?)"


@lru_cache(maxsize=4)
def _workplace_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern.replace("{place}", _PLACE)) for pattern in patterns)


@lru_cache(maxsize=8192)
def text_workplace_conflict(
    body: str | None,
    *,
    city: str | None,
    postal_code: str | None,
    location_text: str | None,
) -> tuple[str, float] | None:
    """(город из текста, км), если текст называет место работы далеко от указанного."""
    if postal_code or not body:
        return None
    source_point = resolve_point(city=city, location_text=location_text)
    if source_point is None:
        return None
    # Текст называет и место источника («Medienabholung in Berlin», «Einsatzort:
    # Hamburg»): мест работы несколько, а не другое место вместо указанного.
    if _source_place_in_text(body, city=city, location_text=location_text):
        return None
    config = get_relevance_config()
    for pattern in _workplace_patterns(config.text_workplace_patterns):
        for match in pattern.finditer(body):
            place = match.group("place")
            key = canonical_city(place)
            if not key or not is_known_place(key):
                continue
            # Источник сам называет этот город («Britz, Berlin» и «in Berlin»):
            # расхождение тогда в координатах района, а не в месте работы.
            if location_matches_city(location_text, place):
                return None
            distance = distance_km(resolve_point(city=place), source_point)
            if distance is None:
                continue
            if distance > config.text_workplace_conflict_km:
                return place, float(round(distance))
            # Первый названный город рядом с указанным местом — противоречия нет.
            return None
    return None


def _source_place_in_text(body: str, *, city: str | None, location_text: str | None) -> bool:
    """Упоминает ли текст населённый пункт, который указал источник.

    Земля («Mecklenburg-Vorpommern») не считается: её называют и вакансии из
    соседнего города.
    """
    text = f" {normalize_text_for_fingerprint(body)} "
    parts = [city or "", *re.split(r"[,;/]", location_text or "")]
    names = {
        name for part in parts
        if (name := normalize_text_for_fingerprint(part)) and len(name) > 3 and is_known_place(canonical_city(part))
    }
    return any(f" {name} " in text for name in names)
