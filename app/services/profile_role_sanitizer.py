"""Отсев обрывков фразы, попавших в списки ролей профиля.

LLM-экстрактор режет свободный текст пользователя по запятым, и в ``excluded_roles``
попадают куски предложения, а не профессии. Реальный разбор фразы

    "...можно курьером, доставка воды, но без знания немецкого, английского,
     а также в доставку воды, но не Amazon и не Hermes"

дал ``['амазон', 'хермес', 'также в доставку воды', 'но без знания немецкого',
'английского', 'Amazon', 'Hermes']``: два последних элемента — это языковые
пожелания, а "также в доставку воды" противоречит роли "доставка воды" в желаемых.

Список исключений — это жёсткий фильтр (``excluded_role`` прячет вакансию), поэтому
мусор в нём стоит дороже, чем пропущенная запись: одно слово-обрывок может убрать из
выдачи целую категорию работы.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_WHITESPACE_RE = re.compile(r"\s+")

# Служебные слова в начале записи — верный признак того, что элемент списка является
# продолжением предложения, а не самостоятельным названием роли.
_SENTENCE_FRAGMENT_PREFIXES: tuple[str, ...] = (
    "а также",
    "также",
    "но ",
    "но не",
    "а не",
    "и не",
    "или ",
    "и ",
    "а ",
    "плюс ",
    "кроме",
    "можно",
    "желательно",
    "хочу",
    "не хочу",
)

# Записи про владение языком — это не профессии. Но "учитель немецкого" профессия,
# поэтому одного упоминания языка мало: запись отсеивается, только если это голое
# название языка либо оборот про знание/уровень языка.
_LANGUAGE_WORDS: tuple[str, ...] = (
    "немецк",
    "английск",
    "русск",
    "украинск",
    "польск",
    "deutsch",
    "english",
    "englisch",
    "sprache",
    "language",
)
# Насколько длинным может быть грамматическое окончание, чтобы слово всё ещё было
# названием языка, а не составным словом: "английск|ого" — да, "deutsch|lehrer" — нет.
_LANGUAGE_INFLECTION_CHARS = 3
_LANGUAGE_KNOWLEDGE_MARKERS: tuple[str, ...] = (
    "знани",
    "владени",
    "уровень",
    "уровня",
    "язык",
    "kenntnis",
    "level",
    "proficiency",
    "fluent",
)


def _is_language_fragment(normalized: str) -> bool:
    if not any(word in normalized for word in _LANGUAGE_WORDS):
        return False
    if any(marker in normalized for marker in _LANGUAGE_KNOWLEDGE_MARKERS):
        return True
    # Голое название языка ("английского", "deutsch") — не роль. Важно отличать его от
    # профессии, в которую язык входит как корень: "Deutschlehrer" — это работа.
    tokens = normalized.split()
    if len(tokens) != 1:
        return False
    token = tokens[0]
    return any(
        token.startswith(word) and len(token) - len(word) <= _LANGUAGE_INFLECTION_CHARS
        for word in _LANGUAGE_WORDS
    )

# Слишком общие слова: как исключение они выкашивают почти любую вакансию.
_TOO_GENERIC_ROLES: frozenset[str] = frozenset(
    {
        "работа",
        "вакансия",
        "работу",
        "job",
        "jobs",
        "arbeit",
        "все",
        "любая",
        "другое",
        "прочее",
    }
)

_MIN_ROLE_CHARS = 3


def _normalize(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip()).casefold()


def looks_like_role(value: str) -> bool:
    """Похожа ли запись на название роли, а не на обрывок фразы."""
    normalized = _normalize(value)
    if len(normalized) < _MIN_ROLE_CHARS:
        return False
    if normalized in _TOO_GENERIC_ROLES:
        return False
    if _is_language_fragment(normalized):
        return False
    if any(normalized.startswith(prefix) for prefix in _SENTENCE_FRAGMENT_PREFIXES):
        return False
    # Запись без единой буквы (например "3,5") ролью быть не может.
    return bool(re.search(r"[a-zа-яёіїєґ]", normalized))


def sanitize_role_list(values: Iterable[str] | None) -> list[str]:
    """Убрать обрывки фраз и дубликаты, сохранив порядок и исходное написание."""
    if not values:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            continue
        value = _WHITESPACE_RE.sub(" ", raw_value.strip())
        if not value or not looks_like_role(value):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


def drop_excluded_roles_contradicting_desired(
    *,
    desired_roles: Sequence[str],
    excluded_roles: Sequence[str],
) -> list[str]:
    """Снять исключения, которые противоречат явно желаемым ролям.

    Исключение всегда сильнее желания — но только когда пользователь действительно
    исключил другое. "Доставка воды" в желаемых и "также в доставку воды" в
    исключениях означает не запрет, а неверно разрезанную фразу.
    """
    desired_stems = {_role_stems(role) for role in desired_roles if role}
    desired_stems.discard(frozenset())
    if not desired_stems:
        return list(excluded_roles)

    kept: list[str] = []
    for excluded in excluded_roles:
        excluded_stems = _role_stems(excluded)
        # Только ПОЛНОЕ совпадение основ: это та же роль в другой словоформе
        # ("в доставку воды" и "доставка воды"). Более широкое правило сняло бы и
        # осмысленное сужение — "Курьер" в желаемых и "Курьер на авто" в исключениях.
        if excluded_stems and excluded_stems in desired_stems:
            continue
        kept.append(excluded)
    return kept


# Грубое усечение вместо стеммера: нужно лишь сопоставить словоформы одного и того же
# названия роли, а не проводить морфологический разбор.
_STEM_CHARS = 5
_MIN_TOKEN_CHARS = 3


def _role_stems(value: str) -> frozenset[str]:
    return frozenset(
        token[:_STEM_CHARS]
        for token in _normalize(value).split()
        if len(token) >= _MIN_TOKEN_CHARS
    )
