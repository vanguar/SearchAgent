"""Deterministic post-processing for ProfileExtractionResult.

Applied after LLM extraction (or conservative fallback). Responsibilities:
- Canonical value normalization (country, language levels, legal status, regions)
- Role family classification (fills desired/excluded_role_families)
- Exclusion priority: excluded always wins over desired (role + family level)
- driving_license != Driver role (unless explicit driving intent is evidenced)
- preferred_regions != current_city (unless explicitly stated as residence)
- physical_work_allowed=false → add physical families to excluded
- Build questions_needed for truly missing critical fields
"""
from __future__ import annotations

from app.services.profile_extraction_model import ProfileExtractionResult
from app.services.profile_role_taxonomy import (
    RoleFamily,
    classify_roles,
    get_physical_families,
    is_it_profile,
    requires_shift_question,
)

# Canonical country normalisations
_COUNTRY_MAP: dict[str, str] = {
    "германия": "Germany",
    "германии": "Germany",
    "germany": "Germany",
    "deutschland": "Germany",
    "de": "Germany",
}

# Canonical language level normalisations
_LANG_LEVEL_MAP: dict[str, str] = {
    "a1": "basic", "a2": "basic",
    "b1": "intermediate", "working proficiency": "intermediate", "intermediate": "intermediate",
    "b2": "advanced", "c1": "advanced", "c2": "advanced", "advanced": "advanced",
    "none": "none", "нет": "none", "zero": "none", "no": "none",
    "basic": "basic", "базовый": "basic",
    "свободно": "advanced", "fluent": "advanced",
}

_LEGAL_STATUS_MAP: dict[str, str] = {
    "section_24": "section_24",
    "§24": "section_24",
    "24": "section_24",
    "paragraph_24": "section_24",
    "eu_citizen": "eu_citizen",
    "work_visa": "work_visa",
    "other": "other",
}

# Phrases that indicate explicit intent to work as a driver
_DRIVER_INTENT_PHRASES: tuple[str, ...] = (
    "ищу работу водителем",
    "работа водителем",
    "хочу работать водителем",
    "ищу водителя",
    "ищу курьера",
    "работаю водителем",
    "delivery driver",
    "driver job",
    "fahrer stelle",
)

# German city name mappings for preferred_regions normalisation
_REGION_ALIASES: dict[str, str] = {
    "berlin": "Berlin",
    "берлин": "Berlin",
    "hamburg": "Hamburg",
    "гамбург": "Hamburg",
    "münchen": "München",
    "munich": "München",
    "мюнхен": "München",
    "rostock": "Rostock",
    "росток": "Rostock",
    "deutschland": "Deutschland",
    "germany": "Deutschland",
    "germany-wide": "Deutschland",
    "whole germany": "Deutschland",
    "вся германия": "Deutschland",
    "по всей германии": "Deutschland",
    "worldwide remote": "worldwide remote",
    "remote worldwide": "worldwide remote",
    "international remote": "international remote",
    "international remote companies": "international remote companies",
    "eu": "EU",
    "european union": "EU",
    "ес": "EU",
    "uk": "UK",
    "united kingdom": "UK",
    "usa": "USA",
    "us": "USA",
    "united states": "USA",
    "сша": "USA",
    "canada": "Canada",
    "канада": "Canada",
    "frankfurt": "Frankfurt",
    "франкфурт": "Frankfurt",
    "köln": "Köln",
    "cologne": "Köln",
    "кёльн": "Köln",
    "stuttgart": "Stuttgart",
    "штутгарт": "Stuttgart",
    "düsseldorf": "Düsseldorf",
    "дюссельдорф": "Düsseldorf",
    "hannover": "Hannover",
    "ганновер": "Hannover",
    "bremen": "Bremen",
    "бремен": "Bremen",
    "stralsund": "Stralsund",
    "штральзунд": "Stralsund",
    "greifswald": "Greifswald",
    "грайфсвальд": "Greifswald",
    "tribsees": "Tribsees",
    "трибзес": "Tribsees",
    "трибсес": "Tribsees",
    "wismar": "Wismar",
    "висмар": "Wismar",
    "schwerin": "Schwerin",
    "шверин": "Schwerin",
    "nürnberg": "Nürnberg",
    "нюрнберг": "Nürnberg",
    "leipzig": "Leipzig",
    "лейпциг": "Leipzig",
    "dresden": "Dresden",
    "дрезден": "Dresden",
}


def process(result: ProfileExtractionResult, raw_text_lower: str = "") -> ProfileExtractionResult:
    """Apply all post-processing rules and return a normalised copy."""
    data = result.model_dump()

    _normalize_country(data)
    _normalize_language_levels(data)
    _normalize_legal_status(data)
    _normalize_regions(data)
    _ensure_remote_worldwide_regions(data, raw_text_lower)
    _normalize_lists(data)

    _classify_role_families(data)
    _enforce_exclusion_priority(data)
    _protect_driver_role(data, raw_text_lower)
    _protect_current_city(data, result)
    _enforce_physical_work_exclusions(data)
    _auto_set_work_authorization(data)

    return ProfileExtractionResult.model_validate(data)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_country(data: dict) -> None:
    country = (data.get("current_country") or "").strip().lower()
    if country:
        data["current_country"] = _COUNTRY_MAP.get(country, data["current_country"].strip().title())


def _normalize_language_levels(data: dict) -> None:
    for field in ("german_level", "english_level"):
        raw = (data.get(field) or "").strip().lower()
        if raw:
            data[field] = _LANG_LEVEL_MAP.get(raw, raw)


def _normalize_legal_status(data: dict) -> None:
    raw = (data.get("legal_status") or "").strip().lower()
    if raw:
        data["legal_status"] = _LEGAL_STATUS_MAP.get(raw, raw)


def _normalize_regions(data: dict) -> None:
    regions = data.get("preferred_regions") or []
    normalised: list[str] = []
    seen: set[str] = set()
    for r in regions:
        canonical = _REGION_ALIASES.get(r.strip().lower(), r.strip())
        if canonical not in seen:
            normalised.append(canonical)
            seen.add(canonical)
    data["preferred_regions"] = normalised


def _ensure_remote_worldwide_regions(data: dict, raw_text_lower: str) -> None:
    regions = list(data.get("preferred_regions") or [])
    seen = {region.lower() for region in regions}

    remote_worldwide = (
        data.get("international_remote_allowed") is True
        or "worldwide remote" in raw_text_lower
        or "remote worldwide" in raw_text_lower
        or "по всему миру" in raw_text_lower
        or "международные remote" in raw_text_lower
        or "international remote" in raw_text_lower
        or "remote-компани" in raw_text_lower
    )
    if not remote_worldwide:
        return

    additions: list[str] = ["worldwide remote", "international remote companies"]
    raw_region_map: tuple[tuple[str, str], ...] = (
        ("germany", "Deutschland"),
        ("германи", "Deutschland"),
        ("deutschland", "Deutschland"),
        (" eu", "EU"),
        (" ес", "EU"),
        (" uk", "UK"),
        (" usa", "USA"),
        (" сша", "USA"),
        ("canada", "Canada"),
        ("канада", "Canada"),
    )
    for token, canonical in raw_region_map:
        if token in raw_text_lower:
            additions.append(canonical)

    for addition in additions:
        if addition.lower() not in seen:
            regions.append(addition)
            seen.add(addition.lower())
    data["preferred_regions"] = regions


def _normalize_lists(data: dict) -> None:
    for field in ("desired_roles", "excluded_roles", "preferred_regions",
                  "desired_role_families", "excluded_role_families",
                  "core_stack", "tools", "native_languages"):
        lst = data.get(field) or []
        data[field] = list(dict.fromkeys(item.strip() for item in lst if item and item.strip()))


# ---------------------------------------------------------------------------
# Role family classification
# ---------------------------------------------------------------------------

def _classify_role_families(data: dict) -> None:
    """Fill desired/excluded_role_families from taxonomy if not provided by LLM."""
    desired_families = set(data.get("desired_role_families") or [])
    excluded_families = set(data.get("excluded_role_families") or [])

    # Classify from roles
    for family in classify_roles(data.get("desired_roles") or []):
        desired_families.add(family.value)

    for family in classify_roles(data.get("excluded_roles") or []):
        excluded_families.add(family.value)

    data["desired_role_families"] = sorted(desired_families)
    data["excluded_role_families"] = sorted(excluded_families)


# ---------------------------------------------------------------------------
# Safety rules
# ---------------------------------------------------------------------------

def _enforce_exclusion_priority(data: dict) -> None:
    """Excluded always wins: remove from desired anything that appears in excluded."""
    excluded_lower = {r.lower() for r in (data.get("excluded_roles") or [])}
    excluded_families = set(data.get("excluded_role_families") or [])

    # Role-level exclusion
    data["desired_roles"] = [
        r for r in (data.get("desired_roles") or [])
        if r.lower() not in excluded_lower
    ]

    # Family-level exclusion
    data["desired_role_families"] = [
        f for f in (data.get("desired_role_families") or [])
        if f not in excluded_families
    ]


def _protect_driver_role(data: dict, raw_text_lower: str) -> None:
    """Remove Driver/Водитель from desired_roles unless there is explicit driving-job intent.

    Merely having a driving license does not mean the person wants to drive professionally.
    """
    driver_aliases = {"водитель", "driver", "fahrer", "kurier", "курьер"}
    has_driver_role = any(
        role.lower() in driver_aliases or any(a in role.lower() for a in driver_aliases)
        for role in (data.get("desired_roles") or [])
    )
    if not has_driver_role:
        return

    # Check evidence from LLM first
    evidence = (data.get("evidence_by_field") or {}).get("desired_roles", "") or ""
    evidence_supports_driver = any(p in evidence.lower() for p in _DRIVER_INTENT_PHRASES)

    # Check raw text if available
    text_supports_driver = any(p in raw_text_lower for p in _DRIVER_INTENT_PHRASES)

    if not (evidence_supports_driver or text_supports_driver):
        data["desired_roles"] = [
            r for r in (data.get("desired_roles") or [])
            if not (r.lower() in driver_aliases or any(a in r.lower() for a in driver_aliases))
        ]
        # Also remove driving family from desired if it got there only from the license
        driving_val = RoleFamily.DRIVING_DELIVERY.value
        if driving_val in (data.get("desired_role_families") or []):
            data["desired_role_families"] = [
                f for f in data["desired_role_families"] if f != driving_val
            ]


def _protect_current_city(
    data: dict, original: ProfileExtractionResult
) -> None:
    """Clear current_city if it looks like it was inferred from preferred_regions.

    We trust LLM evidence: if evidence_by_field["current_city"] is empty/null
    and the city is also in preferred_regions, it was likely inferred — clear it.
    """
    city = data.get("current_city")
    if not city:
        return

    evidence = (data.get("evidence_by_field") or {}).get("current_city") or ""
    preferred = data.get("preferred_regions") or []

    # If LLM provided no explicit evidence for current_city, it might be hallucinated
    if not evidence.strip():
        data["current_city"] = None
        return

    # If the evidenced city is identical to one of the preferred search regions
    # AND there are multiple preferred regions, the user listed search targets, not home
    if city in preferred and len(preferred) > 1:
        data["current_city"] = None


def _enforce_physical_work_exclusions(data: dict) -> None:
    """When physical_work_allowed=False, ensure physical families are excluded."""
    if data.get("physical_work_allowed") is not True and data.get("physical_work_allowed") is not None:
        if data["physical_work_allowed"] is False:
            excluded_families = set(data.get("excluded_role_families") or [])
            for fam in get_physical_families():
                excluded_families.add(fam.value)
            data["excluded_role_families"] = sorted(excluded_families)

            # Remove physical roles from desired
            physical_vals = {f.value for f in get_physical_families()}
            data["desired_role_families"] = [
                f for f in (data.get("desired_role_families") or [])
                if f not in physical_vals
            ]


def _auto_set_work_authorization(data: dict) -> None:
    """section_24 implies work_authorization=True."""
    if data.get("legal_status") == "section_24" and data.get("work_authorization") is None:
        data["work_authorization"] = True
