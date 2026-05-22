"""Deterministic staged keyword fallback ladder for Germany job search.

Expands query candidates only — never touches scoring or filtering.
"""
from __future__ import annotations

from app.services._role_intent_lexicon import RoleIntent
from app.services.role_family import (
    RoleFamily,
    classify_query_ru,
    is_specific_family,
    prohibits_broad_fallback,
)
from app.services.search_models import is_low_german_level

# role_key (lowercase Russian) → ordered German keyword stages
# Index 0 = primary; indices 1+ = deterministic fallback stages
FALLBACK_LADDER: dict[str, tuple[str, ...]] = {
    "склад":            ("lager", "lagermitarbeiter", "lagerhelfer", "helfer"),
    "логистика":        ("logistik", "lagerlogistik", "disponent", "helfer"),
    "упаковка":         ("verpacker", "verpackung", "lagerhelfer", "helfer"),
    "производство":     ("produktion", "produktionshelfer", "maschinenbediener", "helfer"),
    "комплектовщик":    ("kommissionierer", "lagerhelfer", "lager", "helfer"),
    "грузчик":          ("lagerhelfer", "lager", "helfer"),
    "рабочий помощник": ("helfer", "lagerhelfer", "produktionshelfer"),
    "курьер":           ("kurier", "fahrer", "zusteller", "lieferfahrer"),
    "водитель":         ("fahrer", "lkw-fahrer", "kraftfahrer", "kurier"),
    "уборщик":          ("reinigungskraft", "reinigung", "housekeeping", "helfer"),
    "повар":            ("koch", "küchenhelfer", "gastro", "helfer"),
    "официант":         ("kellner", "servicekraft", "gastro", "helfer"),
    "охранник":         ("sicherheitsdienst", "security", "bewachung"),
    "строитель":        ("bauhelfer", "bau", "montage", "helfer"),
    "it":               ("softwareentwickler", "developer", "it-support", "administrator"),
    "программист":      ("softwareentwickler", "developer", "programmierer"),
    "монтажник":        ("monteur", "montage", "techniker", "helfer"),
    "электрик":         ("elektriker", "elektrotechniker", "helfer"),
    "сварщик":          ("schweißer", "metallbau", "schlosser"),
    "слесарь":          ("schlosser", "mechaniker", "monteur"),
    "оператор":         ("maschinenführer", "maschinenbediener", "bediener", "helfer"),
    "кладовщик":        ("lagerist", "lagerverwaltung", "lager"),
    "экспедитор":       ("disponent", "spedition", "logistik"),
    "медсестра":        ("pflegekraft", "pflege", "pflegehelferin"),
    "бухгалтер":        ("buchhalter", "buchhaltung"),
    "менеджер":         ("manager", "teamleiter"),
    "продавец":         ("verkäufer", "einzelhandel", "kassierer"),
    "кассир":           ("kassierer", "verkäufer"),
}

# Low-barrier keywords appended when profile has no usable German/English
# AND the query family allows broad cross-family fallback.
LOW_LANGUAGE_FALLBACK: tuple[str, ...] = (
    "helfer",
    "lagerhelfer",
    "produktionshelfer",
    "reinigungskraft",
)

MAX_DETERMINISTIC_STAGES: int = 2
MAX_LLM_STAGES: int = 1

# Stop condition: at least N non-rejected AND at least M hot
ENOUGH_NON_REJECTED: int = 3
ENOUGH_HOT: int = 1


def get_fallback_keywords(
    *,
    primary_query: str,
    role: str | None,
    low_language: bool,
    query_family: RoleFamily | None = None,
) -> tuple[str, ...]:
    """Return ordered fallback keywords for a role, excluding the primary query.

    IMPORTANT:
    - Unknown roles must NOT automatically degrade into broad low-barrier helper queries.
    - Broad low-language fallback is allowed only for known, compatible low-barrier families.
    """
    role_key = (role or "").strip().lower()
    ladder = FALLBACK_LADDER.get(role_key, ())
    candidates = tuple(kw for kw in ladder if kw != primary_query)

    resolved_family = query_family or (classify_query_ru(role_key) if role_key else RoleFamily.GENERIC)

    # Universal bug fix:
    # if we do not know the role well enough to build a deterministic ladder,
    # do NOT inject generic helper fallback. Better to return few/zero results
    # than to poison the output with irrelevant jobs.
    if not candidates:
        return ()

    broad_fallback_allowed = (
        low_language
        and is_specific_family(resolved_family)
        and not prohibits_broad_fallback(resolved_family)
    )

    if broad_fallback_allowed:
        extra = tuple(
            kw for kw in LOW_LANGUAGE_FALLBACK
            if kw not in candidates and kw != primary_query
        )
        candidates = candidates + extra

    return candidates


def get_intent_fallback_keywords(
    *,
    intent: RoleIntent,
    primary_query: str,
    low_language: bool,
) -> tuple[str, ...]:
    """Return ordered fallback keywords for a known RoleIntent, excluding primary_query."""
    candidates = tuple(kw for kw in intent.synonyms_de if kw != primary_query)
    if low_language and not prohibits_broad_fallback(intent.family):
        extra = tuple(
            kw for kw in LOW_LANGUAGE_FALLBACK
            if kw not in candidates and kw != primary_query
        )
        candidates = candidates + extra
    return candidates


def is_low_language_profile(*, german_level: str | None, english_level: str | None) -> bool:
    """True when both German and English are weak or absent."""
    return is_low_german_level(german_level) and is_low_german_level(english_level)
