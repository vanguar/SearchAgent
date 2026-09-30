"""Public API for multilingual role-intent normalization.

Lexicon data (RoleIntent type, ROLE_INTENT_MAP, sorted keys) lives in
_role_intent_lexicon.py to keep this module focused on matching logic only.

Две вещи, которые здесь решаются и которых раньше не было.

1. Написание без умлаутов. Клавиатура без немецкой раскладки — обычное дело для
   пользователя этого приложения, и «schweisser», «verkaufer», «baecker» должны
   попадать в те же записи словаря, что «schweißer», «verkäufer», «bäcker».
   Раньше запрос сравнивался с ключами как есть, и такие написания либо не
   находились вовсе, либо находили не ту роль: «kuchenhelfer» давал «helfer».

2. Составное название не переписывается в общее. Матч по подстроке остаётся —
   он нужен, чтобы узнать СЕМЕЙСТВО незнакомого композита («Maschinenschlosser»
   — это слесарь), — но запрос к источнику при этом сохраняется тем, что написал
   человек. Иначе «Fahrer Fahrzeuglogistik» уходил в источник как «logistik», а
   «Überführungsfahrer» — как «fahrer», и выдача не имела отношения к запросу.

   Кириллицу это не затрагивает: русское слово источник не понимает, поэтому для
   него перевод в немецкий ключевик остаётся обязательным.
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata

from app.services._role_intent_lexicon import (  # noqa: F401  (re-export RoleIntent)
    IT_WORD_RE,
    ROLE_INTENT_MAP,
    SORTED_INTENT_KEYS,
    RoleIntent,
)

__all__ = ["RoleIntent", "normalize_role_intent"]

_CYRILLIC_RE = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^0-9a-zа-яёіїєґ]+", re.IGNORECASE)
# Немецкие ASCII-диграфы: так пишут умлауты, когда их нечем набрать.
_DIGRAPH_RE = re.compile(r"ae|oe|ue")
_DIGRAPH_REPLACEMENTS = {"ae": "a", "oe": "o", "ue": "u"}
# Ниже этой длины ключ не ищется внутри слова: короткий фрагмент совпадает со
# слишком многим. Токен "it" разбирается отдельно, по границам слова.
_MIN_SUBSTRING_KEY_CHARS = 4


def _fold(text: str) -> str:
    """Регистр, умлауты и пунктуация приведены к одному виду.

    ß → ss делает casefold; ü/ä/ö разбираются NFKD и теряют диакритику. Дефисы и
    прочая пунктуация становятся пробелами, поэтому «водитель-курьер» и
    «водитель курьер» — один и тот же ключ.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_NON_WORD_RE.sub(" ", without_marks).split())


def _digraph_variant(folded: str) -> str | None:
    """Написание, где ae/oe/ue означали умлаут. None, если диграфов нет."""
    if not _DIGRAPH_RE.search(folded):
        return None
    return _DIGRAPH_RE.sub(lambda match: _DIGRAPH_REPLACEMENTS[match.group(0)], folded)


def _build_folded_keys() -> tuple[dict[str, str], tuple[str, ...]]:
    """Свёрнутый ключ → исходный ключ словаря, плюс порядок перебора (длинные первыми).

    При совпадении свёрнутых форм («bäcker» и гипотетический «backer») остаётся
    первый по порядку словаря: порядок в словаре осмыслен, и молча менять его на
    случайный нельзя.
    """
    folded_to_original: dict[str, str] = {}
    for key in SORTED_INTENT_KEYS:
        folded = _fold(key)
        if folded and folded not in folded_to_original:
            folded_to_original[folded] = key
    order = tuple(sorted(folded_to_original, key=len, reverse=True))
    return folded_to_original, order


_FOLDED_TO_ORIGINAL_KEY, _FOLDED_KEYS_BY_LENGTH = _build_folded_keys()


def _query_forms(query: str) -> tuple[str, ...]:
    """Свёрнутые написания запроса, от наиболее вероятного к запасному."""
    folded = _fold(query)
    if not folded:
        return ()
    variant = _digraph_variant(folded)
    return (folded,) if variant is None or variant == folded else (folded, variant)


def _match_whole_string(form: str) -> str | None:
    return form if form in _FOLDED_TO_ORIGINAL_KEY else None


def _match_whole_tokens(form: str) -> str | None:
    """Самый длинный ключ, совпавший с ЦЕЛЫМИ словами запроса.

    Целое слово важнее любой подстроки: в «Fahrer Fahrzeuglogistik» ключ
    «logistik» сидит внутри «fahrzeuglogistik» и профессию не называет, а
    «fahrer» — называет.
    """
    padded = f" {form} "
    for folded_key in _FOLDED_KEYS_BY_LENGTH:
        if f" {folded_key} " in padded:
            return folded_key
    return None


def _match_substring(form: str) -> str | None:
    """Самый длинный ключ, найденный внутри слова, — для незнакомых композитов."""
    for folded_key in _FOLDED_KEYS_BY_LENGTH:
        if len(folded_key) < _MIN_SUBSTRING_KEY_CHARS:
            continue
        if folded_key in form:
            return folded_key
    return None


def normalize_role_intent(query: str) -> RoleIntent | None:
    """Translate a raw user query into a RoleIntent.

    Возвращает None, когда запрос не опознан ни одним ключом словаря.

    `primary_de` — то, что нужно отправить источнику: канонический немецкий
    ключевик для опознанной целиком роли, либо исходный запрос пользователя, если
    словарь узнал лишь часть составного названия. `canonical_keyword` в обоих
    случаях даёт канон для подбора синонимов.
    """
    if not query.strip():
        return None

    forms = _query_forms(query)
    if not forms:
        return None

    # Точное совпадение важнее всего и проверяется по всем написаниям сразу:
    # «kuchenhelfer» должен найти «küchenhelfer», а не «helfer» по подстроке.
    for form in forms:
        folded_key = _match_whole_string(form)
        if folded_key is not None:
            return ROLE_INTENT_MAP[_FOLDED_TO_ORIGINAL_KEY[folded_key]]

    # Токен "it" — только по границам слова, иначе он ловится внутри "quality".
    if IT_WORD_RE.search(forms[0]):
        return _partial_intent(ROLE_INTENT_MAP["it"], query)

    for matcher in (_match_whole_tokens, _match_substring):
        for form in forms:
            folded_key = matcher(form)
            if folded_key is not None:
                return _partial_intent(ROLE_INTENT_MAP[_FOLDED_TO_ORIGINAL_KEY[folded_key]], query)
    return None


def _partial_intent(intent: RoleIntent, query: str) -> RoleIntent:
    """Намерение по части запроса: семейство из словаря, запрос — от пользователя.

    Кириллический запрос всё равно переводится: источник его не понимает, и
    канонический немецкий ключевик здесь единственный работающий вариант.
    """
    if _CYRILLIC_RE.search(query):
        return intent
    stripped = query.strip()
    if stripped.casefold() == intent.primary_de.casefold():
        return intent
    return dataclasses.replace(intent, primary_de=stripped, canonical_de=intent.canonical_keyword)
