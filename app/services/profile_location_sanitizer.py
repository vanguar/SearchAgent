from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_WHITESPACE_RE = re.compile(r"\s+")

_NON_GEOGRAPHIC_LOCATION_PHRASES = frozenset(
    {
        "remote",
        "worldwide",
        "worldwide remote",
        "remote worldwide",
        "international remote",
        "international remote companies",
        "remote companies",
        "worldwide companies",
        "international companies",
        "global remote",
        "fully remote",
        "100% remote",
        "homeoffice",
        "home office",
        "work from home",
        "удалённо",
        "удаленно",
        "удалённая работа",
        "удаленная работа",
        "дистанционно",
        "дистанционная работа",
        "по всему миру",
        "удалённо по всему миру",
        "удаленно по всему миру",
        "международные удалённые компании",
        "международные удаленные компании",
        "международные компании",
    }
)

_REMOTE_NEGATION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bне\s+(?:ищу|хочу|рассматриваю)\b.{0,50}\b(?:remote|удал[её]н\w*|дистанц\w*)\b",
        r"\b(?:удал[её]н\w*\s+работ\w*|remote(?:\s+work)?)\b.{0,40}\bне\s+(?:нуж\w*|интерес\w*)\b",
        r"\b(?:только\s+локальн\w*\s+работ\w*|local(?:\s+work)?\s+only|nur\s+vor\s+ort)\b",
        r"\b(?:no|not\s+looking\s+for)\s+remote\b",
        r"\b(?:kein|keine)\s+remote(?:arbeit)?\b",
    )
)

_REMOTE_POSITIVE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\b(?:ищу|хочу|рассматриваю)\b.{0,50}\b(?:remote|удал[её]н\w*|дистанц\w*)\b",
        r"\b(?:только|полностью)\s+(?:remote|удал[её]н\w*|дистанц\w*)\b",
        r"\b(?:remote|удал[её]н\w*|дистанц\w*)\s+(?:работ\w*|формат\w*)\b",
        r"\b(?:fully|100\s*%)\s+remote\b",
        r"\bwork\s+from\s+home\b",
        r"\bhome\s*office\b",
    )
)

_INTERNATIONAL_REMOTE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bworldwide\b",
        r"\bglobal\s+remote\b",
        r"\binternational\s+remote\b",
        r"\binternational\s+companies\b",
        r"\bпо\s+всему\s+миру\b",
        r"\bмеждународн\w*\b.{0,40}\b(?:компан\w*|remote|удал[её]н\w*)\b",
    )
)


def sanitize_preferred_locations(
    values: Sequence[str] | None,
    *,
    aliases: Mapping[str, str] | None = None,
) -> list[str]:
    """Keep geography while removing explicit remote/search-intent phrases."""
    normalized_aliases = aliases or {}
    cleaned: list[str] = []
    seen: set[str] = set()

    for raw_value in values or ():
        value = _collapse_whitespace(raw_value)
        if not value:
            continue
        lookup_key = value.casefold()
        if _is_non_geographic_search_intent(lookup_key):
            continue
        canonical = normalized_aliases.get(lookup_key, value)
        dedupe_key = canonical.casefold()
        if dedupe_key in seen:
            continue
        cleaned.append(canonical)
        seen.add(dedupe_key)

    return cleaned


def resolve_remote_intent(
    raw_text: str,
    *,
    extracted_remote_allowed: bool | None,
    extracted_international_remote_allowed: bool | None,
    extracted_work_modes: Sequence[str] | None = None,
) -> tuple[bool | None, bool | None]:
    """Resolve remote intent deterministically, giving explicit negation priority."""
    normalized_text = _collapse_whitespace(raw_text).casefold()
    if any(pattern.search(normalized_text) for pattern in _REMOTE_NEGATION_PATTERNS):
        return False, False

    extracted_remote_mode = any(mode.strip().casefold() == "remote" for mode in extracted_work_modes or ())
    explicit_remote = any(pattern.search(normalized_text) for pattern in _REMOTE_POSITIVE_PATTERNS)
    remote_allowed = extracted_remote_allowed
    if explicit_remote or extracted_remote_mode or extracted_international_remote_allowed is True:
        remote_allowed = True

    international_marker = any(pattern.search(normalized_text) for pattern in _INTERNATIONAL_REMOTE_PATTERNS)
    international_remote_allowed = extracted_international_remote_allowed
    if remote_allowed is True and international_marker:
        international_remote_allowed = True
    elif remote_allowed is False:
        international_remote_allowed = False

    return remote_allowed, international_remote_allowed


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.strip())


def _is_non_geographic_search_intent(value: str) -> bool:
    normalized = value.strip(" ,.;:")
    if normalized in _NON_GEOGRAPHIC_LOCATION_PHRASES:
        return True
    hyphen_folded = _WHITESPACE_RE.sub(" ", normalized.replace("-", " "))
    return hyphen_folded in _NON_GEOGRAPHIC_LOCATION_PHRASES
