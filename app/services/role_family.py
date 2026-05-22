"""Deterministic role-family classification for query intent preservation.

Classifies both Russian query/role text and normalized German vacancy titles
into one of the known role families. Used by the fallback ladder and the
profession-family mismatch filter to prevent cross-family relevance pollution.
"""
from __future__ import annotations

import re
from enum import Enum


class RoleFamily(str, Enum):
    IT = "it"
    WAREHOUSE = "warehouse"
    PRODUCTION = "production"
    CLEANING = "cleaning"
    KITCHEN = "kitchen"
    CONSTRUCTION = "construction"
    AGRICULTURE = "agriculture"
    OFFICE = "office"
    SALES = "sales"
    HEALTHCARE = "healthcare"
    DRIVING = "driving"
    SECURITY = "security"
    GENERIC = "generic"


# Families where cross-family broad fallback is inappropriate.
# For these specific high-skill or domain-specific families, appending generic
# low-barrier manual-labor fallback keywords would inject irrelevant results.
_FAMILY_PROHIBITS_BROAD_FALLBACK: frozenset[RoleFamily] = frozenset({
    RoleFamily.IT,
    RoleFamily.HEALTHCARE,
    RoleFamily.OFFICE,
    RoleFamily.SALES,
    RoleFamily.SECURITY,
    RoleFamily.KITCHEN,
    RoleFamily.DRIVING,  # delivery/driving stays in its own family, not generic labor
})

# Undirected cross-family compatibility pairs (excluding self-compatibility and GENERIC).
# Symmetric by construction: frozenset({A, B}) == frozenset({B, A}).
_COMPATIBLE_SPECIFIC_PAIRS: frozenset[frozenset[RoleFamily]] = frozenset({
    frozenset({RoleFamily.IT, RoleFamily.OFFICE}),
    frozenset({RoleFamily.WAREHOUSE, RoleFamily.PRODUCTION}),
    frozenset({RoleFamily.PRODUCTION, RoleFamily.CONSTRUCTION}),
    frozenset({RoleFamily.OFFICE, RoleFamily.SALES}),
})

# Precompiled word-boundary pattern for "it" as a standalone token.
# Without boundaries, "it" would false-positive inside "quality", "transit", etc.
_IT_WORD_RE: re.Pattern[str] = re.compile(r"\bit\b")

# Ordered list of (family, Russian substring tokens) for query/role classification.
# First match wins; order places high-skill/distinctive families first to avoid
# lower-priority tokens being shadowed.
# NOTE: "it" is intentionally absent here — it is matched via _IT_WORD_RE before
# this loop to prevent false positives inside longer words.
_QUERY_RU_TOKENS: list[tuple[RoleFamily, tuple[str, ...]]] = [
    (RoleFamily.IT, (
        "программ", "разработ", "software", "python", "java",
        "developer", "devops", "helpdesk", "техподдержк", "автомат", "сисадм",
    )),
    (RoleFamily.HEALTHCARE, ("медсестра", "медицин", "санитар", "сиделк")),
    (RoleFamily.KITCHEN, ("повар", "кухн", "кухон", "официант", "гастроном", "ресторан")),
    (RoleFamily.CLEANING, ("уборщик", "уборк", "клинер")),
    (RoleFamily.AGRICULTURE, ("сельск", "садовник", "агроном")),
    (RoleFamily.SECURITY, ("охранник", "секьюрити")),
    (RoleFamily.OFFICE, ("бухгалтер", "секретарь", "офис-менеджер", "делопроизводств")),
    (RoleFamily.SALES, ("продавец", "торговый представ", "менеджер по продаж")),
    (RoleFamily.DRIVING, ("водитель", "курьер")),
    (RoleFamily.CONSTRUCTION, (
        "строитель", "монтажник", "электрик", "сварщик", "слесарь", "сантехник", "плотник",
    )),
    (RoleFamily.WAREHOUSE, (
        "склад", "логистик", "комплектовщик", "кладовщик", "грузчик", "упаков",
    )),
    (RoleFamily.PRODUCTION, ("производ", "сборщик", "оператор станка")),
]

# Ordered list of (family, German substring tokens) for normalized job title classification.
# Input is the output of normalize_text_for_fingerprint() — lowercase ASCII, no special chars.
# More specific compound forms are listed before shorter tokens to reduce false positives.
_TITLE_DE_TOKENS: list[tuple[RoleFamily, tuple[str, ...]]] = [
    (RoleFamily.IT, (
        "softwareentwickler", "software", "entwickler", "developer",
        "programm", "python", "devops", "it support", "it-support",
        "helpdesk", "frontend", "backend", "sysadmin",
        "administrator", "systemadministrator", "servicedesk",
        "support engineer", "support specialist", "technical support",
        "it-techniker", "servicetechniker it",
    )),
    (RoleFamily.HEALTHCARE, (
        "pflegekraft", "pflegehelferin", "altenpflege", "krankenschwester", "krankenhaus",
        "pflege", "medizin", "sanitater",
    )),
    (RoleFamily.KITCHEN, (
        "kuchenhelfer", "kuchenhilfe", "kuchenleiter",
        "kuche", "gastronom", "kochin", "kellner", "restaurant", "catering",
        "koch",  # short token listed after compound forms
    )),
    (RoleFamily.CLEANING, (
        "reinigungskraft", "hausreinigung", "reinigung", "housekeeping",
    )),
    (RoleFamily.AGRICULTURE, ("landwirtschaft", "gartner", "ernte", "garten")),
    (RoleFamily.SECURITY, (
        "sicherheitsmitarbeiter", "sicherheitsdienst", "security", "wachschutz", "bewachung",
    )),
    (RoleFamily.OFFICE, (
        "buchhalter", "buchhaltung", "sachbearbeiter", "assistenz", "verwaltung", "kaufm", "controlling",
        "customer support", "customer service", "support specialist", "kundenservice", "kundenbetreuung",
    )),
    (RoleFamily.SALES, (
        "vertriebsmitarbeiter", "kundenberater", "vertrieb", "verkaufer", "einzelhandel", "kassierer",
        "customer success", "client success", "account manager", "account executive", "business development",
    )),
    (RoleFamily.DRIVING, (
        "kraftfahrer", "lieferfahrer", "zusteller", "kurier",
        "fahrer",  # short token after compound forms
    )),
    (RoleFamily.CONSTRUCTION, (
        "bauhelfer", "baustelle", "zimmerer", "maurer", "elektriker", "schlosser", "monteur", "trockenbau",
    )),
    (RoleFamily.WAREHOUSE, (
        "lagermitarbeiter", "lagerhelfer", "lagerist", "kommissionier",
        "intralogistik", "lagerverwaltung",
        "lager",  # short token after compound forms
    )),
    (RoleFamily.PRODUCTION, (
        "produktionsmitarbeiter", "produktionshelfer", "maschinenbediener", "maschinenfuhrer",
        "produktion", "fertigung",
    )),
]


def classify_query_ru(text: str) -> RoleFamily:
    """Classify a Russian role or query text into a RoleFamily.

    Uses substring matching against known Russian role tokens.
    The short token "it" is matched with word boundaries to avoid false positives
    inside longer words (e.g. "quality", "transit").
    Returns GENERIC when no specific family is detected.
    """
    normalized = text.strip().lower()
    if not normalized:
        return RoleFamily.GENERIC
    # Boundary-safe check for "it" as a standalone word
    if _IT_WORD_RE.search(normalized):
        return RoleFamily.IT
    for family, tokens in _QUERY_RU_TOKENS:
        if any(token in normalized for token in tokens):
            return family
    return RoleFamily.GENERIC


def classify_vacancy_de(normalized_title: str) -> RoleFamily:
    """Classify a normalized German job title into a RoleFamily.

    Takes the output of normalize_text_for_fingerprint(canonical.normalized_title).
    Classifies based on the job title only (not body text) to stay conservative
    and avoid false positives from incidental mentions in descriptions.
    Returns GENERIC when the title does not clearly indicate a specific family.
    """
    for family, tokens in _TITLE_DE_TOKENS:
        if any(token in normalized_title for token in tokens):
            return family
    return RoleFamily.GENERIC


def classify_desired_roles(roles: tuple[str, ...]) -> frozenset[RoleFamily]:
    """Return the set of RoleFamilies represented by the profile's desired roles."""
    return frozenset(classify_query_ru(role.strip().lower()) for role in roles if role.strip())


def is_specific_family(family: RoleFamily) -> bool:
    """True when the family represents a specific domain, not GENERIC."""
    return family is not RoleFamily.GENERIC


def families_are_compatible(query_family: RoleFamily, vacancy_family: RoleFamily) -> bool:
    """True when a vacancy of vacancy_family is acceptable for a query of query_family."""
    if query_family is RoleFamily.GENERIC or vacancy_family is RoleFamily.GENERIC:
        return True
    if query_family is vacancy_family:
        return True
    return frozenset({query_family, vacancy_family}) in _COMPATIBLE_SPECIFIC_PAIRS


def prohibits_broad_fallback(family: RoleFamily) -> bool:
    """True when this query family must not expand into generic low-barrier fallback keywords.

    High-skill and domain-specific families should stay within their own family during
    fallback rather than degrading to generic manual-labor roles.
    """
    return family in _FAMILY_PROHIBITS_BROAD_FALLBACK
